import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "leads.db"

NEW_COLUMNS = {
    "industry": "TEXT DEFAULT ''",
    "process": "TEXT DEFAULT ''",
    "ai_type": "TEXT DEFAULT ''",
    "effect": "TEXT DEFAULT ''",
    "priority": "TEXT DEFAULT 'Средний'",
    "status": "TEXT DEFAULT 'Новая'",
    "manager_comment": "TEXT DEFAULT ''",
    "updated_at": "TEXT DEFAULT ''",
}


def get_existing_columns(conn):
    rows = conn.execute("PRAGMA table_info(leads)").fetchall()
    return {row[1] for row in rows}


def migrate():
    with sqlite3.connect(DB_PATH) as conn:
        existing_columns = get_existing_columns(conn)

        for column_name, column_type in NEW_COLUMNS.items():
            if column_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE leads ADD COLUMN {column_name} {column_type}"
                )
                print(f"Добавлена колонка: {column_name}")
            else:
                print(f"Колонка уже есть: {column_name}")

        conn.commit()

    print("Миграция завершена.")


if __name__ == "__main__":
    migrate()