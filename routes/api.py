from __future__ import annotations

from flask import Blueprint, jsonify, request

from services.leads import save_lead, safe_qualify_lead
from telegram_notify import send_telegram_message


api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.post("/leads")
def create_lead():
    data = request.get_json(silent=True) or request.form.to_dict()

    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()

    if not name or not phone:
        return jsonify(
            {
                "ok": False,
                "error": "Укажите имя и телефон.",
            }
        ), 400

    lead_id = save_lead(data)
    qualification = safe_qualify_lead(data.get("message", ""))

    telegram_text = f"""
🔥 Новая заявка AIha

👤 Имя: {name}
📞 Телефон: {phone}
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
""".strip()

    send_telegram_message(telegram_text)

    return jsonify(
        {
            "ok": True,
            "lead_id": lead_id,
        }
    )


@api_bp.post("/callback")
def create_callback():
    data = request.get_json(silent=True) or request.form.to_dict()

    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    message = data.get("message", "").strip() or "Запрос обратного звонка"

    if not phone:
        return jsonify(
            {
                "ok": False,
                "error": "Укажите телефон.",
            }
        ), 400

    lead_id = save_lead(
        {
            "name": name or "Обратный звонок",
            "phone": phone,
            "company": "",
            "message": message,
            "source": "callback_widget",
        }
    )

    qualification = safe_qualify_lead(message)

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
""".strip()

    send_telegram_message(telegram_text)

    return jsonify(
        {
            "ok": True,
            "lead_id": lead_id,
        }
    )