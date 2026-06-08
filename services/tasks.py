from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from db import DB_PATH


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


def create_initial_consulting_task(lead_id: int, company: str) -> int:
    """
    Создаёт стартовую задачу T-001 для новой заявки AIha Consulting.
    """
    return create_agent_task(
        lead_id=lead_id,
        company=company or "не указано",
        agent_type="Intake Agent",
        stage="Intake",
        task_title="Обработать первичную заявку AIha Consulting через Intake Agent v3.3",
        input_source=(
            "leads; client_constraints; форма /consulting/audit; "
            "input block /admin/leads/<lead_id>/intake-input"
        ),
        expected_output=(
            "строки для Leads A–Z, Intake_Control A–L, "
            "Client_Constraints A–R; решение о готовности к Qualification"
        ),
        status="New",
        priority="Высокий",
        owner="Сергей",
        human_required="Да",
        result="не указано",
        next_action="сформировать input block для Intake Agent v3.3 и запустить первичный Intake",
        due_date="не указано",
        comment=(
            "Задача создана автоматически после поступления заявки AIha Consulting. "
            "Перед запуском агента проверить полноту формы и ограничения клиента."
        ),
    )