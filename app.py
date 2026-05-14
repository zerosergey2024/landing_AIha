from __future__ import annotations
from telegram_notify import send_telegram_message
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, redirect, session
from dotenv import load_dotenv
from lead_qualifier import qualify_lead
from export_leads_xlsx import export_leads_to_xlsx

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "leads.db"

app = Flask(__name__)
LEAD_STATUSES = {
    "new": "Новая",
    "in_progress": "В работе",
    "qualified": "Квалифицирована",
    "proposal": "Коммерческое предложение",
    "implementation": "Внедрение",
    "completed": "Завершено",
    "archive": "Архив",
}

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

def admin_required():
    return session.get("admin_logged_in") is True

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = ""

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect("/admin/leads")

        error = "Неверный логин или пароль."

    return render_template("admin_login.html", error=error)


@app.get("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect("/admin/login")

def init_db() -> None:
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
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

def save_lead(payload: dict[str, str]) -> int:
    message = payload.get("message", "").strip()
    qualification = qualify_lead(message)

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


@app.route("/")
def index():
    return render_template("index.html")

@app.get("/admin/leads")
def admin_leads():
    if not admin_required():
        return redirect("/admin/login")
    status_filter = request.args.get("status", "").strip()
    priority_filter = request.args.get("priority", "").strip()
    search_query = request.args.get("q", "").strip()

    query = """
        SELECT
            id,
            created_at,
            updated_at,
            source,
            name,
            phone,
            company,
            message,
            industry,
            process,
            ai_type,
            effect,
            priority,
            status,
            manager_comment
        FROM leads
        WHERE 1 = 1
    """

    params = []

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)

    if priority_filter:
        query += " AND priority = ?"
        params.append(priority_filter)

    if search_query:
        query += """
            AND (
                name LIKE ?
                OR phone LIKE ?
                OR company LIKE ?
                OR message LIKE ?
                OR source LIKE ?
            )
        """
        search_value = f"%{search_query}%"
        params.extend([
            search_value,
            search_value,
            search_value,
            search_value,
            search_value,
        ])

    query += " ORDER BY id DESC"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        leads = conn.execute(query, params).fetchall()

    return render_template(
        "admin_leads.html",
        leads=leads,
        statuses=LEAD_STATUSES,
        current_status=status_filter,
        current_priority=priority_filter,
        current_query=search_query,
        priorities=["Высокий", "Средний", "Низкий"],
    )

@app.post("/admin/export-xlsx")
def admin_export_xlsx():
    if not admin_required():
        return redirect("/admin/login")
    export_leads_to_xlsx()
    return redirect("/admin/leads")

@app.post("/api/leads")
def create_lead():
    data = request.get_json(silent=True) or request.form.to_dict()

    required = {
        "name": data.get("name", "").strip(),
        "phone": data.get("phone", "").strip()
    }

    if not required["name"] or not required["phone"]:
        return jsonify({
            "ok": False,
            "error": "Укажите имя и телефон."
        }), 400

    lead_id = save_lead(data)

    qualification = qualify_lead(
        data.get("message", "")
    )

    telegram_text = f"""
🔥 Новая заявка AIha

👤 Имя: {data.get("name", "").strip()}
📞 Телефон: {data.get("phone", "").strip()}
🏢 Компания: {data.get("company", "").strip() or "Не указана"}

📋 Задача:
{data.get("message", "").strip() or "Не указана"}

🧠 AI-квалификация

• Отрасль: {qualification["industry"]}
• Процесс: {qualification["process"]}
• AI-сценарий: {qualification["ai_type"]}
• Эффект: {qualification["effect"]}
• Приоритет: {qualification["priority"]}

🆔 Lead ID: {lead_id}
"""

    send_telegram_message(telegram_text)

    return jsonify({
        "ok": True,
        "lead_id": lead_id
    })

@app.post("/api/callback")
def create_callback():
    data = request.get_json(silent=True) or request.form.to_dict()

    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    message = data.get("message", "").strip() or "Запрос обратного звонка"

    if not phone:
        return jsonify({
            "ok": False,
            "error": "Укажите телефон."
        }), 400

    lead_id = save_lead({
        "name": name or "Обратный звонок",
        "phone": phone,
        "company": "",
        "message": message,
        "source": "callback_widget"
    })

    qualification = qualify_lead(data.get("message", ""))

    telegram_text = f"""
📞 Запрос обратного звонка AIha

👤 Имя: {name or "Не указано"}
📞 Телефон: {phone}

📋 Комментарий:
{message}

🧠 AI-квалификация

• Отрасль: {qualification["industry"]}
• Процесс: {qualification["process"]}
• AI-сценарий: {qualification["ai_type"]}
• Эффект: {qualification["effect"]}
• Приоритет: {qualification["priority"]}

🆔 Lead ID: {lead_id}
"""

    send_telegram_message(telegram_text)

    return jsonify({
        "ok": True,
        "lead_id": lead_id
    })

@app.post("/api/leads/<int:lead_id>/status")
def update_lead_status(lead_id):
    if not admin_required():
        return redirect("/admin/login")
    data = request.get_json(silent=True) or request.form.to_dict()

    status_code = data.get("status", "").strip()
    new_status = LEAD_STATUSES.get(status_code)
    manager_comment = data.get("manager_comment", "").strip()

    if not new_status:
        return jsonify({
            "ok": False,
            "error": "Некорректный статус."
        }), 400

    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE leads
            SET
                status = ?,
                manager_comment = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                new_status,
                manager_comment,
                updated_at,
                lead_id,
            ),
        )

        conn.commit()

    return jsonify({
        "ok": True,
        "lead_id": lead_id,
        "status": new_status,
    })

@app.get("/admin/leads/<int:lead_id>")
def admin_lead_detail(lead_id):
    if not admin_required():
        return redirect("/admin/login")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        lead = conn.execute(
            """
            SELECT
                id,
                created_at,
                updated_at,
                source,
                name,
                phone,
                company,
                message,
                industry,
                process,
                ai_type,
                effect,
                priority,
                status,
                manager_comment
            FROM leads
            WHERE id = ?
            """,
            (lead_id,)
        ).fetchone()

    if lead is None:
        return "Заявка не найдена", 404

    return render_template(
        "admin_lead_detail.html",
        lead=lead,
        statuses=LEAD_STATUSES,
    )

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
else:
    init_db()
