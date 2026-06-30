from __future__ import annotations

import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "leads.db"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_missing_columns(
    conn: sqlite3.Connection,
    table_name: str,
    required_columns: dict[str, str],
) -> None:
    existing_columns = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }

    for column_name, column_type in required_columns.items():
        if column_name not in existing_columns:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
            )


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

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

        _add_missing_columns(
            conn,
            "leads",
            {
                "updated_at": "TEXT",
                "industry": "TEXT",
                "process": "TEXT",
                "ai_type": "TEXT",
                "effect": "TEXT",
                "priority": "TEXT",
                "status": "TEXT DEFAULT 'Новая'",
                "manager_comment": "TEXT",
            },
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

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_code TEXT NOT NULL,
                lead_id INTEGER NOT NULL,
                company TEXT,
                agent_type TEXT,
                stage TEXT,
                task_title TEXT NOT NULL,
                input_source TEXT,
                expected_output TEXT,
                status TEXT DEFAULT 'New',
                priority TEXT DEFAULT 'Средний',
                owner TEXT,
                human_required TEXT DEFAULT 'Нет',
                result TEXT,
                next_action TEXT,
                due_date TEXT,
                comment TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                diagnostic_run_id INTEGER,
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            )
            """
        )

        _add_missing_columns(
            conn,
            "agent_tasks",
            {
                "diagnostic_run_id": "INTEGER",
            },
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS diagnostic_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                company TEXT,
                contact_name TEXT,
                contact_email TEXT,
                status TEXT NOT NULL DEFAULT 'DIAGNOSTIC_CREATED',
                input_pack_token TEXT UNIQUE NOT NULL,
                input_pack_sent_at TEXT,
                input_pack_received_at TEXT,
                final_decision TEXT,
                decision_confidence TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            )
            """
        )

        _add_missing_columns(
            conn,
            "diagnostic_runs",
            {
                "d001_result": "TEXT",
                "d001_completed_at": "TEXT",
                "d002_result": "TEXT",
                "d002_summary": "TEXT",
                "d002_completed_at": "TEXT",
                "d003_result": "TEXT",
                "d003_summary": "TEXT",
                "d003_completed_at": "TEXT",
                "d004_result": "TEXT",
                "d004_summary": "TEXT",

                "d004_completed_at": "TEXT",
            },
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS diagnostic_input_packs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                diagnostic_run_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'CLIENT_INPUT_RECEIVED',
                raw_payload TEXT NOT NULL,
                consultant_notes TEXT,
                normalized_payload TEXT,
                generated_docx_path TEXT,
                generated_pdf_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                brief_type TEXT NOT NULL DEFAULT 'diagnostic_input_pack',
                source TEXT NOT NULL DEFAULT 'web_form',
                is_active INTEGER NOT NULL DEFAULT 1,
                superseded_at TEXT,
                FOREIGN KEY (diagnostic_run_id) REFERENCES diagnostic_runs(id)
            )
            """
        )

        _add_missing_columns(
            conn,
            "diagnostic_input_packs",
            {
                "consultant_notes": "TEXT",
                "normalized_payload": "TEXT",
                "generated_docx_path": "TEXT",
                "generated_pdf_path": "TEXT",
                "brief_type": "TEXT NOT NULL DEFAULT 'diagnostic_input_pack'",
                "source": "TEXT NOT NULL DEFAULT 'web_form'",
                "is_active": "INTEGER NOT NULL DEFAULT 1",
                "superseded_at": "TEXT",
            },
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS diagnostic_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                diagnostic_run_id INTEGER NOT NULL,
                input_pack_id INTEGER,
                file_type TEXT,
                original_filename TEXT,
                stored_filename TEXT,
                file_path TEXT,
                uploaded_at TEXT NOT NULL,
                FOREIGN KEY (diagnostic_run_id) REFERENCES diagnostic_runs(id),
                FOREIGN KEY (input_pack_id) REFERENCES diagnostic_input_packs(id)
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_tasks_lead_id
            ON agent_tasks(lead_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_tasks_diagnostic_run_id
            ON agent_tasks(diagnostic_run_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_diagnostic_runs_lead_id
            ON diagnostic_runs(lead_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_diagnostic_input_packs_run_brief
            ON diagnostic_input_packs(diagnostic_run_id, brief_type, id)
            """
        )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_diagnostic_input_packs_active_run_brief
            ON diagnostic_input_packs(diagnostic_run_id, brief_type)
            WHERE is_active = 1
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_diagnostic_runs_token
            ON diagnostic_runs(input_pack_token)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_diagnostic_input_packs_run_id
            ON diagnostic_input_packs(diagnostic_run_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_diagnostic_attachments_run_id
            ON diagnostic_attachments(diagnostic_run_id)
            """
        )

        conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized: {DB_PATH}")