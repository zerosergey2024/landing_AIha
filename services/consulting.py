from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from db import DB_PATH


def build_consulting_message(data: dict[str, str]) -> str:
    """Собирает подробное описание заявки AIha Consulting для поля message."""

    return f"""
Заявка AIha Consulting — первичный AI-аудит

Компания: {data.get("company", "").strip() or "не указано"}
Отрасль: {data.get("industry", "").strip() or "не указано"}
Размер компании: {data.get("company_size", "").strip() or "не указано"}

Контактное лицо: {data.get("contact_person", "").strip() or "не указано"}
Телефон: {data.get("phone", "").strip() or "не указано"}
Email: {data.get("email", "").strip() or "не указано"}

Бизнес-боль:
{data.get("business_pain", "").strip() or "не указано"}

Процесс для аудита:
{data.get("audit_process", "").strip() or "не указано"}

Текущие системы:
{data.get("current_systems", "").strip() or "не указано"}

Есть данные:
{data.get("has_data", "").strip() or "не указано"}

Персональные данные:
{data.get("has_personal_data", "").strip() or "не указано"}

Можно обезличить:
{data.get("can_anonymize", "").strip() or "не указано"}

Облако допустимо:
{data.get("cloud_allowed", "").strip() or "не указано"}

Метрики для ROI:
{data.get("roi_metrics_available", "").strip() or "не указано"}

Готовность к MVP:
{data.get("mvp_readiness", "").strip() or "не указано"}

Экономика и метрики процесса:

Объём операций в месяц:
{data.get("monthly_volume", "").strip() or "не указано"}

Среднее время обработки:
{data.get("avg_processing_time", "").strip() or "не указано"}

Время на сложную операцию:
{data.get("complex_processing_time", "").strip() or "не указано"}

Сотрудники в процессе:
{data.get("employees_in_process", "").strip() or "не указано"}

Стоимость часа / ФОТ:
{data.get("hourly_cost", "").strip() or "не указано"}

Ошибки / просрочки / потери:
{data.get("error_delay_loss_rate", "").strip() or "не указано"}

Контроль / отчётность / ручной перенос:
{data.get("control_reporting_time", "").strip() or "не указано"}

Бюджет:
{data.get("budget_details", "").strip() or "не указано"}

Ожидаемый бизнес-эффект:
{data.get("expected_business_effect", "").strip() or "не указано"}

Комментарий:
{data.get("comment", "").strip() or "не указано"}
""".strip()


def build_consulting_telegram_text(data: dict[str, str], lead_id: int) -> str:
    """Собирает Telegram-уведомление по заявке AIha Consulting."""

    return f"""
🧭 Новая заявка AIha Consulting

🏢 Компания: {data.get("company", "").strip() or "Не указана"}
👤 Контакт: {data.get("contact_person", "").strip() or "Не указан"}
📞 Телефон: {data.get("phone", "").strip() or "Не указан"}
✉️ Email: {data.get("email", "").strip() or "Не указан"}

📌 Процесс:
{data.get("audit_process", "").strip() or "Не указан"}

📋 Боль:
{data.get("business_pain", "").strip() or "Не указана"}

🧩 Системы:
{data.get("current_systems", "").strip() or "Не указаны"}

🔐 Ограничения:
• ПДн: {data.get("has_personal_data", "").strip() or "Не указано"}
• Обезличивание: {data.get("can_anonymize", "").strip() or "Не указано"}
• Облако: {data.get("cloud_allowed", "").strip() or "Не указано"}

📊 ROI-метрики: {data.get("roi_metrics_available", "").strip() or "Не указано"}
📈 Объём: {data.get("monthly_volume", "").strip() or "Не указано"}
⏱ Время обработки: {data.get("avg_processing_time", "").strip() or "Не указано"}
⚠️ Ошибки/просрочки/потери: {data.get("error_delay_loss_rate", "").strip() or "Не указано"}
💰 Бюджет: {data.get("budget_details", "").strip() or "Не указано"}
🚀 MVP: {data.get("mvp_readiness", "").strip() or "Не указано"}

🆔 Lead ID: {lead_id}
""".strip()

