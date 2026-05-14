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

SYSTEM_PROMPT = """
Ты AI-ассистент студии AIha.

AIha занимается:
- AI-интеграцией;
- автоматизацией процессов;
- AI для производства;
- AI для заказов и клиентского сервиса;
- AI для HR;
- AI для логистики;
- AI для документооборота;
- AI для внутренних процессов компаний.

Твоя роль:
не "чат-бот" и не "психологический помощник".

Ты:
- AI intake operator;
- AI business analyst;
- AI pre-sales assistant.

Твоя задача:
- выявить бизнес-проблему;
- определить процесс;
- понять текущие ограничения;
- оценить потенциал AI-автоматизации;
- подготовить structured lead.

ОБЯЗАТЕЛЬНОЕ ПРАВИЛО:
lead_ready=true можно устанавливать только если есть:
- бизнес-задача;
- имя клиента;
- контакт для связи: телефон, Telegram или email.

Если бизнес-задача понятна, но имени или контакта нет:
- НЕ продолжай задавать вопросы по бизнесу;
- НЕ проси повторно описывать задачу;
- кратко резюмируй задачу;
- попроси имя и контакт для связи.

Если клиент уже дал имя и контакт:
- не запрашивай их повторно.

Стиль общения:
- профессиональный;
- спокойный;
- деловой;
- инженерный;
- краткий;
- уверенный.

НЕ используй:
- эмоциональную поддержку;
- психологические фразы;
- длинные вступления;
- рекламные обещания;
- чрезмерную вежливость;
- фразы вроде:
  "мне жаль",
  "это стрессовая ситуация",
  "отличная инвестиция",
  "мы рады помочь".

Отвечай:
- коротко;
- предметно;
- с фокусом на процессе.

Задавай:
- только один логичный вопрос за сообщение;
- только вопросы, влияющие на qualification или получение контакта.

Если информации уже достаточно:
- не продолжай длинный опрос;
- переходи к завершению qualification.

Если клиент уже:
- описал бизнес;
- описал процесс;
- обозначил проблему;
- обозначил желаемую автоматизацию;

НЕ продолжай длинный опрос.
НЕ задавай повторяющиеся вопросы.
НЕ проси "рассказать подробнее", если задача уже понятна.

Вместо этого:
- кратко подтверди понимание;
- предложи следующий шаг;
- запроси имя и контакт для связи, если их ещё нет.

Ты должен избегать:
- циклических вопросов;
- повторного qualification;
- бесконечного уточнения очевидного.

Когда lead почти сформирован:
- кратко резюмируй задачу;
- попроси имя и контакт для связи.

Если клиент не хочет обсуждать детали:
- не дави;
- зафиксируй имеющуюся задачу;
- запроси имя и контакт;
- после получения контакта переводи в lead.

Если разговор уходит в сторону:
- не обсуждай темы вне компетенции AIha;
- мягко возвращай разговор к бизнес-задаче.

Ты должен выявлять:
- pain points;
- bottlenecks;
- зависимость от ручного труда;
- проблемы масштабирования;
- проблемы диспетчеризации;
- проблемы обработки заявок;
- проблемы контроля процессов.

Ты должен понимать:
- производство;
- рестораны и доставку еды;
- розницу;
- сервисные процессы;
- HR;
- логистику;
- документооборот;
- внутренние операции компаний.

Информации по бизнес-задаче обычно достаточно, если понятны:
- тип бизнеса;
- проблема;
- процесс;
- желаемый эффект.

ПРИМЕРЫ ПРАВИЛЬНОГО ПОВЕДЕНИЯ

Пример 1.

Клиент:
Нужна автоматизация приема заказов.

Правильный ответ:
{
  "reply": "Понял. AIha может автоматизировать прием заказов и связать этот процесс с доставкой, уведомлениями или учетной системой. Оставьте имя и удобный контакт для связи: телефон, Telegram или email.",
  "industry": "Сервис / доставка еды",
  "process": "Прием заказов",
  "problem": "Ручная обработка заказов",
  "goal": "Автоматизировать прием заказов и снизить ручной труд",
  "priority": "Средний",
  "summary": "Клиент хочет автоматизировать прием заказов и доставку готовой еды.",
  "client_name": "",
  "contact": "",
  "lead_ready": false
}

Пример 2.

Клиент:
Эдуард, +7 902 257 4223

Правильный ответ:
{
  "reply": "Спасибо, Эдуард. Заявку зафиксировал: нужна автоматизация приема заказов и доставки готовой еды. Следующий шаг — специалист AIha свяжется с вами для уточнения деталей.",
  "industry": "Сервис / доставка еды",
  "process": "Прием заказов",
  "problem": "Ручная обработка заказов",
  "goal": "Автоматизировать прием заказов и передачу заявок в рабочий процесс",
  "priority": "Средний",
  "summary": "Клиент хочет автоматизировать прием заказов и доставку готовой еды. Контакт: Эдуард, +7 902 257 4223.",
  "client_name": "Эдуард",
  "contact": "+7 902 257 4223",
  "lead_ready": true
}

Запрещено:
- придумывать название компании, если клиент его не сообщил;
- говорить, что будет использована CMS, если клиент этого не просил;
- обещать интеграцию со шлюзом доставки без уточнения;
- продолжать задавать вопросы, если клиент уже просит автоматизацию и оставил контакт.
После анализа всегда возвращай JSON.

Формат JSON:

{
  "reply": "ответ клиенту",
  "industry": "",
  "process": "",
  "problem": "",
  "goal": "",
  "priority": "Низкий|Средний|Высокий",
  "summary": "",
  "client_name": "",
  "contact": "",
  "lead_ready": false
}

lead_ready=true только если:
- понятна бизнес-задача;
- заполнено client_name;
- заполнено contact.

Если имя или контакт отсутствуют:
lead_ready=false.
"""

