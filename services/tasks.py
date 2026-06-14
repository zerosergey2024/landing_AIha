from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from db import DB_PATH


WORKFLOW_NEXT_STAGE = {
    "Intake Completeness": "Risk Assessment",
    "Risk Assessment": "Economics Assessment",
    "Economics Assessment": "Final Output",
    "Final Output": "Client Delivery",
}


WORKFLOW_DONE_RESULTS = {
    "Intake Completeness": (
        "Проверка полноты анкеты завершена. Анкета достаточна для запуска Risk Assessment "
        "или содержит список данных, которые нужно добрать у клиента."
    ),
    "Risk Assessment": (
        "Risk Assessment завершён. Риски, ограничения, блокеры и mitigation actions зафиксированы."
    ),
    "Economics Assessment": (
        "Economics Assessment завершён. Экономический эффект, сценарии 'было → станет', "
        "часы, деньги, потери и ограничения ROI оценены."
    ),
    "Final Output": (
        "Final Output завершён. Итоговый клиентский отчёт / КП сформирован."
    ),
    "Client Delivery": (
        "Client Delivery завершён. Отчёт / КП переданы клиенту, следующий коммерческий шаг зафиксирован."
    ),
}


WORKFLOW_DONE_NEXT_ACTIONS = {
    "Intake Completeness": "Перейти к Risk Assessment.",
    "Risk Assessment": "Перейти к Economics Assessment.",
    "Economics Assessment": "Перейти к Final Output.",
    "Final Output": "Передать итоговый отчёт / КП клиенту.",
    "Client Delivery": "Workflow завершён.",
}


WORKFLOW_TASK_TEMPLATES = {
    "Intake Completeness": {
        "agent_type": "Intake Completeness Agent",
        "task_title": "Проверить полноту анкеты первичного AI-аудита перед запуском анализа",
        "input_source": (
            "анкета первичного AI-аудита; карточка лида; client_constraints; "
            "экономические метрики; данные, полученные менеджером устно или письменно"
        ),
        "expected_output": (
            "оценка полноты анкеты; список заполненных и незаполненных блоков; "
            "решение: можно запускать анализ / нельзя запускать анализ"
        ),
        "priority": "Высокий",
        "human_required": "Да",
        "next_action": (
            "проверить полноту анкеты; если 7 базовых блоков заполнены — запустить Risk Assessment; "
            "если нет — добрать данные у клиента"
        ),
        "comment": (
            "Анализ не запускается, пока не заполнены 7 базовых блоков: бизнес-боль, процесс, "
            "масштаб, данные, системы, владелец процесса, ожидаемый результат."
        ),
    },
    "Risk Assessment": {
        "agent_type": "Risk Assessment Agent",
        "task_title": "Оценить риски, ограничения и блокеры по данным первичного AI-аудита",
        "input_source": (
            "анкета первичного AI-аудита; client_constraints; описание процесса; данные и системы клиента"
        ),
        "expected_output": (
            "risk report: ПДн, облако, ИБ, NDA, данные, интеграции, организационные риски, "
            "mitigation actions, блокеры диагностики/MVP"
        ),
        "priority": "Высокий",
        "human_required": "Да",
        "next_action": "сформировать Risk Assessment и перейти к оценке экономики",
        "comment": (
            "Риски оцениваются до экономики, чтобы не рекомендовать сценарии, "
            "заблокированные ограничениями."
        ),
    },
    "Economics Assessment": {
        "agent_type": "Economics Assessment Agent",
        "task_title": "Оценить экономику, операционный эффект и ROI-гипотезы",
        "input_source": (
            "анкета первичного AI-аудита; экономические метрики; результат Risk Assessment; "
            "описание процесса и текущих систем"
        ),
        "expected_output": (
            "economics report: было → станет; часы; деньги; потери; просрочки; "
            "сценарии эффекта; ограничения расчёта ROI"
        ),
        "priority": "Высокий",
        "human_required": "Да",
        "next_action": "сформировать Economics Assessment и перейти к итоговому отчёту / КП",
        "comment": (
            "Экономика является ключевым блоком аудита. Нужно показать клиенту "
            "выигрыш в часах, деньгах, потерях и просрочках. Полный ROI не обещается "
            "без стоимости решения."
        ),
    },
    "Final Output": {
        "agent_type": "Final Output Agent",
        "task_title": "Сформировать итоговый отчёт / КП по результатам риска и экономики",
        "input_source": (
            "анкета первичного AI-аудита; Risk Assessment; Economics Assessment; client_constraints"
        ),
        "expected_output": (
            "клиентский итоговый документ: отчёт, экономический эффект, риски, "
            "рекомендованный следующий шаг, КП или маршрут дальнейшей работы"
        ),
        "priority": "Высокий",
        "human_required": "Да",
        "next_action": "передать клиенту итоговый отчёт / КП",
        "comment": (
            "Финальный документ должен быть клиентским: было → станет → выигрыш → риски → "
            "следующий шаг / КП. Не техническое перечисление результатов задач."
        ),
    },
    "Client Delivery": {
        "agent_type": "Human Consultant",
        "task_title": "Передать клиенту итоговый отчёт / КП и зафиксировать реакцию",
        "input_source": "финальный отчёт / КП; решение консультанта",
        "expected_output": (
            "отчёт или КП отправлены клиенту; зафиксирована дата отправки; "
            "определён следующий коммерческий шаг"
        ),
        "priority": "Высокий",
        "human_required": "Да",
        "next_action": "отправить клиенту отчёт / КП и зафиксировать следующий шаг",
        "comment": "Финальный delivery-этап workflow. Выполняется вручную менеджером.",
    },
}


