from __future__ import annotations

import json
import re
from typing import Any

from db import get_db_connection
from services.ai_agent import run_agent_with_prompt
from services.diagnostics import (
    DIAGNOSTIC_STATUS_D003_RUNNING,
    save_d003_result,
    update_diagnostic_status,
)


D003_AGENT_PROMPT_NAME = "diagnostic_report"


def _load_json_object(value: str | None, label: str) -> dict[str, Any]:
    if not value:
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {label}: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must contain a JSON object")

    return parsed


def _count_non_empty(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_count_non_empty(item) for item in value.values())

    if isinstance(value, list):
        return sum(1 for item in value if str(item).strip())

    return 1 if value is not None and str(value).strip() else 0


def _sanitize_diagnostic_run(row: Any) -> dict[str, Any]:
    diagnostic_run = dict(row)

    keys_to_remove = {
        "input_pack_token",
        "d001_result",
        "d002_result",
        "d002_summary",
        "d003_result",
        "d003_summary",
        "d004_result",
        "d004_summary",
        "final_result",
        "final_output",
        "diagnostic_report",
        "commercial_proposal",
    }

    for key in keys_to_remove:
        diagnostic_run.pop(key, None)

    return diagnostic_run


def _sanitize_input_pack(input_pack: dict[str, Any]) -> dict[str, Any]:
    raw_payload = _load_json_object(input_pack.get("raw_payload"), "raw_payload")
    normalized_payload = _load_json_object(
        input_pack.get("normalized_payload"),
        "normalized_payload",
    )

    brief_type = raw_payload.get("brief_type") or normalized_payload.get("brief_type")

    industrial_ai = raw_payload.get("industrial_ai", {})
    if not industrial_ai:
        industrial_ai = normalized_payload.get("industrial_ai", {})

    return {
        "id": input_pack.get("id"),
        "diagnostic_run_id": input_pack.get("diagnostic_run_id"),
        "status": input_pack.get("status"),
        "created_at": input_pack.get("created_at"),
        "updated_at": input_pack.get("updated_at"),
        "brief_type": brief_type,
        "brief_version": raw_payload.get("brief_version")
        or normalized_payload.get("brief_version"),
        "source": raw_payload.get("source") or normalized_payload.get("source"),
        "submitted_at": raw_payload.get("submitted_at")
        or normalized_payload.get("submitted_at"),
        "raw_payload_non_empty": _count_non_empty(raw_payload),
        "normalized_payload_non_empty": _count_non_empty(normalized_payload),
        "industrial_ai_non_empty": _count_non_empty(industrial_ai),
    }


def _get_diagnostic_run_row(diagnostic_run_id: int) -> Any:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"Diagnostic run not found: {diagnostic_run_id}")

    return row


def _get_latest_input_pack_row(diagnostic_run_id: int) -> Any:
    _get_diagnostic_run_row(diagnostic_run_id)

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
              AND (
                    raw_payload IS NOT NULL
                    AND TRIM(raw_payload) != ''
                  OR
                    normalized_payload IS NOT NULL
                    AND TRIM(normalized_payload) != ''
              )
            ORDER BY id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if row is None:
        raise ValueError(
            "Diagnostic input pack with payload not found "
            f"for diagnostic_run_id={diagnostic_run_id}"
        )

    return row


