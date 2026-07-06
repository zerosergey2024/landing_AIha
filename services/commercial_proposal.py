from __future__ import annotations

import json
import re
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

    context_for_case_type = "\n".join(
        [
            json.dumps(payloads, ensure_ascii=False),
            diagnostic_run["d001_result"] or "",
            diagnostic_run["d002_result"] or "",
            diagnostic_run["d003_result"] or "",
        ]
    )

    primary_case_type = _extract_case_type_from_text(context_for_case_type)
    case_profile = build_case_profile(primary_case_type)

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
        "case_profile": case_profile,
        "d001_result": diagnostic_run["d001_result"] or "",
        "d002_summary": diagnostic_run["d002_summary"] or "",
        "d002_result": diagnostic_run["d002_result"] or "",
        "d003_summary": diagnostic_run["d003_summary"] or "",
        "d003_result": diagnostic_run["d003_result"] or "",
    }

def _extract_case_type_from_text(text: str) -> str:
    patterns = [
        r'"primary_case_type"\s*:\s*"([^"]+)"',
        r'"case_type"\s*:\s*"([^"]+)"',
        r'"use_case_type"\s*:\s*"([^"]+)"',
    ]

    for pattern in patterns:
        match = re.search(pattern, text or "")

        if match:
            value = match.group(1).strip()

            if value:
                return value

    lowered = (text or "").lower()

    if any(token in lowered for token in ["downtime_analysis", "простои", "простой", "останов"]):
        return "downtime_analysis"

    if any(token in lowered for token in ["quality_defects", "брак", "дефект", "качество"]):
        return "quality_defects"

    if any(token in lowered for token in ["energy_optimization", "энерг", "электроэнерг"]):
        return "energy_optimization"

    if any(token in lowered for token in ["maintenance_optimization", "тоир", "ремонт", "maintenance"]):
        return "maintenance_optimization"

    if any(token in lowered for token in ["cycle_time", "время цикла", "производственный цикл"]):
        return "process_cycle_time"

    if any(token in lowered for token in ["inventory", "stock", "склад", "запас", "поставка"]):
        return "inventory_or_supply"

    if any(token in lowered for token in ["document", "документ", "заявк", "договор", "акт"]):
        return "document_workflow"

    if any(token in lowered for token in ["support", "обращен", "тикет", "запрос клиента"]):
        return "customer_support"

    return "generic_industrial_ai"


