import os
import re
import time
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from llm_assistant import ask_phi4_conversation


# ============================================================
# Runtime configuration
# ============================================================

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "leads.db"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ============================================================
# In-memory session storage
#
# Для MVP это нормально.
# Для масштабирования лучше перенести сессии в Redis или SQLite.
# ============================================================

user_sessions = {}


# ============================================================
# Text constants
# ============================================================

CONTACT_REQUEST_TEXT = (
    "Если вы заинтересованы в автоматизации рутинных процессов своего бизнеса, "
    "оставьте, пожалуйста, свои координаты: имя и телефон, Telegram или email."
)

START_TEXT = (
    "Здравствуйте. Это AIha Студия.\n\n"
    "Мы помогаем автоматизировать рутинные процессы бизнеса: заявки, заказы, HR, "
    "документы, производство, логистику и клиентский сервис.\n\n"
    "Опишите коротко, какой процесс хотите автоматизировать."
)

RESET_TEXT = (
    "Диалог сброшен.\n\n"
    "Опишите задачу или процесс, который хотите автоматизировать."
)

LEAD_SAVED_TEXT_TEMPLATE = (
    "Спасибо. Заявка AIha сформирована.\n\n"
    "Номер заявки: {lead_id}\n"
    "Специалист AIha свяжется с вами для уточнения деталей."
)

ALREADY_SAVED_TEXT = (
    "Заявка уже зафиксирована. Специалист AIha свяжется с вами."
)

NON_TARGET_TEXT = (
    "AIha занимается автоматизацией бизнес-процессов: заявок, заказов, документов, "
    "HR, логистики и клиентского сервиса.\n\n"
    "Опишите, какой процесс или задачу вы хотите автоматизировать."
)

SPAM_REJECT_TEXT = "Заявка отклонена автоматическим фильтром."


# ============================================================
# Spam rules
#
# Это быстрый deterministic-фильтр.
# Его задача — не идеальная классификация, а отсечение очевидного мусора.
# ============================================================

