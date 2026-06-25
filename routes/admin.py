from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlencode

from dotenv import load_dotenv
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from services.diagnostic_final_outputs import get_diagnostic_final_outputs

from db import DB_PATH
from export_leads_xlsx import export_leads_to_xlsx
from services.ai_agent import run_ai_agent_for_task
from services.final_outputs import get_final_outputs
from services.intake_blocks import (
    build_task_input_block,
    get_lead_with_constraints,
    get_task_for_lead,
)
from services.mvp_design import run_d002_mvp_design
from services.diagnostic_report import run_d003_diagnostic_report
from services.commercial_proposal import run_d004_commercial_proposal
from services.diagnostic_assessment import run_d001_diagnostic_assessment
from services.tasks import (
    create_next_task_after_update,
    get_default_done_next_action,
    update_agent_task,
)
from services.diagnostics import (
    create_diagnostic_run_for_lead,
    get_diagnostic_runs_for_lead,
    get_latest_diagnostic_run_for_lead,
)

load_dotenv()

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")


LEAD_STATUSES = {
    "new": "Новая",
    "in_progress": "В работе",
    "qualified": "Квалифицирована",
    "proposal": "Коммерческое предложение",
    "implementation": "Внедрение",
    "completed": "Завершено",
    "archive": "Архив",
}


AI_RUN_ALLOWED_STAGES = {
    "Intake Completeness",
    "Risk Assessment",
    "Economics Assessment",
    "Final Output",
}


RESULT_REQUIRED_FOR_DONE_STAGES = {
    "Intake Completeness",
    "Risk Assessment",
    "Economics Assessment",
    "Final Output",
    "Client Delivery",
}


TASK_STATUSES = {
    "New",
    "In Progress",
    "Waiting for Client Info",
    "Waiting for Data",
    "Waiting for Human Review",
    "Ready for Intake Agent",
    "Done",
    "Blocked",
    "Cancelled",
}


LEAD_SELECT_SQL = """
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
"""


CONSTRAINTS_SELECT_SQL = """
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
"""


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

def admin_required() -> bool:
    return session.get("admin_logged_in") is True

def redirect_with_error(path: str, error: str):
    query = urlencode({"error": error})
    return redirect(f"{path}?{query}")

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def normalize_form_value(value: str | None, default: str = "не указано") -> str:
    text = (value or "").strip()
    return text if text else default

def is_empty_value(value: str) -> bool:
    return value.strip() in {
        "",
        "не указано",
        "Не указано",
        "none",
        "None",
        "null",
        "NULL",
    }

def get_task_meta(task_id: int) -> sqlite3.Row | None:
    """
    Возвращает минимальные данные задачи для route-логики.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        return conn.execute(
            """
            SELECT
                id,
                lead_id,
                stage,
                status
            FROM agent_tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()


def get_lead_detail_data(
    lead_id: int,
) -> tuple[sqlite3.Row | None, sqlite3.Row | None, list[sqlite3.Row]]:
    """
    Возвращает данные карточки лида: lead, constraints, tasks.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        lead = conn.execute(
            f"""
            {LEAD_SELECT_SQL}
            WHERE id = ?
            """,
            (lead_id,),
        ).fetchone()

        constraints = conn.execute(
            f"""
            {CONSTRAINTS_SELECT_SQL}
            WHERE lead_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()

        tasks = conn.execute(
            f"""
            {TASK_SELECT_SQL}
            WHERE lead_id = ?
            ORDER BY id ASC
            """,
            (lead_id,),
        ).fetchall()

    return lead, constraints, tasks


def get_first_task_id_for_lead(lead_id: int) -> int | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT id
            FROM agent_tasks
            WHERE lead_id = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()

    if row is None:
        return None

    return int(row[0])

