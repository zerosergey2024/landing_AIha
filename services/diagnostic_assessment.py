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


def validate_d001_input(diagnostic_run_id: int) -> None:
    with get_db_connection() as conn:
        diagnostic_run = conn.execute(
            """
            SELECT id
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

        if diagnostic_run is None:
            raise ValueError(f"Diagnostic run not found: {diagnostic_run_id}")

        input_pack = conn.execute(
            """
            SELECT id, raw_payload
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

        if not input_pack["raw_payload"]:
            raise ValueError("raw_payload is required before running D-001")


def get_latest_input_pack_for_d001(diagnostic_run_id: int) -> dict[str, Any]:
    validate_d001_input(diagnostic_run_id)

    with get_db_connection() as conn:
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

        attachments = conn.execute(
            """
            SELECT
                id,
                file_type,
                original_filename,
                stored_filename,
                file_path,
                uploaded_at
            FROM diagnostic_attachments
            WHERE diagnostic_run_id = ?
            ORDER BY id ASC
            """,
            (diagnostic_run_id,),
        ).fetchall()

    if input_pack is None:
        raise ValueError(
            f"Diagnostic input pack not found for diagnostic_run_id={diagnostic_run_id}"
        )

    return {
        "input_pack": dict(input_pack),
        "attachments": [dict(row) for row in attachments],
    }


def ensure_normalized_payload(diagnostic_run_id: int) -> dict[str, Any]:
    data = get_latest_input_pack_for_d001(diagnostic_run_id)
    input_pack = data["input_pack"]

    if input_pack.get("normalized_payload"):
        return json.loads(input_pack["normalized_payload"])

    return normalize_input_pack(int(input_pack["id"]))


def get_existing_d001_result(diagnostic_run_id: int) -> str | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT d001_result
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"Diagnostic run not found: {diagnostic_run_id}")

    return row["d001_result"] or None


def build_d001_prompt_input(diagnostic_run_id: int) -> str:
    normalized_payload = ensure_normalized_payload(diagnostic_run_id)
    data = get_latest_input_pack_for_d001(diagnostic_run_id)

    with get_db_connection() as conn:
        diagnostic_run = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if diagnostic_run is None:
        raise ValueError(f"Diagnostic run not found: {diagnostic_run_id}")

    safe_diagnostic_run = dict(diagnostic_run)
    safe_diagnostic_run.pop("input_pack_token", None)

    return f"""
# INPUT FOR D-001 DIAGNOSTIC ASSESSMENT AGENT

## Diagnostic Run

{json.dumps(safe_diagnostic_run, ensure_ascii=False, indent=2)}

## Normalized Diagnostic Input Pack

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
        agent_prompt_name="diagnostic_assessment",
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