SPAM_PATTERNS = [
    "searchregister",
    "google search index",
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

SPAM_COMPANIES = [
    "web search index",
]


# ============================================================
# Basic utilities
# ============================================================

def now_utc():
    """
    Returns ISO timestamp in UTC.

    Храним время в UTC, чтобы не зависеть от timezone сервера.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_event(event_name, **kwargs):
    """
    Simple structured logging.

    Потом это можно заменить на logging / Sentry / JSON logs.
    """
    payload = " ".join(f"{key}={value}" for key, value in kwargs.items())
    print(f"[{now_utc()}] {event_name} {payload}".strip())


def init_db_runtime():
    """
    SQLite runtime settings.

    WAL снижает вероятность lock-проблем, когда основной поток сохраняет лид,
    а background thread позже обновляет AI enrichment.
    """
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=10000;")


def is_spam_lead(text):
    """
    Fast spam filter.

    Phi-4 сюда не подключаем: спам должен отсекаться быстро и дешево.
    """
    lower = text.lower()

    for pattern in SPAM_PATTERNS:
        if pattern in lower:
            return True

    for company in SPAM_COMPANIES:
        if company in lower:
            return True

    return False


# ============================================================
# Contact extraction
# ============================================================

def extract_phone(text):
    """
    Extracts phone-like strings.

    Regex достаточно простой, но покрывает:
    +49 123 456...
    +7 (999) 123-45-67
    89991234567
    """
    match = re.search(r"(\+?\d[\d\s\-\(\)]{8,}\d)", text)
    return match.group(1).strip() if match else ""


def extract_email(text):
    """
    Extracts email from text.
    """
    match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    return match.group(0) if match else ""


def extract_telegram(text):
    """
    Extracts Telegram handle.
    """
    match = re.search(r"@\w+", text)
    return match.group(0) if match else ""


def extract_contact(text):
    """
    Returns all contacts found in the text as a single string.

    В CRM сейчас поле phone используется как contact field.
    Поэтому туда складываем phone/email/telegram.
    """
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


def extract_client_name(text, telegram_username=""):
    """
    Tries to extract a human-looking client name.

    Это heuristic, не критичная бизнес-логика.
    Если не нашли имя — используем Telegram username / first_name.
    """
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


# ============================================================
# Intent / business context detection
# ============================================================

def has_business_context(text):
    """
    Deterministic business-context detector.

    Если видим явный бизнес-контекст, не зовем Phi-4 в real-time path.
    Просто просим контакт.
    """
    lower = text.lower()

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
        "crm",
        "лид",
        "продаж",
        "менеджер",
        "поддержк",
        "сервис",
        "склад",
        "отчет",
        "отчёт",
        "таблиц",
        "excel",
        "интеграц",
        "api",
        "сайт",
        "лендинг",
        "форма",
        "колл",
        "call",
        "оператор",
        "консультац",
    ]

    return any(signal in lower for signal in signals)


def has_unclear_start_marker(text):
    """
    Detects phrases that often indicate a non-standard but potentially valid lead.

    Пример:
    - "Можно вопрос?"
    - "Сколько стоит?"
    - "У нас есть задача..."
    - "Хочу понять, сможете ли помочь"
    """
    lower = text.lower()

    unclear_markers = [
        "можно",
        "подскажите",
        "интересно",
        "хочу понять",
        "у нас",
        "мы",
        "есть задача",
        "нужна помощь",
        "как это работает",
        "что можете",
        "сколько стоит",
        "цена",
        "стоимость",
        "консультация",
        "консультац",
        "обсудить",
        "вопрос",
        "поможете",
        "можете помочь",
        "надо понять",
        "ищем решение",
        "хотим внедрить",
        "расскажите",
    ]

    return any(marker in lower for marker in unclear_markers)


def build_history_text(history):
    """
    Converts structured session history into plain text.
    """
    return "\n".join(
        f"{item['role']}: {item['content']}"
        for item in history
    )


def trim_history(history, max_messages=8):
    """
    Keeps only last messages for Phi-4.

    Это снижает latency и уменьшает риск длинного контекста.
    """
    return history[-max_messages:]


def make_default_ai_result(history_text):
    """
    Default enrichment structure.

    Используется для быстрого сохранения лида без ожидания Phi-4.
    Потом Phi-4 может обновить эти поля в фоне.
    """
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
        "reply": "",
    }


def should_use_ai_for_reply(session, history_text):
    """
    Controlled Phi-4 fallback.

    Ключевая идея:
    - Phi-4 НЕ нужен после контакта перед ответом клиенту.
    - Phi-4 НЕ нужен, если бизнес-контекст уже понятен: сразу просим контакт.
    - Phi-4 НУЖЕН, если клиент начал нестандартно и deterministic-логика
      может зациклиться.
    """
    history = session.get("history", [])
    unclear_count = session.get("unclear_count", 0)

    user_messages = [
        item["content"]
        for item in history
        if item.get("role") == "user"
    ]

    last_user_message = user_messages[-1] if user_messages else ""
    last_user_message = last_user_message.strip()

    if not last_user_message:
        return False

    # Если уже есть контакт, real-time Phi-4 запрещен.
    # Сначала сохраняем лид и отвечаем.
    if extract_contact(history_text):
        return False

    # Если бизнес-контекст понятен, не зовем Phi-4:
    # быстрее и надежнее попросить контакт.
    if has_business_context(history_text):
        return False

    # Совсем короткие сообщения не стоит отправлять в модель.
    # Например: "да", "ок", "привет".
    if len(last_user_message) < 8:
        return False

    # Anti-loop:
    # если мы уже один раз дали deterministic fallback, но клиент продолжает,
    # подключаем Phi-4, чтобы бот не повторял одно и то же.
    if unclear_count >= 1:
        return True

    # Если клиент уже написал 2+ сообщений, а контекст всё ещё не распознан,
    # лучше подключить Phi-4.
    if len(user_messages) >= 2:
        return True

    # Длинное сообщение может быть нестандартным описанием задачи без явных keywords.
    if len(last_user_message) >= 80:
        return True

    # Нестандартный старт с коммерческими / консультационными маркерами.
    if has_unclear_start_marker(last_user_message):
        return True

    return False


# ============================================================
# Telegram API helpers
# ============================================================

def send_message(chat_id, text):
    """
    Sends Telegram message.

    Возвращает True/False, чтобы при необходимости можно было логировать delivery.
    """
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
        return True

    except requests.RequestException as error:
        log_event(
            "telegram_send_error",
            error_type=type(error).__name__,
            error=str(error),
        )
        return False


def send_typing(chat_id):
    """
    Shows Telegram typing indicator.

    Это важно для UX, если Phi-4 всё-таки подключается в pre-contact path.
    """
    try:
        requests.post(
            f"{API_URL}/sendChatAction",
            json={
                "chat_id": chat_id,
                "action": "typing",
            },
            timeout=5,
        )
    except requests.RequestException:
        pass


# ============================================================
# Session management
# ============================================================

def reset_session(chat_id, username):
    """
    Resets user session.

    unclear_count нужен для anti-loop логики.
    """
    user_sessions[chat_id] = {
        "username": username,
        "history": [],
        "lead_saved": False,
        "asked_contact": False,
        "unclear_count": 0,
        "updated_at": time.time(),
    }


def get_or_create_session(chat_id, username):
    """
    Gets existing session or creates a new one.
    """
    session = user_sessions.get(chat_id)

    if not session:
        reset_session(chat_id, username)
        session = user_sessions[chat_id]

    session["updated_at"] = time.time()

    if username and not session.get("username"):
        session["username"] = username

    return session


def cleanup_old_sessions(max_age_seconds=24 * 60 * 60):
    """
    Removes stale sessions to prevent memory growth.

    For MVP: run once per polling loop.
    For bigger load: run on interval, not every loop.
    """
    now = time.time()

    old_chat_ids = [
        chat_id
        for chat_id, session in user_sessions.items()
        if now - session.get("updated_at", now) > max_age_seconds
    ]

    for chat_id in old_chat_ids:
        user_sessions.pop(chat_id, None)

    if old_chat_ids:
        log_event("sessions_cleaned", count=len(old_chat_ids))


# ============================================================
# Database operations
# ============================================================

def save_telegram_lead(chat_id, username, history, ai_result):
    """
    Fast lead save.

    Важно:
    - Здесь НЕЛЬЗЯ вызывать Phi-4.
    - Эта функция должна быть быстрой.
    - AI enrichment выполняется позже отдельным background job.
    """
    created_at = now_utc()
    history_text = build_history_text(history)

    contact = (
        ai_result.get("contact")
        or extract_contact(history_text)
        or "уточнить в Telegram"
    )

    client_name = (
        ai_result.get("client_name")
        or extract_client_name(history_text, username)
        or username
        or f"Telegram user {chat_id}"
    )

    summary = ai_result.get("summary") or history_text

    with sqlite3.connect(DB_PATH, timeout=10) as conn:
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
                contact,
                "",
                summary,
                "telegram",
                created_at,
                ai_result.get("industry", "Бизнес-процессы"),
                ai_result.get("process", "Автоматизация процесса"),
                "Pending AI enrichment",
                ai_result.get("goal", "Снижение ручного труда и ускорение обработки"),
                ai_result.get("priority", "Средний"),
                "Новая",
                "",
                created_at,
            ),
        )

        conn.commit()
        return cursor.lastrowid


def update_lead_ai_enrichment(lead_id, ai_result):
    """
    Updates lead with Phi-4 enrichment.

    Эта функция вызывается после того, как клиент уже получил ответ.
    """
    updated_at = now_utc()

    industry = ai_result.get("industry") or "Бизнес-процессы"
    process = ai_result.get("process") or "Автоматизация процесса"
    effect = ai_result.get("goal") or "Снижение ручного труда и ускорение обработки"
    priority = ai_result.get("priority") or "Средний"
    summary = ai_result.get("summary") or ""

    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            """
            UPDATE leads
            SET
                industry = ?,
                process = ?,
                ai_type = ?,
                effect = ?,
                priority = ?,
                message = CASE
                    WHEN ? != '' THEN ?
                    ELSE message
                END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                industry,
                process,
                "Phi-4 Mini assistant",
                effect,
                priority,
                summary,
                summary,
                updated_at,
                lead_id,
            ),
        )

        conn.commit()