def build_roi_details_with_extra_fields(
    *,
    roi_metrics_details: str,
    lost_request_value: str,
    complex_request_share: str,
    sla_rules: str,
    integrations: str,
) -> str:
    """
    Добавляет дополнительные уточнения к roi_metrics_details.

    Важно: не дублирует строки, если форма сохраняется несколько раз.
    """
    base_text = roi_metrics_details.strip()
    extra_lines: list[str] = []

    if lost_request_value:
        extra_lines.append(f"Стоимость одной потерянной заявки: {lost_request_value}")

    if complex_request_share:
        extra_lines.append(f"Доля сложных заявок: {complex_request_share}")

    if sla_rules:
        extra_lines.append(f"SLA / правила обработки: {sla_rules}")

    if integrations:
        extra_lines.append(f"Интеграции / текущие системы: {integrations}")

    if not extra_lines:
        return base_text or "не указано"

    existing_text = base_text if base_text != "не указано" else ""
    new_lines = [line for line in extra_lines if line not in existing_text]

    if not new_lines:
        return base_text or "не указано"

    extra_text = "\n".join(new_lines)

    if existing_text:
        return f"{existing_text}\n\nДополнительные уточнения:\n{extra_text}"

    return f"Дополнительные уточнения:\n{extra_text}"


@admin_bp.route("/login", methods=["GET", "POST"])
def admin_login():
    error = ""

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect("/admin/leads")

        error = "Неверный логин или пароль."

    return render_template("admin_login.html", error=error)


@admin_bp.get("/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect("/admin/login")


@admin_bp.get("/leads")
def admin_leads():
    if not admin_required():
        return redirect("/admin/login")

    status_filter = request.args.get("status", "").strip()
    priority_filter = request.args.get("priority", "").strip()
    source_filter = request.args.get("source", "").strip()
    search_query = request.args.get("q", "").strip()

    query = f"""
        {LEAD_SELECT_SQL}
        WHERE 1 = 1
    """

    params: list[str] = []

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)

    if priority_filter:
        query += " AND priority = ?"
        params.append(priority_filter)

    if source_filter:
        query += " AND source = ?"
        params.append(source_filter)

    if search_query:
        query += """
            AND (
                name LIKE ?
                OR phone LIKE ?
                OR company LIKE ?
                OR message LIKE ?
                OR source LIKE ?
            )
        """
        search_value = f"%{search_query}%"
        params.extend(
            [
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
            ]
        )

    query += " ORDER BY id DESC"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        leads = conn.execute(query, params).fetchall()

    return render_template(
        "admin_leads.html",
        leads=leads,
        statuses=LEAD_STATUSES,
        current_status=status_filter,
        current_priority=priority_filter,
        current_source=source_filter,
        current_query=search_query,
        priorities=["Высокий", "Средний", "Низкий"],
        sources=[
            ("", "Все источники"),
            ("landing", "AIha Studio / landing"),
            ("landing_form", "AIha Studio / форма"),
            ("aiha_consulting_audit_form", "AIha Consulting / аудит"),
            ("callback_widget", "Callback"),
            ("telegram", "Telegram"),
        ],
    )

def _diagnostic_value(diagnostic_run, key: str, default=None):
    if diagnostic_run is None:
        return default

    if isinstance(diagnostic_run, dict):
        return diagnostic_run.get(key, default)

    if hasattr(diagnostic_run, "keys") and key in diagnostic_run.keys():
        return diagnostic_run[key]

    return getattr(diagnostic_run, key, default)


def _get_latest_diagnostic_run(diagnostic_runs):
    if not diagnostic_runs:
        return None

    return max(
        diagnostic_runs,
        key=lambda run: _diagnostic_value(run, "id", 0) or 0,
    )


def _build_diagnostic_brief_links(diagnostic_run):
    input_pack_url = _diagnostic_value(diagnostic_run, "input_pack_url")

    input_pack_token = (
        _diagnostic_value(diagnostic_run, "input_pack_token")
        or _diagnostic_value(diagnostic_run, "token")
    )

    if not input_pack_token and input_pack_url:
        input_pack_token = input_pack_url.rstrip("/").split("/")[-1]

    if not input_pack_url and input_pack_token:
        input_pack_url = url_for(
            "diagnostic.input_pack",
            token=input_pack_token,
            _external=True,
        )

    industrial_ai_brief_url = None
    if input_pack_token:
        industrial_ai_brief_url = url_for(
            "diagnostic.industrial_ai_brief",
            token=input_pack_token,
            _external=True,
        )

    return {
        "input_pack_url": input_pack_url,
        "industrial_ai_brief_url": industrial_ai_brief_url,
    }

