from __future__ import annotations

from services.intake_blocks import build_intake_v33_input_block, get_lead_with_constraints
from flask import Blueprint, jsonify, redirect, render_template, request, session
import os
import sqlite3

from dotenv import load_dotenv
from flask import Blueprint, redirect, render_template, request, session

from db import DB_PATH
from export_leads_xlsx import export_leads_to_xlsx
from flask import jsonify
from datetime import datetime, timezone

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


def admin_required() -> bool:
    return session.get("admin_logged_in") is True


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

    query = """
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


@admin_bp.get("/leads/<int:lead_id>")
def admin_lead_detail(lead_id: int):
    if not admin_required():
        return redirect("/admin/login")

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
        tasks = conn.execute(
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
            WHERE lead_id = ?
            ORDER BY id ASC
            """,
            (lead_id,),
        ).fetchall()

    if lead is None:
        return "Заявка не найдена", 404

    return render_template(
        "admin_lead_detail.html",
        lead=lead,
        constraints=constraints,
        tasks=tasks,
        statuses=LEAD_STATUSES,
    )

@admin_bp.get("/leads/<int:lead_id>/intake-input")
def admin_lead_intake_input(lead_id: int):
    if not admin_required():
        return redirect("/admin/login")

    lead, constraints = get_lead_with_constraints(lead_id)

    if lead is None:
        return "Заявка не найдена", 404

    input_block = build_intake_v33_input_block(lead, constraints)

    return render_template(
        "admin_intake_input.html",
        lead=lead,
        input_block=input_block,
    )


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

    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

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