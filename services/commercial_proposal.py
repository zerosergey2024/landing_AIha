from __future__ import annotations

import json
import sqlite3
from typing import Any

from db import get_db_connection
from services.ai_agent import run_agent_with_prompt
from services.diagnostics import (
    DIAGNOSTIC_STATUS_D004_COMPLETED,
    DIAGNOSTIC_STATUS_D004_RUNNING,
    save_d004_result,
    update_diagnostic_status,
)


D004_AGENT_PROMPT_NAME = "commercial_proposal"


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

        try:
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
        except sqlite3.OperationalError:
            constraints = None

    return (
        dict(lead) if lead else None,
        dict(constraints) if constraints else None,
    )


def _get_table_column_names(table_name: str) -> set[str]:
    try:
        with get_db_connection() as conn:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.OperationalError:
        return set()

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

    if not column_names:
        return []

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
        elif "diagnostic_run_id" in column_names:
            rows = conn.execute(
                """
                SELECT *
                FROM diagnostic_attachments
                WHERE diagnostic_run_id = ?
                ORDER BY id ASC
                """,
                (diagnostic_run_id,),
            ).fetchall()
        else:
            rows = []

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
                "raw_payload is required for Industrial AI before running D-004"
            )

        payload_for_d004 = raw_payload
        payload_source = "raw_payload"

    elif normalized_payload:
        payload_for_d004 = normalized_payload
        payload_source = "normalized_payload"

    elif raw_payload:
        payload_for_d004 = raw_payload
        payload_source = "raw_payload"

    else:
        raise ValueError("raw_payload or normalized_payload is required before D-004")

    return {
        "raw_payload": raw_payload,
        "normalized_payload": normalized_payload,
        "payload_for_d004": payload_for_d004,
        "payload_source": payload_source,
        "brief_type": brief_type,
    }


def validate_d004_input(diagnostic_run_id: int) -> None:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)

    if not diagnostic_run["d001_result"]:
        raise ValueError("D-001 result is required before running D-004")

    if not diagnostic_run["d002_result"] and not diagnostic_run["d002_summary"]:
        raise ValueError("D-002 result or D-002 summary is required before running D-004")

    if not diagnostic_run["d003_result"] and not diagnostic_run["d003_summary"]:
        raise ValueError("D-003 result or D-003 summary is required before running D-004")

    input_pack = dict(_get_latest_input_pack_row(diagnostic_run_id))
    _resolve_payloads(input_pack)


def get_existing_d004_result(diagnostic_run_id: int) -> str | None:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)
    return diagnostic_run["d004_result"] or None


def get_d004_input(diagnostic_run_id: int) -> dict[str, Any]:
    validate_d004_input(diagnostic_run_id)

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
        "payload_for_d004": payloads["payload_for_d004"],
        "payload_source": payloads["payload_source"],
        "brief_type": payloads["brief_type"],
        "attachments": attachments,
        "d001_result": diagnostic_run["d001_result"] or "",
        "d002_summary": diagnostic_run["d002_summary"] or "",
        "d002_result": diagnostic_run["d002_result"] or "",
        "d003_summary": diagnostic_run["d003_summary"] or "",
        "d003_result": diagnostic_run["d003_result"] or "",
    }