def build_roi_metrics_details(data: dict[str, str]) -> str:
    """Собирает экономические метрики процесса в единый текстовый блок."""

    return f"""
Объём операций в месяц: {data.get("monthly_volume", "").strip() or "не указано"}
Среднее время обработки: {data.get("avg_processing_time", "").strip() or "не указано"}
Время на сложную операцию: {data.get("complex_processing_time", "").strip() or "не указано"}
Сотрудники в процессе: {data.get("employees_in_process", "").strip() or "не указано"}
Стоимость часа / ФОТ: {data.get("hourly_cost", "").strip() or "не указано"}
Ошибки / просрочки / потери: {data.get("error_delay_loss_rate", "").strip() or "не указано"}
Контроль / отчётность / ручной перенос: {data.get("control_reporting_time", "").strip() or "не указано"}
Ожидаемый бизнес-эффект: {data.get("expected_business_effect", "").strip() or "не указано"}
""".strip()


def evaluate_constraint_risk(data: dict[str, str]) -> str:
    """Оценивает риск ограничений на основании ПДн, облака и обезличивания."""

    has_personal_data = data.get("has_personal_data", "").strip()
    can_anonymize = data.get("can_anonymize", "").strip()
    cloud_allowed = data.get("cloud_allowed", "").strip()

    if has_personal_data == "Да" and can_anonymize == "Нет":
        return "Критический"

    if has_personal_data == "Да" and cloud_allowed in {
        "Нет",
        "Только локально / внутри корпоративной сети",
    }:
        return "Высокий"

    if has_personal_data == "Да" and can_anonymize in {
        "Нужно согласование",
        "Не указано",
        "",
    }:
        return "Высокий"

    if has_personal_data == "Да":
        return "Средний"

    if cloud_allowed in {
        "Нет",
        "Только локально / внутри корпоративной сети",
        "Нужно уточнить",
    }:
        return "Средний"

    return "Низкий"


def determine_constraints_next_action(data: dict[str, str]) -> str:
    """Определяет следующее действие по ограничениям клиента."""

    has_personal_data = data.get("has_personal_data", "").strip()
    can_anonymize = data.get("can_anonymize", "").strip()
    cloud_allowed = data.get("cloud_allowed", "").strip()
    nda_required = data.get("nda_required", "").strip()
    roi_metrics_available = data.get("roi_metrics_available", "").strip()

    if has_personal_data == "Да" and can_anonymize == "Нет":
        return "Ограничить scope диагностики"

    if nda_required == "Да":
        return "Запросить NDA"

    if cloud_allowed in {
        "Нет",
        "Только локально / внутри корпоративной сети",
        "Нужно уточнить",
    }:
        return "Уточнить ИТ / ИБ ограничения"

    if has_personal_data == "Да" and can_anonymize in {
        "Да",
        "Нужно согласование",
        "Не указано",
        "",
    }:
        return "Запросить обезличенные данные"

    if roi_metrics_available in {
        "Пока нет",
        "Затрудняюсь ответить",
        "",
    }:
        return "Уточнить метрики для ROI"

    return "Передать в Qualification Agent"


def save_client_constraints(lead_id: int, data: dict[str, str]) -> None:
    """Сохраняет ограничения клиента в таблицу client_constraints."""

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    has_personal_data = data.get("has_personal_data", "").strip() or "Не знаю"
    can_anonymize = data.get("can_anonymize", "").strip() or "Не указано"
    cloud_allowed = data.get("cloud_allowed", "").strip() or "Нужно уточнить"
    roi_metrics_available = (
        data.get("roi_metrics_available", "").strip() or "Затрудняюсь ответить"
    )
    mvp_readiness = data.get("mvp_readiness", "").strip() or "Пока не знаем"

    constraint_risk = evaluate_constraint_risk(data)
    next_action = determine_constraints_next_action(data)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO client_constraints (
                lead_id,
                has_personal_data,
                personal_data_types,
                can_anonymize,
                cloud_allowed,
                localization_requirements,
                security_policies,
                nda_required,
                roi_metrics_available,
                roi_metrics_details,
                budget_known,
                mvp_readiness,
                scope_limitations,
                constraint_risk,
                next_action,
                comment,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead_id,
                has_personal_data,
                data.get("personal_data_types", "").strip() or "не указано",
                can_anonymize,
                cloud_allowed,
                data.get("localization_requirements", "").strip() or "не указано",
                data.get("security_policies", "").strip() or "не указано",
                data.get("nda_required", "").strip() or "Нужно уточнить",
                roi_metrics_available,
                build_roi_metrics_details(data),
                data.get("budget_details", "").strip()
                or data.get("budget_known", "").strip()
                or "Затрудняюсь ответить",
                mvp_readiness,
                data.get("scope_limitations", "").strip() or "не указано",
                constraint_risk,
                next_action,
                data.get("comment", "").strip() or "не указано",
                now,
                now,
            ),
        )
        conn.commit()