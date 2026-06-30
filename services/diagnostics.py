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
    """
    Возвращает последнюю экспресс-диагностику по lead_id.
    Нужно, чтобы не создавать дубликаты при повторном нажатии кнопки.
    """
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
    """
    Возвращает все диагностики по лиду.
    Используется для отображения в карточке лида.
    """
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

    result = []

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


def _hydrate_input_pack_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None

    result = dict(row)

    if result.get("raw_payload"):
        try:
            result["raw_payload_json"] = json.loads(result["raw_payload"])
        except Exception:
            result["raw_payload_json"] = {}

    if result.get("normalized_payload"):
        try:
            result["normalized_payload_json"] = json.loads(result["normalized_payload"])
        except Exception:
            result["normalized_payload_json"] = {}

    return result


def get_active_input_pack(
    diagnostic_run_id: int,
    brief_type: str = "diagnostic_input_pack",
) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
              AND brief_type = ?
              AND is_active = 1
            ORDER BY
                COALESCE(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (diagnostic_run_id, brief_type),
        ).fetchone()

    return _hydrate_input_pack_row(row)

def get_input_pack_attachments(input_pack_id: int) -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                diagnostic_run_id,
                input_pack_id,
                file_type,
                original_filename,
                stored_filename,
                file_path,
                uploaded_at
            FROM diagnostic_attachments
            WHERE input_pack_id = ?
            ORDER BY uploaded_at DESC, id DESC
            """,
            (input_pack_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def upsert_active_input_pack(
    diagnostic_run_id: int,
    brief_type: str,
    payload: dict[str, Any],
    source: str = "web_form",
) -> int:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    if not brief_type:
        raise ValueError("brief_type is required")

    now = _now_iso()

    payload_to_save = dict(payload)
    payload_to_save["brief_type"] = brief_type
    payload_to_save.setdefault("brief_version", "v1")
    payload_to_save.setdefault("submitted_at", now)

    requested_source = source or payload_to_save.get("source") or "web_form"

    raw_payload = json.dumps(payload_to_save, ensure_ascii=False)

    with get_db_connection() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
              AND brief_type = ?
              AND is_active = 1
            ORDER BY
                COALESCE(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (diagnostic_run_id, brief_type),
        ).fetchone()

        if existing:
            input_pack_id = existing["id"]
            actual_source = (
                "web_form_update"
                if requested_source == "web_form"
                else requested_source
            )

            payload_to_save["source"] = actual_source
            raw_payload = json.dumps(payload_to_save, ensure_ascii=False)

            conn.execute(
                """
                UPDATE diagnostic_input_packs
                SET status = ?,
                    raw_payload = ?,
                    normalized_payload = NULL,
                    source = ?,
                    is_active = 1,
                    superseded_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    DIAGNOSTIC_STATUS_INPUT_RECEIVED,
                    raw_payload,
                    actual_source,
                    now,
                    input_pack_id,
                ),
            )

        else:
            actual_source = requested_source
            payload_to_save["source"] = actual_source
            raw_payload = json.dumps(payload_to_save, ensure_ascii=False)

            conn.execute(
                """
                UPDATE diagnostic_input_packs
                SET is_active = 0,
                    superseded_at = ?
                WHERE diagnostic_run_id = ?
                  AND brief_type = ?
                  AND is_active = 1
                """,
                (now, diagnostic_run_id, brief_type),
            )

            conn.execute(
                """
                INSERT INTO diagnostic_input_packs (
                    diagnostic_run_id,
                    status,
                    raw_payload,
                    created_at,
                    updated_at,
                    brief_type,
                    source,
                    is_active,
                    superseded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL)
                """,
                (
                    diagnostic_run_id,
                    DIAGNOSTIC_STATUS_INPUT_RECEIVED,
                    raw_payload,
                    now,
                    now,
                    brief_type,
                    actual_source,
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


def save_client_input_pack(
    diagnostic_run_id: int,
    payload: dict[str, Any],
) -> int:
    brief_type = payload.get("brief_type") or "diagnostic_input_pack"
    source = payload.get("source") or "web_form"

    return upsert_active_input_pack(
        diagnostic_run_id=diagnostic_run_id,
        brief_type=brief_type,
        payload=payload,
        source=source,
    )


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
    """
    Backward-compatible helper.

    Возвращает последнюю активную форму по diagnostic_run_id.
    Для точной работы с конкретной формой используйте get_active_input_pack(..., brief_type).
    """
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
              AND is_active = 1
            ORDER BY
                COALESCE(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()

    return _hydrate_input_pack_row(row)


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