# ============================================================
# Phi-4 handling
# ============================================================

def get_ai_result_safe(history):
    """
    Safe Phi-4 call wrapper.

    Важно:
    - может быть медленным;
    - не должен использоваться в post-contact blocking path;
    - используем trim_history(), чтобы снизить latency.
    """
    compact_history = trim_history(history)
    started_at = time.perf_counter()

    try:
        result = ask_phi4_conversation(compact_history)

        elapsed = time.perf_counter() - started_at
        log_event(
            "phi4_success",
            latency=f"{elapsed:.2f}s",
            messages=len(compact_history),
            chars=len(build_history_text(compact_history)),
        )

        if not isinstance(result, dict):
            log_event("phi4_invalid_result", result_type=type(result).__name__)
            return make_default_ai_result(build_history_text(compact_history))

        return result

    except Exception as error:
        elapsed = time.perf_counter() - started_at
        log_event(
            "phi4_error",
            latency=f"{elapsed:.2f}s",
            error_type=type(error).__name__,
            error=str(error),
        )

        return make_default_ai_result(build_history_text(compact_history))


def enrich_lead_in_background(lead_id, history):
    """
    Background Phi-4 enrichment.

    Используется после сохранения лида:
    1. клиент уже получил номер заявки;
    2. Phi-4 спокойно делает summary/scoring/enrichment;
    3. CRM обновляется позже.
    """
    history_copy = [item.copy() for item in history]

    def job():
        log_event("ai_enrichment_started", lead_id=lead_id)

        ai_result = get_ai_result_safe(history_copy)

        if not ai_result.get("summary"):
            ai_result["summary"] = build_history_text(history_copy)

        try:
            update_lead_ai_enrichment(lead_id, ai_result)
            log_event("ai_enrichment_completed", lead_id=lead_id)

        except Exception as error:
            log_event(
                "ai_enrichment_db_error",
                lead_id=lead_id,
                error_type=type(error).__name__,
                error=str(error),
            )

    thread = threading.Thread(
        target=job,
        name=f"aiha-enrich-lead-{lead_id}",
        daemon=True,
    )
    thread.start()