def build_case_profile(primary_case_type: str) -> dict[str, str]:
    profiles: dict[str, dict[str, str]] = {
        "downtime_analysis": {
            "case_type": "downtime_analysis",
            "case_name": "анализ простоев",
            "business_problem": "потери из-за простоев, ручного разбора событий и запаздывающих решений",
            "loss_object": "управляемые простои",
            "event_name": "события простоев",
            "object_name": "линия / оборудование / участок",
            "reason_name": "причины остановок",
            "effect_name": "снижение управляемых простоев и сокращение ручного анализа",
            "mvp_focus": "выявление повторяющихся причин простоев и ранжирование объектов по вкладу в потери",
            "data_requirements": "ID события, ID объекта, временные метки, длительность, причина / комментарий",
            "kpi_language": "снижение управляемых простоев, сокращение ручной работы, пригодность данных для регулярного анализа",
        },
        "quality_defects": {
            "case_type": "quality_defects",
            "case_name": "анализ дефектов и отклонений качества",
            "business_problem": "потери из-за брака, переделок, рекламаций и ручного разбора причин качества",
            "loss_object": "дефекты / отклонения качества",
            "event_name": "события качества / дефекты",
            "object_name": "партия / изделие / линия / участок",
            "reason_name": "причины дефектов",
            "effect_name": "снижение повторяющихся дефектов и ускорение анализа причин качества",
            "mvp_focus": "выявление повторяющихся причин дефектов и ранжирование факторов по вкладу в проблему",
            "data_requirements": "ID партии / изделия, дата, тип дефекта, участок, причина / комментарий, статус проверки",
            "kpi_language": "снижение повторяющихся дефектов, сокращение ручного разбора, пригодность данных для регулярного анализа качества",
        },
        "energy_optimization": {
            "case_type": "energy_optimization",
            "case_name": "анализ энергопотребления",
            "business_problem": "потери из-за избыточного энергопотребления, пиковых нагрузок и слабой прозрачности факторов потребления",
            "loss_object": "избыточное энергопотребление",
            "event_name": "периоды / точки потребления",
            "object_name": "линия / оборудование / участок / счётчик",
            "reason_name": "факторы перерасхода",
            "effect_name": "выявление зон перерасхода и проверка гипотезы снижения энергозатрат",
            "mvp_focus": "ранжирование объектов и режимов по вкладу в энергопотребление",
            "data_requirements": "ID объекта, период, потребление, режим работы, выпуск / нагрузка, тариф / стоимость",
            "kpi_language": "снижение управляемого перерасхода, прозрачность факторов потребления, пригодность данных для регулярного анализа",
        },
        "maintenance_optimization": {
            "case_type": "maintenance_optimization",
            "case_name": "оптимизация ТОиР",
            "business_problem": "потери из-за внеплановых ремонтов, повторных отказов и слабой приоритизации работ",
            "loss_object": "внеплановые ремонты / повторные отказы",
            "event_name": "ремонтные события / заявки ТОиР",
            "object_name": "оборудование / узел / линия",
            "reason_name": "причины ремонтов / отказов",
            "effect_name": "сокращение повторных отказов и повышение управляемости ТОиР",
            "mvp_focus": "ранжирование оборудования и причин по вкладу в ремонтную нагрузку",
            "data_requirements": "ID заявки, ID оборудования, дата, тип работы, причина, длительность, статус, комментарий",
            "kpi_language": "снижение повторных отказов, сокращение ручного анализа ТОиР, пригодность данных для регулярного контроля",
        },
        "process_cycle_time": {
            "case_type": "process_cycle_time",
            "case_name": "анализ времени цикла",
            "business_problem": "потери из-за длительных циклов, очередей, ручных согласований и слабой прозрачности узких мест",
            "loss_object": "задержки / длительность цикла",
            "event_name": "этапы процесса / операции",
            "object_name": "процесс / заявка / заказ / операция",
            "reason_name": "причины задержек",
            "effect_name": "сокращение длительности цикла и выявление узких мест",
            "mvp_focus": "выявление этапов, факторов и объектов, формирующих задержки",
            "data_requirements": "ID процесса / заявки, этап, статус, дата начала, дата завершения, ответственный, комментарий",
            "kpi_language": "сокращение времени цикла, снижение ручной работы, пригодность данных для регулярного анализа процесса",
        },
        "inventory_or_supply": {
            "case_type": "inventory_or_supply",
            "case_name": "анализ запасов и поставок",
            "business_problem": "потери из-за дефицитов, избыточных запасов, задержек поставок и ручного планирования",
            "loss_object": "дефициты / излишки / задержки поставок",
            "event_name": "движения запасов / поставки / заказы",
            "object_name": "SKU / материал / поставщик / склад",
            "reason_name": "причины дефицитов или задержек",
            "effect_name": "повышение прозрачности запасов и снижение управляемых потерь в снабжении",
            "mvp_focus": "ранжирование SKU, поставщиков или складов по вкладу в проблему",
            "data_requirements": "ID SKU, дата, остаток, заказ, поставка, потребление, поставщик, статус",
            "kpi_language": "снижение дефицитов / излишков, сокращение ручного анализа, пригодность данных для регулярного контроля",
        },
        "document_workflow": {
            "case_type": "document_workflow",
            "case_name": "анализ документооборота",
            "business_problem": "потери из-за ручной обработки документов, задержек согласования и ошибок классификации",
            "loss_object": "ручная обработка / задержки / ошибки документов",
            "event_name": "документы / заявки / согласования",
            "object_name": "документ / заявка / контрагент / процесс",
            "reason_name": "причины задержек или ошибок",
            "effect_name": "сокращение ручной обработки и ускорение прохождения документов",
            "mvp_focus": "классификация документов, выявление узких мест и подготовка управленческого отчёта",
            "data_requirements": "ID документа, тип, дата поступления, статус, ответственный, дата завершения, комментарий",
            "kpi_language": "сокращение ручной обработки, снижение ошибок, пригодность данных для регулярного контроля документооборота",
        },
        "customer_support": {
            "case_type": "customer_support",
            "case_name": "анализ клиентских обращений",
            "business_problem": "потери из-за ручной классификации обращений, долгих ответов и повторяющихся проблем клиентов",
            "loss_object": "длительность обработки / повторяющиеся обращения",
            "event_name": "обращения / тикеты / запросы",
            "object_name": "клиент / обращение / категория / канал",
            "reason_name": "темы и причины обращений",
            "effect_name": "ускорение обработки обращений и выявление повторяющихся проблем",
            "mvp_focus": "классификация обращений и ранжирование тем по вкладу в нагрузку",
            "data_requirements": "ID обращения, дата, канал, категория, текст, статус, время ответа, ответственный",
            "kpi_language": "сокращение ручной классификации, снижение времени реакции, пригодность данных для регулярного анализа обращений",
        },
    }

    return profiles.get(
        primary_case_type,
        {
            "case_type": "generic_industrial_ai",
            "case_name": "прикладной AI-кейс",
            "business_problem": "управляемые потери, ручной анализ и запаздывающие решения",
            "loss_object": "управляемые потери",
            "event_name": "события / операции / записи процесса",
            "object_name": "объект процесса / линия / участок / заявка",
            "reason_name": "причины / факторы проблемы",
            "effect_name": "снижение управляемых потерь и сокращение ручного анализа",
            "mvp_focus": "выявление повторяющихся причин / паттернов и ранжирование объектов или факторов по вкладу в проблему",
            "data_requirements": "ID объекта или события, дата / время, статус, категория, причина / комментарий, экономический показатель",
            "kpi_language": "снижение управляемых потерь, сокращение ручной работы, пригодность данных для регулярного анализа",
        },
    )


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

