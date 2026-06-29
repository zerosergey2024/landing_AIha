from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from db import get_db_connection
from services.ai_agent import run_agent_with_prompt
from services.diagnostics import (
    DIAGNOSTIC_STATUS_D002_COMPLETED,
    DIAGNOSTIC_STATUS_D002_RUNNING,
    update_diagnostic_status,
)


D002_AGENT_PROMPT_NAME = "mvp_design"


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _load_json_object(value: str | None, label: str) -> dict[str, Any]:
    if not value:
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {label}: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must contain a JSON object")

    return parsed


def _count_non_empty(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_count_non_empty(item) for item in value.values())

    if isinstance(value, list):
        return sum(_count_non_empty(item) for item in value)

    return 1 if value is not None and str(value).strip() else 0


def _sanitize_diagnostic_run(row: Any) -> dict[str, Any]:
    diagnostic_run = dict(row)

    keys_to_remove = {
        "input_pack_token",
        "d001_result",
        "d001_summary",
        "d002_result",
        "d002_summary",
        "d003_result",
        "d003_summary",
        "d004_result",
        "d004_summary",
        "final_result",
        "final_output",
        "diagnostic_report",
        "commercial_proposal",
    }

    for key in keys_to_remove:
        diagnostic_run.pop(key, None)

    return diagnostic_run


def _sanitize_input_pack(input_pack: dict[str, Any]) -> dict[str, Any]:
    raw_payload = _load_json_object(input_pack.get("raw_payload"), "raw_payload")
    normalized_payload = _load_json_object(
        input_pack.get("normalized_payload"),
        "normalized_payload",
    )

    brief_type = raw_payload.get("brief_type") or normalized_payload.get("brief_type")

    industrial_ai_payload = (
        raw_payload.get("industrial_ai", {})
        or normalized_payload.get("industrial_ai", {})
        or {}
    )

    return {
        "id": input_pack.get("id"),
        "diagnostic_run_id": input_pack.get("diagnostic_run_id"),
        "status": input_pack.get("status"),
        "created_at": input_pack.get("created_at"),
        "updated_at": input_pack.get("updated_at"),
        "brief_type": brief_type,
        "brief_version": raw_payload.get("brief_version")
        or normalized_payload.get("brief_version"),
        "source": raw_payload.get("source") or normalized_payload.get("source"),
        "submitted_at": raw_payload.get("submitted_at")
        or normalized_payload.get("submitted_at"),
        "raw_payload_non_empty": _count_non_empty(raw_payload),
        "normalized_payload_non_empty": _count_non_empty(normalized_payload),
        "industrial_ai_non_empty": _count_non_empty(industrial_ai_payload),
    }


def _get_diagnostic_run_row(diagnostic_run_id: int) -> Any:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"Diagnostic run not found: {diagnostic_run_id}")

    return row


def _get_latest_input_pack_row(diagnostic_run_id: int) -> Any:
    _get_diagnostic_run_row(diagnostic_run_id)

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
              AND (
                    (raw_payload IS NOT NULL AND TRIM(raw_payload) != '')
                    OR
                    (normalized_payload IS NOT NULL AND TRIM(normalized_payload) != '')
                  )
            ORDER BY id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if row is None:
        raise ValueError(
            "Diagnostic input pack with payload not found "
            f"for diagnostic_run_id={diagnostic_run_id}"
        )

    return row


def _get_attachments_for_input_pack(
    diagnostic_run_id: int,
    input_pack_id: int,
) -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        try:
            columns = conn.execute(
                "PRAGMA table_info(diagnostic_attachments)"
            ).fetchall()
        except sqlite3.OperationalError:
            return []

        column_names = {row["name"] for row in columns}

        if not column_names:
            return []

        if "input_pack_id" in column_names:
            rows = conn.execute(
                """
                SELECT *
                FROM diagnostic_attachments
                WHERE input_pack_id = ?
                ORDER BY id ASC
                """,
                (input_pack_id,),
            ).fetchall()
        elif "diagnostic_run_id" in column_names:
            rows = conn.execute(
                """
                SELECT *
                FROM diagnostic_attachments
                WHERE diagnostic_run_id = ?
                ORDER BY id ASC
                """,
                (diagnostic_run_id,),
            ).fetchall()
        else:
            return []

    return [dict(row) for row in rows]


def _resolve_payloads(input_pack: dict[str, Any]) -> dict[str, Any]:
    raw_payload = _load_json_object(input_pack.get("raw_payload"), "raw_payload")
    normalized_payload = _load_json_object(
        input_pack.get("normalized_payload"),
        "normalized_payload",
    )

    brief_type = raw_payload.get("brief_type") or normalized_payload.get("brief_type")

    if brief_type == "industrial_ai":
        if not raw_payload:
            raise ValueError(
                "raw_payload is required for Industrial AI before running D-002"
            )

        payload_for_d002 = raw_payload
        payload_source = "raw_payload"

    elif normalized_payload:
        payload_for_d002 = normalized_payload
        payload_source = "normalized_payload"

    elif raw_payload:
        payload_for_d002 = raw_payload
        payload_source = "raw_payload"

    else:
        raise ValueError("raw_payload or normalized_payload is required before D-002")

    return {
        "raw_payload": raw_payload,
        "normalized_payload": normalized_payload,
        "payload_for_d002": payload_for_d002,
        "payload_source": payload_source,
        "brief_type": brief_type,
    }


