from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from db import DB_PATH
from lead_qualifier import qualify_lead


def safe_qualify_lead(message: str) -> dict[str, str]:
    """
    Безопасная обёртка над qualify_lead.
    Если AI-квалификация недоступна или вернула неполный ответ,
    заявка всё равно сохраняется.
    """
    defaults = {
        "industry": "Не определено",
        "process": "Не определено",
        "ai_type": "Не определено",
        "effect": "Не определено",
        "priority": "Средний",
    }

    try:
        result = qualify_lead(message) or {}
    except Exception:
        result = {}

    return {
        "industry": result.get("industry") or defaults["industry"],
        "process": result.get("process") or defaults["process"],
        "ai_type": result.get("ai_type") or defaults["ai_type"],
        "effect": result.get("effect") or defaults["effect"],
        "priority": result.get("priority") or defaults["priority"],
    }


def save_lead(payload: dict[str, str]) -> int:
    message = payload.get("message", "").strip()
    qualification = safe_qualify_lead(message)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO leads (
                name,
                phone,
                company,
                message,
                source,
                created_at,
                industry,
                process,
                ai_type,
                effect,
                priority,
                status,
                manager_comment,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("name", "").strip(),
                payload.get("phone", "").strip(),
                payload.get("company", "").strip(),
                message,
                payload.get("source", "landing"),
                now,
                qualification["industry"],
                qualification["process"],
                qualification["ai_type"],
                qualification["effect"],
                qualification["priority"],
                payload.get("status", "Новая"),
                payload.get("manager_comment", ""),
                now,
            ),
        )

        conn.commit()
        return int(cursor.lastrowid)