@admin_bp.get("/leads/<int:lead_id>")
def admin_lead_detail(lead_id: int):
    if not admin_required():
        return redirect("/admin/login")

    lead, constraints, tasks = get_lead_detail_data(lead_id)

    if lead is None:
        return "Заявка не найдена", 404

    diagnostic_runs = get_diagnostic_runs_for_lead(lead_id)
    latest_diagnostic_run = _get_latest_diagnostic_run(diagnostic_runs)

    has_completed_t004 = any(
        task["task_code"] == "T-004" and task["status"] == "Done"
        for task in tasks
    )

    diagnostic_brief_links = None
    if latest_diagnostic_run is not None:
        diagnostic_brief_links = _build_diagnostic_brief_links(
            latest_diagnostic_run
        )

    return render_template(
        "admin_lead_detail.html",
        lead=lead,
        constraints=constraints,
        tasks=tasks,
        diagnostic_runs=diagnostic_runs,
        latest_diagnostic_run=latest_diagnostic_run,
        diagnostic_brief_links=diagnostic_brief_links,
        has_completed_t004=has_completed_t004,
        statuses=LEAD_STATUSES,
        ai_run_allowed_stages=AI_RUN_ALLOWED_STAGES,
    )

def _build_diagnostic_brief_links(diagnostic_run):
    input_pack_url = diagnostic_run.get("input_pack_url")

    input_pack_token = (
        diagnostic_run.get("input_pack_token")
        or diagnostic_run.get("token")
    )

    if not input_pack_token and input_pack_url:
        input_pack_token = input_pack_url.rstrip("/").split("/")[-1]

    if not input_pack_url and input_pack_token:
        input_pack_url = url_for(
            "diagnostic.input_pack",
            token=input_pack_token,
            _external=True,
        )

    industrial_ai_brief_url = None
    if input_pack_token:
        industrial_ai_brief_url = url_for(
            "diagnostic.industrial_ai_brief",
            token=input_pack_token,
            _external=True,
        )

    return input_pack_url, industrial_ai_brief_url

@admin_bp.post("/leads/<int:lead_id>/create-diagnostic")
def create_diagnostic_for_lead(lead_id: int):
    if not admin_required():
        return redirect("/admin/login")

    existing_diagnostic = get_latest_diagnostic_run_for_lead(lead_id)

    if existing_diagnostic is not None:
        input_pack_url, industrial_ai_brief_url = _build_diagnostic_brief_links(
            existing_diagnostic
        )

        flash(
            "Экспресс-диагностика для этого лида уже создана. "
            f"Diagnostic Input Pack: {input_pack_url} | "
            f"Industrial AI Brief: {industrial_ai_brief_url}",
            "info",
        )
        return redirect(url_for("admin.admin_lead_detail", lead_id=lead_id))

    try:
        diagnostic_run = create_diagnostic_run_for_lead(lead_id)
    except Exception as exc:
        flash(f"Ошибка создания экспресс-диагностики: {exc}", "error")
        return redirect(url_for("admin.admin_lead_detail", lead_id=lead_id))

    input_pack_url, industrial_ai_brief_url = _build_diagnostic_brief_links(
        diagnostic_run
    )

    flash(
        "Экспресс-диагностика создана. "
        f"Diagnostic Input Pack: {input_pack_url} | "
        f"Industrial AI Brief: {industrial_ai_brief_url}",
        "success",
    )

    return redirect(url_for("admin.admin_lead_detail", lead_id=lead_id))