def build_ai_reply_from_result(ai_result, history_text):
    """
    Normalizes Phi-4 response for pre-contact conversation.

    Phi-4 может вернуть:
    - reply;
    - lead_ready;
    - summary;
    - intent fields.

    Но пользователю нужен короткий следующий шаг, а не длинный анализ.
    """
    ai_reply = ai_result.get("reply", "")
    ai_reply = ai_reply.strip() if isinstance(ai_reply, str) else ""

    # Если модель решила, что лид готов, но контакта нет,
    # не продолжаем беседу бесконечно — просим контакт.
    if ai_result.get("lead_ready") and not extract_contact(history_text):
        return CONTACT_REQUEST_TEXT

    # Защита от пустого или слишком длинного ответа.
    if not ai_reply or len(ai_reply) > 600:
        return (
            "Понял. Чтобы точнее оценить задачу, уточните, пожалуйста: "
            "какой процесс вы хотите автоматизировать и где сейчас возникает ручная работа?"
        )

    return ai_reply


# ============================================================
# Main message handler
# ============================================================

def handle_message(update):
    """
    Main Telegram update handler.

    Core architecture:
    1. /start and /reset handled immediately.
    2. Spam handled immediately.
    3. If contact exists:
       - save lead immediately;
       - reply immediately;
       - run AI enrichment in background.
    4. If business context exists but no contact:
       - ask contact immediately.
    5. If unclear / non-standard:
       - first deterministic fallback;
       - then Phi-4 controlled fallback to avoid loops.
    """
    message = update.get("message", {})
    chat = message.get("chat", {})
    user = message.get("from", {})

    chat_id = chat.get("id")
    text = message.get("text", "").strip()
    username = user.get("username") or user.get("first_name") or ""

    if not chat_id or not text:
        return

    if text == "/reset":
        reset_session(chat_id, username)
        send_message(chat_id, RESET_TEXT)
        return

    if text in ["/start", "/lead"]:
        reset_session(chat_id, username)
        send_message(chat_id, START_TEXT)
        return

    session = get_or_create_session(chat_id, username)

    if session.get("lead_saved"):
        send_message(chat_id, ALREADY_SAVED_TEXT)
        return

    # Save user message to session history.
    session["history"].append(
        {
            "role": "user",
            "content": text,
        }
    )
    session["updated_at"] = time.time()

    history_text = build_history_text(session["history"])

    # --------------------------------------------------------
    # 1. Spam filter
    # --------------------------------------------------------
    if is_spam_lead(history_text):
        send_message(chat_id, SPAM_REJECT_TEXT)
        reset_session(chat_id, username)
        return

    contact = extract_contact(history_text)
    client_name = extract_client_name(history_text, username)
    business_ready = has_business_context(history_text)

    # --------------------------------------------------------
    # 2. Fast path: contact found
    #
    # This is the most important production branch.
    # No Phi-4 call before responding to the user.
    # --------------------------------------------------------
    if contact:
        ai_result = make_default_ai_result(history_text)
        ai_result["lead_ready"] = True
        ai_result["client_name"] = client_name
        ai_result["contact"] = contact
        ai_result["summary"] = history_text

        lead_id = save_telegram_lead(
            chat_id=chat_id,
            username=session.get("username"),
            history=session["history"],
            ai_result=ai_result,
        )

        session["lead_saved"] = True
        session["updated_at"] = time.time()

        # Client gets response immediately.
        send_message(
            chat_id,
            LEAD_SAVED_TEXT_TEMPLATE.format(lead_id=lead_id),
        )

        # AI enrichment happens after the client-facing response.
        enrich_lead_in_background(
            lead_id=lead_id,
            history=session["history"],
        )

        return

    # --------------------------------------------------------
    # 3. Clear business context, but no contact
    #
    # No Phi-4 needed. Ask for contact immediately.
    # --------------------------------------------------------
    if business_ready and not contact:
        session["asked_contact"] = True
        session["unclear_count"] = 0
        session["updated_at"] = time.time()

        session["history"].append(
            {
                "role": "assistant",
                "content": CONTACT_REQUEST_TEXT,
            }
        )

        send_message(chat_id, CONTACT_REQUEST_TEXT)
        return

    # --------------------------------------------------------
    # 4. Unclear / non-standard path
    #
    # Here we decide whether to use Phi-4.
    # This prevents loops like:
    # bot: "describe process"
    # user: "can you help?"
    # bot: "describe process"
    # --------------------------------------------------------
    if should_use_ai_for_reply(session, history_text):
        send_typing(chat_id)

        ai_result = get_ai_result_safe(session["history"])
        ai_reply = build_ai_reply_from_result(ai_result, history_text)

        session["history"].append(
            {
                "role": "assistant",
                "content": ai_reply,
            }
        )

        session["updated_at"] = time.time()

        send_message(chat_id, ai_reply)
        return

    # --------------------------------------------------------
    # 5. First deterministic fallback
    #
    # This is intentionally cheap and fast.
    # If the user continues after this, unclear_count triggers Phi-4.
    # --------------------------------------------------------
    session["unclear_count"] = session.get("unclear_count", 0) + 1
    session["updated_at"] = time.time()

    session["history"].append(
        {
            "role": "assistant",
            "content": NON_TARGET_TEXT,
        }
    )

    send_message(chat_id, NON_TARGET_TEXT)


# ============================================================
# Polling loop
# ============================================================

def run_bot():
    """
    Telegram long polling loop.

    For MVP it is OK.
    For higher load consider webhook + queue.
    """
    offset = None

    init_db_runtime()

    log_event("telegram_bot_started")

    while True:
        cleanup_old_sessions()

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
            log_event(
                "telegram_polling_error",
                error_type=type(error).__name__,
                error=str(error),
            )
            time.sleep(2)
            continue

        for update in data.get("result", []):
            offset = update["update_id"] + 1

            try:
                handle_message(update)

            except Exception as error:
                log_event(
                    "handle_message_error",
                    error_type=type(error).__name__,
                    error=str(error),
                )


if __name__ == "__main__":
    run_bot()