from __future__ import annotations

import json
from typing import Any

from db import get_db_connection
from services.ai_agent import run_agent_with_prompt
from services.diagnostics import (
    DIAGNOSTIC_STATUS_D003_COMPLETED,
    DIAGNOSTIC_STATUS_D003_RUNNING,
    save_d003_result,
    update_diagnostic_status,
    DIAGNOSTIC_STATUS_COMPLETED,
    complete_diagnostic_run,
)


def validate_d003_input(diagnostic_run_id: int) -> None:
    with get_db_connection() as conn:
        diagnostic_run = conn.execute(
            """
            SELECT
                id,
                d001_result,
                d002_result
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

        if diagnostic_run is None:
            raise ValueError(f"Diagnostic run not found: {diagnostic_run_id}")

        if not diagnostic_run["d001_result"]:
            raise ValueError("D-001 result is required before running D-003")

        if not diagnostic_run["d002_result"]:
            raise ValueError("D-002 result is required before running D-003")

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
            raise ValueError("normalized_payload is required before running D-003")


def get_existing_d003_result(diagnostic_run_id: int) -> str | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT d003_result
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"Diagnostic run not found: {diagnostic_run_id}")

    return row["d003_result"] or None


def get_d003_input(diagnostic_run_id: int) -> dict[str, Any]:
    validate_d003_input(diagnostic_run_id)

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

        lead = conn.execute(
            """
            SELECT *
            FROM leads
            WHERE id = ?
            """,
            (diagnostic_run["lead_id"],),
        ).fetchone()

        constraints = conn.execute(
            """
            SELECT *
            FROM client_constraints
            WHERE lead_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (diagnostic_run["lead_id"],),
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
        "lead": dict(lead) if lead else None,
        "constraints": dict(constraints) if constraints else None,
        "input_pack": dict(input_pack),
        "normalized_payload": normalized_payload,
        "d001_result": diagnostic_run["d001_result"],
        "d002_result": diagnostic_run["d002_result"],
        "d002_summary": diagnostic_run["d002_summary"],
    }


def build_d003_prompt_input(diagnostic_run_id: int) -> str:
    data = get_d003_input(diagnostic_run_id)

    safe_diagnostic_run = dict(data["diagnostic_run"])
    safe_diagnostic_run.pop("input_pack_token", None)

    return f"""
# INPUT FOR D-003 DIAGNOSTIC REPORT AGENT

## Lead

{json.dumps(data["lead"], ensure_ascii=False, indent=2)}

## Client Constraints

{json.dumps(data["constraints"], ensure_ascii=False, indent=2)}

## Diagnostic Run

{json.dumps(safe_diagnostic_run, ensure_ascii=False, indent=2)}

## Normalized Diagnostic Input Pack

{json.dumps(data["normalized_payload"], ensure_ascii=False, indent=2)}

## D-001 Diagnostic Assessment Result

{data["d001_result"]}

## D-002 MVP Design Result

{data["d002_result"]}

## D-002 Summary

{data["d002_summary"]}
""".strip()


def build_d003_summary(result: str) -> str:
    """
    Формирует компактную выжимку для карточки диагностики и будущего КП.
    """
    lines = [line.rstrip() for line in result.splitlines()]

    important_headers = {
        "# D-003 Diagnostic Report",
        "## 1. Executive Summary",
        "## 4. MVP Recommendation",
        "## 5. Risks and Constraints",
        "## 8. Economic Potential",
        "## 9. Final Recommendation",
        "## 10. Recommended Next Actions",
    }

    selected: list[str] = []
    capture = False

    for line in lines:
        if line.strip() in important_headers:
            capture = True
            selected.append(line)
            continue

        if line.startswith("## ") and capture:
            capture = False

        if capture:
            selected.append(line)

    if not selected:
        return result[:5000]

    summary = "\n".join(selected).strip()

    if len(summary) > 7000:
        summary = summary[:7000].rstrip() + "\n\n[Summary truncated]"

    return summary


def run_d003_diagnostic_report(
    diagnostic_run_id: int,
    force_rebuild: bool = False,
) -> str:
    existing_result = get_existing_d003_result(diagnostic_run_id)

    if existing_result and not force_rebuild:
        return existing_result

    validate_d003_input(diagnostic_run_id)

    update_diagnostic_status(
        diagnostic_run_id=diagnostic_run_id,
        status=DIAGNOSTIC_STATUS_D003_RUNNING,
    )

    prompt_input = build_d003_prompt_input(diagnostic_run_id)

    result = run_agent_with_prompt(
        agent_prompt_name="diagnostic_report",
        user_input=prompt_input,
    )

    summary = build_d003_summary(result)

    save_d003_result(
        diagnostic_run_id=diagnostic_run_id,
        result=result,
        summary=summary,
    )

    complete_diagnostic_run(diagnostic_run_id),

    return result