@admin_bp.post("/diagnostic/<int:diagnostic_run_id>/run-d001")
def admin_run_d001(diagnostic_run_id: int):
    if not admin_required():
        return redirect("/admin/login")

    try:
        run_d001_diagnostic_assessment(
            diagnostic_run_id=diagnostic_run_id,
            force_rebuild=True,
        )

    except Exception as exc:
        return redirect_with_error(
            "/admin/leads",
            f"D-001 failed: {exc}",
        )

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            """
            SELECT lead_id
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if row:
        return redirect(f"/admin/leads/{row['lead_id']}")

    return redirect("/admin/leads")

@admin_bp.get("/diagnostic/<int:diagnostic_run_id>/d001")
def admin_d001_result(diagnostic_run_id: int):
    if not admin_required():
        return redirect("/admin/login")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        diagnostic = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if diagnostic is None:
        return "Diagnostic not found", 404

    return render_template(
        "admin_d001_result.html",
        diagnostic=diagnostic,
    )

@admin_bp.post("/diagnostic/<int:diagnostic_run_id>/run-d002")
def admin_run_d002(diagnostic_run_id: int):
    if not admin_required():
        return redirect("/admin/login")

    lead_id = request.form.get("lead_id", "").strip()
    force_rebuild = request.form.get("force_rebuild") == "1"

    try:
        run_d002_mvp_design(
            diagnostic_run_id=diagnostic_run_id,
            force_rebuild=force_rebuild,
        )
        flash("D-002 MVP Design успешно выполнен.", "success")
    except Exception as exc:
        flash(f"Ошибка запуска D-002: {exc}", "error")

    if lead_id:
        return redirect(url_for("admin.admin_lead_detail", lead_id=int(lead_id)))

    return redirect("/admin/leads")

@admin_bp.get("/diagnostic/<int:diagnostic_run_id>/d002")
def admin_d002_result(diagnostic_run_id: int):
    if not admin_required():
        return redirect("/admin/login")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        diagnostic = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if diagnostic is None:
        return "Экспресс-диагностика не найдена", 404

    return render_template(
        "admin_d002_result.html",
        diagnostic=diagnostic,
    )

@admin_bp.post("/diagnostic/<int:diagnostic_run_id>/run-d003")
def admin_run_d003(diagnostic_run_id: int):
    if not admin_required():
        return redirect("/admin/login")

    lead_id = request.form.get("lead_id", "").strip()
    force_rebuild = request.form.get("force_rebuild") == "1"

    try:
        run_d003_diagnostic_report(
            diagnostic_run_id=diagnostic_run_id,
            force_rebuild=force_rebuild,
        )
        flash("D-003 Diagnostic Report успешно выполнен.", "success")
    except Exception as exc:
        flash(f"Ошибка запуска D-003: {exc}", "error")

    if lead_id:
        return redirect(url_for("admin.admin_lead_detail", lead_id=int(lead_id)))

    return redirect("/admin/leads")

@admin_bp.get("/diagnostic/<int:diagnostic_run_id>/d003")
def admin_d003_result(diagnostic_run_id: int):
    if not admin_required():
        return redirect("/admin/login")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        diagnostic = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if diagnostic is None:
        return "Экспресс-диагностика не найдена", 404

    return render_template(
        "admin_d003_result.html",
        diagnostic=diagnostic,
    )

@admin_bp.post("/diagnostic/<int:diagnostic_run_id>/run-d004")
def admin_run_d004(diagnostic_run_id: int):
    if not admin_required():
        return redirect("/admin/login")

    lead_id = request.form.get("lead_id", "").strip()

    try:
        force_rebuild = (
            request.form.get("force_rebuild", "0") == "1"
        )

        run_d004_commercial_proposal(
            diagnostic_run_id=diagnostic_run_id,
            force_rebuild=force_rebuild,
        )

    except Exception as exc:
        if lead_id:
            return redirect_with_error(
                f"/admin/leads/{lead_id}",
                f"D-004 error: {exc}",
            )

        return str(exc), 500

    if lead_id:
        return redirect(f"/admin/leads/{lead_id}")

    return redirect("/admin/leads")

@admin_bp.get("/diagnostic/<int:diagnostic_run_id>/d004")
def admin_d004_result(diagnostic_run_id: int):
    if not admin_required():
        return redirect("/admin/login")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        diagnostic = conn.execute(
            """
            SELECT *
            FROM diagnostic_runs
            WHERE id = ?
            """,
            (diagnostic_run_id,),
        ).fetchone()

    if diagnostic is None:
        return "Diagnostic run not found", 404

    return render_template(
        "admin_d004_result.html",
        diagnostic=diagnostic,
    )

@admin_bp.get("/diagnostic/<int:diagnostic_run_id>/final")
def admin_diagnostic_final_outputs(diagnostic_run_id: int):
    if not admin_required():
        return redirect("/admin/login")

    outputs = get_diagnostic_final_outputs(diagnostic_run_id)

    if outputs is None:
        return "Экспресс-диагностика не найдена", 404

    return render_template(
        "admin_diagnostic_final_outputs.html",
        diagnostic=outputs["diagnostic"],
        lead=outputs["lead"],
        full_output=outputs["full_output"],
        client_output=outputs["client_output"],
    )

@admin_bp.post("/leads/<int:lead_id>/constraints/update")
def admin_update_constraints(lead_id: int):
    if not admin_required():
        return redirect("/admin/login")

    data = request.form.to_dict()

    has_personal_data = normalize_form_value(data.get("has_personal_data"))
    personal_data_types = normalize_form_value(data.get("personal_data_types"))
    can_anonymize = normalize_form_value(data.get("can_anonymize"))
    cloud_allowed = normalize_form_value(data.get("cloud_allowed"))
    localization_requirements = normalize_form_value(data.get("localization_requirements"))
    security_policies = normalize_form_value(data.get("security_policies"))
    nda_required = normalize_form_value(data.get("nda_required"))

    roi_metrics_available = normalize_form_value(data.get("roi_metrics_available"))
    roi_metrics_details = normalize_form_value(data.get("roi_metrics_details"))

    lost_request_value = (data.get("lost_request_value") or "").strip()
    complex_request_share = (data.get("complex_request_share") or "").strip()
    sla_rules = (data.get("sla_rules") or "").strip()
    integrations = (data.get("integrations") or "").strip()

    roi_metrics_details = build_roi_details_with_extra_fields(
        roi_metrics_details=roi_metrics_details,
        lost_request_value=lost_request_value,
        complex_request_share=complex_request_share,
        sla_rules=sla_rules,
        integrations=integrations,
    )

    budget_known = normalize_form_value(data.get("budget_known"))
    mvp_readiness = normalize_form_value(data.get("mvp_readiness"))
    scope_limitations = normalize_form_value(data.get("scope_limitations"))

    constraint_risk = normalize_form_value(data.get("constraint_risk"))
    next_action = normalize_form_value(data.get("next_action"))
    comment = normalize_form_value(data.get("comment"))

    now = utc_now()

    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM client_constraints
            WHERE lead_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE client_constraints
                SET
                    has_personal_data = ?,
                    personal_data_types = ?,
                    can_anonymize = ?,
                    cloud_allowed = ?,
                    localization_requirements = ?,
                    security_policies = ?,
                    nda_required = ?,
                    roi_metrics_available = ?,
                    roi_metrics_details = ?,
                    budget_known = ?,
                    mvp_readiness = ?,
                    scope_limitations = ?,
                    constraint_risk = ?,
                    next_action = ?,
                    comment = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
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
                    now,
                    existing[0],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO client_constraints (
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
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
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
                    now,
                    now,
                ),
            )

        conn.commit()

    return redirect(f"/admin/leads/{lead_id}")


@admin_bp.get("/leads/<int:lead_id>/final")
def admin_final_outputs(lead_id: int):
    if not admin_required():
        return redirect("/admin/login")

    outputs = get_final_outputs(lead_id)

    if outputs is None:
        return "Заявка не найдена", 404

    return render_template(
        "admin_final_outputs.html",
        lead=outputs["lead"],
        constraints=outputs["constraints"],
        tasks=outputs["tasks"],
        workflow_type=outputs["workflow_type"],
        final_output_task=outputs["final_output_task"],
        report_task=outputs["report_task"],
        human_review_task=outputs["human_review_task"],
        commercial_proposal_task=outputs["commercial_proposal_task"],
        final_report=outputs["final_report"],
        commercial_proposal=outputs["commercial_proposal"],
    )


@admin_bp.get("/leads/<int:lead_id>/tasks/<int:task_id>/input")
def admin_task_input(lead_id: int, task_id: int):
    if not admin_required():
        return redirect("/admin/login")

    lead, constraints = get_lead_with_constraints(lead_id)
    task = get_task_for_lead(lead_id, task_id)

    if lead is None:
        return "Заявка не найдена", 404

    if task is None:
        return "Задача не найдена", 404

    try:
        input_block = build_task_input_block(
            lead=lead,
            constraints=constraints,
            task=task,
        )
    except ValueError as exc:
        return str(exc), 400

    return render_template(
        "admin_task_input.html",
        lead=lead,
        task=task,
        input_block=input_block,
    )


@admin_bp.get("/leads/<int:lead_id>/intake-input")
def admin_lead_intake_input(lead_id: int):
    """
    Старый URL оставлен для совместимости.
    Теперь Intake v3.3 не используется.

    Редиректим на input block первой активной задачи лида.
    Обычно это T-001 / Intake Completeness.
    """
    if not admin_required():
        return redirect("/admin/login")

    task_id = get_first_task_id_for_lead(lead_id)

    if task_id is None:
        return "Для этой заявки ещё нет задач workflow", 404

    return redirect(f"/admin/leads/{lead_id}/tasks/{task_id}/input")


@admin_bp.post("/tasks/<int:task_id>/run-ai")
def admin_run_ai_task(task_id: int):
    if not admin_required():
        return redirect("/admin/login")

    form_lead_id = request.form.get("lead_id", "").strip()
    task = get_task_meta(task_id)

    if task is None:
        if form_lead_id:
            return redirect_with_error(f"/admin/leads/{form_lead_id}", "Задача не найдена")
        return "Задача не найдена", 404

    lead_id = int(task["lead_id"])
    stage = task["stage"]

    if stage not in AI_RUN_ALLOWED_STAGES:
        return redirect_with_error(
            f"/admin/leads/{lead_id}",
            f"AI Agent нельзя запускать для этапа {stage}",
        )

    try:
        result = run_ai_agent_for_task(task_id)
    except Exception as exc:
        return redirect_with_error(
            f"/admin/leads/{lead_id}",
            f"Ошибка запуска AI Agent: {exc}",
        )

    lead_id_from_result = result.get("lead_id") or lead_id
    return redirect(f"/admin/leads/{lead_id_from_result}")


@admin_bp.post("/export-xlsx")
def admin_export_xlsx():
    if not admin_required():
        return redirect("/admin/login")

    export_leads_to_xlsx()
    return redirect("/admin/leads")


@admin_bp.post("/api/leads/<int:lead_id>/status")
def update_lead_status(lead_id: int):
    if not admin_required():
        return redirect("/admin/login")

    data = request.get_json(silent=True) or request.form.to_dict()

    status_code = data.get("status", "").strip()
    new_status = LEAD_STATUSES.get(status_code)
    manager_comment = data.get("manager_comment", "").strip()

    if not new_status:
        return jsonify(
            {
                "ok": False,
                "error": "Некорректный статус.",
            }
        ), 400

    updated_at = utc_now()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE leads
            SET
                status = ?,
                manager_comment = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                new_status,
                manager_comment,
                updated_at,
                lead_id,
            ),
        )
        conn.commit()

    return jsonify(
        {
            "ok": True,
            "lead_id": lead_id,
            "status": new_status,
        }
    )


@admin_bp.post("/tasks/<int:task_id>/update")
def admin_update_task(task_id: int):
    if not admin_required():
        return redirect("/admin/login")

    data = request.form.to_dict()

    lead_id = data.get("lead_id", "").strip()
    stage = data.get("stage", "").strip()
    status = data.get("status", "").strip() or "New"

    result = data.get("result", "").strip()
    next_action = data.get("next_action", "").strip()
    comment = data.get("comment", "").strip() or "не указано"

    if status not in TASK_STATUSES:
        return "Некорректный статус задачи", 400

    if status == "Done":
        if stage in RESULT_REQUIRED_FOR_DONE_STAGES and is_empty_value(result):
            return (
                "Нельзя закрыть задачу в Done без результата. "
                "Запустите AI Agent или вставьте результат вручную."
            ), 400

        if is_empty_value(next_action):
            next_action = get_default_done_next_action(stage)
    else:
        if is_empty_value(result):
            result = "не указано"

        if is_empty_value(next_action):
            next_action = "не указано"

    update_agent_task(
        task_id=task_id,
        status=status,
        result=result,
        next_action=next_action,
        comment=comment,
    )

    create_next_task_after_update(task_id)

    if lead_id:
        return redirect(f"/admin/leads/{lead_id}")

    return redirect("/admin/leads")