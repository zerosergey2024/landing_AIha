from __future__ import annotations

import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "leads.db"


def init_db() -> None:
    """
    Создаёт таблицы leads и client_constraints.
    Для leads также добавляет недостающие колонки,
    если база уже существовала в старой версии.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                company TEXT,
                message TEXT,
                source TEXT DEFAULT 'landing',
                created_at TEXT NOT NULL,
                updated_at TEXT,
                industry TEXT,
                process TEXT,
                ai_type TEXT,
                effect TEXT,
                priority TEXT,
                status TEXT DEFAULT 'Новая',
                manager_comment TEXT
            )
            """
        )

        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()
        }

        required_columns = {
            "updated_at": "TEXT",
            "industry": "TEXT",
            "process": "TEXT",
            "ai_type": "TEXT",
            "effect": "TEXT",
            "priority": "TEXT",
            "status": "TEXT DEFAULT 'Новая'",
            "manager_comment": "TEXT",
        }

        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE leads ADD COLUMN {column_name} {column_type}"
                )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS client_constraints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                has_personal_data TEXT,
                personal_data_types TEXT,
                can_anonymize TEXT,
                cloud_allowed TEXT,
                localization_requirements TEXT,
                security_policies TEXT,
                nda_required TEXT,
                roi_metrics_available TEXT,
                roi_metrics_details TEXT,
                budget_known TEXT,
                mvp_readiness TEXT,
                scope_limitations TEXT,
                constraint_risk TEXT,
                next_action TEXT,
                comment TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            )
            """
        )

        conn.commit()