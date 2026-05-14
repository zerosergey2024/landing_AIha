import shutil
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "leads.db"
BACKUP_DIR = BASE_DIR / "backups"


def backup_database():
    if not DB_PATH.exists():
        print(f"База не найдена: {DB_PATH}")
        return

    BACKUP_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = BACKUP_DIR / f"leads_backup_{timestamp}.db"

    shutil.copy2(DB_PATH, backup_path)

    print(f"Backup создан: {backup_path}")


if __name__ == "__main__":
    backup_database()