## Case Profile

Use this profile to adapt all client-facing wording.
Do not force downtime-specific language if case_profile.case_type is not downtime_analysis.

{json.dumps(data["case_profile"], ensure_ascii=False, indent=2)}

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
    Финальная нормализация D-004.

    D-004 теперь должен быть единым клиентским business value report,
    а не связкой "технический диагностический отчёт + КП".

    Основные задачи:
    - убрать legacy diagnostic report, если модель его всё ещё сгенерировала;
    - оставить один клиентский документ;
    - смягчить внутренние статусы;
    - не допустить фраз "файл нужно передать", если вложения уже есть;
    - не возвращать старую структуру data audit.
    """
    fixed = (result or "").strip()

    if not fixed:
        return fixed

    def strip_before_last_business_report(markdown: str) -> str:
        markers = [
            "# Итоговый отчёт AIha Consulting — Industrial AI",
            "# Итоговый отчет AIha Consulting — Industrial AI",
        ]

        last_position = -1

        for marker in markers:
            position = markdown.rfind(marker)

            if position > last_position:
                last_position = position

        if last_position > 0:
            return markdown[last_position:].strip()

        return markdown.strip()

    def remove_legacy_wrapper(markdown: str) -> str:
        legacy_markers = [
            "## Коммерческое предложение на следующий этап",
            "# Коммерческое предложение AIha Consulting — Industrial AI",
            "# Итоговый диагностический отчёт AIha Consulting — Industrial AI",
            "# Итоговый диагностический отчет AIha Consulting — Industrial AI",
        ]

        business_markers = [
            "# Итоговый отчёт AIha Consulting — Industrial AI",
            "# Итоговый отчет AIha Consulting — Industrial AI",
        ]

        for business_marker in business_markers:
            business_pos = markdown.rfind(business_marker)

            if business_pos > 0:
                for legacy_marker in legacy_markers:
                    legacy_pos = markdown.find(legacy_marker)

                    if legacy_pos >= 0 and legacy_pos < business_pos:
                        return markdown[business_pos:].strip()

        return markdown.strip()

    def extract_prompt_json_value(markdown: str, keys: list[str]) -> str:
        for key in keys:
            patterns = [
                rf'"{re.escape(key)}"\s*:\s*"([^"]+)"',
                rf"'{re.escape(key)}'\s*:\s*'([^']+)'",
            ]

            for pattern in patterns:
                match = re.search(pattern, markdown)

                if not match:
                    continue

                value = match.group(1).strip()

                if is_usable_prompt_value(value):
                    return value

        return ""

    def extract_prompt_table_value(markdown: str, first_cells: list[str]) -> str:
        for first_cell in first_cells:
            pattern = (
                rf"^\|\s*{re.escape(first_cell)}\s*\|\s*([^|\n]+?)\s*\|"
            )

            match = re.search(pattern, markdown, flags=re.MULTILINE)

            if not match:
                continue

            value = match.group(1).strip()

            if is_usable_prompt_value(value):
                return value

        return ""

    def is_usable_prompt_value(value: str) -> bool:
        normalized = value.strip().lower()

        if not normalized:
            return False

        blocked_values = {
            "none",
            "null",
            "unknown",
            "n/a",
            "не указано",
            "неизвестно",
            "требует уточнения",
            "частично подтверждено",
            "—",
            "-",
        }

        if normalized in blocked_values:
            return False

        return True

    def is_likely_action_not_object(value: str) -> bool:
        normalized = value.strip().lower()

        action_markers = [
            "мониторинг простоев",
            "выявление повторяющихся причин",
            "выявление причин",
            "анализ исторических данных",
            "формирование отчет",
            "формирование отчёт",
            "dashboard",
            "дашборд",
            "отчёт для владельца",
            "отчет для владельца",
        ]

        return any(marker in normalized for marker in action_markers)

    def clean_markdown_table_cell(value: str) -> str:
        cleaned = str(value or "").strip()
        cleaned = cleaned.replace("\n", " ")
        cleaned = cleaned.replace("|", "/")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def get_pilot_object_from_prompt(markdown: str) -> str:
        table_value = extract_prompt_table_value(
            markdown,
            [
                "Объект пилота",
                "Пилотный объект",
                "Объект анализа",
                "Процесс / объект анализа",
                "Текущий процесс / объект анализа",
            ],
        )

        if table_value and not is_likely_action_not_object(table_value):
            return clean_markdown_table_cell(table_value)

        json_value = extract_prompt_json_value(
            markdown,
            [
                "pilot_object",
                "pilot_area",
                "pilot_line",
                "production_area",
                "production_line",
                "process_object",
                "object_of_analysis",
                "target_object",
                "target_line",
                "workshop",
                "shop",
                "line",
                "area",
            ],
        )

        if json_value and not is_likely_action_not_object(json_value):
            return clean_markdown_table_cell(json_value)

        return "Пилотная линия / участок, указанные в Industrial AI Brief"

    fixed = remove_legacy_wrapper(fixed)
    fixed = strip_before_last_business_report(fixed)

    # Если модель всё равно оставила служебную обёртку в начале.
    fixed = re.sub(
        r"^\s*#\s*Экспресс-диагностика AIha Consulting\s*"
        r"(?:\n+##\s*Итоговый диагностический отч[её]т\s*)?",
        "",
        fixed,
        flags=re.IGNORECASE,
    ).strip()

    # Внутренний статус не должен доминировать в клиентском документе.
    fixed = fixed.replace(
        "GO_WITH_CONSTRAINTS",
        "рекомендуется переходить к подготовительному этапу и MVP-пилоту с ограничениями",
    )

    fixed = fixed.replace(
        "Итоговое решение | рекомендуется переходить к подготовительному этапу и MVP-пилоту с ограничениями",
        "Рекомендация | Перейти к подготовительному этапу и MVP-пилоту с ограничениями по данным, ИБ и подтверждению экономики",
    )

    # Если файлы уже были приложены / D-001 видел вложения, не просим “передать тестовый файл” как будто его нет.
    has_attachments = (
        '"attachments"' in prompt_input
        or '"attachment' in prompt_input
        or '"files_count"' in prompt_input
        or '"minimum_downtime_export_fields_present": true' in prompt_input
        or '"minimum_downtime_export_fields_present": True' in prompt_input
    )

    if has_attachments:
        fixed = fixed.replace(
            "тестовый файл нужно передать",
            "требуется подтвердить репрезентативность, стабильность формата и регулярность выгрузки",
        )
        fixed = fixed.replace(
            "Тестовая Excel / CSV выгрузка передана | частично выполнено | Возможность Excel / CSV выгрузки подтверждена, тестовый файл нужно передать",
            "Тестовая Excel / CSV выгрузка передана | частично выполнено | Файл / пример выгрузки получен; требуется подтвердить репрезентативность, стабильность формата и регулярность выгрузки",
        )
        fixed = fixed.replace(
            "Запросить обезличенные данные и подтвердить идентификаторы",
            "Подтвердить репрезентативность обезличенной выгрузки, стабильность формата, baseline и правила обработки данных",
        )

    # Если в prompt есть явные признаки минимальных downtime-полей, формулируем как валидацию, а не отсутствие данных.
    downtime_fields_present = all(
        field in prompt_input
        for field in [
            "event_id",
            "line_id",
            "equipment_id",
            "start_time",
            "end_time",
            "duration_min",
            "reason_code",
        ]
    )

    def replace_table_row_by_first_cell(
        markdown: str,
        first_cell: str,
        replacement_cell: str,
        *,
        expected_columns: int = 2,
        target_column_index: int = 1,
    ) -> str:
        """
        Заменяет одну ячейку markdown-таблицы по значению первой ячейки.

        По умолчанию работает для двухколоночных таблиц:
        | Элемент | Рекомендация |

        Для таблицы этапов:
        | Этап | Срок | Результат |
        вызывай с expected_columns=3, target_column_index=2.

        Не добавляет клиентские факты — только нормализует уже сгенерированную строку.
        """
        lines = markdown.splitlines()
        fixed_lines: list[str] = []

        for line in lines:
            stripped = line.strip()

            if not stripped.startswith("|") or not stripped.endswith("|"):
                fixed_lines.append(line)
                continue

            cells = [
                cell.strip()
                for cell in stripped.strip("|").split("|")
            ]

            if len(cells) != expected_columns:
                fixed_lines.append(line)
                continue

            if cells[0] != first_cell:
                fixed_lines.append(line)
                continue

            if target_column_index >= len(cells):
                fixed_lines.append(line)
                continue

            cells[target_column_index] = replacement_cell
            fixed_lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(fixed_lines)

    case_profile = build_case_profile(
        _extract_case_type_from_text(prompt_input)
    )

    case_type = case_profile.get("case_type", "generic_industrial_ai")
    is_downtime = case_type == "downtime_analysis"

    mvp_focus = case_profile.get(
        "mvp_focus",
        "выявление повторяющихся причин / паттернов и ранжирование объектов или факторов по вкладу в проблему",
    )
    kpi_language = case_profile.get(
        "kpi_language",
        "снижение управляемых потерь, сокращение ручной работы, пригодность данных для регулярного анализа",
    )
    effect_name = case_profile.get(
        "effect_name",
        "снижение управляемых потерь и сокращение ручного анализа",
    )
    data_requirements = case_profile.get(
        "data_requirements",
        "ID объекта или события, дата / время, статус, категория, причина / комментарий, экономический показатель",
    )

    downtime_fields_present = is_downtime and all(
        field in prompt_input
        for field in [
            "event_id",
            "line_id",
            "equipment_id",
            "start_time",
            "end_time",
            "duration_min",
            "reason_code",
        ]
    )

    if downtime_fields_present:
        fixed = fixed.replace(
            "Требуется уточнение идентификаторов и временных меток",
            "Ключевые поля представлены в тестовой структуре; требуется подтвердить их стабильность в регулярных выгрузках",
        )
        fixed = fixed.replace(
            "Неполные данные и отсутствие необходимых идентификаторов",
            "Требуется подтвердить стабильность идентификаторов, временных меток, baseline и регулярность выгрузки",
        )
        fixed = fixed.replace(
            "Подтвердить наличие стабильного ID события простоя или правило его формирования",
            "Проверить на тестовой выгрузке и зафиксировать правило формирования ID события простоя",
        )
        fixed = fixed.replace(
            "Начало простоя подтверждено, конец или длительность нужно проверить на тестовой выгрузке",
            "Начало, конец и длительность представлены в тестовой структуре; требуется подтвердить единый формат времени и timezone в регулярных выгрузках",
        )

    # Экономика должна оставаться гипотезой до baseline.
    fixed = fixed.replace(
        "Подтвержденная экономика",
        "Предварительная гипотеза, требует подтверждения baseline",
    )
    fixed = fixed.replace(
        "Подтверждённая экономика",
        "Предварительная гипотеза, требует подтверждения baseline",
    )
    fixed = fixed.replace(
        "может привести к значительной экономии.",
        "рассматривается как предварительная гипотеза; точный экономический эффект фиксируется после подтверждения baseline.",
    )

    fixed = fixed.replace(
        "Бюджет обсуждается после подтверждения экономики.",
        "Ориентир бюджета: подготовительный этап — 150 000–300 000 ₽; MVP-пилот — 500 000–1 200 000 ₽. Финальная цена фиксируется после подтверждения scope, данных, ИБ, критериев успеха и формата следующего этапа.",
    )
    fixed = fixed.replace(
        "Ориентир бюджета фиксируется после подтверждения scope, данных, ИБ, критериев успеха и формата следующего этапа.",
        "Ориентир бюджета: подготовительный этап — 150 000–300 000 ₽; MVP-пилот — 500 000–1 200 000 ₽. Финальная цена фиксируется после подтверждения scope, данных, ИБ, критериев успеха и формата следующего этапа.",
    )

    # Клиентский стиль: меньше шаблонности, больше консалтинговой конкретики.
    fixed = fixed.replace(
        "В данном отчёте рассматривается кейс",
        "Мы видим прикладной AI-кейс",
    )
    fixed = fixed.replace(
        "В данном отчете рассматривается кейс",
        "Мы видим прикладной AI-кейс",
    )
    fixed = fixed.replace(
        "Внедрение ИИ может привести к следующим потенциальным выигрышам:",
        "AI-пилот позволит проверить следующие источники бизнес-выигрыша:",
    )
    fixed = fixed.replace(
        "Внедрение ИИ может потенциально привести к следующим эффектам:",
        "AI-пилот позволит проверить следующие источники бизнес-выигрыша:",
    )
    fixed = fixed.replace(
        "Внедрение ИИ может потенциально привести к следующим выигрышам:",
        "AI-пилот позволит проверить следующие источники бизнес-выигрыша:",
    )
    fixed = fixed.replace(
        "Внедрение ИИ может привести к следующим эффектам:",
        "AI-пилот позволит проверить следующие источники бизнес-выигрыша:",
    )
    fixed = fixed.replace(
        "Внедрение ИИ даст клиенту следующие конкурентные преимущества:",
        "Практический конкурентный выигрыш для клиента:",
    )
    fixed = fixed.replace(
        "Внедрение ИИ предоставит клиенту следующие конкурентные преимущества:",
        "Практический конкурентный выигрыш для клиента:",
    )

    fixed = re.sub(
        r"(?:внедрение|Внедрение) ИИ может значительно [^.]+?\.",
        f"AI-пилот позволит проверить {effect_name} после подтверждения baseline.",
        fixed,
    )

    fixed = fixed.replace(
        "значительно повысить эффективность работы",
        "проверить потенциал повышения управляемости и сокращения потерь",
    )

    # Не используем слабую формулировку “уточнить у клиента”.
    fixed = fixed.replace(
        "Уточнить у клиента",
        "Проверить и зафиксировать на подготовительном этапе",
    )
    fixed = fixed.replace(
        "уточнить у клиента",
        "проверить и зафиксировать на подготовительном этапе",
    )
    fixed = fixed.replace(
        "Уточнить у финансового блока",
        "Проверить и согласовать с финансовым блоком",
    )
    fixed = fixed.replace(
        "уточнить у финансового блока",
        "проверить и согласовать с финансовым блоком",
    )

    # Для AIha Consulting не нужна “полная выгрузка”; нужна ограниченная репрезентативная выборка.
    fixed = fixed.replace(
        "Полная выгрузка данных",
        "Ограниченная обезличенная выгрузка за согласованный период",
    )
    fixed = fixed.replace(
        "полная выгрузка данных",
        "ограниченная обезличенная выгрузка за согласованный период",
    )
    fixed = fixed.replace(
        "полную выгрузку данных",
        "ограниченную обезличенную выгрузку за согласованный период",
    )

    # Коммерческий блок: результат должен звучать как продаваемый outcome, а не как техническая активность.
    fixed = fixed.replace(
        "Рабочий прототип",
        "MVP-артефакт",
    )
    fixed = fixed.replace(
        "рабочий прототип",
        "MVP-артефакт",
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
        "Формирование отчетов.",
        "Подготовка управленческого отчёта.",
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
        "Формирование отчёта для владельца процесса.",
        "Подготовка управленческого отчёта / dashboard для владельца процесса.",
    )
    fixed = fixed.replace(
        "Формирование отчета для владельца процесса.",
        "Подготовка управленческого отчёта / dashboard для владельца процесса.",
    )

    # Downtime-specific нормализация — только для кейса простоев.
    if is_downtime:
        fixed = fixed.replace(
            "что приведет к экономии на простоях",
            "при подтверждении baseline может дать экономический эффект за счёт снижения управляемых потерь",
        )
        fixed = fixed.replace(
            "что приведёт к экономии на простоях",
            "при подтверждении baseline может дать экономический эффект за счёт снижения управляемых потерь",
        )
        fixed = fixed.replace(
            "Снижение затрат на простои и ремонты.",
            "Снижение управляемых потерь после подтверждения baseline.",
        )
        fixed = fixed.replace(
            "Ускорение анализа причин простоев и принятия решений о ремонте.",
            "Более быстрое выявление повторяющихся причин и приоритетных зон для управленческих действий.",
        )
        fixed = fixed.replace(
            "Повышение стабильности работы оборудования за счёт выявления повторяющихся проблем.",
            "Повышение стабильности процесса за счёт раннего выявления повторяющихся причин потерь.",
        )
        fixed = fixed.replace(
            "Улучшение контроля над процессами и уменьшение зависимости от ручного ввода данных.",
            "Повышение управляемости: меньше зависимости от ручных журналов, разрозненных файлов и экспертной памяти.",
        )
        fixed = fixed.replace(
            "Анализ исторических данных о простоях.",
            "Проверка структуры исторических данных и расчёт baseline-гипотезы.",
        )
        fixed = fixed.replace(
            "Выявление причин отказов.",
            "AI-анализ повторяющихся причин и ранжирование объектов / факторов по вкладу в проблему.",
        )

        fixed = re.sub(
            r"Потенциальное снижение простоев на ([^.\n]+?) может сэкономить значительные средства\.",
            r"Потенциальное снижение простоев на \1 рассматривается как предварительная гипотеза; точный экономический эффект фиксируется после подтверждения baseline.",
            fixed,
        )
        fixed = re.sub(
            r"Снижение простоев на ([^.\n]+?) может сэкономить значительные средства\.",
            r"Снижение простоев на \1 рассматривается как предварительная гипотеза; точный экономический эффект фиксируется после подтверждения baseline.",
            fixed,
        )

        fixed = fixed.replace(
            "| Неполные данные | Отсутствие необходимых идентификаторов и временных меток может затруднить анализ. | Проверить и зафиксировать на подготовительном этапе. |",
            "| Неполные данные | Отсутствие стабильных идентификаторов и единых временных меток может затруднить расчёт baseline и анализ повторяющихся причин. | Проверить тестовую выгрузку, зафиксировать обязательные поля и правила формирования ID событий. |",
        )

    # Если модель ошибочно записала функцию MVP в строку "Объект пилота",
    # заменяем её на объект из prompt_input или безопасный универсальный fallback.
    pilot_object = get_pilot_object_from_prompt(prompt_input)

    fixed = re.sub(
        r"\|\s*Объект пилота\s*\|\s*Мониторинг простоев[^|\n]*\|",
        f"| Объект пилота | {pilot_object} |",
        fixed,
    )
    fixed = re.sub(
        r"\|\s*Объект пилота\s*\|\s*Анализ простоев[^|\n]*\|",
        f"| Объект пилота | {pilot_object} |",
        fixed,
    )

    # Универсальная полировка строк MVP через case_profile.
    fixed = replace_table_row_by_first_cell(
        fixed,
        "Что входит",
        f"Проверка структуры данных, {mvp_focus}, dashboard / управленческий отчёт для владельца процесса.",
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "KPI успеха",
        f"Проверить {kpi_language}; точные KPI фиксируются после baseline.",
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "MVP-пилот",
        f"MVP-артефакт: dashboard / аналитический отчёт / {mvp_focus}",
        expected_columns=3,
        target_column_index=2,
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "Подготовка данных",
        "Согласованная обезличенная выгрузка, правила ИБ и критерии успеха",
        expected_columns=3,
        target_column_index=2,
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "Тестирование",
        "Проверка качества, стабильности формата и применимости данных для MVP",
        expected_columns=3,
        target_column_index=2,
    )

    # Чистим дубли без добавления новых клиентских фактов.
    fixed = fixed.replace(
        "dashboard / аналитический отчёт / список повторяющихся причин, отчёт",
        "dashboard / аналитический отчёт / список повторяющихся причин",
    )
    fixed = fixed.replace(
        "dashboard / аналитический отчет / список повторяющихся причин, отчет",
        "dashboard / аналитический отчет / список повторяющихся причин",
    )
    fixed = fixed.replace(
        "dashboard / аналитический отчёт / список повторяющихся причин / dashboard / отчёт",
        "dashboard / аналитический отчёт / список повторяющихся причин",
    )
    fixed = fixed.replace(
        "dashboard / аналитический отчет / список повторяющихся причин / dashboard / отчет",
        "dashboard / аналитический отчет / список повторяющихся причин",
    )
    fixed = fixed.replace(
        "MVP-артефакт, отчёт",
        f"MVP-артефакт: dashboard / аналитический отчёт / {mvp_focus}",
    )
    fixed = fixed.replace(
        "MVP-артефакт, отчет",
        f"MVP-артефакт: dashboard / аналитический отчёт / {mvp_focus}",
    )

    # Уточняем следующий шаг: не просто “получить данные”, а согласовать business/data gate.
    fixed = fixed.replace(
        "Для этого необходимо получить ограниченную обезличенную выгрузку данных и подтвердить идентификаторы.",
        "Для этого необходимо согласовать ограниченную обезличенную выгрузку за выбранный период, подтвердить идентификаторы, baseline и критерии успеха MVP.",
    )
    fixed = fixed.replace(
        "Для этого нам потребуется получить ограниченную обезличенную выгрузку данных и согласовать условия обработки.",
        "Для этого необходимо согласовать ограниченную обезличенную выгрузку за выбранный период, критерии успеха MVP и правила обработки данных.",
    )
    fixed = fixed.replace(
        "получение ограниченной обезличенной выгрузки данных",
        "согласование ограниченной обезличенной выгрузки за выбранный период",
    )
    fixed = fixed.replace(
        "Обезличенные данные.",
        "Ограниченная обезличенная выгрузка за согласованный период.",
    )
    fixed = fixed.replace(
        "Обезличенные данные для анализа.",
        "Ограниченная обезличенная выгрузка за согласованный период.",
    )
    fixed = fixed.replace(
        "Обезличенные данные, подтверждение стабильности идентификаторов, согласование NDA.",
        "Ограниченная обезличенная выгрузка за согласованный период, подтверждение стабильности идентификаторов, baseline и правил обработки данных.",
    )
    fixed = fixed.replace(
        "Подтверждение идентификаторов и временных меток.",
        "Подтверждение идентификаторов, временных меток, baseline и правил обработки данных.",
    )
    fixed = fixed.replace(
        "Подтверждение стоимости часа простоя.",
        "Подтверждение baseline, стоимости потерь и правил обработки данных.",
    )
    fixed = fixed.replace(
        "Подтверждение стоимости часа простоя",
        "Подтверждение baseline, стоимости потерь и правил обработки данных",
    )

    # В клиентском сообщении требования к данным должны быть универсальными.
    fixed = fixed.replace(
        "Готовы согласовать состав обезличенной выгрузки, критерии успеха MVP и формат подготовительного этапа.",
        "Готовы согласовать состав обезличенной выгрузки, критерии успеха MVP и формат подготовительного этапа.",
    )

    # Следующий коммерческий шаг: не повторный анализ, а переход к MVP.
    fixed = fixed.replace(
        "## 9. Коммерческое предложение",
        "## 9. Возможный следующий коммерческий этап",
    )

    fixed = fixed.replace(
        "### 9.1. Что предлагаем",
        "### 9.1. Что предлагаем дальше",
    )

    fixed = fixed.replace(
        "- Анализ данных о простоях и выявление причин отказов.",
        "- Data & Scope Gate перед MVP: подтверждение scope, состава данных, ИБ, baseline и критериев успеха MVP.",
    )

    fixed = fixed.replace(
        "- Анализ простоев на производственной линии с целью выявления причин и уменьшения потерь.",
        "- Data & Scope Gate перед MVP: подтверждение scope, состава данных, ИБ, baseline и критериев успеха MVP.",
    )

    fixed = fixed.replace(
        "- Проведение анализа простоев на пилотной линии с использованием ИИ.",
        "- Data & Scope Gate перед MVP: подтверждение scope, состава данных, ИБ, baseline и критериев успеха MVP.",
    )

    fixed = fixed.replace(
        "### 9.2. Что входит",
        "### 9.2. Что входит в Data & Scope Gate",
    )

    fixed = fixed.replace(
        "- AI-анализ повторяющихся причин.",
        "- Проверка пригодности данных для MVP без глубокой BI-аналитики.",
    )

    fixed = fixed.replace(
        "- Dashboard / отчёт для владельца процесса.",
        "- Фиксация MVP scope, критериев успеха и требований к обезличенной выгрузке.",
    )

    fixed = fixed.replace(
        "- Подготовка управленческого отчёта / dashboard для владельца процесса.",
        "- Фиксация MVP scope, критериев успеха и требований к обезличенной выгрузке.",
    )

    fixed = fixed.replace(
        "### 9.4. Сроки",
        "### 9.4. Сроки следующего этапа",
    )

    fixed = fixed.replace(
        "- Подготовительный этап: 3–5 рабочих дней.",
        "- Data & Scope Gate перед MVP: 3–5 рабочих дней.",
    )

    fixed = fixed.replace(
        "- Подготовка данных: 3–5 рабочих дней.",
        "- Data & Scope Gate перед MVP: 3–5 рабочих дней.",
    )

    fixed = fixed.replace(
        "- MVP-пилот: 10–15 рабочих дней.",
        "- MVP-пилот: 10–15 рабочих дней после подтверждения данных, ИБ и критериев успеха.",
    )

    fixed = fixed.replace(
        "### 9.5. Бюджетный ориентир",
        "### 9.5. Бюджетный ориентир следующего этапа",
    )

    fixed = fixed.replace(
        "- Подготовительный этап: 150 000–300 000 ₽.",
        "- Data & Scope Gate перед MVP: 150 000–300 000 ₽.",
    )

    fixed = fixed.replace(
        "Ориентир бюджета: подготовительный этап — 150 000–300 000 ₽; MVP-пилот — 500 000–1 200 000 ₽.",
        "Ориентир бюджета: Data & Scope Gate перед MVP — 150 000–300 000 ₽; MVP-пилот — 500 000–1 200 000 ₽.",
    )

    fixed = fixed.replace(
        "Финальная цена фиксируется после подтверждения scope, данных, ИБ и критериев успеха.",
        "Финальная цена фиксируется после подтверждения scope, состава данных, правил ИБ, критериев успеха и формата MVP.",
    )

    fixed = fixed.replace(
        "Финальная цена фиксируется после подтверждения scope, данных, ИБ, критериев успеха и формата следующего этапа.",
        "Финальная цена фиксируется после подтверждения scope, состава данных, правил ИБ, критериев успеха и формата MVP.",
    )

    fixed = fixed.replace(
        "### 9.6. Что потребуется от клиента",
        "### 9.6. Что потребуется от клиента для следующего этапа",
    )

    fixed = fixed.replace(
        "- Обезличенная выгрузка данных.",
        "- Ограниченная обезличенная выгрузка за согласованный период.",
    )

    fixed = fixed.replace(
        "- Подтверждение baseline, стоимости потерь и правил обработки данных и стабильности идентификаторов.",
        "- Подтверждение baseline, стоимости потерь, стабильности идентификаторов и правил обработки данных.",
    )

    # Финальная case-aware полировка.
    fixed = fixed.replace(
        "Рассмотрен кейс",
        "Мы видим прикладной AI-кейс",
    )

    fixed = fixed.replace(
        "автоматизация мониторинга простоев и выявление причин отказов могут существенно снизить потери и повысить эффективность",
        "AI-пилот позволит проверить потенциал снижения управляемых потерь, сокращения ручного анализа и ускорения принятия решений после подтверждения baseline",
    )

    fixed = fixed.replace(
        "автоматизация мониторинга простоев и выявление причин отказов могут значительно снизить потери и повысить эффективность",
        "AI-пилот позволит проверить потенциал снижения управляемых потерь, сокращения ручного анализа и ускорения принятия решений после подтверждения baseline",
    )

    fixed = re.sub(
        r"\|\s*Количество событий простоев\s*\|\s*([^|\n]+?)\s*\|\s*Подтверждено\s*\|",
        r"| Количество событий простоев | \1 | Предварительно указано; требует подтверждения на baseline |",
        fixed,
    )

    fixed = re.sub(
        r"\|\s*Количество событий простоев\s*\|\s*([^|\n]+?)\s*\|\s*Подтверждено\s*\|",
        r"| Количество событий простоев | \1 | Предварительно указано; требует подтверждения на baseline |",
        fixed,
    )

    fixed = replace_table_row_by_first_cell(
        fixed,
        "Тестирование и корректировка",
        "Проверка качества, стабильности формата и применимости данных для MVP",
        expected_columns=3,
        target_column_index=2,
    )

    fixed = fixed.replace(
        "Для этого необходимо получить ограниченную обезличенную выгрузку данных и подтвердить ключевые параметры.",
        "Для этого необходимо согласовать ограниченную обезличенную выгрузку за выбранный период, подтвердить baseline, ключевые идентификаторы и критерии успеха MVP.",
    )

    fixed = fixed.replace(
        "Обезличенная выгрузка данных.",
        "Ограниченная обезличенная выгрузка за согласованный период.",
    )

    fixed = fixed.replace(
        "Подтверждение baseline, стоимости потерь и правил обработки данных и стабильности идентификаторов.",
        "Подтверждение baseline, стоимости потерь, стабильности идентификаторов и правил обработки данных.",
    )

    # Убрать эмоциональный CTA, если модель его вернула.
    fixed = fixed.replace(
        "С нетерпением ждем вашего ответа!",
        "Готовы согласовать состав обезличенной выгрузки, критерии успеха MVP и формат подготовительного этапа.",
    )
    fixed = fixed.replace(
        "С нетерпением ждём вашего ответа!",
        "Готовы согласовать состав обезличенной выгрузки, критерии успеха MVP и формат подготовительного этапа.",
    )

    # Если модель вернула две одинаковые горизонтальные линии подряд / лишние пустоты.
    fixed = re.sub(r"\n{3,}", "\n\n", fixed).strip()

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