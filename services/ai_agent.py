from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import OpenAI

from db import DB_PATH
from services.intake_blocks import (
    build_task_input_block,
    get_lead_with_constraints,
    get_task_for_lead,
)
from services.tasks import create_next_task_after_update


load_dotenv()

client = OpenAI()


SYSTEM_PROMPT = """
Ты AIha Consulting workflow agent.

Твоя задача — обработать входной блок конкретного этапа агентного workflow
и вернуть готовый результат для сохранения в поле result задачи.

Правила:
1. Пиши на русском языке.
2. Не пересказывай входной блок полностью.
3. Не добавляй выдуманные факты.
4. Если данных недостаточно, явно укажи, какие данные отсутствуют.
5. Разделяй факты, гипотезы и допущения.
6. Не обещай гарантированный ROI.
7. Не обещай MVP или внедрение без подтверждения данных, рисков, интеграций и scope.
8. Если есть персональные данные, учитывай обезличивание, NDA и ограничения хранения.
9. Если задача просит сформировать строку для таблицы, сформируй её в конце отдельным блоком.
10. Результат должен быть пригоден для вставки в поле result текущей задачи.
""".strip()


def get_task_by_id(task_id: int) -> sqlite3.Row | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        return conn.execute(
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


def update_task_after_ai_run(
    *,
    task_id: int,
    result: str,
    next_action: str,
    comment: str,
    status: str = "Done",
) -> None:
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


def build_ai_next_action(stage: str) -> str:
    return f"AI Agent завершил этап {stage}. Перейти к следующему этапу workflow."


def run_openai_agent(input_block: str, stage: str) -> str:
    model = os.getenv("OPENAI_MODEL", "gpt-5.5")

    user_prompt = f"""
Этап workflow: {stage}

Обработай входной блок ниже и сформируй результат текущего этапа.

ВХОДНОЙ БЛОК:
{input_block}
""".strip()

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
    )

    output_text = getattr(response, "output_text", "") or ""

    if not output_text.strip():
        raise RuntimeError("OpenAI API вернул пустой ответ.")

    return output_text.strip()


def run_ai_agent_for_task(task_id: int) -> dict[str, object]:
    task = get_task_by_id(task_id)

    if task is None:
        raise ValueError("Задача не найдена.")

    lead_id = int(task["lead_id"])
    stage = task["stage"] or "не указано"

    lead, constraints = get_lead_with_constraints(lead_id)
    task_for_lead = get_task_for_lead(lead_id, task_id)

    if lead is None:
        raise ValueError("Заявка не найдена.")

    if task_for_lead is None:
        raise ValueError("Задача не найдена для указанной заявки.")

    input_block = build_task_input_block(
        lead=lead,
        constraints=constraints,
        task=task_for_lead,
    )

    result = run_openai_agent(
        input_block=input_block,
        stage=stage,
    )

    update_task_after_ai_run(
        task_id=task_id,
        result=result,
        next_action=build_ai_next_action(stage),
        comment="Задача обработана AI Agent через OpenAI API.",
        status="Done",
    )

    next_task_id = create_next_task_after_update(task_id)

    return {
        "ok": True,
        "task_id": task_id,
        "lead_id": lead_id,
        "stage": stage,
        "next_task_id": next_task_id,
    }