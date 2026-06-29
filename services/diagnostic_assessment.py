from __future__ import annotations

import json
from typing import Any

from db import get_db_connection
from services.ai_agent import run_agent_with_prompt
from services.diagnostic_normalizer import normalize_input_pack
from services.diagnostics import (
    DIAGNOSTIC_STATUS_D001_COMPLETED,
    DIAGNOSTIC_STATUS_D001_RUNNING,
    save_d001_result,
    update_diagnostic_status,
)


D001_AGENT_PROMPT_NAME = "diagnostic_assessment"
MIN_INDUSTRIAL_FILLED_FIELDS = 20


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
        return sum(1 for item in value if str(item).strip())

    return 1 if value is not None and str(value).strip() else 0


def _sanitize_diagnostic_run(row: Any) -> dict[str, Any]:
    diagnostic_run = dict(row)

    keys_to_remove = {
        "input_pack_token",
        "d001_result",
        "d002_result",
        "d003_result",
        "d004_result",
        "final_result",
        "final_output",
        "diagnostic_report",
        "commercial_proposal",
    }

    for key in keys_to_remove:
        diagnostic_run.pop(key, None)

    return diagnostic_run


def _validate_raw_payload_for_d001(
    input_pack_id: int,
    raw_payload: dict[str, Any],
) -> None:
    if not raw_payload:
        raise ValueError(
            f"raw_payload is empty for diagnostic input pack id={input_pack_id}"
        )

    brief_type = raw_payload.get("brief_type")

    if brief_type != "industrial_ai":
        return

    industrial_ai = raw_payload.get("industrial_ai")

    if not isinstance(industrial_ai, dict):
        raise ValueError(
            f"industrial_ai object is missing in raw_payload for input_pack id={input_pack_id}"
        )

    filled_fields = _count_non_empty(industrial_ai)

    if filled_fields < MIN_INDUSTRIAL_FILLED_FIELDS:
        raise ValueError(
            "Industrial AI Brief is too sparse for D-001: "
            f"input_pack_id={input_pack_id}, "
            f"industrial_non_empty={filled_fields}. "
            "Submit a filled Industrial AI Brief before running D-001."
        )


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
              AND raw_payload IS NOT NULL
              AND TRIM(raw_payload) != ''
            ORDER BY id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if row is None:
        raise ValueError(
            "Diagnostic input pack with raw_payload not found "
            f"for diagnostic_run_id={diagnostic_run_id}"
        )

    return row


def _get_attachments_for_input_pack(input_pack_id: int) -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                input_pack_id,
                file_type,
                original_filename,
                stored_filename,
                file_path,
                uploaded_at
            FROM diagnostic_attachments
            WHERE input_pack_id = ?
            ORDER BY id ASC
            """,
            (input_pack_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def validate_d001_input(diagnostic_run_id: int) -> None:
    input_pack = _get_latest_input_pack_row(diagnostic_run_id)
    raw_payload = _load_json_object(input_pack["raw_payload"], "raw_payload")

    _validate_raw_payload_for_d001(
        input_pack_id=int(input_pack["id"]),
        raw_payload=raw_payload,
    )


def get_latest_input_pack_for_d001(diagnostic_run_id: int) -> dict[str, Any]:
    input_pack_row = _get_latest_input_pack_row(diagnostic_run_id)
    input_pack = dict(input_pack_row)

    raw_payload = _load_json_object(input_pack.get("raw_payload"), "raw_payload")

    _validate_raw_payload_for_d001(
        input_pack_id=int(input_pack["id"]),
        raw_payload=raw_payload,
    )

    attachments = _get_attachments_for_input_pack(int(input_pack["id"]))

    return {
        "input_pack": input_pack,
        "raw_payload": raw_payload,
        "attachments": attachments,
    }


def ensure_normalized_payload(diagnostic_run_id: int) -> dict[str, Any]:
    data = get_latest_input_pack_for_d001(diagnostic_run_id)
    input_pack = data["input_pack"]
    raw_payload = data["raw_payload"]

    if raw_payload.get("brief_type") == "industrial_ai":
        return raw_payload

    if input_pack.get("normalized_payload"):
        return _load_json_object(
            input_pack["normalized_payload"],
            "normalized_payload",
        )

    return normalize_input_pack(int(input_pack["id"]))


def get_existing_d001_result(diagnostic_run_id: int) -> str | None:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)
    return diagnostic_run["d001_result"] or None


def build_d001_prompt_input(diagnostic_run_id: int) -> str:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)
    data = get_latest_input_pack_for_d001(diagnostic_run_id)

    input_pack = data["input_pack"]
    raw_payload = data["raw_payload"]
    normalized_payload = ensure_normalized_payload(diagnostic_run_id)

    safe_diagnostic_run = _sanitize_diagnostic_run(diagnostic_run)

    safe_input_pack = {
        "id": input_pack.get("id"),
        "diagnostic_run_id": input_pack.get("diagnostic_run_id"),
        "status": input_pack.get("status"),
        "created_at": input_pack.get("created_at"),
        "updated_at": input_pack.get("updated_at"),
        "brief_type": raw_payload.get("brief_type"),
        "brief_version": raw_payload.get("brief_version"),
        "source": raw_payload.get("source"),
        "submitted_at": raw_payload.get("submitted_at"),
        "raw_payload_non_empty": _count_non_empty(raw_payload),
        "industrial_ai_non_empty": _count_non_empty(
            raw_payload.get("industrial_ai", {})
        ),
    }

    return f"""
# INPUT FOR D-001 DIAGNOSTIC ASSESSMENT AGENT

## Diagnostic Run

{json.dumps(safe_diagnostic_run, ensure_ascii=False, indent=2)}

## Selected Input Pack

{json.dumps(safe_input_pack, ensure_ascii=False, indent=2)}

## Raw Diagnostic Input Pack / Industrial AI Brief

This is the source of truth.

If `brief_type` is `industrial_ai`, analyze the `industrial_ai` object directly.
Do not treat the brief as empty if `industrial_ai` contains filled fields.
Do not let the normalized helper payload override the raw Industrial AI Brief.

{json.dumps(raw_payload, ensure_ascii=False, indent=2)}

## Normalized Diagnostic Input Pack

Use this only as a helper.
If this section conflicts with the raw payload above, the raw payload wins.

{json.dumps(normalized_payload, ensure_ascii=False, indent=2)}

## Attachments Summary

{json.dumps(data["attachments"], ensure_ascii=False, indent=2)}
""".strip()


def run_d001_diagnostic_assessment(
    diagnostic_run_id: int,
    force_rebuild: bool = False,
) -> str:
    existing_result = get_existing_d001_result(diagnostic_run_id)

    if existing_result and not force_rebuild:
        return existing_result

    validate_d001_input(diagnostic_run_id)

    update_diagnostic_status(
        diagnostic_run_id=diagnostic_run_id,
        status=DIAGNOSTIC_STATUS_D001_RUNNING,
    )

    prompt_input = build_d001_prompt_input(diagnostic_run_id)

    result = run_agent_with_prompt(
        agent_prompt_name=D001_AGENT_PROMPT_NAME,
        user_input=prompt_input,
    )

    save_d001_result(
        diagnostic_run_id=diagnostic_run_id,
        result=result,
    )

    update_diagnostic_status(
        diagnostic_run_id=diagnostic_run_id,
        status=DIAGNOSTIC_STATUS_D001_COMPLETED,
    )

    return result