import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


DB_PATH = Path("leads.db")


INDUSTRIAL_HINT_KEYS = {
    "brief_type",
    "primary_case_type",
    "mvp_case",
    "industrial_non_empty",
    "erp_status",
    "mes_status",
    "scada_status",
    "equipment_type",
    "production_line",
    "downtime_frequency",
    "oee",
    "mtbf",
    "mttr",
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def backup_db() -> Path:
    backup_dir = Path("_backups")
    backup_dir.mkdir(exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_H%M%S")
    backup_path = backup_dir / f"leads_before_brief_type_migration_{stamp}.db"

    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def get_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def add_column_if_missing(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    sql_definition: str,
) -> None:
    columns = get_columns(conn, table_name)

    if column_name in columns:
        print(f"column already exists: {column_name}")
        return

    conn.execute(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_definition}"
    )
    print(f"added column: {column_name}")


def load_payload(raw_payload: str | None) -> dict:
    if not raw_payload:
        return {}

    try:
        value = json.loads(raw_payload)
    except Exception:
        return {}

    if isinstance(value, dict):
        return value

    return {}


def detect_brief_type(payload: dict) -> str:
    explicit = payload.get("brief_type")

    if explicit == "industrial_ai":
        return "industrial_ai"

    if explicit in {"diagnostic_input_pack", "input_pack"}:
        return "diagnostic_input_pack"

    if explicit in {"audit_brief", "ai_audit"}:
        return "audit_brief"

    keys = set(payload.keys())

    if keys & INDUSTRIAL_HINT_KEYS:
        return "industrial_ai"

    return "diagnostic_input_pack"


def migrate_rows(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT
            id,
            diagnostic_run_id,
            raw_payload,
            created_at,
            updated_at
        FROM diagnostic_input_packs
        ORDER BY diagnostic_run_id ASC, id ASC
        """
    ).fetchall()

    for row in rows:
        payload = load_payload(row["raw_payload"])
        brief_type = detect_brief_type(payload)

        existing_source = payload.get("source") or payload.get("_source")
        source = existing_source if existing_source else "web_form"

        conn.execute(
            """
            UPDATE diagnostic_input_packs
            SET brief_type = ?,
                source = ?
            WHERE id = ?
            """,
            (brief_type, source, row["id"]),
        )

        print(
            f"classified input_pack id={row['id']} "
            f"run={row['diagnostic_run_id']} "
            f"brief_type={brief_type} "
            f"source={source}"
        )


def deactivate_duplicate_active_packs(conn: sqlite3.Connection) -> None:
    """
    Оставляет активной только последнюю строку для пары:
    diagnostic_run_id + brief_type.

    Старые строки не удаляются, а помечаются is_active = 0.
    """
    groups = conn.execute(
        """
        SELECT diagnostic_run_id, brief_type, COUNT(*) AS cnt
        FROM diagnostic_input_packs
        GROUP BY diagnostic_run_id, brief_type
        HAVING COUNT(*) > 1
        """
    ).fetchall()

    for group in groups:
        rows = conn.execute(
            """
            SELECT id
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
              AND brief_type = ?
            ORDER BY
                COALESCE(updated_at, created_at) DESC,
                id DESC
            """,
            (group["diagnostic_run_id"], group["brief_type"]),
        ).fetchall()

        active_id = rows[0]["id"]
        inactive_ids = [row["id"] for row in rows[1:]]

        conn.execute(
            """
            UPDATE diagnostic_input_packs
            SET is_active = 1,
                superseded_at = NULL
            WHERE id = ?
            """,
            (active_id,),
        )

        if inactive_ids:
            placeholders = ",".join("?" for _ in inactive_ids)
            conn.execute(
                f"""
                UPDATE diagnostic_input_packs
                SET is_active = 0,
                    superseded_at = ?
                WHERE id IN ({placeholders})
                """,
                [now_text(), *inactive_ids],
            )

        print(
            f"active pack for run={group['diagnostic_run_id']} "
            f"brief_type={group['brief_type']} -> id={active_id}; "
            f"inactive={inactive_ids}"
        )


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_diagnostic_input_packs_run_brief
        ON diagnostic_input_packs (diagnostic_run_id, brief_type, id)
        """
    )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_diagnostic_input_packs_active_run_brief
        ON diagnostic_input_packs (diagnostic_run_id, brief_type)
        WHERE is_active = 1
        """
    )

    print("indexes ok")


def print_final_state(conn: sqlite3.Connection) -> None:
    print()
    print("=== diagnostic_input_packs after migration ===")

    rows = conn.execute(
        """
        SELECT
            id,
            diagnostic_run_id,
            brief_type,
            source,
            is_active,
            status,
            created_at,
            updated_at,
            superseded_at
        FROM diagnostic_input_packs
        ORDER BY diagnostic_run_id ASC, brief_type ASC, id ASC
        """
    ).fetchall()

    for row in rows:
        print(dict(row))


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB not found: {DB_PATH}")

    backup_path = backup_db()
    print(f"backup created: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("BEGIN")

        add_column_if_missing(
            conn,
            "diagnostic_input_packs",
            "brief_type",
            "TEXT NOT NULL DEFAULT 'diagnostic_input_pack'",
        )

        add_column_if_missing(
            conn,
            "diagnostic_input_packs",
            "source",
            "TEXT NOT NULL DEFAULT 'web_form'",
        )

        add_column_if_missing(
            conn,
            "diagnostic_input_packs",
            "is_active",
            "INTEGER NOT NULL DEFAULT 1",
        )

        add_column_if_missing(
            conn,
            "diagnostic_input_packs",
            "superseded_at",
            "TEXT",
        )

        migrate_rows(conn)
        deactivate_duplicate_active_packs(conn)
        create_indexes(conn)

        conn.commit()
        print("migration committed")

        print_final_state(conn)

    except Exception:
        conn.rollback()
        print("migration rolled back")
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()