def validate_d002_input(diagnostic_run_id: int) -> None:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)

    if not diagnostic_run["d001_result"]:
        raise ValueError("D-001 result is required before running D-002")

    input_pack_row = _get_latest_input_pack_row(diagnostic_run_id)
    input_pack = dict(input_pack_row)

    _resolve_payloads(input_pack)


def get_existing_d002_result(diagnostic_run_id: int) -> str | None:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)
    return diagnostic_run["d002_result"] or None


def get_d002_input(diagnostic_run_id: int) -> dict[str, Any]:
    validate_d002_input(diagnostic_run_id)

    diagnostic_run = dict(_get_diagnostic_run_row(diagnostic_run_id))
    input_pack = dict(_get_latest_input_pack_row(diagnostic_run_id))

    payloads = _resolve_payloads(input_pack)

    attachments = _get_attachments_for_input_pack(
        diagnostic_run_id=diagnostic_run_id,
        input_pack_id=int(input_pack["id"]),
    )

    return {
        "diagnostic_run": diagnostic_run,
        "input_pack": input_pack,
        "safe_input_pack": _sanitize_input_pack(input_pack),
        "raw_payload": payloads["raw_payload"],
        "normalized_payload": payloads["normalized_payload"],
        "payload_for_d002": payloads["payload_for_d002"],
        "payload_source": payloads["payload_source"],
        "brief_type": payloads["brief_type"],
        "attachments": attachments,
        "d001_result": diagnostic_run["d001_result"],
    }


def build_d002_prompt_input(diagnostic_run_id: int) -> str:
    data = get_d002_input(diagnostic_run_id)

    safe_diagnostic_run = _sanitize_diagnostic_run(data["diagnostic_run"])

    return f"""
# INPUT FOR D-002 MVP DESIGN AGENT

## Diagnostic Run

{json.dumps(safe_diagnostic_run, ensure_ascii=False, indent=2)}

## Selected Input Pack

{json.dumps(data["safe_input_pack"], ensure_ascii=False, indent=2)}

## Payload Selected For D-002

Use this payload as the main structured input for D-002.

Payload source: {data["payload_source"]}

{json.dumps(data["payload_for_d002"], ensure_ascii=False, indent=2)}

## Raw Diagnostic Input Pack / Industrial AI Brief

For Industrial AI, raw payload is the source of truth.

{json.dumps(data["raw_payload"], ensure_ascii=False, indent=2)}

## Normalized Diagnostic Input Pack

Use this only as a helper.
If it conflicts with raw payload for Industrial AI, raw payload wins.

{json.dumps(data["normalized_payload"], ensure_ascii=False, indent=2)}

## D-001 Diagnostic Assessment Result

{data["d001_result"]}

## Attachments Summary

{json.dumps(data["attachments"], ensure_ascii=False, indent=2)}
""".strip()


