from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from db import get_db_connection
from services.site_links import get_site_links


DIAGNOSTIC_STATUS_CREATED = "DIAGNOSTIC_CREATED"
DIAGNOSTIC_STATUS_FORM_SENT = "CLIENT_INPUT_FORM_SENT"
DIAGNOSTIC_STATUS_INPUT_RECEIVED = "CLIENT_INPUT_RECEIVED"
DIAGNOSTIC_STATUS_INPUT_NORMALIZED = "DIAGNOSTIC_INPUT_NORMALIZED"
DIAGNOSTIC_STATUS_COMPLETED = "COMPLETED"

DIAGNOSTIC_STATUS_D001_RUNNING = "D001_RUNNING"
DIAGNOSTIC_STATUS_D001_COMPLETED = "D001_COMPLETED"
DIAGNOSTIC_STATUS_D002_RUNNING = "D002_RUNNING"
DIAGNOSTIC_STATUS_D002_COMPLETED = "D002_COMPLETED"
DIAGNOSTIC_STATUS_D003_RUNNING = "D003_RUNNING"
DIAGNOSTIC_STATUS_D003_COMPLETED = "D003_COMPLETED"
DIAGNOSTIC_STATUS_D004_RUNNING = "D004_RUNNING"
DIAGNOSTIC_STATUS_D004_COMPLETED = "D004_COMPLETED"


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def generate_input_pack_token() -> str:
    return secrets.token_urlsafe(24)


def build_input_pack_url(token: str) -> str:
    site_links = get_site_links()
    consulting_url = site_links["consulting_url"].rstrip("/")
    return f"{consulting_url}/diagnostic/input-pack/{token}"


def create_diagnostic_run_for_lead(lead_id: int) -> dict[str, Any]:
    now = _now_iso()
    token = generate_input_pack_token()

    with get_db_connection() as conn:
        lead = conn.execute(
            """
            SELECT id, name, company
            FROM leads
            WHERE id = ?
            """,
            (lead_id,),
        ).fetchone()

        if lead is None:
            raise ValueError(f"Lead not found: {lead_id}")

        conn.execute(
            """
            INSERT INTO diagnostic_runs (
                lead_id,
                company,
                contact_name,
                contact_email,
                status,
                input_pack_token,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead["id"],
                lead["company"],
                lead["name"],
                None,
                DIAGNOSTIC_STATUS_CREATED,
                token,
                now,
                now,
            ),
        )

        diagnostic_run_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        conn.commit()

    return {
        "id": diagnostic_run_id,
        "lead_id": lead_id,
        "status": DIAGNOSTIC_STATUS_CREATED,
        "input_pack_token": token,
        "input_pack_url": build_input_pack_url(token),
    }


def get_diagnostic_run(diagnostic_run_id: int) -> dict[str, Any] | None:
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
        return None

    result = dict(row)
    result["input_pack_url"] = build_input_pack_url(result["input_pack_token"])
    return result


def get_diagnostic_run_by_token(token: str) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE input_pack_token = ?
            """,
            (token,),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    result["input_pack_url"] = build_input_pack_url(result["input_pack_token"])
    return result


def get_latest_diagnostic_run_for_lead(lead_id: int) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE lead_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    result["input_pack_url"] = build_input_pack_url(result["input_pack_token"])
    return result


def get_diagnostic_runs_for_lead(lead_id: int) -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE lead_id = ?
            ORDER BY id DESC
            """,
            (lead_id,),
        ).fetchall()

    result: list[dict[str, Any]] = []

    for row in rows:
        item = dict(row)
        item["input_pack_url"] = build_input_pack_url(item["input_pack_token"])
        result.append(item)

    return result


def update_diagnostic_status(
    diagnostic_run_id: int,
    status: str,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def mark_input_pack_sent(diagnostic_run_id: int) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET status = ?,
                input_pack_sent_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                DIAGNOSTIC_STATUS_FORM_SENT,
                now,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def save_client_input_pack(
    diagnostic_run_id: int,
    payload: dict[str, Any],
) -> int:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO diagnostic_input_packs (
                diagnostic_run_id,
                status,
                raw_payload,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                diagnostic_run_id,
                DIAGNOSTIC_STATUS_INPUT_RECEIVED,
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
            ),
        )

        input_pack_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        conn.execute(
            """
            UPDATE diagnostic_runs
            SET status = ?,
                input_pack_received_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                DIAGNOSTIC_STATUS_INPUT_RECEIVED,
                now,
                now,
                diagnostic_run_id,
            ),
        )

        conn.commit()

    return input_pack_id


def save_diagnostic_attachment(
    diagnostic_run_id: int,
    input_pack_id: int,
    file_type: str,
    original_filename: str,
    stored_filename: str,
    file_path: str,
) -> int:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO diagnostic_attachments (
                diagnostic_run_id,
                input_pack_id,
                file_type,
                original_filename,
                stored_filename,
                file_path,
                uploaded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                diagnostic_run_id,
                input_pack_id,
                file_type,
                original_filename,
                stored_filename,
                file_path,
                now,
            ),
        )

        attachment_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        conn.commit()

    return attachment_id


def get_latest_input_pack(diagnostic_run_id: int) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)

    if result.get("raw_payload"):
        result["raw_payload_json"] = json.loads(result["raw_payload"])

    if result.get("normalized_payload"):
        result["normalized_payload_json"] = json.loads(result["normalized_payload"])

    return result


def save_d001_result(
    diagnostic_run_id: int,
    result: str,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET d001_result = ?,
                d001_completed_at = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                result,
                now,
                DIAGNOSTIC_STATUS_D001_COMPLETED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def save_d002_result(
    diagnostic_run_id: int,
    result: str,
    summary: str | None = None,
) -> None:
    now = _now_iso()

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

def save_d003_result(
    diagnostic_run_id: int,
    result: str,
    summary: str | None = None,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET d003_result = ?,
                d003_summary = ?,
                d003_completed_at = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                result,
                summary,
                now,
                DIAGNOSTIC_STATUS_D003_COMPLETED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()

def complete_diagnostic_run(diagnostic_run_id: int) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                DIAGNOSTIC_STATUS_COMPLETED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()

def save_d004_result(
    diagnostic_run_id: int,
    result: str,
    summary: str | None = None,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET d004_result = ?,
                d004_summary = ?,
                d004_completed_at = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                result,
                summary,
                now,
                DIAGNOSTIC_STATUS_D004_COMPLETED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()