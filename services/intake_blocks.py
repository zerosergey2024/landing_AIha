from __future__ import annotations
import json
import sqlite3

from db import DB_PATH


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


def get_lead_with_constraints(
    lead_id: int,
) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    """
    Возвращает лид и последний блок client_constraints.
    """
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


def extract_form_value(message: str, label: str) -> str:
    """
    Достаёт значение из сырого текста анкеты.

    Поддерживает оба формата:

    1. Отрасль:
       Строительство

    2. Отрасль: Строительство
    """
    if not message:
        return "не указано"

    lines = message.splitlines()
    normalized_label = label.strip().lower().rstrip(":")

    for index, line in enumerate(lines):
        raw_line = line.strip()
        normalized_line = raw_line.lower()

        inline_prefix = f"{normalized_label}:"
        if normalized_line.startswith(inline_prefix):
            inline_value = raw_line[len(inline_prefix):].strip()
            if inline_value:
                return inline_value

        if normalized_line.rstrip(":") == normalized_label:
            for next_line in lines[index + 1:]:
                text = next_line.strip()
                if text:
                    return text

    return "не указано"


def choose_source_value(primary: str, fallback: str) -> str:
    """
    Берёт значение из анкеты, если оно есть.
    Иначе берёт структурированное поле лида.
    """
    primary = (primary or "").strip()
    fallback = (fallback or "").strip()

    if primary and primary.lower() != "не указано":
        return primary

    if fallback and fallback.lower() != "не указано":
        return fallback

    return "не указано"


def get_lead_business_context(lead: sqlite3.Row) -> dict[str, str]:
    """
    Формирует единый источник истины для отрасли, процесса, боли и эффекта.

    Приоритет:
    1. значения из сырой анкеты;
    2. структурированные поля lead;
    3. "не указано".
    """
    raw_message = value(lead, "message")

    business_pain = choose_source_value(
        extract_form_value(raw_message, "Бизнес-боль"),
        value(lead, "message"),
    )

    industry = choose_source_value(
        extract_form_value(raw_message, "Отрасль"),
        value(lead, "industry"),
    )

    process = choose_source_value(
        extract_form_value(raw_message, "Процесс для аудита"),
        value(lead, "process"),
    )

    expected_effect = choose_source_value(
        extract_form_value(raw_message, "Ожидаемый бизнес-эффект"),
        value(lead, "effect"),
    )

    return {
        "business_pain": business_pain,
        "industry": industry,
        "process": process,
        "ai_type": value(lead, "ai_type"),
        "effect": expected_effect,
        "priority": value(lead, "priority"),
    }


def detect_platform_source(source: str) -> str:
    if source == "aiha_consulting_audit_form":
        return "AIha Consulting"

    if source in {"landing", "landing_form", "callback_widget"}:
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


