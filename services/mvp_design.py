from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from db import get_db_connection
from services.ai_agent import run_agent_with_prompt
from services.diagnostics import (
    DIAGNOSTIC_STATUS_D002_COMPLETED,
    DIAGNOSTIC_STATUS_D002_RUNNING,
    update_diagnostic_status,
)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _clean(value: Any, default: str = "не указано") -> str:
    if value is None:
        return default

    text = str(value).strip()
    return text if text else default


def validate_d002_input(diagnostic_run_id: int) -> None:
    with get_db_connection() as conn:
        diagnostic_run = conn.execute(
            """
            SELECT id, d001_result
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

        if diagnostic_run is None:
            raise ValueError(f"Diagnostic run not found: {diagnostic_run_id}")

        if not diagnostic_run["d001_result"]:
            raise ValueError("D-001 result is required before running D-002")

        input_pack = conn.execute(
            """
            SELECT id, normalized_payload
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()

        if input_pack is None:
            raise ValueError(
                f"Diagnostic input pack not found for diagnostic_run_id={diagnostic_run_id}"
            )

        if not input_pack["normalized_payload"]:
            raise ValueError(
                "normalized_payload is required before running D-002"
            )


def get_existing_d002_result(diagnostic_run_id: int) -> str | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT d002_result
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"Diagnostic run not found: {diagnostic_run_id}")

    return row["d002_result"] or None


def get_d002_input(diagnostic_run_id: int) -> dict[str, Any]:
    validate_d002_input(diagnostic_run_id)

    with get_db_connection() as conn:
        diagnostic_run = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

        input_pack = conn.execute(
            """
            SELECT *
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if diagnostic_run is None:
        raise ValueError(f"Diagnostic run not found: {diagnostic_run_id}")

    if input_pack is None:
        raise ValueError(
            f"Diagnostic input pack not found for diagnostic_run_id={diagnostic_run_id}"
        )

    normalized_payload = json.loads(input_pack["normalized_payload"])

    return {
        "diagnostic_run": dict(diagnostic_run),
        "input_pack": dict(input_pack),
        "normalized_payload": normalized_payload,
        "d001_result": diagnostic_run["d001_result"],
    }


def build_d002_prompt_input(diagnostic_run_id: int) -> str:
    data = get_d002_input(diagnostic_run_id)

    safe_diagnostic_run = dict(data["diagnostic_run"])
    safe_diagnostic_run.pop("input_pack_token", None)

    return f"""
# INPUT FOR D-002 MVP DESIGN AGENT

## Diagnostic Run

{json.dumps(safe_diagnostic_run, ensure_ascii=False, indent=2)}

## Normalized Diagnostic Input Pack

{json.dumps(data["normalized_payload"], ensure_ascii=False, indent=2)}

## D-001 Diagnostic Assessment Result

{data["d001_result"]}
""".strip()


def build_d002_summary(result: str) -> str:
    """
    Формирует компактное summary для D-003.

    Пока используем безопасную markdown-выжимку без второго AI-вызова.
    Позже можно заменить на отдельный summarizer-agent.
    """
    lines = [line.rstrip() for line in result.splitlines()]

    important_headers = {
        "# D-002 MVP Design Report",
        "## 1. Executive MVP Summary",
        "## 4. MVP Scope",
        "## 8. MVP Architecture",
        "## 10. KPI And Success Criteria",
        "## 13. MVP Risks",
        "## 15. Recommendation For D-003",
        "## 16. Open Questions",
    }

    selected: list[str] = []
    capture = False
    captured_sections = 0

    for line in lines:
        if line.strip() in important_headers:
            capture = True
            captured_sections += 1
            selected.append(line)
            continue

        if line.startswith("## ") and capture:
            capture = False

        if capture:
            selected.append(line)

    if not selected:
        return result[:4000]

    summary = "\n".join(selected).strip()

    if len(summary) > 6000:
        summary = summary[:6000].rstrip() + "\n\n[Summary truncated]"

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
    existing_result = get_existing_d002_result(diagnostic_run_id)

    if existing_result and not force_rebuild:
        return existing_result

    validate_d002_input(diagnostic_run_id)

    update_diagnostic_status(
        diagnostic_run_id=diagnostic_run_id,
        status=DIAGNOSTIC_STATUS_D002_RUNNING,
    )

    prompt_input = build_d002_prompt_input(diagnostic_run_id)

    result = run_agent_with_prompt(
        agent_prompt_name="mvp_design",
        user_input=prompt_input,
    )

    save_d002_result(
        diagnostic_run_id=diagnostic_run_id,
        result=result,
    )

    return result