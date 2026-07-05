from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from typing import Any
from pathlib import Path

from db import get_db_connection
from services.site_links import get_site_links


DIAGNOSTIC_STATUS_CREATED = "DIAGNOSTIC_CREATED"
DIAGNOSTIC_STATUS_FORM_SENT = "CLIENT_INPUT_FORM_SENT"
DIAGNOSTIC_STATUS_INPUT_RECEIVED = "CLIENT_INPUT_RECEIVED"
DIAGNOSTIC_STATUS_INPUT_NORMALIZED = "DIAGNOSTIC_INPUT_NORMALIZED"
DIAGNOSTIC_STATUS_COMPLETED = "COMPLETED"

DIAGNOSTIC_STATUS_D001_RUNNING = "D001_RUNNING"
DIAGNOSTIC_STATUS_D001_COMPLETED = "D001_COMPLETED"
DIAGNOSTIC_STATUS_D002_RUNNING = "D002_RUNNING"
DIAGNOSTIC_STATUS_D002_COMPLETED = "D002_COMPLETED"
DIAGNOSTIC_STATUS_D003_RUNNING = "D003_RUNNING"
DIAGNOSTIC_STATUS_D003_COMPLETED = "D003_COMPLETED"
DIAGNOSTIC_STATUS_D004_RUNNING = "D004_RUNNING"
DIAGNOSTIC_STATUS_D004_COMPLETED = "D004_COMPLETED"


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def generate_input_pack_token() -> str:
    return secrets.token_urlsafe(24)


def build_input_pack_url(token: str) -> str:
    site_links = get_site_links()
    consulting_url = site_links["consulting_url"].rstrip("/")
    return f"{consulting_url}/diagnostic/input-pack/{token}"


def create_diagnostic_run_for_lead(lead_id: int) -> dict[str, Any]:
    now = _now_iso()
    token = generate_input_pack_token()

    with get_db_connection() as conn:
        lead = conn.execute(
            """
            SELECT id, name, company
            FROM leads
            WHERE id = ?
            """,
            (lead_id,),
        ).fetchone()

        if lead is None:
            raise ValueError(f"Lead not found: {lead_id}")

        conn.execute(
            """
            INSERT INTO diagnostic_runs (
                lead_id,
                company,
                contact_name,
                contact_email,
                status,
                input_pack_token,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead["id"],
                lead["company"],
                lead["name"],
                None,
                DIAGNOSTIC_STATUS_CREATED,
                token,
                now,
                now,
            ),
        )

        diagnostic_run_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        conn.commit()

    return {
        "id": diagnostic_run_id,
        "lead_id": lead_id,
        "status": DIAGNOSTIC_STATUS_CREATED,
        "input_pack_token": token,
        "input_pack_url": build_input_pack_url(token),
    }


def get_diagnostic_run(diagnostic_run_id: int) -> dict[str, Any] | None:
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
        return None

    result = dict(row)
    result["input_pack_url"] = build_input_pack_url(result["input_pack_token"])
    return result


def get_diagnostic_run_by_token(token: str) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE input_pack_token = ?
            """,
            (token,),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    result["input_pack_url"] = build_input_pack_url(result["input_pack_token"])
    return result


def get_latest_diagnostic_run_for_lead(lead_id: int) -> dict[str, Any] | None:
    """
    Возвращает последнюю экспресс-диагностику по lead_id.
    Нужно, чтобы не создавать дубликаты при повторном нажатии кнопки.
    """
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE lead_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    result["input_pack_url"] = build_input_pack_url(result["input_pack_token"])
    return result


def get_diagnostic_runs_for_lead(lead_id: int) -> list[dict[str, Any]]:
    """
    Возвращает все диагностики по лиду.
    Используется для отображения в карточке лида.
    """
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE lead_id = ?
            ORDER BY id DESC
            """,
            (lead_id,),
        ).fetchall()

    result = []

    for row in rows:
        item = dict(row)
        item["input_pack_url"] = build_input_pack_url(item["input_pack_token"])
        result.append(item)

    return result


def update_diagnostic_status(
    diagnostic_run_id: int,
    status: str,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def mark_input_pack_sent(diagnostic_run_id: int) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET status = ?,
                input_pack_sent_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                DIAGNOSTIC_STATUS_FORM_SENT,
                now,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def _hydrate_input_pack_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None

    result = dict(row)

    if result.get("raw_payload"):
        try:
            result["raw_payload_json"] = json.loads(result["raw_payload"])
        except Exception:
            result["raw_payload_json"] = {}

    if result.get("normalized_payload"):
        try:
            result["normalized_payload_json"] = json.loads(
                result["normalized_payload"]
            )
        except Exception:
            result["normalized_payload_json"] = {}

    return result


def get_active_input_pack(
    diagnostic_run_id: int,
    brief_type: str = "diagnostic_input_pack",
) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
              AND brief_type = ?
              AND is_active = 1
            ORDER BY
                COALESCE(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (diagnostic_run_id, brief_type),
        ).fetchone()

    return _hydrate_input_pack_row(row)


def get_input_pack_attachments(input_pack_id: int) -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                diagnostic_run_id,
                input_pack_id,
                file_type,
                original_filename,
                stored_filename,
                file_path,
                uploaded_at
            FROM diagnostic_attachments
            WHERE input_pack_id = ?
            ORDER BY uploaded_at DESC, id DESC
            """,
            (input_pack_id,),
        ).fetchall()

    return [dict(row) for row in rows]