def _get_lead_and_constraints(
    lead_id: int | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if lead_id is None:
        return None, None

    with get_db_connection() as conn:
        lead = conn.execute(
            """
            SELECT *
            FROM leads
            WHERE id = ?
            """,
            (lead_id,),
        ).fetchone()

        constraints = conn.execute(
            """
            SELECT *
            FROM client_constraints
            WHERE lead_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()

    return (
        dict(lead) if lead else None,
        dict(constraints) if constraints else None,
    )


def _get_table_column_names(table_name: str) -> set[str]:
    with get_db_connection() as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()

    column_names: set[str] = set()

    for row in rows:
        try:
            column_names.add(row["name"])
        except (TypeError, KeyError):
            column_names.add(row[1])

    return column_names


def _get_attachments_for_input_pack(
    diagnostic_run_id: int,
    input_pack_id: int,
) -> list[dict[str, Any]]:
    column_names = _get_table_column_names("diagnostic_attachments")

    with get_db_connection() as conn:
        if "input_pack_id" in column_names:
            rows = conn.execute(
                """
                SELECT *
                FROM diagnostic_attachments
                WHERE input_pack_id = ?
                ORDER BY id ASC
                """,
                (input_pack_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM diagnostic_attachments
                WHERE diagnostic_run_id = ?
                ORDER BY id ASC
                """,
                (diagnostic_run_id,),
            ).fetchall()

    return [dict(row) for row in rows]


def _resolve_payloads(input_pack: dict[str, Any]) -> dict[str, Any]:
    raw_payload = _load_json_object(input_pack.get("raw_payload"), "raw_payload")
    normalized_payload = _load_json_object(
        input_pack.get("normalized_payload"),
        "normalized_payload",
    )

    brief_type = raw_payload.get("brief_type") or normalized_payload.get("brief_type")

    if brief_type == "industrial_ai":
        if not raw_payload:
            raise ValueError(
                "raw_payload is required for Industrial AI before running D-003"
            )

        payload_for_d003 = raw_payload
        payload_source = "raw_payload"

    elif normalized_payload:
        payload_for_d003 = normalized_payload
        payload_source = "normalized_payload"

    elif raw_payload:
        payload_for_d003 = raw_payload
        payload_source = "raw_payload"

    else:
        raise ValueError("raw_payload or normalized_payload is required before D-003")

    return {
        "raw_payload": raw_payload,
        "normalized_payload": normalized_payload,
        "payload_for_d003": payload_for_d003,
        "payload_source": payload_source,
        "brief_type": brief_type,
    }


def validate_d003_input(diagnostic_run_id: int) -> None:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)

    if not diagnostic_run["d001_result"]:
        raise ValueError("D-001 result is required before running D-003")

    if not diagnostic_run["d002_result"] and not diagnostic_run["d002_summary"]:
        raise ValueError("D-002 result or D-002 summary is required before running D-003")

    input_pack = dict(_get_latest_input_pack_row(diagnostic_run_id))
    _resolve_payloads(input_pack)


def get_existing_d003_result(diagnostic_run_id: int) -> str | None:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)
    return diagnostic_run["d003_result"] or None


def get_d003_input(diagnostic_run_id: int) -> dict[str, Any]:
    validate_d003_input(diagnostic_run_id)

    diagnostic_run = dict(_get_diagnostic_run_row(diagnostic_run_id))
    input_pack = dict(_get_latest_input_pack_row(diagnostic_run_id))

    payloads = _resolve_payloads(input_pack)

    lead, constraints = _get_lead_and_constraints(diagnostic_run.get("lead_id"))

    attachments = _get_attachments_for_input_pack(
        diagnostic_run_id=diagnostic_run_id,
        input_pack_id=int(input_pack["id"]),
    )

    return {
        "diagnostic_run": diagnostic_run,
        "lead": lead,
        "constraints": constraints,
        "input_pack": input_pack,
        "safe_input_pack": _sanitize_input_pack(input_pack),
        "raw_payload": payloads["raw_payload"],
        "normalized_payload": payloads["normalized_payload"],
        "payload_for_d003": payloads["payload_for_d003"],
        "payload_source": payloads["payload_source"],
        "brief_type": payloads["brief_type"],
        "attachments": attachments,
        "d001_result": diagnostic_run["d001_result"],
        "d002_summary": diagnostic_run["d002_summary"] or "",
        "d002_result": diagnostic_run["d002_result"] or "",
    }


def build_d003_prompt_input(diagnostic_run_id: int) -> str:
    data = get_d003_input(diagnostic_run_id)

    safe_diagnostic_run = _sanitize_diagnostic_run(data["diagnostic_run"])

    return f"""
# INPUT FOR D-003 DIAGNOSTIC REPORT AGENT

## Lead

{json.dumps(data["lead"], ensure_ascii=False, indent=2)}

## Client Constraints

{json.dumps(data["constraints"], ensure_ascii=False, indent=2)}

## Diagnostic Run

{json.dumps(safe_diagnostic_run, ensure_ascii=False, indent=2)}

## Selected Input Pack

{json.dumps(data["safe_input_pack"], ensure_ascii=False, indent=2)}

## Payload Selected For D-003

Use this payload as the main structured input for D-003.

Payload source: {data["payload_source"]}

{json.dumps(data["payload_for_d003"], ensure_ascii=False, indent=2)}

## Raw Diagnostic Input Pack / Industrial AI Brief

For Industrial AI, raw payload is the source of truth.

{json.dumps(data["raw_payload"], ensure_ascii=False, indent=2)}

## Normalized Diagnostic Input Pack

Use this only as a helper.
If it conflicts with raw payload for Industrial AI, raw payload wins.

{json.dumps(data["normalized_payload"], ensure_ascii=False, indent=2)}

## D-001 Diagnostic Assessment Result

{data["d001_result"]}

## D-002 Summary

Use this as the primary D-002 input for D-003.

{data["d002_summary"]}

## D-002 Full Result

Use this only as supporting context if the summary is incomplete.

{data["d002_result"]}

## Attachments Summary

{json.dumps(data["attachments"], ensure_ascii=False, indent=2)}
""".strip()