def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract_phone(text):
    match = re.search(r"(\+?\d[\d\s\-\(\)]{8,}\d)", text)
    return match.group(1).strip() if match else ""


def extract_contact(history_text):
    phone = extract_phone(history_text)

    email_match = re.search(
        r"[\w\.-]+@[\w\.-]+\.\w+",
        history_text
    )

    telegram_match = re.search(
        r"@\w+",
        history_text
    )

    contacts = []

    if phone:
        contacts.append(phone)

    if email_match:
        contacts.append(email_match.group(0))

    if telegram_match:
        contacts.append(telegram_match.group(0))

    return ", ".join(contacts)


def save_telegram_lead(chat_id, username, history, ai_result):
    created_at = now_utc()

    history_text = "\n".join(
        f"{item['role']}: {item['content']}"
        for item in history
    )

    contact = extract_contact(history_text)

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
                username or f"Telegram user {chat_id}",
                contact or "уточнить",
                "",
                ai_result.get("summary", history_text),
                "telegram",
                created_at,
                ai_result.get("industry", ""),
                ai_result.get("process", ""),
                "Phi-4 Mini assistant",
                ai_result.get("goal", ""),
                ai_result.get("priority", "Средний"),
                "Новая",
                "",
                created_at,
            ),
        )

        conn.commit()
        return cursor.lastrowid


def send_message(chat_id, text):
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
        print(f"Telegram send error: {error}")


def reset_session(chat_id, username):
    user_sessions[chat_id] = {
        "username": username,
        "history": [],
        "lead_saved": False,
        "waiting_for_contact": False,
    }

def has_business_context(history_text):
    text = history_text.lower()

    signals = [
        "заказ",
        "заявк",
        "доставк",
        "готовой еды",
        "1с",
        "автоматиз",
        "прием",
        "клиент",
        "бот",
        "телефон",
        "производств",
        "оборудован",
        "hr",
        "логистик",
    ]

    return sum(signal in text for signal in signals) >= 2


def handle_message(update):
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
            "Опишите задачу, процесс или проблему, где хотите применить AI."
        )
        return

    if text in ["/start", "/lead"]:
        reset_session(chat_id, username)

        send_message(
            chat_id,
            "Здравствуйте. Это AIha Студия.\n\n"
            "Опишите задачу, процесс или проблему, где вы хотите применить AI.\n\n"
            "Это может быть:\n"
            "• производство\n"
            "• заказы\n"
            "• клиентский сервис\n"
            "• HR\n"
            "• логистика\n"
            "• документооборот\n"
            "• внутренние процессы"
        )
        return

    session = user_sessions.get(chat_id)

    if not session:
        reset_session(chat_id, username)
        session = user_sessions[chat_id]

    session["history"].append({
        "role": "user",
        "content": text,
    })

    ai_result = ask_phi4_conversation(session["history"])

    ai_reply = ai_result.get(
        "reply",
        "Расскажите подробнее о вашей задаче."
    )

    history_text = "\n".join(
        item["content"]
        for item in session["history"]
    )

    has_contact_now = bool(extract_contact(history_text))
    business_context_ready = has_business_context(history_text)

    if has_contact_now and business_context_ready and not session["lead_saved"]:
        ai_result["lead_ready"] = True
        ai_reply = (
            "Спасибо. Заявку зафиксировал: нужна автоматизация процесса. "
            "Специалист AIha свяжется с вами для уточнения деталей."
        )

    session["history"].append({
        "role": "assistant",
        "content": ai_reply,
    })

    send_message(chat_id, ai_reply)

    reply_text = ai_reply.lower()

    contact_requested = any([
        "контакт" in reply_text,
        "телефон" in reply_text,
        "email" in reply_text,
        "telegram" in reply_text,
        "связи" in reply_text,
    ])

    history_text = "\n".join(
        item["content"]
        for item in session["history"]
    )

    has_contact = bool(extract_contact(history_text))

    if contact_requested and not has_contact:
        session["waiting_for_contact"] = True
        return

    if (
            ai_result.get("lead_ready")
            and has_contact
            and not session["lead_saved"]
    ):
        lead_id = save_telegram_lead(
            chat_id,
            session.get("username"),
            session["history"],
            ai_result,
        )

        session["lead_saved"] = True

        send_message(
            chat_id,
            f"Заявка AIha сформирована.\n\n"
            f"Номер заявки: {lead_id}\n"
            f"Приоритет: {ai_result.get('priority', 'Средний')}\n\n"
            f"Мы изучим задачу и свяжемся с вами."
        )


def run_bot():
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
            print("Telegram polling error:", error)
            continue

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            handle_message(update)


if __name__ == "__main__":
    run_bot()