def build_d004_prompt_input(diagnostic_run_id: int) -> str:
    data = get_d004_input(diagnostic_run_id)

    safe_diagnostic_run = _sanitize_diagnostic_run(data["diagnostic_run"])

    return f"""
# INPUT FOR D-004 COMMERCIAL PROPOSAL AGENT

## Lead

{json.dumps(data["lead"], ensure_ascii=False, indent=2)}

## Client Constraints

{json.dumps(data["constraints"], ensure_ascii=False, indent=2)}

## Diagnostic Run

{json.dumps(safe_diagnostic_run, ensure_ascii=False, indent=2)}

## Selected Input Pack

{json.dumps(data["safe_input_pack"], ensure_ascii=False, indent=2)}

## Payload Selected For D-004

Use this payload as the main structured input for D-004.

Payload source: {data["payload_source"]}

{json.dumps(data["payload_for_d004"], ensure_ascii=False, indent=2)}

## Raw Diagnostic Input Pack / Industrial AI Brief

For Industrial AI, raw payload is the source of truth.

{json.dumps(data["raw_payload"], ensure_ascii=False, indent=2)}

## Normalized Diagnostic Input Pack

Use this only as a helper.
If it conflicts with raw payload for Industrial AI, raw payload wins.

{json.dumps(data["normalized_payload"], ensure_ascii=False, indent=2)}

## D-001 Diagnostic Assessment Result

Use this as diagnostic background only.

{data["d001_result"]}

## D-002 Summary

Use this as the primary MVP-design input for D-004.

{data["d002_summary"]}

## D-002 Full Result

Use this only as supporting context if the summary is incomplete.

{data["d002_result"]}

## D-003 Summary

Use this as the primary commercial decision input for D-004.

{data["d003_summary"]}

## D-003 Full Result

Use this only as supporting context if the summary is incomplete.

{data["d003_result"]}

## Attachments Summary

{json.dumps(data["attachments"], ensure_ascii=False, indent=2)}
""".strip()