def build_d003_summary(result: str) -> str:
    """
    Формирует компактную выжимку для карточки диагностики и D-004.
    Summary ориентировано на новую русскую структуру D-003.
    """
    lines = [line.rstrip() for line in result.splitlines()]

    important_headers = {
        "# Итоговый диагностический отчёт AIha Consulting",
        "# Итоговый диагностический отчёт AIha Consulting — Industrial AI",
        "## 1. Управленческое резюме",
        "## 3. Экономическая оценка",
        "## 4. Рекомендуемый MVP",
        "## 5. Готовность данных и условий запуска",
        "## 6. Риски и ограничения",
        "## 7. План перехода к MVP",
        "## 8. Итоговое решение",
        "## 9. Рекомендуемый следующий шаг",
    }

    selected: list[str] = []
    capture = False

    for line in lines:
        stripped = line.strip()

        if stripped in important_headers:
            capture = True
            selected.append(line)
            continue

        if stripped.startswith("## ") and capture:
            capture = False

        if capture:
            selected.append(line)

    if not selected:
        return result[:7000]

    summary = "\n".join(selected).strip()

    if len(summary) > 9000:
        summary = summary[:9000].rstrip() + "\n\n[Summary truncated]"

    return summary

def postprocess_d003_result(result: str, prompt_input: str) -> str:
    """
    Исправляет устойчивые формальные ошибки D-003,
    которые надёжнее закрывать кодом, а не расширением prompt.
    """
    is_downtime = '"primary_case_type": "downtime_analysis"' in prompt_input
    is_industrial = '"brief_type": "industrial_ai"' in prompt_input

    if not is_downtime and not is_industrial:
        return result

    fixed = result

    safe_effect_text = (
        "MVP должен проверить возможность снижения простоев "
        "и выявления повторяющихся причин отказов."
    )

    effect_promise_patterns = [
        r"Внедрение AI[^.\n]*позволит[^.\n]*(?:снизить|снижения|потери|потерь|эффективность|производительность)[^.\n]*\.",
        r"Внедрение AI[^.\n]*имеет потенциал[^.\n]*(?:снижения|снизить|потери|потерь|эффективность|производительность)[^.\n]*\.",
        r"Внедрение AI[^.\n]*должно помочь[^.\n]*(?:снизить|снижения|потери|потерь|эффективность|производительность)[^.\n]*\.",
        r"Проект имеет потенциал[^.\n]*(?:снижения|снизить|потери|потерь|эффективность|производительность)[^.\n]*\.",
        r"Внедрение AI[^.\n]*может[^.\n]*(?:снизить|снижения|снизить потери|потери|потерь|улучшить|повысить|эффективность|производительность|планирование)[^.\n]*\.",
        r"AI[^.\n]*может[^.\n]*(?:снизить|снижения|снизить потери|потери|потерь|улучшить|повысить|эффективность|производительность|планирование)[^.\n]*\.",
    ]

    for pattern in effect_promise_patterns:
        fixed = re.sub(pattern, safe_effect_text, fixed)

    # 1. Не обещать эффект.
    promise_patterns = [
        "Ожидается, что внедрение AI позволит снизить простои и выявить повторяющиеся причины отказов.",
        "Ожидается, что внедрение AI позволит снизить простои и выявить повторяющиеся причины отказов",
        "Внедрение AI на пилотной линии позволит снизить простои и выявить повторяющиеся причины отказов.",
        "Внедрение AI на пилотной линии позволит снизить простои и выявить повторяющиеся причины отказов",
        "Внедрение AI должно помочь снизить простои и выявить повторяющиеся причины отказов на пилотной линии.",
        "Внедрение AI должно помочь снизить простои и выявить повторяющиеся причины отказов на пилотной линии",
        "Внедрение AI должно помочь снизить простои и выявить повторяющиеся причины отказов.",
        "Внедрение AI должно помочь снизить простои и выявить повторяющиеся причины отказов",
        "Внедрение AI в анализ простоев на пилотной линии позволит снизить потери и повысить эффективность процессов.",
        "Внедрение AI в анализ простоев на пилотной линии позволит снизить потери и повысить эффективность процессов",
        "AI позволит снизить простои и выявить повторяющиеся причины отказов.",
        "AI позволит снизить простои и выявить повторяющиеся причины отказов",
    ]

    safe_effect_text = (
        "MVP должен проверить возможность снижения простоев "
        "и выявления повторяющихся причин отказов."
    )

    for pattern in promise_patterns:
        fixed = fixed.replace(pattern, safe_effect_text)

    replacements = {
        "| KPI успеха | Снижение простоев на 5–10%, сокращение ручной отчетности на 20–30% |": (
            "| KPI успеха | Проверить гипотезу снижения простоев на 5–10% и сокращения ручной отчетности на 20–30% |"
        ),
        "| Что не входит | Автономное управление оборудованием, полная интеграция со всеми системами |": (
            "| Что не входит | Автономное управление оборудованием, real-time контур, замена ERP / MES / SCADA, полная интеграция со всеми системами, масштабирование на все линии до подтверждения эффекта |"
        ),
        "| Тестовая выгрузка возможна | YES | Excel доступен |  |": (
            "| Тестовая выгрузка возможна | YES | Возможность Excel / CSV выгрузки подтверждена | Передать тестовый файл выгрузки |"
        ),
        "| Тестовая выгрузка возможна | PARTIAL | Excel доступен | Тестовая Excel / CSV выгрузка передана |": (
            "| Тестовая выгрузка возможна | YES | Возможность Excel / CSV выгрузки подтверждена | Передать тестовый файл выгрузки |"
        ),
        "| Кейс выбран | YES |  |  |": (
            "| Кейс выбран | YES | downtime_analysis / анализ простоев | — |"
        ),
        "| Объект пилота выбран | YES |  |  |": (
            "| Объект пилота выбран | YES | одна пилотная линия обработки / сборки | — |"
        ),
        "| Владелец процесса | PARTIAL |  | Назначить владельца процесса |": (
            "| Владелец процесса | PARTIAL | роль пользователя определена: руководитель производства | Назначить конкретного владельца процесса со стороны клиента |"
        ),
        "| Владелец процесса | PARTIAL | Руководитель производства | Назначить ответственного |": (
            "| Владелец процесса | PARTIAL | роль пользователя определена: руководитель производства | Назначить конкретного владельца процесса со стороны клиента |"
        ),
        "| Baseline / экономика | PARTIAL | Потенциальные потери | Подтвердить baseline и стоимость часа простоя |": (
            "| Baseline / экономика | PARTIAL | Есть предварительная оценка потерь | Подтвердить baseline, количество событий, среднюю длительность простоя, стоимость часа простоя и целевой эффект |"
        ),
        "| Baseline / экономика | PARTIAL | Потенциальные потери | Подтвердить количество событий, среднюю длительность, стоимость часа простоя |": (
            "| Baseline / экономика | PARTIAL | Есть предварительная оценка потерь | Подтвердить baseline, количество событий, среднюю длительность простоя, стоимость часа простоя и целевой эффект |"
        ),
        "| Baseline / экономика | PARTIAL | Потенциальные потери | Подтвердить количество событий, среднюю длительность простоя, стоимость часа простоя |": (
            "| Baseline / экономика | PARTIAL | Есть предварительная оценка потерь | Подтвердить baseline, количество событий, среднюю длительность простоя, стоимость часа простоя и целевой эффект |"
        ),
        "| ИБ / ПДн / коммерческая тайна | PARTIAL | Обезличивание требуется | Согласовать NDA и правила обработки |": (
            "| ИБ / ПДн / коммерческая тайна | PARTIAL | Обезличивание требуется | Согласовать NDA, правила передачи, хранения, удаления и локальной обработки данных |"
        ),
        "| ИБ / ПДн / коммерческая тайна | PARTIAL | Обезличивание требуется | Согласовать NDA и правила обработки данных |": (
            "| ИБ / ПДн / коммерческая тайна | PARTIAL | Обезличивание требуется | Согласовать NDA, правила передачи, хранения, удаления и локальной обработки данных |"
        ),
        "| ID события простоя подтверждён | требует выполнения | Необходимо подтвердить ID события |": (
            "| ID события простоя подтверждён | требует выполнения | Подтвердить наличие стабильного ID события простоя или правило его формирования |"
        ),
        "| ID оборудования / линии подтверждены | частично выполнено | ID оборудования и линии подтверждены, но требуется уточнение |": (
            "| ID оборудования / линии подтверждены | частично выполнено | Проверить на тестовой выгрузке связь события простоя с оборудованием и линией |"
        ),
        "| Начало и конец простоя или длительность доступны | частично выполнено | Начало и конец простоя подтверждены, но временные метки событий требуют уточнения |": (
            "| Начало и конец простоя или длительность доступны | частично выполнено | Начало простоя подтверждено, конец или длительность нужно проверить на тестовой выгрузке |"
        ),
        "| Причина простоя или комментарий доступны | требует выполнения | Необходимо подтвердить причины простоев |": (
            "| Причина простоя или комментарий доступны | частично выполнено | Проверить наличие причины простоя или текстового комментария в выгрузке |"
        ),
        "| Baseline и способ расчёта потерь согласованы | требует выполнения | Требуется согласовать baseline и стоимость часа простоя |": (
            "| Baseline и способ расчёта потерь согласованы | требует выполнения | Согласовать baseline, количество событий, среднюю длительность простоя, стоимость часа простоя и целевой эффект |"
        ),
        "| ИБ / NDA / локальная обработка согласованы | требует выполнения | NDA и правила обработки данных нужно согласовать |": (
            "| ИБ / NDA / локальная обработка согласованы | требует выполнения | Согласовать NDA, правила передачи, хранения, удаления и локальной обработки данных |"
        ),
        "| Владелец процесса назначен | частично выполнено | Необходимо назначить ответственного |": (
            "| Владелец процесса назначен | частично выполнено | Назначить конкретного владельца процесса со стороны клиента |"
        ),
        "| Что не входит | Автономное управление оборудованием, полная интеграция со всеми системами, масштабирование на все площадки |": (
            "| Что не входит | Автономное управление оборудованием, real-time контур, замена ERP / MES / SCADA, полная интеграция со всеми системами, масштабирование на все линии / площадки, production-grade эксплуатация |"
        ),
        "| ИБ / ПДн / коммерческая тайна | PARTIAL | Обезличивание требуется | Согласовать NDA, правила передачи, хранения и удаления данных |": (
            "| ИБ / ПДн / коммерческая тайна | PARTIAL | Обезличивание требуется | Согласовать NDA, правила передачи, хранения, удаления и локальной обработки данных |"
        ),
        "| Владелец процесса | PARTIAL | Руководитель производства | Уточнить ФИО владельца процесса |": (
            "| Владелец процесса | PARTIAL | роль пользователя определена: руководитель производства | Назначить конкретного владельца процесса со стороны клиента |"
        ),
    }

    for old, new in replacements.items():
        fixed = fixed.replace(old, new)

    lines = fixed.splitlines()
    fixed_lines: list[str] = []

    for line in lines:
        stripped = line.strip()

        if is_downtime and stripped.startswith("| Что нужно подтвердить |"):
            fixed_lines.append(
                "| Что нужно подтвердить | количество событий простоев, среднюю длительность простоя, стоимость часа простоя, baseline потерь, целевой эффект | HIGH | ID оборудования, ID линии, ID события и временные метки относятся к готовности данных |"
            )
            continue

        fixed_lines.append(line)

    fixed = "\n".join(fixed_lines)

    def parse_markdown_table_row(line: str) -> list[str] | None:
        """
        Возвращает ячейки markdown-строки таблицы.

        Не обрабатывает строки-разделители вида:
        | --- | --- |
        """
        stripped = line.strip()

        if not stripped.startswith("|") or not stripped.endswith("|"):
            return None

        cells = [
            cell.strip()
            for cell in stripped.strip("|").split("|")
        ]

        if not cells:
            return None

        if all(set(cell.replace(":", "").strip()) <= {"-"} for cell in cells):
            return None

        return cells


    def replace_table_row_by_first_cell(
        markdown: str,
        first_cell: str,
        new_row: str,
    ) -> str:
        """
        Заменяет markdown-строку таблицы по точному значению первой ячейки.

        Устойчивее fixed.replace(), потому что модель часто меняет
        формулировки во 2–4 колонках, пробелы и выравнивание таблицы.
        """
        lines = markdown.splitlines()
        result_lines: list[str] = []

        for line in lines:
            cells = parse_markdown_table_row(line)

            if cells and cells[0] == first_cell:
                result_lines.append(new_row)
                continue

            result_lines.append(line)

        return "\n".join(result_lines)


    def remove_table_row_by_first_cell(
        markdown: str,
        first_cell: str,
    ) -> str:
        """
        Удаляет markdown-строку таблицы по первой ячейке.
        Используется перед вставкой обязательных риск-строк, чтобы не плодить дубли.
        """
        lines = markdown.splitlines()
        result_lines: list[str] = []

        for line in lines:
            cells = parse_markdown_table_row(line)

            if cells and cells[0] == first_cell:
                continue

            result_lines.append(line)

        return "\n".join(result_lines)


    def markdown_row_exists(
        markdown: str,
        first_cell: str,
    ) -> bool:
        for line in markdown.splitlines():
            cells = parse_markdown_table_row(line)

            if cells and cells[0] == first_cell:
                return True

        return False


    def insert_rows_before_section(
        markdown: str,
        next_section_heading: str,
        rows: list[str],
    ) -> str:
        """
        Вставляет строки таблицы перед следующим разделом.

        Для D-003 используется, чтобы добавлять риск-строки в конец таблицы
        раздела 6 перед `## 7. План перехода к MVP`, а не вне таблицы.
        """
        rows_to_insert = [
            row.strip()
            for row in rows
            if row and row.strip()
        ]

        if not rows_to_insert:
            return markdown

        rows_block = "\n".join(rows_to_insert)

        markers = [
            f"\n\n---\n\n{next_section_heading}",
            f"\n---\n\n{next_section_heading}",
            f"\n\n{next_section_heading}",
            f"\n{next_section_heading}",
        ]

        for marker in markers:
            if marker in markdown:
                if marker.startswith("\n\n---"):
                    return markdown.replace(
                        marker,
                        f"\n{rows_block}{marker}",
                        1,
                    )

                return markdown.replace(
                    marker,
                    f"\n{rows_block}\n{marker}",
                    1,
                )

        return markdown


    attachment_evidence_present = any(
        token in fixed
        for token in [
            "limited consulting-grade review",
            "event_id",
            "line_id",
            "equipment_id",
            "start_time",
            "end_time",
            "duration_min",
            "reason_code",
            "reason_description",
            "Тестовая выгрузка",
            "тестовой выгрузке",
        ]
    )

    fixed = fixed.replace(
        "Подготовка данных и требований",
        "Data & Scope Gate перед MVP",
    )

    fixed = fixed.replace(
        "Получение тестовой Excel/CSV выгрузки",
        "Подтверждены scope, ограниченная обезличенная выгрузка, правила ИБ, baseline и критерии успеха",
    )

    fixed = fixed.replace(
        "Рабочий прототип / dashboard / отчёт",
        "MVP-артефакт: dashboard / аналитический отчёт / список повторяющихся причин / паттернов",
    )

    fixed = fixed.replace(
        "Рабочий прототип / dashboard / отчет",
        "MVP-артефакт: dashboard / аналитический отчёт / список повторяющихся причин / паттернов",
    )

    fixed = fixed.replace(
        "Масштабирование после подтверждения эффекта",
        "Решение о real-time / API / MES / ERP-интеграциях после подтверждения ценности MVP",
    )

    fixed = fixed.replace(
        "Запросить обезличенные данные и подтвердить стабильность идентификаторов",
        "Перейти к Data & Scope Gate перед MVP: согласовать ограниченную обезличенную выгрузку, baseline, критерии успеха, ИБ и стабильность идентификаторов",
    )

    fixed = fixed.replace(
        "Запросить обезличенные данные",
        "Согласовать ограниченную обезличенную выгрузку за выбранный период",
    )

    fixed = fixed.replace(
        "Интеграции с MES и WMS отсутствуют, что также требует внимания.",
        "Интеграции с MES / SCADA / API не обязательны для первого MVP и могут быть отложены до подтверждения ценности пилота.",
    )

    fixed = fixed.replace(
        "Интеграции с MES и WMS отсутствуют",
        "Интеграции с MES / SCADA / API не обязательны для первого MVP",
    )

    fixed = fixed.replace(
        "формирование отчетов",
        "подготовка управленческого отчёта",
    )

    fixed = fixed.replace(
        "формирование отчётов",
        "подготовка управленческого отчёта",
    )

    fixed = fixed.replace(
        "Формирование отчетов",
        "Подготовка управленческого отчёта",
    )

    fixed = fixed.replace(
        "Формирование отчётов",
        "Подготовка управленческого отчёта",
    )

    fixed = fixed.replace(
        "полная интеграция со всеми системами",
        "интеграция со всеми системами",
    )

    # Не завышаем экономическую уверенность.
    fixed = replace_table_row_by_first_cell(
        fixed,
        "Подтверждённые числа",
        "| Предварительно указанные числа | 80–120 событий в месяц | MEDIUM | Требует подтверждения на baseline-периоде |",
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "Подтвержденные числа",
        "| Предварительно указанные числа | 80–120 событий в месяц | MEDIUM | Требует подтверждения на baseline-периоде |",
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "Потенциальный эффект",
        "| Потенциальный эффект | Предварительная гипотеза снижения управляемых потерь | MEDIUM | Конкретный эффект фиксируется после baseline |",
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "Что нужно подтвердить",
        "| Что нужно подтвердить | количество событий простоев, среднюю длительность простоя, стоимость часа простоя, baseline потерь, целевой эффект | MEDIUM | Это экономические параметры; ID и временные метки относятся к готовности данных |",
    )

    if is_downtime:
        fixed = replace_table_row_by_first_cell(
            fixed,
            "Что входит",
            "| Что входит | Проверка структуры данных, выявление повторяющихся причин / паттернов, ранжирование объектов или факторов по вкладу в проблему, dashboard / управленческий отчёт для владельца процесса |",
        )

        fixed = replace_table_row_by_first_cell(
            fixed,
            "Что не входит",
            "| Что не входит | Автономное управление оборудованием, real-time контур, замена ERP / MES / SCADA, интеграция со всеми системами, масштабирование на все линии / площадки, production-grade эксплуатация |",
        )

        fixed = replace_table_row_by_first_cell(
            fixed,
            "KPI успеха",
            "| KPI успеха | Проверить гипотезу снижения управляемых простоев и сокращения ручной отчётности; конкретные целевые значения фиксируются после baseline |",
        )

        fixed = replace_table_row_by_first_cell(
            fixed,
            "Качество данных",
            "| Качество данных | PARTIAL | Структура видна на тестовой выгрузке | Проверить дубликаты, пропуски, стабильность формата и полноту периода |",
        )

        fixed = replace_table_row_by_first_cell(
            fixed,
            "Baseline / экономика",
            "| Baseline / экономика | PARTIAL | Есть предварительная оценка потерь | Подтвердить baseline, количество событий, среднюю длительность простоя, стоимость часа простоя и целевой эффект |",
        )

        fixed = replace_table_row_by_first_cell(
            fixed,
            "ИБ / ПДн / коммерческая тайна",
            "| ИБ / ПДн / коммерческая тайна | PARTIAL | Обезличивание требуется | Согласовать NDA, правила передачи, хранения, удаления и локальной обработки данных |",
        )

        fixed = replace_table_row_by_first_cell(
            fixed,
            "Владелец процесса",
            "| Владелец процесса | PARTIAL | роль пользователя определена | Назначить конкретного владельца процесса со стороны клиента |",
        )

        if attachment_evidence_present:
            fixed = replace_table_row_by_first_cell(
                fixed,
                "Тестовая Excel / CSV выгрузка передана",
                "| Тестовая Excel / CSV выгрузка передана | частично выполнено | Тестовая выгрузка получена и использована в limited consulting-grade review; требуется подтвердить регулярность выгрузки и репрезентативность периода |",
            )

            fixed = replace_table_row_by_first_cell(
                fixed,
                "ID события простоя подтверждён",
                "| ID события простоя подтверждён | частично выполнено | event_id найден в тестовой структуре; требуется подтвердить стабильность ID в регулярной выгрузке |",
            )

            fixed = replace_table_row_by_first_cell(
                fixed,
                "ID оборудования / линии подтверждены",
                "| ID оборудования / линии подтверждены | частично выполнено | line_id и equipment_id найдены в тестовой структуре; требуется подтвердить стабильность справочников в регулярной выгрузке |",
            )

            fixed = replace_table_row_by_first_cell(
                fixed,
                "Начало и конец простоя или длительность доступны",
                "| Начало и конец простоя или длительность доступны | частично выполнено | start_time, end_time и duration_min найдены в тестовой структуре; требуется подтвердить timezone, единый формат timestamp и полноту периода |",
            )

            fixed = replace_table_row_by_first_cell(
                fixed,
                "Причина простоя или комментарий доступны",
                "| Причина простоя или комментарий доступны | частично выполнено | reason_code / reason_description найдены в тестовой структуре; требуется подтвердить единый справочник причин или правила классификации |",
            )

        else:
            fixed = replace_table_row_by_first_cell(
                fixed,
                "Тестовая Excel / CSV выгрузка передана",
                "| Тестовая Excel / CSV выгрузка передана | требует выполнения | Согласовать и передать ограниченную обезличенную выгрузку за выбранный период |",
            )

            fixed = replace_table_row_by_first_cell(
                fixed,
                "ID события простоя подтверждён",
                "| ID события простоя подтверждён | требует выполнения | Подтвердить наличие стабильного ID события простоя или правило его формирования |",
            )

            fixed = replace_table_row_by_first_cell(
                fixed,
                "ID оборудования / линии подтверждены",
                "| ID оборудования / линии подтверждены | требует выполнения | Подтвердить наличие стабильных ID оборудования и линии / участка |",
            )

            fixed = replace_table_row_by_first_cell(
                fixed,
                "Начало и конец простоя или длительность доступны",
                "| Начало и конец простоя или длительность доступны | требует выполнения | Подтвердить наличие начала, конца простоя или длительности в едином формате времени |",
            )

            fixed = replace_table_row_by_first_cell(
                fixed,
                "Причина простоя или комментарий доступны",
                "| Причина простоя или комментарий доступны | требует выполнения | Подтвердить наличие причины простоя, reason_code, reason_description или текстового комментария |",
            )

        fixed = replace_table_row_by_first_cell(
            fixed,
            "Baseline и способ расчёта потерь согласованы",
            "| Baseline и способ расчёта потерь согласованы | требует выполнения | Согласовать baseline, количество событий, среднюю длительность простоя, стоимость часа простоя и целевой эффект |",
        )

        fixed = replace_table_row_by_first_cell(
            fixed,
            "ИБ / NDA / локальная обработка согласованы",
            "| ИБ / NDA / локальная обработка согласованы | требует выполнения | Согласовать NDA, правила передачи, хранения, удаления и локальной обработки данных |",
        )

        fixed = replace_table_row_by_first_cell(
            fixed,
            "Владелец процесса назначен",
            "| Владелец процесса назначен | частично выполнено | Назначить конкретного владельца процесса со стороны клиента |",
        )

    # Удаляем старые / дублирующие риск-строки перед нормальной вставкой.
    risk_rows_to_manage = [
        "MVP ошибочно используется как основание для автоматического управления оборудованием",
        "Неподтверждённая доступность MES / SCADA как регулярных источников",
        "Низкое доверие к рекомендациям AI",
        "Низкая доверие к рекомендациям AI",
    ]

    for risk_first_cell in risk_rows_to_manage:
        fixed = remove_table_row_by_first_cell(fixed, risk_first_cell)

    extra_risks: list[str] = []

    if is_industrial:
        extra_risks.append(
            "| MVP ошибочно используется как основание для автоматического управления оборудованием | Производственная безопасность | HIGH | Зафиксировать human-in-the-loop и исключить real-time управление из MVP |"
        )

        extra_risks.append(
            "| Неподтверждённая доступность MES / SCADA как регулярных источников | Интеграции | MEDIUM | Начать с Excel / CSV; SCADA использовать только опционально после проверки доступности; MES не считать обязательным источником первого MVP |"
        )

    # Нормализуем частую опечатку, если модель её всё же вернула.
    fixed = fixed.replace(
        "| Низкая доверие к рекомендациям AI |",
        "| Низкое доверие к рекомендациям AI |",
    )

    if extra_risks and "## 6. Риски и ограничения" in fixed:
        fixed = insert_rows_before_section(
            fixed,
            "## 7. План перехода к MVP",
            extra_risks,
        )

    # Финальная нормализация markdown перед разделом 7.
    fixed = re.sub(
        r"(\| MVP ошибочно используется как основание для автоматического управления оборудованием \| Производственная безопасность \| HIGH \| Зафиксировать human-in-the-loop и исключить real-time управление из MVP \|)\n---",
        r"\1\n\n---",
        fixed,
    )

    fixed = re.sub(
        r"(\| Неподтверждённая доступность MES / SCADA как регулярных источников \| Интеграции \| MEDIUM \| Начать с Excel / CSV; SCADA использовать только опционально после проверки доступности; MES не считать обязательным источником первого MVP \|)\n---",
        r"\1\n\n---",
        fixed,
    )

    fixed = re.sub(r"\n{3,}", "\n\n", fixed).strip()

    return fixed

def run_d003_diagnostic_report(
    diagnostic_run_id: int,
    force_rebuild: bool = False,
) -> str:
    existing_result = get_existing_d003_result(diagnostic_run_id)

    if existing_result and not force_rebuild:
        return existing_result

    validate_d003_input(diagnostic_run_id)

    update_diagnostic_status(
        diagnostic_run_id=diagnostic_run_id,
        status=DIAGNOSTIC_STATUS_D003_RUNNING,
    )

    prompt_input = build_d003_prompt_input(diagnostic_run_id)

    result = run_agent_with_prompt(
        agent_prompt_name=D003_AGENT_PROMPT_NAME,
        user_input=prompt_input,
    )

    result = postprocess_d003_result(result, prompt_input)

    summary = build_d003_summary(result)

    save_d003_result(
        diagnostic_run_id=diagnostic_run_id,
        result=result,
        summary=summary,
    )


    return result