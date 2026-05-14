import json
import re
import requests

from lead_qualifier import qualify_lead


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "phi4-mini"

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

def extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)

    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    return {}

def detect_lead_readiness(history_text: str) -> bool:
    text = history_text.lower()

    business_signals = [
        "заказы",
        "доставка",
        "1с",
        "магазин",
        "ресторан",
        "производство",
        "склад",
        "логистика",
        "заявки",
        "оборудование",
        "hr",
        "клиенты",
        "бот",
        "автоматизация",
    ]

    enough_context = (
        len(text) > 120 and
        sum(word in text for word in business_signals) >= 2
    )

    return enough_context

def normalize_result(result: dict, original_text: str) -> dict:
    fallback = qualify_lead(original_text)

    return {
        "reply": result.get("reply") or (
            "Понял задачу. "
            "AIha занимается автоматизацией подобных процессов. "
            "Оставьте удобный контакт для связи: телефон, Telegram или email."
        ),
        "industry": result.get("industry") or fallback.get("industry", "Не определено"),
        "process": result.get("process") or fallback.get("process", "Не определено"),
        "problem": result.get("problem") or original_text,
        "goal": result.get("goal") or fallback.get("effect", "Требует уточнения"),
        "priority": result.get("priority") or fallback.get("priority", "Средний"),
        "summary": result.get("summary") or original_text,
        "lead_ready": (
                bool(result.get("lead_ready", False))
                or detect_lead_readiness(original_text)
        ),
    }

def ask_phi4_conversation(history: list[dict]) -> dict:
    dialogue = ""

    for item in history[-12:]:
        role = item.get("role", "user")
        content = item.get("content", "")
        dialogue += f"{role}: {content}\n"

    prompt = f"""
{SYSTEM_PROMPT}

История диалога:
{dialogue}
"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": False,
            },
            timeout=90,
        )

        response.raise_for_status()

        data = response.json()
        raw_text = data.get("response", "").strip()

        print("=== PHI-4 RAW RESPONSE ===")
        print(raw_text)
        print("==========================")

        parsed = extract_json(raw_text)
        return normalize_result(parsed, dialogue)

    except Exception as error:
        print(f"Phi-4 Mini error: {error}")
        return normalize_result({}, dialogue)


def ask_phi4(user_message: str) -> dict:
    return ask_phi4_conversation([
        {
            "role": "user",
            "content": user_message,
        }
    ])