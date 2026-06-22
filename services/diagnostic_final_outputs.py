from __future__ import annotations

from typing import Any

from db import get_db_connection


def _clean(value: Any, default: str = "не указано") -> str:
    if value is None:
        return default

    text = str(value).strip()
    return text if text else default


def build_full_output(diagnostic: dict[str, Any]) -> str:
    return f"""
# Экспресс-диагностика AIha Consulting — полный результат

## D-001 Diagnostic Assessment

{_clean(diagnostic.get("d001_result"))}

---

## D-002 MVP Design

{_clean(diagnostic.get("d002_result"))}

---

## D-003 Diagnostic Report

{_clean(diagnostic.get("d003_result"))}

---

## D-004 Commercial Proposal

{_clean(diagnostic.get("d004_result"))}
""".strip()


def build_client_output(diagnostic: dict[str, Any]) -> str:
    return f"""
# Экспресс-диагностика AIha Consulting

## Итоговый диагностический отчёт

{_clean(diagnostic.get("d003_result"))}

---

## Коммерческое предложение на следующий этап

{_clean(diagnostic.get("d004_result"))}
""".strip()


def get_diagnostic_final_outputs(
    diagnostic_run_id: int,
) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        diagnostic_row = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

        if diagnostic_row is None:
            return None

        diagnostic = dict(diagnostic_row)

        lead_row = conn.execute(
            """
            SELECT *
            FROM leads
            WHERE id = ?
            """,
            (diagnostic["lead_id"],),
        ).fetchone()

    lead = dict(lead_row) if lead_row else None

    return {
        "diagnostic": diagnostic,
        "lead": lead,
        "full_output": build_full_output(diagnostic),
        "client_output": build_client_output(diagnostic),
    }