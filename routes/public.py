from __future__ import annotations
from services.tasks import create_initial_consulting_task
from flask import Blueprint, redirect, render_template, request

from services.consulting import (
    build_consulting_message,
    build_consulting_telegram_text,
    save_client_constraints,
)
from services.leads import save_lead
from telegram_notify import send_telegram_message


public_bp = Blueprint("public", __name__)

@public_bp.route("/")
def index():
    return render_template("ecosystem.html")

@public_bp.route("/studio")
def studio_index():
    return render_template("index.html")

@public_bp.route("/consulting")
def consulting_index():
    return render_template("consulting/index.html")

@public_bp.route("/consulting/audit", methods=["GET", "POST"])
def consulting_audit():
    if request.method == "POST":
        data = request.form.to_dict()

        contact_person = data.get("contact_person", "").strip()
        phone = data.get("phone", "").strip()
        email = data.get("email", "").strip()

        if not contact_person:
            return render_template(
                "consulting/audit_form.html",
                error="Укажите контактное лицо.",
                form=data,
            ), 400

        if not phone and not email:
            return render_template(
                "consulting/audit_form.html",
                error="Укажите телефон или email.",
                form=data,
            ), 400

        lead_id = save_lead(
            {
                "name": contact_person,
                "phone": phone or "не указано",
                "company": data.get("company", "").strip(),
                "message": build_consulting_message(data),
                "source": "aiha_consulting_audit_form",
                "status": "Новая",
            }
        )

        save_client_constraints(lead_id, data)

        create_initial_consulting_task(
            lead_id=lead_id,
            company=data.get("company", "").strip(),
        )

        telegram_text = build_consulting_telegram_text(data, lead_id)
        send_telegram_message(telegram_text)

        return redirect("/consulting/thanks")

    return render_template("consulting/audit_form.html")


@public_bp.route("/consulting/thanks")
def consulting_thanks():
    return render_template("consulting/thanks.html")