import csv
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "leads.db"
EXPORT_PATH = BASE_DIR / "exports" / "leads_export.csv"


def export_leads_to_csv():
    EXPORT_PATH.parent.mkdir(exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                id,
                created_at,
                source,
                name,
                phone,
                company,
                message
            FROM leads
            ORDER BY id DESC
            """
        ).fetchall()

    with open(EXPORT_PATH, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file, delimiter=";")

        writer.writerow([
            "ID",
            "Дата",
            "Источник",
            "Имя",
            "Телефон",
            "Компания",
            "Задача"
        ])

        for row in rows:
            writer.writerow([
                row["id"],
                row["created_at"],
                row["source"],
                row["name"],
                row["phone"],
                row["company"],
                row["message"]
            ])

    print(f"Экспорт готов: {EXPORT_PATH}")


if __name__ == "__main__":
    export_leads_to_csv()