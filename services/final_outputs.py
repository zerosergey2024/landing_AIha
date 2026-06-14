from __future__ import annotations

import re
import sqlite3

from db import DB_PATH


WORKFLOW_TYPE = "fast_audit"

TASK_SELECT_SQL = """
    SELECT
        id,
        task_code,
        lead_id,
        company,
        agent_type,
        stage,
        task_title,
        input_source,
        expected_output,
        status,
        priority,
        owner,
        human_required,
        result,
        next_action,
        due_date,
        comment,
        created_at,
        updated_at
    FROM agent_tasks
"""


def value(row: sqlite3.Row | None, key: str, default: str = "не указано") -> str:
    """
    Безопасно достаёт значение из sqlite3.Row.
    """
    if row is None:
        return default

    try:
        item = row[key]
    except (KeyError, IndexError):
        return default

    if item is None:
        return default

    text = str(item).strip()
    return text if text else default


def get_lead(lead_id: int) -> sqlite3.Row | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        return conn.execute(
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


def get_constraints(lead_id: int) -> sqlite3.Row | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        return conn.execute(
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


def get_tasks(lead_id: int) -> list[sqlite3.Row]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        return conn.execute(
            f"""
            {TASK_SELECT_SQL}
            WHERE lead_id = ?
            ORDER BY id ASC
            """,
            (lead_id,),
        ).fetchall()


def get_latest_done_task_by_stage(lead_id: int, stage: str) -> sqlite3.Row | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        return conn.execute(
            f"""
            {TASK_SELECT_SQL}
            WHERE lead_id = ?
              AND stage = ?
              AND status = 'Done'
            ORDER BY id DESC
            LIMIT 1
            """,
            (lead_id, stage),
        ).fetchone()


def is_meaningful_result(text: str) -> bool:
    normalized = text.strip().lower()

    empty_values = {
        "",
        "не указано",
        "none",
        "null",
    }

    if normalized in empty_values:
        return False

    technical_stubs = [
        "agent завершён",
        "agent завершен",
        "final output завершён",
        "final output завершен",
        "итоговый клиентский отчёт / кп сформирован",
        "итоговый клиентский отчет / кп сформирован",
    ]

    return not any(stub in normalized for stub in technical_stubs)


def build_not_ready_text(
    *,
    title: str,
    reason: str,
    next_action: str,
) -> str:
    return f"""
{title}

Статус: не готово.

Причина:
{reason}

Что нужно сделать:
{next_action}
""".strip()


def strip_before_client_document(text: str) -> str:
    """
    Убирает внутреннюю обёртку до начала клиентского документа.

    Например, срезает:
    - КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ / СЛЕДУЮЩИЙ ШАГ
    - Клиент...
    - Основание...
    - Материал для КП / следующего шага:
    """
    if not text:
        return text

    markers = [
        "# Предварительный итоговый",
        "## Предварительный итоговый",
        "Предварительный итоговый",
        "# Итоговый отчёт",
        "## Итоговый отчёт",
        "Итоговый отчёт",
        "# Итоговый отчет",
        "## Итоговый отчет",
        "Итоговый отчет",
        "# Отчёт",
        "## Отчёт",
        "# Отчет",
        "## Отчет",
        "Отчёт и рекомендации",
        "Отчет и рекомендации",
        "=== ИТОГОВЫЙ ОТЧЁТ ДЛЯ КЛИЕНТА ===",
        "=== ИТОГОВЫЙ ОТЧЕТ ДЛЯ КЛИЕНТА ===",
        "=== ПРЕДВАРИТЕЛЬНЫЙ ИТОГОВЫЙ ОТЧЁТ ДЛЯ КЛИЕНТА ===",
        "=== ПРЕДВАРИТЕЛЬНЫЙ ИТОГОВЫЙ ОТЧЕТ ДЛЯ КЛИЕНТА ===",
    ]

    positions: list[int] = []

    for marker in markers:
        index = text.find(marker)
        if index >= 0:
            positions.append(index)

    if positions:
        return text[min(positions):].strip()

    material_markers = [
        "Материал для КП / следующего шага:",
        "Материал для КП:",
        "Материал для коммерческого предложения:",
    ]

    for marker in material_markers:
        index = text.find(marker)
        if index >= 0:
            return text[index + len(marker):].strip()

    return text.strip()


def strip_internal_csv_block(text: str) -> str:
    """
    Убирает служебные CSV / строки для таблицы / внутренние таблицы состояния.
    """
    if not text:
        return text

    stop_markers = [
        "\n---\n\nСтрока для таблицы",
        "\nСтрока для таблицы",
        "\nСтрока для таблицы состояния",
        "\nСтрока для таблицы (CSV)",
        "\nСтрока для записи в таблицу",
        "\nСтрока для таблицы результата",
        "\n---\n\nCSV",
        "\nCSV",
        "\n### Таблица состояния",
        "\nТаблица состояния этапов",
    ]

    cut_positions: list[int] = []

    for marker in stop_markers:
        index = text.find(marker)
        if index >= 0:
            cut_positions.append(index)

    service_line_match = re.search(
        r"\nL-\d{3}\s*[;|].*?(?:\n|$)",
        text,
        flags=re.IGNORECASE,
    )
    if service_line_match:
        cut_positions.append(service_line_match.start())

    if not cut_positions:
        return text.strip()

    return text[: min(cut_positions)].strip()


def strip_internal_terms_block(text: str) -> str:
    """
    Убирает внутренний блок 'Условия для КП', если он используется как
    служебная приписка платформы, а не как часть клиентского КП.
    """
    if not text:
        return text

    markers = [
        "\n---\n\nУсловия для КП:",
        "\nУсловия для КП:",
    ]

    for marker in markers:
        index = text.find(marker)
        if index >= 0:
            return text[:index].strip()

    return text.strip()


def strip_internal_heading_noise(text: str) -> str:
    """
    Чистит отдельные внутренние фразы, если они попали в тело документа.
    """
    if not text:
        return text

    noisy_phrases = [
        "Если клиенту требуется отдельное КП, используйте вывод Final Output как основу для коммерческого предложения.",
        "Итоговый документ сформирован по результатам fast_audit workflow:",
        "Intake Completeness → Risk Assessment → Economics Assessment → Final Output.",
        "Материал для КП / следующего шага:",
        "Материал для КП:",
    ]

    cleaned = text

    for phrase in noisy_phrases:
        cleaned = cleaned.replace(phrase, "")

    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_client_output_text(text: str) -> str:
    """
    Делает клиентскую версию итогового документа:
    - убирает внутреннюю обёртку;
    - убирает CSV / служебные строки;
    - убирает внутренние условия КП;
    - чистит лишние пустые строки.
    """
    cleaned = text or ""

    cleaned = strip_before_client_document(cleaned)
    cleaned = strip_internal_csv_block(cleaned)
    cleaned = strip_internal_terms_block(cleaned)
    cleaned = strip_internal_heading_noise(cleaned)

    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()


def build_fast_audit_client_document(
    *,
    final_output_task: sqlite3.Row | None,
) -> str:
    """
    Возвращает очищенную клиентскую версию Final Output.
    """
    final_output_result = value(final_output_task, "result")

    if final_output_task is None:
        return build_not_ready_text(
            title="ИТОГОВЫЙ ОТЧЁТ / КП AIha Consulting",
            reason="Задача Final Output ещё не создана или не завершена.",
            next_action=(
                "Запустите этап Final Output через AI Agent или вставьте "
                "клиентский итоговый результат вручную."
            ),
        )

    if not is_meaningful_result(final_output_result):
        return build_not_ready_text(
            title="ИТОГОВЫЙ ОТЧЁТ / КП AIha Consulting",
            reason="В задаче Final Output нет полноценного клиентского документа.",
            next_action=(
                "Запустите AI Agent на этапе Final Output или вставьте "
                "итоговый отчёт / КП в поле result задачи."
            ),
        )

    return normalize_client_output_text(final_output_result)


def get_final_outputs(lead_id: int) -> dict[str, object] | None:
    lead = get_lead(lead_id)

    if lead is None:
        return None

    constraints = get_constraints(lead_id)
    tasks = get_tasks(lead_id)
    final_output_task = get_latest_done_task_by_stage(lead_id, "Final Output")

    client_document = build_fast_audit_client_document(
        final_output_task=final_output_task,
    )

    return {
        "lead": lead,
        "constraints": constraints,
        "tasks": tasks,
        "workflow_type": WORKFLOW_TYPE,
        "final_output_task": final_output_task,

        # Совместимость с routes/admin.py и admin_final_outputs.html.
        # В компактной модели эти сущности не используются.
        "report_task": None,
        "human_review_task": None,
        "commercial_proposal_task": None,

        # В текущей модели это один клиентский документ:
        # итоговый отчёт + КП / следующий шаг.
        "final_report": client_document,
        "commercial_proposal": client_document,
    }