def get_diagnostic_attachment(
    attachment_id: int,
) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                diagnostic_run_id,
                input_pack_id,
                file_type,
                original_filename,
                stored_filename,
                file_path,
                uploaded_at
            FROM diagnostic_attachments
            WHERE id = ?
            """,
            (attachment_id,),
        ).fetchone()

    if row is None:
        return None

    return dict(row)


def delete_diagnostic_attachment(
    attachment_id: int,
    *,
    diagnostic_run_id: int | None = None,
    input_pack_id: int | None = None,
) -> bool:
    attachment = get_diagnostic_attachment(attachment_id)

    if attachment is None:
        return False

    if diagnostic_run_id is not None:
        if int(attachment["diagnostic_run_id"]) != int(diagnostic_run_id):
            return False

    if input_pack_id is not None:
        if int(attachment["input_pack_id"]) != int(input_pack_id):
            return False

    file_path = attachment.get("file_path")

    with get_db_connection() as conn:
        conn.execute(
            """
            DELETE FROM diagnostic_attachments
            WHERE id = ?
            """,
            (attachment_id,),
        )
        conn.commit()

    if file_path:
        try:
            path = Path(file_path)

            if path.exists() and path.is_file():
                path.unlink()
        except OSError:
            # DB-запись уже удалена. Файл мог быть перемещён или недоступен.
            # Не валим пользовательский сценарий из-за ошибки файловой системы.
            pass

    return True


def reset_diagnostic_results_after_input_change(
    diagnostic_run_id: int,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET
                d001_result = NULL,
                d001_completed_at = NULL,
                d002_result = NULL,
                d002_summary = NULL,
                d002_completed_at = NULL,
                d003_result = NULL,
                d003_summary = NULL,
                d003_completed_at = NULL,
                d004_result = NULL,
                d004_summary = NULL,
                d004_completed_at = NULL,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                DIAGNOSTIC_STATUS_INPUT_RECEIVED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def _row_value(row: Any, key: str, default: str = "") -> str:
    if row is None:
        return default

    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default

    if value is None:
        return default

    return str(value).strip()


def _extract_sections_from_lead_message(message: str) -> dict[str, str]:
    if not message:
        return {}

    known_labels = {
        "Компания",
        "Отрасль",
        "Размер компании",
        "Контактное лицо",
        "Телефон",
        "Email",
        "Бизнес-боль",
        "Процесс",
        "AI-сценарий",
        "AI-сценарий / ожидание",
        "Ожидаемый эффект",
        "Ожидаемый результат",
        "Приоритет",
        "Объём / частота / масштаб",
        "Объем / частота / масштаб",
        "Данные / документы / примеры",
        "Текущие системы",
        "Владелец процесса / контакт",
        "Экономика и метрики",
        "Ограничения",
        "Персональные данные",
        "Типы ПДн",
        "Можно обезличить",
        "Облако допустимо",
        "Требования к локализации",
        "Политики ИБ",
        "NDA",
        "Ограничения scope",
        "Комментарий",
    }

    sections: dict[str, list[str]] = {}
    current_label: str | None = None

    for raw_line in message.splitlines():
        line = raw_line.strip()

        match = re.match(r"^([^:\n]{2,90}):\s*(.*)$", line)

        if match:
            label = match.group(1).strip()
            value = match.group(2).strip()

            if label in known_labels:
                current_label = label
                sections.setdefault(current_label, [])

                if value:
                    sections[current_label].append(value)

                continue

        if current_label and line:
            sections[current_label].append(line)

    return {
        label: "\n".join(parts).strip()
        for label, parts in sections.items()
        if "\n".join(parts).strip()
    }

def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue

        text = str(value).strip()

        if text:
            return text

    return ""


def _extract_colon_fields(text: str) -> dict[str, str]:
    if not text:
        return {}

    fields: dict[str, list[str]] = {}
    current_label: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        match = re.match(r"^([^:\n]{2,100}):\s*(.*)$", line)

        if match:
            current_label = match.group(1).strip()
            value = match.group(2).strip()
            fields.setdefault(current_label, [])

            if value:
                fields[current_label].append(value)

            continue

        if current_label:
            fields[current_label].append(line)

    return {
        label: "\n".join(parts).strip()
        for label, parts in fields.items()
        if "\n".join(parts).strip()
    }


def _detect_request_channels(*texts: str) -> list[str]:
    joined = "\n".join(text for text in texts if text).lower()

    channels: list[str] = []

    if "email" in joined or "почт" in joined:
        channels.append("Email")

    if "telegram" in joined or "телеграм" in joined:
        channels.append("Telegram")

    if "телефон" in joined or "звон" in joined:
        channels.append("Телефон")

    if "1с" in joined or "1c" in joined:
        channels.append("1C")

    if "excel" in joined or "xlsx" in joined or "xls" in joined:
        channels.append("Excel")

    if "сайт" in joined or "форма" in joined:
        channels.append("Сайт / форма")

    return sorted(set(channels))


def _pick_colon_field(
    fields: dict[str, str],
    *labels: str,
) -> str:
    for label in labels:
        value = fields.get(label)

        if value:
            return value

    return ""


def _get_latest_constraints_for_lead(lead_id: int) -> Any:
    with get_db_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM client_constraints
            WHERE lead_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()


def _get_lead_for_diagnostic_run(diagnostic_run_id: int) -> tuple[Any, Any]:
    with get_db_connection() as conn:
        diagnostic_run = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

        if diagnostic_run is None:
            return None, None

        lead = conn.execute(
            """
            SELECT *
            FROM leads
            WHERE id = ?
            """,
            (diagnostic_run["lead_id"],),
        ).fetchone()

    return diagnostic_run, lead


def build_diagnostic_input_pack_payload_from_lead(
    *,
    lead: Any,
    constraints: Any,
) -> dict[str, Any]:
    message = _row_value(lead, "message")
    sections = _extract_sections_from_lead_message(message)

    constraints_roi_details = _row_value(constraints, "roi_metrics_details")

    combined_colon_fields = {}
    combined_colon_fields.update(_extract_colon_fields(message))
    combined_colon_fields.update(_extract_colon_fields(constraints_roi_details))

    company = _first_non_empty(
        sections.get("Компания"),
        _row_value(lead, "company"),
    )

    contact_name = _first_non_empty(
        sections.get("Контактное лицо"),
        _row_value(lead, "name"),
    )

    contact_phone = _first_non_empty(
        sections.get("Телефон"),
        _row_value(lead, "phone"),
    )

    contact_email = _first_non_empty(
        sections.get("Email"),
        _row_value(lead, "email"),
    )

    industry = _first_non_empty(
        sections.get("Отрасль"),
        _row_value(lead, "industry"),
    )

    business_pain = _first_non_empty(
        sections.get("Бизнес-боль"),
        _row_value(lead, "message"),
    )

    process = _first_non_empty(
        sections.get("Процесс"),
        _row_value(lead, "process"),
        "требует уточнения",
    )

    ai_type = _first_non_empty(
        sections.get("AI-сценарий"),
        sections.get("AI-сценарий / ожидание"),
        _row_value(lead, "ai_type"),
    )

    expected_effect = _first_non_empty(
        sections.get("Ожидаемый эффект"),
        sections.get("Ожидаемый результат"),
        _row_value(lead, "effect"),
    )

    volume_frequency_scale = _first_non_empty(
        sections.get("Объём / частота / масштаб"),
        sections.get("Объем / частота / масштаб"),
        _pick_colon_field(
            combined_colon_fields,
            "Объём / частота / масштаб",
            "Объем / частота / масштаб",
            "Объём операций в месяц",
            "Объем операций в месяц",
            "Объём заявок",
            "Объем заявок",
        ),
    )

    data_description = _first_non_empty(
        sections.get("Данные / документы / примеры"),
        _pick_colon_field(
            combined_colon_fields,
            "Данные / документы / примеры",
            "Данные",
            "Документы",
            "Примеры",
        ),
    )

    current_systems = _first_non_empty(
        sections.get("Текущие системы"),
        _pick_colon_field(
            combined_colon_fields,
            "Текущие системы",
            "Интеграции / текущие системы",
            "Интеграции",
            "Системы",
        ),
    )

    roi_details = _first_non_empty(
        constraints_roi_details,
        sections.get("Экономика и метрики"),
        volume_frequency_scale,
    )

    operations_volume = _first_non_empty(
        _pick_colon_field(
            combined_colon_fields,
            "Объём операций в месяц",
            "Объем операций в месяц",
            "Объём заявок",
            "Объем заявок",
            "Количество заявок",
        ),
        volume_frequency_scale,
    )

    avg_processing_time = _pick_colon_field(
        combined_colon_fields,
        "Среднее время обработки",
        "Среднее время обработки заявки",
        "Время обработки",
    )

    complex_operation_time = _pick_colon_field(
        combined_colon_fields,
        "Время на сложную операцию",
        "Ручной контроль и отчётность",
        "Контроль / отчётность / ручной перенос",
        "Контроль / отчетность / ручной перенос",
    )

    employees_in_process = _pick_colon_field(
        combined_colon_fields,
        "Сотрудники в процессе",
        "Участники процесса",
        "Роли",
    )

    cost_per_hour = _pick_colon_field(
        combined_colon_fields,
        "Стоимость часа / ФОТ",
        "Стоимость часа",
        "ФОТ",
    )

    errors_losses = _pick_colon_field(
        combined_colon_fields,
        "Ошибки / просрочки / потери",
        "Потери",
        "Просрочки",
        "Ошибки",
    )

    manual_reporting = _pick_colon_field(
        combined_colon_fields,
        "Контроль / отчётность / ручной перенос",
        "Контроль / отчетность / ручной перенос",
        "Ручная отчётность",
        "Ручная отчетность",
    )

    lost_request_cost = _pick_colon_field(
        combined_colon_fields,
        "Стоимость одной потерянной заявки",
        "Стоимость потерянной заявки",
        "Цена потерянной заявки",
    )

    sla_rules = _pick_colon_field(
        combined_colon_fields,
        "SLA / правила обработки",
        "SLA",
        "Правила SLA",
    )

    integrations_current_systems = _first_non_empty(
        _pick_colon_field(
            combined_colon_fields,
            "Интеграции / текущие системы",
            "Интеграции",
            "Текущие системы",
        ),
        current_systems,
    )

    request_channels = _detect_request_channels(
        message,
        current_systems,
        integrations_current_systems,
    )

    personal_data_types = _first_non_empty(
        _row_value(constraints, "personal_data_types"),
        sections.get("Типы ПДн"),
        _pick_colon_field(combined_colon_fields, "Типы ПДн"),
    )

    can_anonymize = _first_non_empty(
        _row_value(constraints, "can_anonymize"),
        sections.get("Можно обезличить"),
        _pick_colon_field(combined_colon_fields, "Можно обезличить"),
    )

    has_personal_data = _first_non_empty(
        _row_value(constraints, "has_personal_data"),
        sections.get("Персональные данные"),
        _pick_colon_field(combined_colon_fields, "Персональные данные"),
    )

    cloud_allowed = _first_non_empty(
        _row_value(constraints, "cloud_allowed"),
        sections.get("Облако допустимо"),
        _pick_colon_field(combined_colon_fields, "Облако допустимо"),
    )

    localization_requirements = _first_non_empty(
        _row_value(constraints, "localization_requirements"),
        sections.get("Требования к локализации"),
        _pick_colon_field(combined_colon_fields, "Требования к локализации"),
    )

    security_policies = _first_non_empty(
        _row_value(constraints, "security_policies"),
        sections.get("Политики ИБ"),
        _pick_colon_field(combined_colon_fields, "Политики ИБ"),
    )

    nda_status = _first_non_empty(
        _row_value(constraints, "nda_status"),
        _row_value(constraints, "nda_required"),
        sections.get("NDA"),
        _pick_colon_field(combined_colon_fields, "NDA"),
    )

    scope_limits = _first_non_empty(
        _row_value(constraints, "scope_limits"),
        _row_value(constraints, "scope_limitations"),
        sections.get("Ограничения scope"),
        _pick_colon_field(combined_colon_fields, "Ограничения scope"),
    )

    payload: dict[str, Any] = {
        "brief_type": "diagnostic_input_pack",
        "brief_version": "v1",
        "source": "lead_form_backfill",
        "client": {
            "company": company,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "contact_email": contact_email,
            "industry": industry,
            "process_owner": _first_non_empty(
                sections.get("Владелец процесса / контакт"),
                contact_name,
            ),
        },
        "diagnostic_goal": {
            "goal_automation": "Да",
            "goal_ai_feasibility": "Да",
            "goal_economics": "Да",
            "goal_bottlenecks": "",
            "goal_mvp_scope": "",
            "goal_other": "",
        },
        "process": {
            "process_name": process,
            "process_description": business_pain,
            "main_problem": business_pain,
            "expected_effect": expected_effect,
            "ai_scenario": ai_type,
            "request_start": "",
            "request_channels": request_channels,
            "registration_place": integrations_current_systems,
            "roles_desc": employees_in_process,
            "volume_frequency_scale": volume_frequency_scale,
        },
        "data": {
            "data_description": data_description,
            "data_examples": data_description,
            "personal_data_types": personal_data_types,
            "can_anonymize": can_anonymize,
        },
        "systems": {
            "current_systems": current_systems,
        },
        "integrations": {
            "integration_description": integrations_current_systems,
        },
        "security": {
            "has_personal_data": has_personal_data,
            "personal_data_types": personal_data_types,
            "can_anonymize": can_anonymize,
            "cloud_allowed": cloud_allowed,
            "localization_requirements": localization_requirements,
            "security_policies": security_policies,
            "nda_status": nda_status,
            "scope_limits": scope_limits,
        },
        "economics": {
            "roi_metrics_available": _row_value(
                constraints,
                "roi_metrics_available",
            ),
            "roi_metrics_details": roi_details,
            "operations_volume": operations_volume,
            "avg_processing_time": avg_processing_time,
            "complex_operation_time": complex_operation_time,
            "employees_in_process": employees_in_process,
            "cost_per_hour": cost_per_hour,
            "errors_losses": errors_losses,
            "manual_reporting": manual_reporting,
            "lost_request_cost": lost_request_cost,
            "sla_rules": sla_rules,
            "expected_business_effect": expected_effect,
            "budget_known": _row_value(constraints, "budget_known"),
            "mvp_readiness": _row_value(constraints, "mvp_readiness"),
            "expected_effect": expected_effect,
        },
        "contacts": {
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "contact_email": contact_email,
        },
        "client_questions": {
            "questions": "",
        },
        "confirmation": {
            "source_lead_id": _row_value(lead, "id"),
            "source_lead_created_at": _row_value(lead, "created_at"),
            "source_lead_source": _row_value(lead, "source"),
            "backfill_note": (
                "Initial Diagnostic Input Pack was created from lead form "
                "because no active diagnostic_input_pack existed for this diagnostic run."
            ),
        },
        "prefill_aliases": {
            "company": company,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "contact_email": contact_email,
            "industry": industry,
            "process_owner": _first_non_empty(
                sections.get("Владелец процесса / контакт"),
                contact_name,
            ),
            "process_name": process,
            "process_description": business_pain,
            "main_problem": business_pain,
            "expected_effect": expected_effect,
            "ai_scenario": ai_type,
            "request_start": "",
            "request_channels": request_channels,
            "registration_place": integrations_current_systems,
            "roles_desc": employees_in_process,
            "volume_frequency_scale": volume_frequency_scale,
            "data_description": data_description,
            "data_examples": data_description,
            "current_systems": current_systems,
            "integration_description": integrations_current_systems,
            "has_personal_data": has_personal_data,
            "personal_data_types": personal_data_types,
            "can_anonymize": can_anonymize,
            "cloud_allowed": cloud_allowed,
            "localization_requirements": localization_requirements,
            "security_policies": security_policies,
            "nda_status": nda_status,
            "scope_limits": scope_limits,
            "roi_metrics_available": _row_value(
                constraints,
                "roi_metrics_available",
            ),
            "roi_metrics_details": roi_details,
            "operations_volume": operations_volume,
            "avg_processing_time": avg_processing_time,
            "complex_operation_time": complex_operation_time,
            "employees_in_process": employees_in_process,
            "cost_per_hour": cost_per_hour,
            "errors_losses": errors_losses,
            "manual_reporting": manual_reporting,
            "lost_request_cost": lost_request_cost,
            "sla_rules": sla_rules,
            "expected_business_effect": expected_effect,
            "budget_known": _row_value(constraints, "budget_known"),
            "mvp_readiness": _row_value(constraints, "mvp_readiness"),
        },
    }

    return payload


def create_diagnostic_input_pack_from_lead_if_missing(
    diagnostic_run_id: int,
) -> int | None:
    existing = get_active_input_pack(
        diagnostic_run_id=diagnostic_run_id,
        brief_type="diagnostic_input_pack",
    )

    if existing is not None:
        return int(existing["id"])

    diagnostic_run, lead = _get_lead_for_diagnostic_run(diagnostic_run_id)

    if diagnostic_run is None or lead is None:
        return None

    constraints = _get_latest_constraints_for_lead(int(lead["id"]))

    payload = build_diagnostic_input_pack_payload_from_lead(
        lead=lead,
        constraints=constraints,
    )

    return upsert_active_input_pack(
        diagnostic_run_id=diagnostic_run_id,
        brief_type="diagnostic_input_pack",
        payload=payload,
        source="lead_form_backfill",
    )


def upsert_active_input_pack(
    diagnostic_run_id: int,
    brief_type: str,
    payload: dict[str, Any],
    source: str = "web_form",
) -> int:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    if not brief_type:
        raise ValueError("brief_type is required")

    now = _now_iso()

    payload_to_save = dict(payload)
    payload_to_save["brief_type"] = brief_type
    payload_to_save.setdefault("brief_version", "v1")
    payload_to_save.setdefault("submitted_at", now)

    requested_source = source or payload_to_save.get("source") or "web_form"

    with get_db_connection() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
              AND brief_type = ?
              AND is_active = 1
            ORDER BY
                COALESCE(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (diagnostic_run_id, brief_type),
        ).fetchone()

        if existing:
            input_pack_id = existing["id"]
            actual_source = (
                "web_form_update"
                if requested_source == "web_form"
                else requested_source
            )

            payload_to_save["source"] = actual_source
            raw_payload = json.dumps(payload_to_save, ensure_ascii=False)

            conn.execute(
                """
                UPDATE diagnostic_input_packs
                SET status = ?,
                    raw_payload = ?,
                    normalized_payload = NULL,
                    source = ?,
                    is_active = 1,
                    superseded_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    DIAGNOSTIC_STATUS_INPUT_RECEIVED,
                    raw_payload,
                    actual_source,
                    now,
                    input_pack_id,
                ),
            )

        else:
            actual_source = requested_source
            payload_to_save["source"] = actual_source
            raw_payload = json.dumps(payload_to_save, ensure_ascii=False)

            conn.execute(
                """
                UPDATE diagnostic_input_packs
                SET is_active = 0,
                    superseded_at = ?
                WHERE diagnostic_run_id = ?
                  AND brief_type = ?
                  AND is_active = 1
                """,
                (now, diagnostic_run_id, brief_type),
            )

            conn.execute(
                """
                INSERT INTO diagnostic_input_packs (
                    diagnostic_run_id,
                    status,
                    raw_payload,
                    created_at,
                    updated_at,
                    brief_type,
                    source,
                    is_active,
                    superseded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL)
                """,
                (
                    diagnostic_run_id,
                    DIAGNOSTIC_STATUS_INPUT_RECEIVED,
                    raw_payload,
                    now,
                    now,
                    brief_type,
                    actual_source,
                ),
            )

            input_pack_id = conn.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]

        conn.execute(
            """
            UPDATE diagnostic_runs
            SET status = ?,
                input_pack_received_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                DIAGNOSTIC_STATUS_INPUT_RECEIVED,
                now,
                now,
                diagnostic_run_id,
            ),
        )

        conn.commit()

    return input_pack_id


def save_client_input_pack(
    diagnostic_run_id: int,
    payload: dict[str, Any],
) -> int:
    brief_type = payload.get("brief_type") or "diagnostic_input_pack"
    source = payload.get("source") or "web_form"

    return upsert_active_input_pack(
        diagnostic_run_id=diagnostic_run_id,
        brief_type=brief_type,
        payload=payload,
        source=source,
    )


def save_diagnostic_attachment(
    diagnostic_run_id: int,
    input_pack_id: int,
    file_type: str,
    original_filename: str,
    stored_filename: str,
    file_path: str,
) -> int:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO diagnostic_attachments (
                diagnostic_run_id,
                input_pack_id,
                file_type,
                original_filename,
                stored_filename,
                file_path,
                uploaded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                diagnostic_run_id,
                input_pack_id,
                file_type,
                original_filename,
                stored_filename,
                file_path,
                now,
            ),
        )

        attachment_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        conn.commit()

    return attachment_id


def get_latest_input_pack(diagnostic_run_id: int) -> dict[str, Any] | None:
    """
    Backward-compatible helper.

    Возвращает последнюю активную форму по diagnostic_run_id.
    Для точной работы с конкретной формой используйте
    get_active_input_pack(..., brief_type).
    """
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
              AND is_active = 1
            ORDER BY
                COALESCE(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()

    return _hydrate_input_pack_row(row)


def save_d001_result(
    diagnostic_run_id: int,
    result: str,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET d001_result = ?,
                d001_completed_at = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                result,
                now,
                DIAGNOSTIC_STATUS_D001_COMPLETED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def save_d002_result(
    diagnostic_run_id: int,
    result: str,
    summary: str | None = None,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET d002_result = ?,
                d002_summary = ?,
                d002_completed_at = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                result,
                summary,
                now,
                DIAGNOSTIC_STATUS_D002_COMPLETED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def save_d003_result(
    diagnostic_run_id: int,
    result: str,
    summary: str | None = None,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET d003_result = ?,
                d003_summary = ?,
                d003_completed_at = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                result,
                summary,
                now,
                DIAGNOSTIC_STATUS_D003_COMPLETED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def complete_diagnostic_run(diagnostic_run_id: int) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                DIAGNOSTIC_STATUS_COMPLETED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()


def save_d004_result(
    diagnostic_run_id: int,
    result: str,
    summary: str | None = None,
) -> None:
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE diagnostic_runs
            SET d004_result = ?,
                d004_summary = ?,
                d004_completed_at = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                result,
                summary,
                now,
                DIAGNOSTIC_STATUS_D004_COMPLETED,
                now,
                diagnostic_run_id,
            ),
        )
        conn.commit()