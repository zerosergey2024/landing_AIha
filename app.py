from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from db import DB_PATH, init_db
from routes.diagnostic import diagnostic_bp

from datetime import datetime
from pathlib import Path

from flask import Blueprint, abort, redirect, render_template, request, send_file, url_for
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from services.diagnostics import (
    get_diagnostic_run_by_token,
    save_client_input_pack,
    save_diagnostic_attachment,
)
from services.site_links import get_site_links
from dotenv import load_dotenv
from flask import Flask
from routes.public import public_bp
from routes.api import api_bp
from routes.admin import admin_bp

load_dotenv()

app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

app.register_blueprint(public_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)
app.register_blueprint(diagnostic_bp)

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

diagnostic_bp = Blueprint(
    "diagnostic",
    __name__,
    url_prefix="/consulting/diagnostic",
)


BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_ROOT = BASE_DIR / "uploads" / "diagnostics"


ALLOWED_EXTENSIONS = {
    "xlsx",
    "xls",
    "csv",
    "pdf",
    "doc",
    "docx",
    "txt",
    "png",
    "jpg",
    "jpeg",
}


DOWNLOAD_TEMPLATES = {
    "docx": {
        "path": BASE_DIR
        / "static"
        / "consulting"
        / "downloads"
        / "Diagnostic_Input_Pack_Form_v1.docx",
        "download_name": "AIha_Diagnostic_Input_Pack_Form_v1.docx",
        "mimetype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    "pdf": {
        "path": BASE_DIR
        / "static"
        / "consulting"
        / "downloads"
        / "Diagnostic_Input_Pack_Form_v1.pdf",
        "download_name": "AIha_Diagnostic_Input_Pack_Form_v1.pdf",
        "mimetype": "application/pdf",
    },
}


def _is_allowed_file(filename: str) -> bool:
    if "." not in filename:
        return False

    extension = filename.rsplit(".", 1)[1].lower()
    return extension in ALLOWED_EXTENSIONS


def _detect_file_type(filename: str) -> str:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if extension in {"xlsx", "xls", "csv"}:
        return "DATA_FILE"

    if extension in {"doc", "docx", "pdf"}:
        return "DOCUMENT"

    if extension in {"png", "jpg", "jpeg"}:
        return "IMAGE"

    return "OTHER"


