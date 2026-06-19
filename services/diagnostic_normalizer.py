from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from db import get_db_connection


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _clean(value: Any, default: str = "не указано") -> str:
    if value is None:
        return default

    if isinstance(value, str):
        text = value.strip()
        return text if text else default

    return str(value).strip() or default


def _as_bool(value: Any) -> bool | None:
    text = _clean(value, default="").lower()

    if text in {"да", "yes", "true", "1"}:
        return True

    if text in {"нет", "no", "false", "0"}:
        return False

    return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [_clean(item) for item in value if _clean(item, "")]

    text = _clean(value, default="")
    return [text] if text else []


def _extract_number(value: Any) -> float | None:
    text = _clean(value, default="")

    if not text:
        return None

    normalized = (
        text.replace(" ", "")
        .replace("₽", "")
        .replace("руб.", "")
        .replace("руб", "")
        .replace(",", ".")
    )

    digits = []
    dot_used = False

    for char in normalized:
        if char.isdigit():
            digits.append(char)
        elif char == "." and not dot_used:
            digits.append(char)
            dot_used = True

    if not digits:
        return None

    try:
        return float("".join(digits))
    except ValueError:
        return None


def normalize_diagnostic_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    client = raw_payload.get("client") or {}
    diagnostic_goal = raw_payload.get("diagnostic_goal") or {}
    process = raw_payload.get("process") or {}
    data = raw_payload.get("data") or {}
    systems = raw_payload.get("systems") or {}
    integrations = raw_payload.get("integrations") or {}
    security = raw_payload.get("security") or {}
    economics = raw_payload.get("economics") or {}
    contacts = raw_payload.get("contacts") or {}
    confirmation = raw_payload.get("confirmation") or {}

    monthly_requests = _extract_number(economics.get("monthly_requests"))
    weekly_requests = _extract_number(economics.get("weekly_requests"))
    employees_involved = _extract_number(economics.get("employees_involved"))
    avg_processing_time = _extract_number(economics.get("avg_processing_time"))
    monthly_hours = _extract_number(economics.get("monthly_hours"))
    hour_cost = _extract_number(economics.get("hour_cost"))

    estimated_monthly_labor_cost = None
    if monthly_hours is not None and hour_cost is not None:
        estimated_monthly_labor_cost = monthly_hours * hour_cost

    return {
        "meta": {
            "normalized_at": _now_iso(),
            "normalizer_version": "diagnostic_normalizer_v1",
        },
        "client": {
            "company": _clean(client.get("company")),
            "contact_name": _clean(client.get("contact_name")),
            "contact_email": _clean(client.get("contact_email")),
            "process_owner": _clean(client.get("process_owner")),
        },
        "diagnostic_goal": {
            "automation_feasibility": _as_bool(diagnostic_goal.get("goal_automation")),
            "ai_feasibility": _as_bool(diagnostic_goal.get("goal_ai_feasibility")),
            "economics_validation": _as_bool(diagnostic_goal.get("goal_economics")),
            "bottleneck_analysis": _as_bool(diagnostic_goal.get("goal_bottlenecks")),
            "mvp_scope": _as_bool(diagnostic_goal.get("goal_mvp_scope")),
            "other": _clean(diagnostic_goal.get("goal_other")),
        },
        "process": {
            "process_name": _clean(process.get("process_name")),
            "description": _clean(process.get("process_description")),
            "main_problem": _clean(process.get("main_problem")),
            "request_start": _clean(process.get("request_start")),
            "request_channels": _as_list(process.get("request_channels")),
            "registration_place": _clean(process.get("registration_place")),
            "roles_description": _clean(process.get("roles_description")),
            "statuses_description": _clean(process.get("statuses_description")),
            "sla_description": _clean(process.get("sla_description")),
            "manual_operations": _clean(process.get("manual_operations")),
            "bottlenecks": _clean(process.get("bottlenecks")),
        },
        "data": {
            "excel_log_available": _as_bool(data.get("excel_log_available")),
            "data_period": _clean(data.get("data_period")),
            "approx_rows": _extract_number(data.get("approx_rows")),
            "data_owner": _clean(data.get("data_owner")),
            "has_request_id": _as_bool(data.get("has_request_id")),
            "has_timestamps": _clean(data.get("has_timestamps")),
            "has_statuses": _clean(data.get("has_statuses")),
            "has_responsible": _clean(data.get("has_responsible")),
            "has_category": _clean(data.get("has_category")),
            "has_result": _clean(data.get("has_result")),
            "has_free_text": _clean(data.get("has_free_text")),
            "data_quality_issues": _clean(data.get("data_quality_issues")),
        },
        "systems": {
            "systems_used": _as_list(systems.get("systems_used")),
            "systems_description": _clean(systems.get("systems_description")),
        },
        "integrations": {
            "api_available": _as_bool(integrations.get("api_available")),
            "exports_available": _as_bool(integrations.get("exports_available")),
            "manual_exchange": _as_bool(integrations.get("manual_exchange")),
            "integration_description": _clean(integrations.get("integration_description")),
            "it_contact": _clean(integrations.get("it_contact")),
        },
        "security": {
            "has_personal_data": _as_bool(security.get("has_personal_data")),
            "personal_data_description": _clean(security.get("personal_data_description")),
            "can_anonymize": _clean(security.get("can_anonymize")),
            "nda_required": _as_bool(security.get("nda_required")),
            "nda_signed": _clean(security.get("nda_signed")),
            "cloud_allowed": _clean(security.get("cloud_allowed")),
            "security_requirements": _clean(security.get("security_requirements")),
            "personal_data_requirements": _clean(security.get("personal_data_requirements")),
        },
        "economics": {
            "monthly_requests": monthly_requests,
            "weekly_requests": weekly_requests,
            "employees_involved": employees_involved,
            "avg_processing_time_raw": _clean(economics.get("avg_processing_time")),
            "avg_processing_time_number": avg_processing_time,
            "monthly_hours": monthly_hours,
            "hour_cost": hour_cost,
            "estimated_monthly_labor_cost": estimated_monthly_labor_cost,
            "losses_from_errors": _clean(economics.get("losses_from_errors")),
            "losses_from_delays": _clean(economics.get("losses_from_delays")),
            "expected_effect": _clean(economics.get("expected_effect")),
        },
        "contacts": {
            "process_contact": _clean(contacts.get("process_contact")),
            "data_contact": _clean(contacts.get("data_contact")),
            "it_contact": _clean(contacts.get("it_contact")),
            "security_contact": _clean(contacts.get("security_contact")),
            "finance_contact": _clean(contacts.get("finance_contact")),
        },
        "confirmation": {
            "data_usage_confirmed": _clean(confirmation.get("data_usage_confirmed")),
            "limitations": _clean(confirmation.get("limitations")),
            "responsible_person": _clean(confirmation.get("responsible_person")),
        },
        "client_questions": _clean(raw_payload.get("client_questions")),
        "readiness_flags": {
            "has_basic_process_description": bool(_clean(process.get("process_description"), "")),
            "has_economic_inputs": any(
                value is not None
                for value in [
                    monthly_requests,
                    weekly_requests,
                    employees_involved,
                    monthly_hours,
                    hour_cost,
                ]
            ),
            "has_data_structure_inputs": any(
                _clean(data.get(key), "")
                for key in [
                    "excel_log_available",
                    "has_request_id",
                    "has_timestamps",
                    "has_statuses",
                    "has_responsible",
                    "has_category",
                    "has_result",
                ]
            ),
            "has_integration_inputs": any(
                _clean(integrations.get(key), "")
                for key in [
                    "api_available",
                    "exports_available",
                    "manual_exchange",
                    "integration_description",
                ]
            ),
            "has_security_inputs": any(
                _clean(security.get(key), "")
                for key in [
                    "has_personal_data",
                    "can_anonymize",
                    "nda_required",
                    "cloud_allowed",
                    "security_requirements",
                ]
            ),
        },
    }


def normalize_input_pack(input_pack_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        input_pack = conn.execute(
            """
            SELECT *
            FROM diagnostic_input_packs
            WHERE id = ?
            """,
            (input_pack_id,),
        ).fetchone()

        if input_pack is None:
            raise ValueError(f"Diagnostic input pack not found: {input_pack_id}")

        raw_payload = json.loads(input_pack["raw_payload"])
        normalized_payload = normalize_diagnostic_payload(raw_payload)
        now = _now_iso()

        conn.execute(
            """
            UPDATE diagnostic_input_packs
            SET normalized_payload = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(normalized_payload, ensure_ascii=False, indent=2),
                now,
                input_pack_id,
            ),
        )

        conn.commit()

    return normalized_payload


def normalize_latest_input_pack_for_run(diagnostic_run_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        input_pack = conn.execute(
            """
            SELECT id
            FROM diagnostic_input_packs
            WHERE diagnostic_run_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if input_pack is None:
        raise ValueError(
            f"No diagnostic input pack found for diagnostic_run_id={diagnostic_run_id}"
        )

    return normalize_input_pack(int(input_pack["id"]))