def get_next_task_code(lead_id: int) -> str:
    """
    Возвращает следующий код задачи для лида: T-001, T-002, T-003...
    Считает задачи по конкретному lead_id.
    """
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM agent_tasks
            WHERE lead_id = ?
            """,
            (lead_id,),
        ).fetchone()

    task_number = int(row[0]) + 1
    return f"T-{task_number:03d}"


def create_agent_task(
    *,
    lead_id: int,
    company: str,
    agent_type: str,
    stage: str,
    task_title: str,
    input_source: str,
    expected_output: str,
    status: str = "New",
    priority: str = "Средний",
    owner: str = "Сергей",
    human_required: str = "Нет",
    result: str = "не указано",
    next_action: str = "не указано",
    due_date: str = "не указано",
    comment: str = "не указано",
) -> int:
    """
    Создаёт задачу агента / консультанта в agent_tasks.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    task_code = get_next_task_code(lead_id)

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO agent_tasks (
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
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
                now,
                now,
            ),
        )

        conn.commit()
        return int(cursor.lastrowid)


def create_task_for_stage(lead_id: int, company: str, stage: str) -> int:
    """
    Создаёт задачу по шаблону workflow stage.
    """
    template = WORKFLOW_TASK_TEMPLATES.get(stage)

    if template is None:
        raise ValueError(f"Unknown workflow stage: {stage}")

    return create_agent_task(
        lead_id=lead_id,
        company=company or "не указано",
        agent_type=template["agent_type"],
        stage=stage,
        task_title=template["task_title"],
        input_source=template["input_source"],
        expected_output=template["expected_output"],
        status="New",
        priority=template["priority"],
        owner="Сергей",
        human_required=template["human_required"],
        result="не указано",
        next_action=template["next_action"],
        due_date="не указано",
        comment=template["comment"],
    )


def create_initial_consulting_task(lead_id: int, company: str) -> int:
    """
    Создаёт стартовую задачу T-001 для новой заявки AIha Consulting.
    """
    return create_task_for_stage(
        lead_id=lead_id,
        company=company,
        stage="Intake Completeness",
    )


def update_agent_task(
    *,
    task_id: int,
    status: str,
    result: str,
    next_action: str,
    comment: str,
) -> None:
    """
    Обновляет статус и рабочие поля задачи агента / консультанта.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE agent_tasks
            SET
                status = ?,
                result = ?,
                next_action = ?,
                comment = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                result,
                next_action,
                comment,
                now,
                task_id,
            ),
        )
        conn.commit()


def get_default_done_result(stage: str) -> str:
    """
    Возвращает fallback-result для ручного закрытия задачи.
    Для аналитических этапов лучше вставлять реальный result или запускать AI Agent.
    """
    return WORKFLOW_DONE_RESULTS.get(
        stage,
        f"Этап {stage} завершён.",
    )


def get_default_done_next_action(stage: str) -> str:
    """
    Возвращает fallback-next_action для ручного закрытия задачи.
    """
    return WORKFLOW_DONE_NEXT_ACTIONS.get(
        stage,
        "Перейти к следующему этапу workflow.",
    )


def get_task_by_id(task_id: int) -> sqlite3.Row | None:
    """
    Возвращает задачу по id.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        task = conn.execute(
            """
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
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

    return task


def task_exists_for_stage(lead_id: int, stage: str) -> bool:
    """
    Проверяет, есть ли уже задача по stage для лида.
    """
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM agent_tasks
            WHERE lead_id = ?
              AND stage = ?
            """,
            (lead_id, stage),
        ).fetchone()

    return int(row[0]) > 0


def create_next_task_after_update(task_id: int) -> int | None:
    """
    Создаёт следующую задачу workflow после обновления текущей задачи.

    Правило:
    - следующая задача создаётся только если текущая задача имеет status = Done;
    - используется только компактный AIha Consulting workflow;
    - если следующий stage не определён, ничего не создаётся;
    - если задача следующего stage уже существует, дубль не создаётся.
    """
    task = get_task_by_id(task_id)

    if task is None:
        return None

    lead_id = int(task["lead_id"])
    stage = task["stage"]
    status = task["status"]
    company = task["company"] or "не указано"

    if status != "Done":
        return None

    next_stage = WORKFLOW_NEXT_STAGE.get(stage)

    if next_stage is None:
        return None

    if task_exists_for_stage(lead_id, next_stage):
        return None

    return create_task_for_stage(
        lead_id=lead_id,
        company=company,
        stage=next_stage,
    )