import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from llm_assistant import ask_phi4_conversation


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "leads.db"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

user_sessions = {}


CONTACT_REQUEST_TEXT = (
    "Если вы заинтересованы в автоматизации рутинных процессов своего бизнеса, "
    "оставьте, пожалуйста, свои координаты: имя и телефон, Telegram или email."
)


SPAM_PATTERNS = [
    "searchregister",
    "google search index",
    "googlesearchindex",
    "web search index",
    "seo",
    "backlink",
    "traffic",
    "ranking",
    "domain authority",
    "casino",
    "crypto",
    "forex",
    "viagra",
    "adult traffic",
]


def is_spam_lead(text: str) -> bool:
    lower = text.lower()

    return any(pattern in lower for pattern in SPAM_PATTERNS)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract_phone(text: str) -> str:
    match = re.search(r"(\+?\d[\d\s\-\(\)]{8,}\d)", text)
    return match.group(1).strip() if match else ""


def extract_email(text: str) -> str:
    match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    return match.group(0) if match else ""


def extract_telegram(text: str) -> str:
    match = re.search(r"@\w+", text)
    return match.group(0) if match else ""


def extract_contact(text: str) -> str:
    contacts = []

    phone = extract_phone(text)
    email = extract_email(text)
    telegram = extract_telegram(text)

    if phone:
        contacts.append(phone)

    if email:
        contacts.append(email)

    if telegram:
        contacts.append(telegram)

    return ", ".join(contacts)


def extract_client_name(text: str, telegram_username: str = "") -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in reversed(lines):
        clean = line

        if clean.lower().startswith("user:"):
            clean = clean[5:].strip()

        clean_without_contact = clean
        clean_without_contact = re.sub(
            r"(\+?\d[\d\s\-\(\)]{8,}\d)",
            "",
            clean_without_contact,
        )
        clean_without_contact = re.sub(
            r"[\w\.-]+@[\w\.-]+\.\w+",
            "",
            clean_without_contact,
        )
        clean_without_contact = re.sub(
            r"@\w+",
            "",
            clean_without_contact,
        )
        clean_without_contact = clean_without_contact.replace(",", " ").strip()

        if (
            2 <= len(clean_without_contact) <= 80
            and not any(char.isdigit() for char in clean_without_contact)
            and len(clean_without_contact.split()) <= 3
        ):
            return clean_without_contact

    return telegram_username or ""


def has_business_context(text: str) -> bool:
    text = text.lower()

    signals = [
        "автоматиз",
        "заказ",
        "заявк",
        "доставк",
        "готовой еды",
        "резюме",
        "кандидат",
        "прием",
        "приём",
        "отбор",
        "оценк",
        "hr",
        "персонал",
        "документооборот",
        "1с",
        "производство",
        "оборудование",
        "логистик",
        "клиент",
        "бот",
        "телефон",
        "рутин",
        "процесс",
        "отчет",
        "отчёт",
        "регламент",
        "диспетчер",
    ]

    return any(signal in text for signal in signals)


def build_history_text(history: list[dict]) -> str:
    return "\n".join(
        f"{item['role']}: {item['content']}"
        for item in history
    )


def make_default_ai_result(history_text: str) -> dict:
    return {
        "industry": "Бизнес-процессы",
        "process": "Автоматизация процесса",
        "problem": "Ручная обработка или рутинный процесс",
        "goal": "Снижение ручного труда и ускорение обработки",
        "priority": "Средний",
        "summary": history_text,
        "client_name": "",
        "contact": "",
        "lead_ready": False,
    }


def save_telegram_lead(chat_id, username, history, ai_result) -> int:
    created_at = now_utc()
    history_text = build_history_text(history)

    contact = extract_contact(history_text)

    client_name = (
        ai_result.get("client_name")
        or extract_client_name(history_text, username)
        or username
        or f"Telegram user {chat_id}"
    )

    summary = ai_result.get("summary") or history_text

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
                client_name,
                contact or "уточнить в Telegram",
                "",
                summary,
                "telegram",
                created_at,
                ai_result.get("industry", "Бизнес-процессы"),
                ai_result.get("process", "Автоматизация процесса"),
                "Phi-4 Mini assistant",
                ai_result.get("goal", "Снижение ручного труда и ускорение обработки"),
                ai_result.get("priority", "Средний"),
                "Новая",
                "",
                created_at,
            ),
        )

        conn.commit()
        return cursor.lastrowid


