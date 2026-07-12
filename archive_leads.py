import sqlite3
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

ACTIVE_DB_PATH = BASE_DIR / "leads.db"
ARCHIVE_DB_PATH = BASE_DIR / "leads_archive.db"

ARCHIVE_STATUSES = [
    "Архив",
]


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [row[1] for row in rows]


def init_archive_db():
    with sqlite3.connect(ACTIVE_DB_PATH) as active_conn:
        active_schema = active_conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table'
            AND name = 'leads'
            """
        ).fetchone()

    if not active_schema:
        raise RuntimeError("Таблица leads не найдена в leads.db")

    create_sql = active_schema[0]

    with sqlite3.connect(ARCHIVE_DB_PATH) as archive_conn:
        archive_conn.execute(create_sql.replace(
            "CREATE TABLE leads",
            "CREATE TABLE IF NOT EXISTS leads"
        ))

        existing_columns = get_columns(archive_conn, "leads")

        if "archived_at" not in existing_columns:
            archive_conn.execute(
                "ALTER TABLE leads ADD COLUMN archived_at TEXT DEFAULT ''"
            )

        archive_conn.commit()



def archive_lead_by_id(lead_id: int) -> bool:
    """
    Move one lead from active leads.db to archive leads_archive.db.
    Used when an admin changes lead status to Archive.
    """
    init_archive_db()

    with sqlite3.connect(ACTIVE_DB_PATH) as active_conn, sqlite3.connect(ARCHIVE_DB_PATH) as archive_conn:
        active_conn.row_factory = sqlite3.Row
        archive_conn.row_factory = sqlite3.Row

        lead = active_conn.execute(
            """
            SELECT *
            FROM leads
            WHERE id = ?
            """,
            (lead_id,),
        ).fetchone()

        if lead is None:
            return False

        active_columns = get_columns(active_conn, "leads")
        archive_columns = get_columns(archive_conn, "leads")

        insert_columns = [
            column for column in active_columns
            if column in archive_columns
        ]

        if "archived_at" in archive_columns and "archived_at" not in insert_columns:
            insert_columns.append("archived_at")

        columns_sql = ", ".join(insert_columns)
        values_sql = ", ".join("?" for _ in insert_columns)

        values = []
        for column in insert_columns:
            if column == "archived_at":
                values.append(now_utc())
            else:
                values.append(lead[column])

        archive_conn.execute(
            f"""
            INSERT OR REPLACE INTO leads ({columns_sql})
            VALUES ({values_sql})
            """,
            values,
        )

        active_conn.execute(
            """
            DELETE FROM leads
            WHERE id = ?
            """,
            (lead_id,),
        )

        archive_conn.commit()
        active_conn.commit()

    return True

def archive_leads():
    init_archive_db()

    with sqlite3.connect(ACTIVE_DB_PATH) as active_conn, sqlite3.connect(ARCHIVE_DB_PATH) as archive_conn:
        active_conn.row_factory = sqlite3.Row
        archive_conn.row_factory = sqlite3.Row

        placeholders = ",".join("?" for _ in ARCHIVE_STATUSES)

        leads_to_archive = active_conn.execute(
            f"""
            SELECT *
            FROM leads
            WHERE status IN ({placeholders})
            """,
            ARCHIVE_STATUSES,
        ).fetchall()

        if not leads_to_archive:
            print("Нет заявок для архивации.")
            return

        active_columns = get_columns(active_conn, "leads")
        archive_columns = get_columns(archive_conn, "leads")

        insert_columns = [
            column for column in active_columns
            if column in archive_columns
        ]

        if "archived_at" in archive_columns:
            insert_columns.append("archived_at")

        columns_sql = ", ".join(insert_columns)
        values_sql = ", ".join("?" for _ in insert_columns)

        archived_ids = []

        for lead in leads_to_archive:
            values = []

            for column in insert_columns:
                if column == "archived_at":
                    values.append(now_utc())
                else:
                    values.append(lead[column])

            archive_conn.execute(
                f"""
                INSERT INTO leads ({columns_sql})
                VALUES ({values_sql})
                """,
                values,
            )

            archived_ids.append(lead["id"])

        id_placeholders = ",".join("?" for _ in archived_ids)

        active_conn.execute(
            f"""
            DELETE FROM leads
            WHERE id IN ({id_placeholders})
            """,
            archived_ids,
        )

        archive_conn.commit()
        active_conn.commit()

        print(f"Архивировано заявок: {len(archived_ids)}")
        print(f"Archive DB: {ARCHIVE_DB_PATH}")


if __name__ == "__main__":
    archive_leads()
