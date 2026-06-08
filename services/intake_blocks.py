from __future__ import annotations

import sqlite3

from db import DB_PATH


def get_lead_with_constraints(lead_id: int) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
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
            (lead_id,),
        ).fetchone()

        constraints = conn.execute(
            """
            SELECT
                id,
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
            FROM client_constraints
            WHERE lead_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()

    return lead, constraints


def value(row: sqlite3.Row | None, key: str, default: str = "не указано") -> str:
    if row is None:
        return default

    item = row[key]

    if item is None:
        return default

    text = str(item).strip()
    return text if text else default


def build_intake_v33_input_block(lead: sqlite3.Row, constraints: sqlite3.Row | None) -> str:
    return f"""
Входные данные для Intake Agent v3.3:

1. Источник заявки

Lead_ID: L-{int(lead["id"]):03d}
Дата_заявки: {value(lead, "created_at")}
Платформа_источник: {detect_platform_source(value(lead, "source"))}
Канал_заявки: {detect_channel(value(lead, "source"))}
Источник: {value(lead, "source")}
Тип_заявки: {detect_request_type(value(lead, "source"), value(lead, "message"))}

2. Компания и контакт

Компания: {value(lead, "company")}
Контактное_лицо: {value(lead, "name")}
Телефон: {value(lead, "phone")}
Email: не указано
Отрасль: {value(lead, "industry")}
Размер_компании: не указано

3. Бизнес-запрос

Краткая_боль:
{value(lead, "message")}

Что клиент хочет решить:
Нужно определить по тексту заявки. Возможные направления: аудит процесса, снижение ручного труда, контроль сроков, оценка данных, проверка AI-readiness, подготовка roadmap или MVP scope.

Ожидаемый_результат:
не указано

4. Процесс для аудита

Процесс_для_аудита:
{value(lead, "process")}

Краткое описание процесса:
не указано

Участники процесса:
не указано

Ручные операции:
не указано

Где возникают проблемы:
не указано

Есть ли регламент:
не указано

5. Системы и данные

Текущие_системы:
не указано

Какие данные есть:
не указано

История_данных:
не указано

Объём_данных:
не указано

Качество данных:
не указано

Можно предоставить:
не указано

6. Ограничения, безопасность и персональные данные

Есть_персональные_данные:
{value(constraints, "has_personal_data")}

Типы_персональных_данных:
{value(constraints, "personal_data_types")}

Можно_обезличить:
{value(constraints, "can_anonymize")}

Облако_допустимо:
{value(constraints, "cloud_allowed")}

Требования_к_локализации:
{value(constraints, "localization_requirements")}

Есть_политики_ИБ:
{value(constraints, "security_policies")}

Нужен_NDA:
{value(constraints, "nda_required")}

7. Метрики и экономика

Есть_метрики_для_ROI:
{value(constraints, "roi_metrics_available")}

Какие_метрики_есть:
{value(constraints, "roi_metrics_details")}

Бюджет_известен:
{value(constraints, "budget_known")}

8. Ожидания по ИИ и MVP

Клиент уже понимает, какое ИИ-решение хочет:
{value(lead, "ai_type")}

Потенциально интересные решения:
не указано

Готовность_к_MVP:
{value(constraints, "mvp_readiness")}

Ограничения_scope:
{value(constraints, "scope_limitations")}

9. Предпочтительный следующий шаг

Удобный формат следующего шага:
не указано

Комментарий:
Источник лида: {value(lead, "source")}.
Статус лида: {value(lead, "status")}.
Приоритет AI-квалификации: {value(lead, "priority")}.
Риск ограничений: {value(constraints, "constraint_risk")}.
Следующее действие по ограничениям: {value(constraints, "next_action")}.
Комментарий по ограничениям: {value(constraints, "comment")}.
""".strip()


def detect_platform_source(source: str) -> str:
    if source == "aiha_consulting_audit_form":
        return "AIha Consulting"

    if source in {"landing", "landing_form"}:
        return "AIha Studio"

    if source == "callback_widget":
        return "AIha Studio"

    return "не указано"


def detect_channel(source: str) -> str:
    if source == "aiha_consulting_audit_form":
        return "Форма AI-аудита"

    if source in {"landing", "landing_form"}:
        return "Лендинг"

    if source == "callback_widget":
        return "Callback widget"

    return source or "не указано"


def detect_request_type(source: str, message: str) -> str:
    text = message.lower()

    if source == "aiha_consulting_audit_form":
        return "AI-аудит"

    if "аудит" in text or "диагност" in text:
        return "AI-аудит"

    if "mvp" in text or "внедр" in text:
        return "Внедрение ИИ-решения"

    return "Не определено"