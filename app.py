from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from db import DB_PATH, init_db


from dotenv import load_dotenv
from flask import Flask
from routes.public import public_bp
from routes.api import api_bp
from routes.admin import admin_bp

load_dotenv()

app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

app.register_blueprint(public_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

LEAD_STATUSES = {
    "new": "Новая",
    "in_progress": "В работе",
    "qualified": "Квалифицирована",
    "proposal": "Коммерческое предложение",
    "implementation": "Внедрение",
    "completed": "Завершено",
    "archive": "Архив",
}

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
else:
    init_db()