def get_task_for_lead(lead_id: int, task_id: int) -> sqlite3.Row | None:
    """
    Возвращает задачу по task_id и lead_id.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        return conn.execute(
            f"""
            {TASK_SELECT_SQL}
            WHERE id = ?
              AND lead_id = ?
            """,
            (task_id, lead_id),
        ).fetchone()


def get_latest_done_task_by_stage(lead_id: int, stage: str) -> sqlite3.Row | None:
    """
    Возвращает последнюю завершённую задачу по stage для лида.
    Используется, чтобы подставлять результат предыдущего агента
    в следующий input block.
    """
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

def _load_json_object(value: str | None) -> dict:
    if not value:
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}

    if isinstance(parsed, dict):
        return parsed

    return {}


def _count_non_empty(value) -> int:
    if value is None:
        return 0

    if isinstance(value, str):
        return 1 if value.strip() else 0

    if isinstance(value, (int, float, bool)):
        return 1

    if isinstance(value, list):
        return sum(_count_non_empty(item) for item in value)

    if isinstance(value, dict):
        return sum(_count_non_empty(item) for item in value.values())

    return 0


def _get_task_diagnostic_run_id(task: sqlite3.Row) -> int | None:
    try:
        diagnostic_run_id = task["diagnostic_run_id"]
    except (KeyError, IndexError):
        return None

    if diagnostic_run_id is None:
        return None

    try:
        return int(diagnostic_run_id)
    except (TypeError, ValueError):
        return None


def _get_latest_diagnostic_run_for_lead(lead_id: int) -> sqlite3.Row | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        return conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE lead_id = ?
            ORDER BY
                COALESCE(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()


def _get_active_diagnostic_input_pack_for_t_workflow(
    *,
    lead: sqlite3.Row,
    task: sqlite3.Row,
) -> sqlite3.Row | None:
    diagnostic_run_id = _get_task_diagnostic_run_id(task)

    if diagnostic_run_id is None:
        latest_run = _get_latest_diagnostic_run_for_lead(int(lead["id"]))
        if latest_run is not None:
            diagnostic_run_id = int(latest_run["id"])

    if diagnostic_run_id is None:
        return None

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        return conn.execute(
            """
            SELECT *
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
              AND brief_type = 'diagnostic_input_pack'
              AND is_active = 1
              AND raw_payload IS NOT NULL
              AND TRIM(raw_payload) != ''
            ORDER BY
                COALESCE(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()


def _get_attachments_for_input_pack(input_pack_id: int) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """
            SELECT
                id,
                diagnostic_run_id,
                input_pack_id,
                file_type,
                original_filename,
                stored_filename,
                uploaded_at
            FROM diagnostic_attachments
            WHERE input_pack_id = ?
            ORDER BY uploaded_at DESC, id DESC
            """,
            (input_pack_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def _build_diagnostic_input_pack_summary(payload: dict) -> dict:
    return {
        "brief_type": payload.get("brief_type"),
        "brief_version": payload.get("brief_version"),
        "source": payload.get("source"),
        "submitted_at": payload.get("submitted_at"),
        "non_empty_fields_count": _count_non_empty(payload),
        "top_level_keys": list(payload.keys()),
    }


def _build_t001_input_from_diagnostic_input_pack(
    *,
    lead: sqlite3.Row,
    constraints: sqlite3.Row | None,
    task: sqlite3.Row,
    input_pack: sqlite3.Row,
) -> str:
    payload = _load_json_object(input_pack["raw_payload"])
    summary = _build_diagnostic_input_pack_summary(payload)
    attachments = _get_attachments_for_input_pack(int(input_pack["id"]))

    return f"""
Входные данные для Intake Completeness Agent:

1. Идентификация

Lead_ID: L-{int(lead["id"]):03d}
Task_ID: {value(task, "id")}
Task_Code: {value(task, "task_code")}
Diagnostic_Run_ID: {value(input_pack, "diagnostic_run_id")}
Input_Pack_ID: {value(input_pack, "id")}
Дата_заявки: {value(input_pack, "created_at")}
Компания: {value(lead, "company")}
Контактное_лицо: {value(lead, "name")}
Телефон: {value(lead, "phone")}
Источник: diagnostic_input_pack
Платформа_источник: AIha Consulting
Канал_заявки: Diagnostic Input Pack
Тип_заявки: AI-аудит / предварительная диагностика

2. Приоритет источников

Основной источник для T-001:
active Diagnostic Input Pack.

Источник_raw_payload: {payload.get("source") or value(input_pack, "source")}

Используй raw payload ниже как источник истины для оценки полноты анкеты.
Карточку лида и client_constraints используй только как fallback или дополнительный контекст.
Если данные из карточки лида противоречат Diagnostic Input Pack, приоритет имеет Diagnostic Input Pack.

3. Diagnostic Input Pack Summary

{json.dumps(summary, ensure_ascii=False, indent=2)}

4. Raw Diagnostic Input Pack

{json.dumps(payload, ensure_ascii=False, indent=2)}

5. Загруженные материалы

{json.dumps(attachments, ensure_ascii=False, indent=2)}

6. Ограничения клиента из client_constraints

Персональные данные:
{value(constraints, "has_personal_data")}

Типы ПДн:
{value(constraints, "personal_data_types")}

Можно обезличить:
{value(constraints, "can_anonymize")}

Облако допустимо:
{value(constraints, "cloud_allowed")}

Требования к локализации:
{value(constraints, "localization_requirements")}

Политики ИБ:
{value(constraints, "security_policies")}

NDA:
{value(constraints, "nda_status")}

Ограничения scope:
{value(constraints, "scope_limits")}

Риск ограничений:
{value(constraints, "restriction_risk")}

Комментарий по ограничениям:
{value(constraints, "restriction_comment")}

7. Задача Intake Completeness

Stage:
{value(task, "stage")}

Agent:
{value(task, "agent_type")}

Task:
{value(task, "task_title")}

Expected output:
{value(task, "expected_output")}

8. Что нужно получить

Проверь, можно ли запускать аналитическую цепочку.

Критерий:
- если все 7 базовых блоков заполнены или достаточно понятны — можно переходить к Risk Assessment;
- если хотя бы один критичный блок отсутствует — анализ запускать нельзя;
- если блок заполнен частично — укажи, можно ли двигаться дальше с оговоркой или нужно добрать данные.

7 базовых блоков:
1. Бизнес-боль.
2. Конкретный процесс.
3. Объём / частота / масштаб.
4. Данные / документы / примеры.
5. Текущие системы.
6. Владелец процесса / контакт.
7. Ожидаемый результат.

9. Требуемый результат

Сформируй результат для поля result задачи Intake Completeness:

1. Intake Completeness Summary.
2. Проверка 7 базовых блоков:
   - блок;
   - статус: заполнен / частично / не заполнен;
   - комментарий.
3. Проверка ограничений.
4. Проверка экономики.
5. Недостающие данные.
6. Вопросы клиенту.
7. Решение:
   - READY_FOR_ANALYSIS
   - NOT_READY_FOR_ANALYSIS
8. Рекомендованный следующий шаг.

Если анализ можно запускать, явно напиши:
"Анкета достаточна для запуска Risk Assessment."

Если анализ запускать нельзя, явно напиши:
"Анализ запускать нельзя до дозаполнения анкеты."

10. Жёсткое ограничение scope

Оцени только процесс, отрасль, ограничения и данные, которые есть в Diagnostic Input Pack.

Запрещено добавлять альтернативные процессы, отрасли или сценарии, которых нет в анкете клиента.
""".strip()

def _build_diagnostic_input_pack_context_for_t_workflow(
    *,
    lead: sqlite3.Row,
    task: sqlite3.Row,
) -> str:
    input_pack = _get_active_diagnostic_input_pack_for_t_workflow(
        lead=lead,
        task=task,
    )

    if input_pack is None:
        return ""

    payload = _load_json_object(input_pack["raw_payload"])
    summary = _build_diagnostic_input_pack_summary(payload)
    attachments = _get_attachments_for_input_pack(int(input_pack["id"]))

    return f"""
0. Active Diagnostic Input Pack Context

Diagnostic_Run_ID: {value(input_pack, "diagnostic_run_id")}
Input_Pack_ID: {value(input_pack, "id")}
Источник: diagnostic_input_pack
Источник_raw_payload: {payload.get("source") or value(input_pack, "source")}

Правило источников для T-цепочки:
Для T-001, T-002, T-003 и T-004 основным источником является active Diagnostic Input Pack.
Не используй Industrial AI Brief как основной источник для T-цепочки.
Industrial AI Brief относится к D-001–D-004 и не должен подменять базовый AI Audit Brief.
Если нижний блок после разделителя содержит старый lead-form context, legacy audit form source, industrial AI references или другой Input_Pack_ID, используй его только как исторический/вспомогательный контекст.
При конфликте всегда побеждает Active Diagnostic Input Pack Context выше.

Diagnostic Input Pack Summary:

{json.dumps(summary, ensure_ascii=False, indent=2)}

Raw Diagnostic Input Pack:

{json.dumps(payload, ensure_ascii=False, indent=2)}

Attachments linked to Diagnostic Input Pack:

{json.dumps(attachments, ensure_ascii=False, indent=2)}
""".strip()

def _sanitize_legacy_t_context(task_context: str) -> str:
    if not task_context:
        return ""

    replacements = {
        "Источник: aiha_consulting_audit_form": (
            "Источник_legacy_lead_form: legacy_consulting_audit_form "
            "(fallback context only; do not use as primary source)"
        ),
        "aiha_consulting_audit_form": "legacy_consulting_audit_form",
        "Канал_заявки: Форма AI-аудита": (
            "Канал_legacy_lead_form: Форма AI-аудита "
            "(fallback context only)"
        ),
    }

    sanitized = task_context

    for old, new in replacements.items():
        sanitized = sanitized.replace(old, new)

    return sanitized


def build_task_input_block(
    *,
    lead: sqlite3.Row,
    constraints: sqlite3.Row | None,
    task: sqlite3.Row,
) -> str:
    """
    Универсальный генератор входного блока по задаче workflow.

    Активная модель AIha Consulting:
    - Intake Completeness
    - Risk Assessment
    - Economics Assessment
    - Final Output
    - Client Delivery
    """
    stage = value(task, "stage")

    if stage == "Intake Completeness":
        input_pack = _get_active_diagnostic_input_pack_for_t_workflow(
            lead=lead,
            task=task,
        )

        if input_pack is not None:
            return _build_t001_input_from_diagnostic_input_pack(
                lead=lead,
                constraints=constraints,
                task=task,
                input_pack=input_pack,
            )

        return build_intake_completeness_input_block(
            lead=lead,
            constraints=constraints,
            task=task,
        )

    if stage == "Risk Assessment":
        diagnostic_context = _build_diagnostic_input_pack_context_for_t_workflow(
            lead=lead,
            task=task,
        )

        task_context = build_risk_assessment_input_block(
            lead=lead,
            constraints=constraints,
            task=task,
        )
        task_context = _sanitize_legacy_t_context(task_context)

        if diagnostic_context:
            return f"{diagnostic_context}\n\n---\n\n{task_context}"

        return task_context

    if stage == "Economics Assessment":
        diagnostic_context = _build_diagnostic_input_pack_context_for_t_workflow(
            lead=lead,
            task=task,
        )

        task_context = build_economics_assessment_input_block(
            lead=lead,
            constraints=constraints,
            task=task,
        )
        task_context = _sanitize_legacy_t_context(task_context)

        if diagnostic_context:
            return f"{diagnostic_context}\n\n---\n\n{task_context}"

        return task_context

    if stage == "Final Output":
        diagnostic_context = _build_diagnostic_input_pack_context_for_t_workflow(
            lead=lead,
            task=task,
        )

        task_context = build_final_output_input_block(
            lead=lead,
            constraints=constraints,
            task=task,
        )
        task_context = _sanitize_legacy_t_context(task_context)

        if diagnostic_context:
            return f"{diagnostic_context}\n\n---\n\n{task_context}"

        return task_context

    if stage == "Client Delivery":
        diagnostic_context = _build_diagnostic_input_pack_context_for_t_workflow(
            lead=lead,
            task=task,
        )

        task_context = build_client_delivery_input_block(
            lead=lead,
            constraints=constraints,
            task=task,
        )
        task_context = _sanitize_legacy_t_context(task_context)

        if diagnostic_context:
            return f"{diagnostic_context}\n\n---\n\n{task_context}"

        return task_context

    raise ValueError(f"Unknown active workflow stage: {stage}")


def build_intake_completeness_input_block(
    *,
    lead: sqlite3.Row,
    constraints: sqlite3.Row | None,
    task: sqlite3.Row,
) -> str:
    context = get_lead_business_context(lead)

    return f"""
Входные данные для Intake Completeness Agent:

1. Идентификация

Lead_ID: L-{int(lead["id"]):03d}
Task_ID: {value(task, "id")}
Task_Code: {value(task, "task_code")}
Дата_заявки: {value(lead, "created_at")}
Компания: {value(lead, "company")}
Контактное_лицо: {value(lead, "name")}
Телефон: {value(lead, "phone")}
Источник: {value(lead, "source")}
Платформа_источник: {detect_platform_source(value(lead, "source"))}
Канал_заявки: {detect_channel(value(lead, "source"))}
Тип_заявки: {detect_request_type(value(lead, "source"), value(lead, "message"))}

2. Единый бизнес-контекст заявки

Бизнес-боль:
{context["business_pain"]}

Конкретный процесс:
{context["process"]}

Отрасль:
{context["industry"]}

AI-сценарий / ожидание:
{context["ai_type"]}

Ожидаемый эффект:
{context["effect"]}

Приоритет:
{context["priority"]}

Важно:
Если структурированные поля лида противоречат исходной анкете клиента, приоритет имеет исходная анкета.
Не добавляй альтернативную отрасль или процесс, если они явно не указаны в анкете.

3. Проверка 7 базовых блоков

1. Бизнес-боль:
{context["business_pain"]}

2. Конкретный процесс:
{context["process"]}

3. Объём / частота / масштаб:
{value(constraints, "roi_metrics_details")}

4. Данные / документы / примеры:
Типы данных / ПДн: {value(constraints, "personal_data_types")}
Можно обезличить: {value(constraints, "can_anonymize")}

5. Текущие системы:
Проверь по экономическим метрикам и дополнительным уточнениям.
Если явно не указаны — отметь как недостающий блок.

Экономические метрики / дополнительные уточнения:
{value(constraints, "roi_metrics_details")}

6. Владелец процесса / контакт:
Контактное лицо: {value(lead, "name")}
Телефон: {value(lead, "phone")}

7. Ожидаемый результат:
Ожидаемый эффект: {context["effect"]}
Экономика / метрики:
{value(constraints, "roi_metrics_details")}

4. Ограничения клиента

Персональные данные:
{value(constraints, "has_personal_data")}

Типы ПДн:
{value(constraints, "personal_data_types")}

Можно обезличить:
{value(constraints, "can_anonymize")}

Облако допустимо:
{value(constraints, "cloud_allowed")}

Требования к локализации:
{value(constraints, "localization_requirements")}

Политики ИБ:
{value(constraints, "security_policies")}

NDA:
{value(constraints, "nda_required")}

Ограничения scope:
{value(constraints, "scope_limitations")}

Риск ограничений:
{value(constraints, "constraint_risk")}

Комментарий по ограничениям:
{value(constraints, "comment")}

5. Экономика и метрики

ROI-метрики:
{value(constraints, "roi_metrics_available")}

Экономика и метрики процесса:
{value(constraints, "roi_metrics_details")}

Бюджет:
{value(constraints, "budget_known")}

Готовность к MVP:
{value(constraints, "mvp_readiness")}

6. Задача Intake Completeness

Stage:
{value(task, "stage")}

Agent:
{value(task, "agent_type")}

Task:
{value(task, "task_title")}

Expected output:
{value(task, "expected_output")}

7. Что нужно получить

Проверь, можно ли запускать аналитическую цепочку.

Критерий:
- если все 7 базовых блоков заполнены или достаточно понятны — можно переходить к Risk Assessment;
- если хотя бы один критичный блок отсутствует — анализ запускать нельзя;
- если блок заполнен частично — укажи, можно ли двигаться дальше с оговоркой или нужно добрать данные.

8. Требуемый результат

Сформируй результат для поля result задачи Intake Completeness:

1. Intake Completeness Summary.
2. Проверка 7 базовых блоков:
   - блок;
   - статус: заполнен / частично / не заполнен;
   - комментарий.
3. Проверка ограничений.
4. Проверка экономики.
5. Недостающие данные.
6. Вопросы клиенту.
7. Решение:
   - READY_FOR_ANALYSIS
   - NOT_READY_FOR_ANALYSIS
8. Рекомендованный следующий шаг.

Если анализ можно запускать, явно напиши:
"Анкета достаточна для запуска Risk Assessment."

Если анализ запускать нельзя, явно напиши:
"Анализ запускать нельзя до дозаполнения анкеты."

9. Жёсткое ограничение scope

Оцени только процесс и отрасль из единого бизнес-контекста:

Отрасль:
{context["industry"]}

Процесс:
{context["process"]}

Запрещено добавлять альтернативные процессы, отрасли или сценарии, которых нет в анкете клиента.
""".strip()


def build_risk_assessment_input_block(
    *,
    lead: sqlite3.Row,
    constraints: sqlite3.Row | None,
    task: sqlite3.Row,
) -> str:
    context = get_lead_business_context(lead)

    intake_task = get_latest_done_task_by_stage(
        lead_id=int(lead["id"]),
        stage="Intake Completeness",
    )

    return f"""
Входные данные для Risk Assessment Agent:

1. Клиент

Lead_ID: L-{int(lead["id"]):03d}
Task_ID: {value(task, "id")}
Task_Code: {value(task, "task_code")}
Компания: {value(lead, "company")}
Контактное_лицо: {value(lead, "name")}
Телефон: {value(lead, "phone")}
Дата_заявки: {value(lead, "created_at")}
Источник: {value(lead, "source")}

2. Единый бизнес-контекст заявки

Бизнес-боль:
{context["business_pain"]}

Процесс:
{context["process"]}

Отрасль:
{context["industry"]}

AI-сценарий / ожидание:
{context["ai_type"]}

Ожидаемый эффект:
{context["effect"]}

Важно:
Если структурированные поля лида противоречат исходной анкете клиента, приоритет имеет исходная анкета.
Не добавляй альтернативную отрасль или процесс, если они явно не указаны в анкете.

3. Ограничения клиента

Персональные данные:
{value(constraints, "has_personal_data")}

Типы ПДн:
{value(constraints, "personal_data_types")}

Можно обезличить:
{value(constraints, "can_anonymize")}

Облако допустимо:
{value(constraints, "cloud_allowed")}

Требования к локализации:
{value(constraints, "localization_requirements")}

Политики ИБ:
{value(constraints, "security_policies")}

NDA:
{value(constraints, "nda_required")}

Ограничения scope:
{value(constraints, "scope_limitations")}

Риск ограничений:
{value(constraints, "constraint_risk")}

Комментарий по ограничениям:
{value(constraints, "comment")}

4. Экономика и данные

ROI-метрики:
{value(constraints, "roi_metrics_available")}

Экономические метрики:
{value(constraints, "roi_metrics_details")}

Бюджет:
{value(constraints, "budget_known")}

Готовность к MVP:
{value(constraints, "mvp_readiness")}

5. Результат Intake Completeness

{value(intake_task, "result")}

6. Задача Risk Assessment

Stage:
{value(task, "stage")}

Agent:
{value(task, "agent_type")}

Task:
{value(task, "task_title")}

Expected output:
{value(task, "expected_output")}

7. Что нужно получить

Сформируй развёрнутую карту рисков:
- данные;
- ПДн;
- облако;
- NDA;
- ИБ;
- интеграции;
- экономика;
- MVP;
- организационные риски.

Отдельно укажи:
- что блокирует диагностику;
- что не блокирует диагностику, но требует мер;
- что блокирует MVP;
- что блокирует точный ROI;
- mitigation plan.

8. Жёсткое ограничение scope

Оцени риски только по процессу и отрасли из единого бизнес-контекста:

Процесс:
{context["process"]}

Отрасль:
{context["industry"]}

Бизнес-боль:
{context["business_pain"]}

Не добавляй производственные, оборудовательные или складские процессы, если они явно не указаны во входных данных.

Если процесс связан с обработкой клиентских заявок, оценивай риски вокруг:
- обработки клиентских заявок;
- SLA;
- потерь заявок;
- Excel / Telegram / 1C;
- персональных данных клиента;
- адресов объектов;
- обезличивания;
- интеграций и выгрузок.
""".strip()


def build_economics_assessment_input_block(
    *,
    lead: sqlite3.Row,
    constraints: sqlite3.Row | None,
    task: sqlite3.Row,
) -> str:
    context = get_lead_business_context(lead)

    intake_task = get_latest_done_task_by_stage(
        lead_id=int(lead["id"]),
        stage="Intake Completeness",
    )

    risk_task = get_latest_done_task_by_stage(
        lead_id=int(lead["id"]),
        stage="Risk Assessment",
    )

    return f"""
Входные данные для Economics Assessment Agent:

1. Клиент

Lead_ID: L-{int(lead["id"]):03d}
Task_ID: {value(task, "id")}
Task_Code: {value(task, "task_code")}
Компания: {value(lead, "company")}
Контактное_лицо: {value(lead, "name")}
Телефон: {value(lead, "phone")}
Дата_заявки: {value(lead, "created_at")}
Источник: {value(lead, "source")}

2. Единый бизнес-контекст заявки

Бизнес-боль:
{context["business_pain"]}

Процесс:
{context["process"]}

Отрасль:
{context["industry"]}

AI-сценарий / ожидание:
{context["ai_type"]}

Ожидаемый эффект:
{context["effect"]}

Важно:
Если структурированные поля лида противоречат исходной анкете клиента, приоритет имеет исходная анкета.
Не добавляй альтернативную отрасль или процесс, если они явно не указаны в анкете.

3. Экономические исходные данные

ROI-метрики:
{value(constraints, "roi_metrics_available")}

Экономика и метрики процесса:
{value(constraints, "roi_metrics_details")}

Бюджет:
{value(constraints, "budget_known")}

Готовность к MVP:
{value(constraints, "mvp_readiness")}

4. Ограничения клиента

Персональные данные:
{value(constraints, "has_personal_data")}

Типы ПДн:
{value(constraints, "personal_data_types")}

Можно обезличить:
{value(constraints, "can_anonymize")}

Облако допустимо:
{value(constraints, "cloud_allowed")}

NDA:
{value(constraints, "nda_required")}

Политики ИБ:
{value(constraints, "security_policies")}

Ограничения scope:
{value(constraints, "scope_limitations")}

Риск ограничений:
{value(constraints, "constraint_risk")}

5. Результат Intake Completeness

{value(intake_task, "result")}

6. Результат Risk Assessment

{value(risk_task, "result")}

7. Задача Economics Assessment

Stage:
{value(task, "stage")}

Agent:
{value(task, "agent_type")}

Task:
{value(task, "task_title")}

Expected output:
{value(task, "expected_output")}

8. Что нужно получить

Сформируй управленческую экономическую оценку.

Главный фокус:
- было → станет;
- часы;
- деньги;
- сокращение потерь;
- сокращение просрочек;
- ограничения расчёта;
- что уточнить для точного ROI.

Важно:
Это предварительная оценка, а не бухгалтерский отчёт.
Можно использовать реалистичные диапазоны и явно маркировать их как гипотезы.

Если есть данные по объёму, времени обработки, стоимости часа, контролю, потерям и просрочкам — обязательно считай.
Не ограничивайся процентной оценкой.

9. Жёсткое ограничение scope

Экономику считай только по процессу из единого бизнес-контекста:

Процесс:
{context["process"]}

Отрасль:
{context["industry"]}

Бизнес-боль:
{context["business_pain"]}

Не добавляй альтернативные процессы, отрасли или сценарии, которых нет в анкете клиента.
Если в результатах предыдущих этапов есть противоречие, приоритет имеет единый бизнес-контекст заявки.
""".strip()


def build_final_output_input_block(
    *,
    lead: sqlite3.Row,
    constraints: sqlite3.Row | None,
    task: sqlite3.Row,
) -> str:
    context = get_lead_business_context(lead)

    intake_task = get_latest_done_task_by_stage(
        lead_id=int(lead["id"]),
        stage="Intake Completeness",
    )

    risk_task = get_latest_done_task_by_stage(
        lead_id=int(lead["id"]),
        stage="Risk Assessment",
    )

    economics_task = get_latest_done_task_by_stage(
        lead_id=int(lead["id"]),
        stage="Economics Assessment",
    )

    return f"""
Входные данные для Final Output Agent:

1. Клиент

Lead_ID: L-{int(lead["id"]):03d}
Task_ID: {value(task, "id")}
Task_Code: {value(task, "task_code")}
Компания: {value(lead, "company")}
Контактное_лицо: {value(lead, "name")}
Телефон: {value(lead, "phone")}
Дата_заявки: {value(lead, "created_at")}
Источник: {value(lead, "source")}

2. Единый бизнес-контекст заявки

Бизнес-боль:
{context["business_pain"]}

Процесс:
{context["process"]}

Отрасль:
{context["industry"]}

AI-сценарий / ожидание:
{context["ai_type"]}

Ожидаемый эффект:
{context["effect"]}

Важно:
Если структурированные поля лида противоречат исходной анкете клиента, приоритет имеет исходная анкета.
Не добавляй альтернативную отрасль или процесс, если они явно не указаны в анкете.

3. Ограничения клиента

Персональные данные:
{value(constraints, "has_personal_data")}

Типы ПДн:
{value(constraints, "personal_data_types")}

Можно обезличить:
{value(constraints, "can_anonymize")}

Облако допустимо:
{value(constraints, "cloud_allowed")}

NDA:
{value(constraints, "nda_required")}

Политики ИБ:
{value(constraints, "security_policies")}

Ограничения scope:
{value(constraints, "scope_limitations")}

Риск ограничений:
{value(constraints, "constraint_risk")}

4. Экономические исходные данные

ROI-метрики:
{value(constraints, "roi_metrics_available")}

Экономика и метрики процесса:
{value(constraints, "roi_metrics_details")}

Бюджет:
{value(constraints, "budget_known")}

Готовность к MVP:
{value(constraints, "mvp_readiness")}

5. Результат Intake Completeness

{value(intake_task, "result")}

6. Результат Risk Assessment

{value(risk_task, "result")}

7. Результат Economics Assessment

{value(economics_task, "result")}

8. Обязательный экономический фокус для итогового отчёта

В итоговом отчёте обязательно показать состав потенциального эффекта:

- экономия трудозатрат;
- снижение потерь заявок;
- итоговый потенциальный эффект в месяц;
- итоговый потенциальный эффект в год.

Если во входных данных есть стоимость одной потерянной заявки, использовать её для расчёта потерь.

Расчёт должен быть основан на фактических данных анкеты и Economics Assessment.
Не копируй примерные цифры из инструкции, если они не соответствуют данным лида.

Формула:
итоговый потенциальный эффект = экономия трудозатрат + снижение потерь заявок.

Не называй это гарантированным ROI.
Это предварительный управленческий потенциал.

9. Коммерческие условия AIha Consulting для следующего этапа

Тип:
Экспресс-диагностика данных и подготовка MVP scope.

Стоимость:
150 000 ₽.

Срок:
10 рабочих дней.

Формат:
Онлайн-интервью + анализ материалов.

Deliverables:
- уточнённая экономика;
- карта рисков;
- MVP scope;
- список данных и интеграций;
- решение go / no-go.

10. Задача Final Output

Stage:
{value(task, "stage")}

Agent:
{value(task, "agent_type")}

Task:
{value(task, "task_title")}

Expected output:
{value(task, "expected_output")}

11. Что нужно получить

Сформируй клиентский итоговый документ.

Главный фокус:
- деньги;
- часы;
- потери;
- риски;
- конкретный следующий шаг;
- КП на следующий этап, если оно уместно.

Не пересказывай анкету.
Не ограничивайся рекомендациями "рассмотреть".
Не отправляй клиента на уже выполненные этапы Risk Assessment или Economics Assessment.

12. Жёсткое ограничение scope

Финальный документ формируй только по процессу из единого бизнес-контекста:

Процесс:
{context["process"]}

Отрасль:
{context["industry"]}

Бизнес-боль:
{context["business_pain"]}

Если результаты предыдущих этапов противоречат этому контексту, приоритет имеет единый бизнес-контекст заявки.
Не добавляй альтернативные процессы, отрасли или сценарии, которых нет в анкете клиента.
""".strip()


def build_client_delivery_input_block(
    *,
    lead: sqlite3.Row,
    constraints: sqlite3.Row | None,
    task: sqlite3.Row,
) -> str:
    final_output_task = get_latest_done_task_by_stage(
        lead_id=int(lead["id"]),
        stage="Final Output",
    )

    lead_id = int(lead["id"])

    return f"""
Delivery-блок для менеджера:

1. Клиент

Lead_ID: L-{lead_id:03d}
Компания: {value(lead, "company")}
Контактное_лицо: {value(lead, "name")}
Телефон: {value(lead, "phone")}
Email: проверьте в карточке заявки / тексте заявки
Источник: {value(lead, "source")}

2. Что отправляем клиенту

Откройте очищенную клиентскую версию документа:
/admin/leads/{lead_id}/final

Полный рабочий результат Final Output сохранён в задаче:
{value(final_output_task, "result")}

3. Что нужно сделать менеджеру

1. Открыть /admin/leads/{lead_id}/final.
2. Скопировать или скачать итоговый отчёт / КП.
3. Проверить, что в тексте нет:
   - обещаний гарантированного ROI;
   - обещаний MVP без согласования scope;
   - автоматического перехода в AIha Studio;
   - обработки реальных ПДн без NDA / обезличивания / согласования.
4. Отправить клиенту итоговый отчёт / КП.
5. Зафиксировать:
   - дату отправки;
   - канал отправки;
   - кому отправлено;
   - реакцию клиента;
   - следующий коммерческий шаг.

4. Рекомендуемый шаблон результата для поля result

Отчёт / КП отправлены клиенту.
Дата отправки: [указать дату].
Канал отправки: [email / Telegram / встреча / другое].
Получатель: {value(lead, "name")}.
Реакция клиента: [указать].
Следующий шаг: [указать].
Комментарий менеджера: [указать].

5. Задача

Stage:
{value(task, "stage")}

Task:
{value(task, "task_title")}

Expected output:
{value(task, "expected_output")}
""".strip()