def _save_uploaded_files(
    files: list[FileStorage],
    diagnostic_run_id: int,
    input_pack_id: int,
) -> None:
    """
    Сохраняет приложенные клиентом файлы в uploads/diagnostics/<diagnostic_run_id>/.
    """
    upload_dir = UPLOAD_ROOT / str(diagnostic_run_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    for uploaded_file in files:
        if not uploaded_file or not uploaded_file.filename:
            continue

        original_filename = uploaded_file.filename

        if not _is_allowed_file(original_filename):
            continue

        safe_name = secure_filename(original_filename)
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        stored_filename = f"{timestamp}_{safe_name}"
        file_path = upload_dir / stored_filename

        uploaded_file.save(file_path)

        save_diagnostic_attachment(
            diagnostic_run_id=diagnostic_run_id,
            input_pack_id=input_pack_id,
            file_type=_detect_file_type(original_filename),
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=str(file_path),
        )


@diagnostic_bp.route("/input-pack/<token>", methods=["GET", "POST"])
def input_pack(token: str):
    diagnostic_run = get_diagnostic_run_by_token(token)

    if diagnostic_run is None:
        return render_template(
            "consulting/diagnostic_input_pack_invalid.html",
            site_links=get_site_links(),
        ), 404

    if request.method == "POST":
        payload = {
            "client": {
                "company": request.form.get("company"),
                "contact_name": request.form.get("contact_name"),
                "contact_email": request.form.get("contact_email"),
                "process_owner": request.form.get("process_owner"),
            },
            "diagnostic_goal": {
                "goal_automation": request.form.get("goal_automation"),
                "goal_ai_feasibility": request.form.get("goal_ai_feasibility"),
                "goal_economics": request.form.get("goal_economics"),
                "goal_bottlenecks": request.form.get("goal_bottlenecks"),
                "goal_mvp_scope": request.form.get("goal_mvp_scope"),
                "goal_other": request.form.get("goal_other"),
            },
            "process": {
                "process_name": request.form.get("process_name"),
                "process_description": request.form.get("process_description"),
                "main_problem": request.form.get("main_problem"),
                "request_start": request.form.get("request_start"),
                "request_channels": request.form.getlist("request_channels"),
                "registration_place": request.form.get("registration_place"),
                "roles_description": request.form.get("roles_description"),
                "statuses_description": request.form.get("statuses_description"),
                "sla_description": request.form.get("sla_description"),
                "manual_operations": request.form.get("manual_operations"),
                "bottlenecks": request.form.get("bottlenecks"),
            },
            "data": {
                "excel_log_available": request.form.get("excel_log_available"),
                "data_period": request.form.get("data_period"),
                "approx_rows": request.form.get("approx_rows"),
                "data_owner": request.form.get("data_owner"),
                "has_request_id": request.form.get("has_request_id"),
                "has_timestamps": request.form.get("has_timestamps"),
                "has_statuses": request.form.get("has_statuses"),
                "has_responsible": request.form.get("has_responsible"),
                "has_category": request.form.get("has_category"),
                "has_result": request.form.get("has_result"),
                "has_free_text": request.form.get("has_free_text"),
                "data_quality_issues": request.form.get("data_quality_issues"),
            },
            "systems": {
                "systems_used": request.form.getlist("systems_used"),
                "systems_description": request.form.get("systems_description"),
            },
            "integrations": {
                "api_available": request.form.get("api_available"),
                "exports_available": request.form.get("exports_available"),
                "manual_exchange": request.form.get("manual_exchange"),
                "integration_description": request.form.get("integration_description"),
                "it_contact": request.form.get("it_contact"),
            },
            "security": {
                "has_personal_data": request.form.get("has_personal_data"),
                "personal_data_description": request.form.get("personal_data_description"),
                "can_anonymize": request.form.get("can_anonymize"),
                "nda_required": request.form.get("nda_required"),
                "nda_signed": request.form.get("nda_signed"),
                "cloud_allowed": request.form.get("cloud_allowed"),
                "security_requirements": request.form.get("security_requirements"),
                "personal_data_requirements": request.form.get("personal_data_requirements"),
            },
            "economics": {
                "monthly_requests": request.form.get("monthly_requests"),
                "weekly_requests": request.form.get("weekly_requests"),
                "employees_involved": request.form.get("employees_involved"),
                "avg_processing_time": request.form.get("avg_processing_time"),
                "monthly_hours": request.form.get("monthly_hours"),
                "hour_cost": request.form.get("hour_cost"),
                "losses_from_errors": request.form.get("losses_from_errors"),
                "losses_from_delays": request.form.get("losses_from_delays"),
                "expected_effect": request.form.get("expected_effect"),
            },
            "contacts": {
                "process_contact": request.form.get("process_contact"),
                "data_contact": request.form.get("data_contact"),
                "it_contact": request.form.get("it_contact"),
                "security_contact": request.form.get("security_contact"),
                "finance_contact": request.form.get("finance_contact"),
            },
            "client_questions": request.form.get("client_questions"),
            "confirmation": {
                "data_usage_confirmed": request.form.get("data_usage_confirmed"),
                "limitations": request.form.get("limitations"),
                "responsible_person": request.form.get("responsible_person"),
            },
        }

        input_pack_id = save_client_input_pack(
            diagnostic_run_id=diagnostic_run["id"],
            payload=payload,
        )

        uploaded_files = request.files.getlist("attachments")
        _save_uploaded_files(
            files=uploaded_files,
            diagnostic_run_id=diagnostic_run["id"],
            input_pack_id=input_pack_id,
        )

        return redirect(
            url_for(
                "diagnostic.input_pack_submitted",
                token=token,
            )
        )

    return render_template(
        "consulting/diagnostic_input_pack.html",
        site_links=get_site_links(),
        diagnostic_run=diagnostic_run,
    )


@diagnostic_bp.route("/input-pack/<token>/download/<file_format>")
def download_input_pack_template(token: str, file_format: str):
    diagnostic_run = get_diagnostic_run_by_token(token)

    if diagnostic_run is None:
        abort(404)

    file_config = DOWNLOAD_TEMPLATES.get(file_format)

    if file_config is None:
        abort(404)

    file_path = file_config["path"]

    if not file_path.exists():
        abort(404)

    return send_file(
        file_path,
        as_attachment=True,
        download_name=file_config["download_name"],
        mimetype=file_config["mimetype"],
    )


@diagnostic_bp.route("/input-pack/<token>/submitted")
def input_pack_submitted(token: str):
    diagnostic_run = get_diagnostic_run_by_token(token)

    if diagnostic_run is None:
        abort(404)

    return render_template(
        "consulting/diagnostic_input_pack_submitted.html",
        site_links=get_site_links(),
        diagnostic_run=diagnostic_run,
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
else:
    init_db()