def build_d002_summary(result: str) -> str:
    """
    Формирует компактное summary для D-003 без второго AI-вызова.

    Главное:
    - раздел 10 "Рекомендация для D-003" обязателен;
    - раздел 10 ставится сразу после раздела 1;
    - заголовки распознаются устойчиво: дефисы, пробелы и unicode-варианты
      не должны ломать сборку summary.
    """
    dash_translation = str.maketrans(
        {
            "\u2010": "-",  # hyphen
            "\u2011": "-",  # non-breaking hyphen
            "\u2012": "-",  # figure dash
            "\u2013": "-",  # en dash
            "\u2014": "-",  # em dash
            "\u2212": "-",  # minus
            "\u00a0": " ",  # non-breaking space
        }
    )

    def normalize_heading(line: str) -> str:
        value = line.strip().translate(dash_translation)
        value = re.sub(r"\s+", " ", value)
        return value

    def section_key(line: str) -> str | None:
        heading = normalize_heading(line)

        if heading == "# D-002 Дизайн MVP Industrial AI":
            return "title_industrial"

        if heading == "# D-002 Дизайн MVP":
            return "title_standard"

        if heading == "## 1. Краткий вывод":
            return "section_1"

        if heading == "## 2. Граница MVP":
            return "section_2"

        if heading == "## 3. Функциональный дизайн":
            return "section_3"

        if heading == "## 4. Дизайн данных":
            return "section_4"

        if heading == "## 5. Интеграции и размещение":
            return "section_5"

        if heading == "## 6. ИБ, ПДн и коммерческая тайна":
            return "section_6"

        if heading == "## 7. KPI и критерии успеха":
            return "section_7"

        if heading == "## 8. План MVP":
            return "section_8"

        if heading == "## 9. Риски и ограничения":
            return "section_9"

        # Устойчивое распознавание раздела 10:
        # допускает разные дефисы в D-003 и небольшие отличия пробелов.
        if re.match(r"^##\s+10\.\s+", heading):
            if "Рекомендация" in heading and "D-003" in heading:
                return "section_10"

        return None

    canonical_headers = {
        "title_industrial": "# D-002 Дизайн MVP Industrial AI",
        "title_standard": "# D-002 Дизайн MVP",
        "section_1": "## 1. Краткий вывод",
        "section_2": "## 2. Граница MVP",
        "section_3": "## 3. Функциональный дизайн",
        "section_4": "## 4. Дизайн данных",
        "section_5": "## 5. Интеграции и размещение",
        "section_6": "## 6. ИБ, ПДн и коммерческая тайна",
        "section_7": "## 7. KPI и критерии успеха",
        "section_8": "## 8. План MVP",
        "section_9": "## 9. Риски и ограничения",
        "section_10": "## 10. Рекомендация для D-003",
    }

    sections: dict[str, list[str]] = {}
    current_key: str | None = None

    for raw_line in result.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        key = section_key(stripped)

        if key:
            current_key = key
            # Сохраняем канонический заголовок, чтобы проверки вида
            # "## 10. Рекомендация для D-003" in summary
            # работали стабильно.
            sections[current_key] = [canonical_headers[key]]
            continue

        # Если начался новый # или ## раздел, который нам не нужен,
        # прекращаем захват текущей секции.
        # Важно: ### подзаголовки внутри секций не обрываем.
        if stripped.startswith("# ") or stripped.startswith("## "):
            current_key = None
            continue

        if current_key:
            sections[current_key].append(line)

    selected: list[str] = []

    def clean_section(section: list[str]) -> list[str]:
        cleaned = section[:]

        while cleaned and not cleaned[0].strip():
            cleaned = cleaned[1:]

        while cleaned and not cleaned[-1].strip():
            cleaned = cleaned[:-1]

        return cleaned

    def append_section(key: str) -> None:
        section = sections.get(key)
        if not section:
            return

        cleaned = clean_section(section)
        if not cleaned:
            return

        if selected and selected[-1].strip():
            selected.append("")

        selected.extend(cleaned)

    # 1. Заголовок.
    if "title_industrial" in sections:
        append_section("title_industrial")
    elif "title_standard" in sections:
        append_section("title_standard")

    # 2. Обязательные секции. Их нельзя обрезать.
    append_section("section_1")
    append_section("section_10")

    # 3. Остальные секции — контекст. Их можно ограничивать.
    optional_keys = [
        "section_2",
        "section_4",
        "section_5",
        "section_6",
        "section_7",
        "section_8",
        "section_9",
    ]

    max_summary_chars = 16000

    for key in optional_keys:
        section = sections.get(key)
        if not section:
            continue

        cleaned = clean_section(section)
        if not cleaned:
            continue

        candidate = selected[:]

        if candidate and candidate[-1].strip():
            candidate.append("")

        candidate.extend(cleaned)

        if len("\n".join(candidate)) <= max_summary_chars:
            selected = candidate

    summary = "\n".join(selected).strip()

    if not summary:
        return result.strip()

    return summary


def save_d002_summary(
    diagnostic_run_id: int,
    summary: str,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET d002_summary = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                summary,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def rebuild_d002_summary(
    diagnostic_run_id: int,
) -> str:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)
    result = diagnostic_run["d002_result"]

    if not result:
        raise ValueError(f"D-002 result is empty for diagnostic_run_id={diagnostic_run_id}")

    summary = build_d002_summary(result)
    save_d002_summary(diagnostic_run_id, summary)

    return summary


def save_d002_result(
    diagnostic_run_id: int,
    result: str,
    summary: str | None = None,
) -> None:
    now = _now_iso()

    if summary is None:
        summary = build_d002_summary(result)

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET d002_result = ?,
                d002_summary = ?,
                d002_completed_at = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                result,
                summary,
                now,
                DIAGNOSTIC_STATUS_D002_COMPLETED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def run_d002_mvp_design(
    diagnostic_run_id: int,
    force_rebuild: bool = False,
) -> str:
    diagnostic_run = dict(_get_diagnostic_run_row(diagnostic_run_id))
    existing_result = diagnostic_run.get("d002_result") or None

    if existing_result and not force_rebuild:
        existing_summary = diagnostic_run.get("d002_summary") or ""

        if "## 10. Рекомендация для D-003" not in existing_summary:
            summary = build_d002_summary(existing_result)
            save_d002_summary(diagnostic_run_id, summary)

        return existing_result

    validate_d002_input(diagnostic_run_id)

    update_diagnostic_status(
        diagnostic_run_id=diagnostic_run_id,
        status=DIAGNOSTIC_STATUS_D002_RUNNING,
    )

    prompt_input = build_d002_prompt_input(diagnostic_run_id)

    result = run_agent_with_prompt(
        agent_prompt_name=D002_AGENT_PROMPT_NAME,
        user_input=prompt_input,
    )

    save_d002_result(
        diagnostic_run_id=diagnostic_run_id,
        result=result,
    )

    return result