def build_d004_summary(result: str) -> str:
    """
    Формирует компактную выжимку коммерческого предложения.
    Summary ориентировано на новую русскую структуру D-004.
    """
    lines = [line.rstrip() for line in result.splitlines()]

    important_headers = {
        "# Коммерческое предложение AIha Consulting",
        "# Коммерческое предложение AIha Consulting — Industrial AI",
        "## 1. Краткое резюме предложения",
        "## 3. Экономическое обоснование",
        "## 4. Рекомендуемый формат работ",
        "## 5. Scope ближайшего этапа",
        "## 6. План работ и сроки",
        "## 7. Бюджетная оценка",
        "## 10. Риски и ограничения",
        "## 13. Рекомендуемый следующий шаг",
        "## 14. Короткое сообщение клиенту",
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

def postprocess_d004_result(result: str, prompt_input: str) -> str:
    """
    Исправляет устойчивые формальные ошибки D-004 для Industrial AI / downtime_analysis.

    Важно:
    - если в Industrial Brief указано частичное наличие MES / SCADA, не удаляем это из контекста;
    - но не разрешаем делать MES / SCADA обязательной интеграцией первого MVP;
    - строки, добавленные postprocess-ом, должны оставаться внутри markdown-таблиц.
    """
    is_downtime = '"primary_case_type": "downtime_analysis"' in prompt_input
    is_industrial = '"brief_type": "industrial_ai"' in prompt_input

    if not is_downtime and not is_industrial:
        return result

    fixed = result

    def replace_table_row_by_first_cell(markdown: str, first_cell: str, new_row: str) -> str:
        """
        Заменяет строку markdown-таблицы по первой ячейке.
        Это устойчивее, чем полное fixed.replace(), потому что модель
        часто меняет текст во 2-й и 3-й колонках.
        """
        lines = markdown.splitlines()
        result_lines: list[str] = []

        prefix = f"| {first_cell} |"

        for line in lines:
            if line.strip().startswith(prefix):
                result_lines.append(new_row)
            else:
                result_lines.append(line)

        return "\n".join(result_lines)

    def collapse_blank_before_table_row(markdown: str, first_cell: str) -> str:
        """
        Убирает пустую строку перед добавленной строкой таблицы,
        чтобы markdown не разбивал одну таблицу на две.
        """
        return markdown.replace(
            f"\n\n| {first_cell} |",
            f"\n| {first_cell} |",
        )

    # Экономика: эффект только как гипотеза.
    fixed = replace_table_row_by_first_cell(
        fixed,
        "Потенциальный эффект",
        "| Потенциальный эффект | 20 000–60 000 ₽ в месяц | Предварительная гипотеза 5–10% от месячных потерь, требует проверки на MVP |",
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "Что уже подтверждено",
        "| Что уже подтверждено | Возможность выгрузки данных в Excel / CSV; частично подтверждены данные о событиях простоев. |",
    )

    # Не писать "после подтверждения эффекта" — эффект не подтверждается заранее.
    fixed = fixed.replace(
        "Масштабирование после подтверждения эффекта",
        "Масштабирование после проверки ценности MVP",
    )

    fixed = fixed.replace(
        "после подтверждения эффекта",
        "после проверки ценности MVP",
    )

    fixed = fixed.replace(
        "После подтверждения успешности MVP и согласования условий.",
        "После проверки ценности MVP, готовности данных, ИБ и интеграционных ограничений.",
    )

    fixed = fixed.replace(
        "После подтверждения ценности MVP и наличия необходимых данных.",
        "После проверки ценности MVP, готовности данных, ИБ и интеграционных ограничений.",
    )

    fixed = fixed.replace(
        "| Интеграции и масштабирование | Масштабирование после проверки ценности MVP | отдельно после MVP | — |",
        "| Интеграции и масштабирование | Масштабирование после проверки ценности MVP | отдельно после MVP | Оценивается отдельным этапом |",
    )

    fixed = fixed.replace(
        "| Интеграции | Масштабирование после проверки ценности MVP | отдельно | — |",
        "| Интеграции | Масштабирование после проверки ценности MVP | отдельно | Оценивается отдельным этапом |",
    )

    # Scope exclusions.
    fixed = replace_table_row_by_first_cell(
        fixed,
        "Автономное управление оборудованием",
        "| Автономное управление оборудованием | MVP не управляет оборудованием и не принимает автономные производственные решения. |",
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "Полная интеграция со всеми системами",
        "| Полная интеграция со всеми системами | Регулярные интеграции оцениваются отдельно после MVP. |",
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "Real-time контур",
        "| Real-time контур | Не входит в первый этап и требует отдельной технической и ИБ-оценки. |",
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "Production-grade эксплуатация",
        "| Production-grade эксплуатация | Требует отдельного этапа после MVP и отдельного согласования. |",
    )

    # Добавляем недостающие исключения scope перед блоком "Может быть добавлено после MVP".
    if "### Может быть добавлено после MVP" in fixed:
        exclusion_rows: list[str] = []

        if "| Real-time контур |" not in fixed:
            exclusion_rows.append(
                "| Real-time контур | Не входит в первый этап и требует отдельной технической и ИБ-оценки. |"
            )

        if "| Замена ERP / MES / SCADA |" not in fixed:
            exclusion_rows.append(
                "| Замена ERP / MES / SCADA | Существующие системы не заменяются в рамках MVP; возможны выгрузки или отдельные интеграции после MVP. |"
            )

        if "| Production-grade эксплуатация |" not in fixed:
            exclusion_rows.append(
                "| Production-grade эксплуатация | Требует отдельного этапа после MVP и отдельного согласования. |"
            )

        if exclusion_rows:
            fixed = fixed.replace(
                "\n### Может быть добавлено после MVP",
                "\n" + "\n".join(exclusion_rows) + "\n\n### Может быть добавлено после MVP",
                1,
            )

    # Scope "Входит": добавить MVP-артефакты, если модель оставила только подготовку.
    if "| AI-анализ / dashboard |" not in fixed and "### Не входит" in fixed:
        fixed = fixed.replace(
            "\n### Не входит",
            "\n| AI-анализ / dashboard | Анализ событий простоев, повторяющихся причин и подготовка отчёта / dashboard для владельца процесса. |\n"
            "| Human-in-the-loop проверка | Проверка рекомендаций руководителем производства или назначенным экспертом. |\n\n"
            "### Не входит",
            1,
        )

    # Ответственность клиента.
    if "| Назначить производственного эксперта |" not in fixed and "## 9. Ответственность AIha Consulting" in fixed:
        fixed = fixed.replace(
            "\n---\n\n## 9. Ответственность AIha Consulting",
            "\n| Назначить производственного эксперта | Для проверки причин простоев и валидации рекомендаций |\n"
            "| Назначить ИТ / OT-контакт | Для проверки выгрузки, формата данных, доступности 1C / MES / SCADA / OEE и инфраструктурных ограничений |\n\n"
            "---\n\n## 9. Ответственность AIha Consulting",
            1,
        )

    # Ответственность AIha Consulting.
    if "## 10. Риски и ограничения" in fixed:
        aiha_rows: list[str] = []

        if "| Не заменяет ERP / MES / SCADA |" not in fixed:
            aiha_rows.append(
                "| Не заменяет ERP / MES / SCADA | Существующие системы остаются источниками данных или объектами отдельных интеграций после MVP |"
            )

        if "| Не гарантирует экономический эффект |" not in fixed:
            aiha_rows.append(
                "| Не гарантирует экономический эффект | Эффект проверяется как гипотеза MVP после фиксации baseline |"
            )

        if "| Не запускает real-time / production-grade контур |" not in fixed:
            aiha_rows.append(
                "| Не запускает real-time / production-grade контур | Эти работы требуют отдельной технической и ИБ-оценки |"
            )

        if aiha_rows:
            fixed = fixed.replace(
                "\n---\n\n## 10. Риски и ограничения",
                "\n" + "\n".join(aiha_rows) + "\n\n---\n\n## 10. Риски и ограничения",
                1,
            )

    # Риски D-004.
    additional_risk_rows: list[str] = []

    if "| ИБ / коммерческая тайна |" not in fixed:
        additional_risk_rows.append(
            "| ИБ / коммерческая тайна | Без согласованных правил передачи, хранения, удаления и локальной обработки нельзя запускать MVP | Согласовать NDA и правила обработки данных до передачи тестовой выгрузки |"
        )

    if "| Неподтверждённый baseline |" not in fixed:
        additional_risk_rows.append(
            "| Неподтверждённый baseline | Нельзя корректно оценить экономический эффект MVP | Зафиксировать baseline, количество событий, среднюю длительность простоя и стоимость часа простоя |"
        )

    if "| Ошибочное ожидание real-time или автоматического управления |" not in fixed:
        additional_risk_rows.append(
            "| Ошибочное ожидание real-time или автоматического управления | Может привести к неверному scope и рискам производственной безопасности | Зафиксировать, что MVP не управляет оборудованием и работает только с human-in-the-loop проверкой |"
        )

    if "| Недоверие производственных экспертов к рекомендациям AI |" not in fixed:
        additional_risk_rows.append(
            "| Недоверие производственных экспертов к рекомендациям AI | Рекомендации могут не использоваться в процессе | Назначить производственного эксперта и валидировать выводы на исторических данных |"
        )

    if "| Доступность 1C / MES / SCADA / OEE для регулярной выгрузки |" not in fixed:
        additional_risk_rows.append(
            "| Доступность 1C / MES / SCADA / OEE для регулярной выгрузки | Частичный статус источников может ограничить регулярность анализа | Начать с Excel / CSV, проверить доступность источников на подготовительном этапе, интеграции оценивать отдельно после MVP |"
        )

    if additional_risk_rows and "## 11. Критерии успеха MVP" in fixed:
        fixed = fixed.replace(
            "\n---\n\n## 11. Критерии успеха MVP",
            "\n" + "\n".join(additional_risk_rows) + "\n\n---\n\n## 11. Критерии успеха MVP",
            1,
        )

    # Клиентская формулировка вместо внутренних статусов.
    fixed = replace_table_row_by_first_cell(
        fixed,
        "Готовность к масштабированию",
        "| Готовность к масштабированию | Результат MVP-пилота | Рекомендация: масштабировать, масштабировать с ограничениями или не масштабировать |",
    )

    fixed = fixed.replace(
        "Если baseline не указан, в критериях и экономике пишите:",
        "Если baseline не указан, в критериях и экономике указывается:",
    )

    fixed = fixed.replace(
        "Ждем вашего ответа.",
        "Готовы согласовать подготовительный этап и список данных для первой тестовой выгрузки.",
    )

    fixed = fixed.replace(
        "Ждём вашего ответа.",
        "Готовы согласовать подготовительный этап и список данных для первой тестовой выгрузки.",
    )

    fixed = fixed.replace(
        "Для этого потребуется от вас передать тестовую Excel / CSV выгрузку и подтвердить ID событий, оборудования и линии. Результатом станет подготовка данных для анализа и выявление причин простоев.",
        "Для этого потребуется передать тестовую Excel / CSV выгрузку, подтвердить ID событий, оборудования и линии, согласовать baseline и правила обработки данных. Результатом станет подготовка данных для MVP-пилота и проверка гипотезы снижения простоев.",
    )

    fixed = fixed.replace(
        "Для этого потребуется от вас передать тестовую Excel / CSV выгрузку и подтвердить ID событий, оборудования и линии.",
        "Для этого потребуется передать тестовую Excel / CSV выгрузку, подтвердить ID событий, оборудования и линии, согласовать baseline и правила обработки данных.",
    )

    fixed = fixed.replace(
        "Для этого потребуется от вас передать тестовую Excel/CSV выгрузку и подтвердить ID событий, оборудования и линии.",
        "Для этого потребуется передать тестовую Excel / CSV выгрузку, подтвердить ID событий, оборудования и линии, согласовать baseline и правила обработки данных.",
    )

    fixed = fixed.replace(
        "Для этого потребуется от вас передать тестовую выгрузку и подтвердить ID событий, оборудования и линии.",
        "Для этого потребуется передать тестовую Excel / CSV выгрузку, подтвердить ID событий, оборудования и линии, согласовать baseline и правила обработки данных.",
    )

    fixed = fixed.replace(
        "| Интеграция с MES/SCADA | После подтверждения эффективности MVP и наличия необходимых данных. |",
        "| Интеграция с MES/SCADA | После проверки ценности MVP, готовности данных, ИБ и интеграционных ограничений. |",
    )

    fixed = fixed.replace(
        "Для этого необходимо получить ограниченную обезличенную выгрузку данных, проверить ID событий, оборудования и линии, а также согласовать baseline. Это позволит перейти к следующему этапу — MVP-пилоту, который поможет проверить гипотезу снижения простоев и выявить повторяющиеся причины отказов.",
        "Для этого необходимо получить ограниченную обезличенную выгрузку данных, проверить ID событий, оборудования и линии, согласовать baseline и правила обработки данных. Это позволит перейти к следующему этапу — MVP-пилоту, который проверит гипотезу снижения простоев и поможет выявить повторяющиеся причины отказов.",
    )

    fixed = fixed.replace("| HIGH |", "| Высокое |")
    fixed = fixed.replace("| MEDIUM |", "| Среднее |")
    fixed = fixed.replace("| LOW |", "| Низкое |")

    # Финальный markdown cleanup: добавленные строки должны оставаться внутри таблиц.
    for first_cell in [
        "AI-анализ / dashboard",
        "Human-in-the-loop проверка",
        "Real-time контур",
        "Замена ERP / MES / SCADA",
        "Production-grade эксплуатация",
        "Назначить производственного эксперта",
        "Назначить ИТ / OT-контакт",
        "Не заменяет ERP / MES / SCADA",
        "Не гарантирует экономический эффект",
        "Не запускает real-time / production-grade контур",
        "ИБ / коммерческая тайна",
        "Неподтверждённый baseline",
        "Ошибочное ожидание real-time или автоматического управления",
        "Недоверие производственных экспертов к рекомендациям AI",
        "Доступность 1C / MES / SCADA / OEE для регулярной выгрузки",
    ]:
        fixed = collapse_blank_before_table_row(fixed, first_cell)

    return fixed


def run_d004_commercial_proposal(
    diagnostic_run_id: int,
    force_rebuild: bool = False,
) -> str:
    existing_result = get_existing_d004_result(diagnostic_run_id)

    if existing_result and not force_rebuild:
        return existing_result

    validate_d004_input(diagnostic_run_id)

    update_diagnostic_status(
        diagnostic_run_id=diagnostic_run_id,
        status=DIAGNOSTIC_STATUS_D004_RUNNING,
    )

    prompt_input = build_d004_prompt_input(diagnostic_run_id)

    result = run_agent_with_prompt(
        agent_prompt_name=D004_AGENT_PROMPT_NAME,
        user_input=prompt_input,
    )

    result = postprocess_d004_result(result, prompt_input)

    summary = build_d004_summary(result)

    save_d004_result(
        diagnostic_run_id=diagnostic_run_id,
        result=result,
        summary=summary,
    )

    update_diagnostic_status(
        diagnostic_run_id=diagnostic_run_id,
        status=DIAGNOSTIC_STATUS_D004_COMPLETED,
    )

    return result