def send_message(chat_id, text: str) -> None:
    try:
        response = requests.post(
            f"{API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        response.raise_for_status()

    except requests.RequestException as error:
        print(f"Telegram send error: {type(error).__name__}")


def reset_session(chat_id, username) -> None:
    user_sessions[chat_id] = {
        "username": username,
        "history": [],
        "lead_saved": False,
        "asked_contact": False,
    }


def get_ai_result_safe(history: list[dict]) -> dict:
    try:
        return ask_phi4_conversation(history)
    except Exception as error:
        print(f"Phi-4 handler error: {type(error).__name__}")
        return make_default_ai_result(build_history_text(history))


def handle_message(update) -> None:
    message = update.get("message", {})
    chat = message.get("chat", {})
    user = message.get("from", {})

    chat_id = chat.get("id")
    text = message.get("text", "").strip()
    username = user.get("username") or user.get("first_name")

    if not chat_id or not text:
        return

    if text == "/reset":
        reset_session(chat_id, username)
        send_message(
            chat_id,
            "Диалог сброшен.\n\n"
            "Опишите задачу или процесс, который хотите автоматизировать.",
        )
        return

    if text in ["/start", "/lead"]:
        reset_session(chat_id, username)
        send_message(
            chat_id,
            "Здравствуйте. Это AIha Студия.\n\n"
            "Мы помогаем автоматизировать рутинные процессы бизнеса: заявки, заказы, HR, документы, производство, логистику и клиентский сервис.\n\n"
            "Опишите коротко, какой процесс хотите автоматизировать.",
        )
        return

    session = user_sessions.get(chat_id)

    if not session:
        reset_session(chat_id, username)
        session = user_sessions[chat_id]

    if session.get("lead_saved"):
        send_message(
            chat_id,
            "Заявка уже зафиксирована. Специалист AIha свяжется с вами.",
        )
        return

    session["history"].append(
        {
            "role": "user",
            "content": text,
        }
    )

    history_text = build_history_text(session["history"])

    if is_spam_lead(history_text):
        send_message(
            chat_id,
            "Заявка отклонена автоматическим фильтром.",
        )
        reset_session(chat_id, username)
        return

    contact = extract_contact(history_text)
    client_name = extract_client_name(history_text, username)
    business_ready = has_business_context(history_text)

    if contact:
        if business_ready:
            ai_result = get_ai_result_safe(session["history"])
        else:
            ai_result = make_default_ai_result(history_text)

        ai_result["lead_ready"] = True
        ai_result["client_name"] = ai_result.get("client_name") or client_name
        ai_result["contact"] = ai_result.get("contact") or contact

        if not ai_result.get("summary"):
            ai_result["summary"] = history_text

        lead_id = save_telegram_lead(
            chat_id,
            session.get("username"),
            session["history"],
            ai_result,
        )

        session["lead_saved"] = True

        send_message(
            chat_id,
            f"Спасибо. Заявка AIha сформирована.\n\n"
            f"Номер заявки: {lead_id}\n"
            f"Специалист AIha свяжется с вами для уточнения деталей.",
        )
        return

    if business_ready and not contact:
        session["asked_contact"] = True

        session["history"].append(
            {
                "role": "assistant",
                "content": CONTACT_REQUEST_TEXT,
            }
        )

        send_message(chat_id, CONTACT_REQUEST_TEXT)
        return

    ai_result = get_ai_result_safe(session["history"])
    ai_reply = ai_result.get("reply", "").strip()

    if not ai_reply or len(ai_reply) > 600:
        ai_reply = CONTACT_REQUEST_TEXT

    if not has_business_context(ai_reply):
        ai_reply = (
            "Понял. AIha занимается автоматизацией бизнес-процессов: "
            "заявок, заказов, документов, HR, логистики и клиентского сервиса.\n\n"
            f"{CONTACT_REQUEST_TEXT}"
        )

    session["history"].append(
        {
            "role": "assistant",
            "content": ai_reply,
        }
    )

    send_message(chat_id, ai_reply)


def run_bot() -> None:
    offset = None

    print("Telegram bot started")

    while True:
        params = {
            "timeout": 5,
            "offset": offset,
        }

        try:
            response = requests.get(
                f"{API_URL}/getUpdates",
                params=params,
                timeout=10,
            )

            response.raise_for_status()
            data = response.json()

        except Exception as error:
            print(f"[{now_utc()}] telegram_polling_error error_type={type(error).__name__}")
            continue

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            handle_message(update)


if __name__ == "__main__":
    run_bot()