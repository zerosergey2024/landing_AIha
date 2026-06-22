from __future__ import annotations

import json
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from flask import (
    Blueprint,
    abort,
    after_this_request,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from services.diagnostics import (
    get_diagnostic_run_by_token,
    get_latest_input_pack,
    save_client_input_pack,
    save_diagnostic_attachment,
)
from services.site_links import get_site_links


diagnostic_bp = Blueprint(
    "diagnostic",
    __name__,
    url_prefix="/consulting/diagnostic",
)


BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_ROOT = BASE_DIR / "uploads" / "diagnostics"

DOWNLOAD_ROOT = BASE_DIR / "static" / "consulting" / "downloads"

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

FIELD_LABELS = {
    "client.company": "Компания",
    "client.contact_name": "Контактное лицо",
    "client.contact_email": "Email",
    "client.process_owner": "Владелец процесса",

    "diagnostic_goal.goal_automation": "Цель: автоматизация",
    "diagnostic_goal.goal_ai_feasibility": "Цель: проверка применимости AI",
    "diagnostic_goal.goal_economics": "Цель: экономика",
    "diagnostic_goal.goal_bottlenecks": "Цель: узкие места",
    "diagnostic_goal.goal_mvp_scope": "Цель: scope MVP",
    "diagnostic_goal.goal_other": "Другая цель",

    "process.process_name": "Название процесса",
    "process.process_description": "Описание процесса",
    "process.main_problem": "Главная проблема",
    "process.request_start": "Как начинается заявка / операция",
    "process.request_channels": "Каналы поступления заявок",
    "process.registration_place": "Где регистрируется заявка",
    "process.roles_description": "Роли в процессе",
    "process.statuses_description": "Статусы процесса",
    "process.sla_description": "SLA / сроки",
    "process.manual_operations": "Ручные операции",
    "process.bottlenecks": "Узкие места",

    "data.excel_log_available": "Есть ли журнал / Excel / выгрузка",
    "data.data_period": "Период данных",
    "data.approx_rows": "Примерное количество строк",
    "data.data_owner": "Владелец данных",
    "data.has_request_id": "Есть ID заявки",
    "data.has_timestamps": "Есть временные метки",
    "data.has_statuses": "Есть статусы",
    "data.has_responsible": "Есть ответственные",
    "data.has_category": "Есть категории",
    "data.has_result": "Есть результат обработки",
    "data.has_free_text": "Есть свободный текст",
    "data.data_quality_issues": "Проблемы качества данных",

    "systems.systems_used": "Используемые системы",
    "systems.systems_description": "Описание систем",

    "integrations.api_available": "Есть API",
    "integrations.exports_available": "Есть выгрузки",
    "integrations.manual_exchange": "Есть ручной обмен",
    "integrations.integration_description": "Описание интеграций",
    "integrations.it_contact": "ИТ-контакт",

    "security.has_personal_data": "Есть персональные данные",
    "security.personal_data_description": "Описание персональных данных",
    "security.can_anonymize": "Можно обезличить",
    "security.nda_required": "Требуется NDA",
    "security.nda_signed": "NDA подписан",
    "security.cloud_allowed": "Облако допустимо",
    "security.security_requirements": "Требования ИБ",
    "security.personal_data_requirements": "Требования к персональным данным",

    "economics.monthly_requests": "Операций в месяц",
    "economics.weekly_requests": "Операций в неделю",
    "economics.employees_involved": "Сотрудников в процессе",
    "economics.avg_processing_time": "Среднее время обработки",
    "economics.monthly_hours": "Часов в месяц",
    "economics.hour_cost": "Стоимость часа",
    "economics.losses_from_errors": "Потери от ошибок",
    "economics.losses_from_delays": "Потери от задержек",
    "economics.expected_effect": "Ожидаемый эффект",

    "contacts.process_contact": "Контакт по процессу",
    "contacts.data_contact": "Контакт по данным",
    "contacts.it_contact": "ИТ-контакт",
    "contacts.security_contact": "Контакт по ИБ",
    "contacts.finance_contact": "Финансовый контакт",

    "client_questions": "Вопросы клиента",

    "confirmation.data_usage_confirmed": "Согласие на использование данных",
    "confirmation.limitations": "Ограничения",
    "confirmation.responsible_person": "Ответственное лицо",
}

BLANK_FORM_SECTIONS = [
    (
        "1. Общая информация",
        [
            "Компания",
            "Контактное лицо",
            "Email контактного лица",
            "Владелец процесса",
        ],
    ),
    (
        "2. Цель диагностики",
        [
            "Понять, можно ли автоматизировать процесс",
            "Оценить целесообразность AI-решения",
            "Проверить экономический эффект",
            "Найти узкие места процесса",
            "Подготовить MVP scope",
            "Другое",
        ],
    ),
    (
        "3. Описание процесса",
        [
            "Название процесса",
            "Как появляется заявка",
            "Краткое описание процесса",
            "Основная проблема процесса",
            "Каналы поступления заявок",
            "Где регистрируется заявка",
            "SLA / сроки обработки",
            "Роли в процессе",
            "Статусы заявки",
            "Ручные операции",
            "Узкие места и потери",
        ],
    ),
    (
        "4. Данные и Excel-журнал",
        [
            "Есть ли Excel-журнал заявок",
            "Период данных",
            "Примерное количество строк",
            "Кто владелец данных",
            "Есть ID заявки",
            "Есть временные метки",
            "Есть статусы",
            "Есть ответственный",
            "Есть категория заявки",
            "Есть результат обработки",
            "Есть свободный текст",
            "Известные проблемы качества данных",
        ],
    ),
    (
        "5. Используемые системы",
        [
            "Excel",
            "1C",
            "CRM",
            "Email",
            "Telegram",
            "Helpdesk / Service Desk",
            "Внутренняя система",
            "Другое",
            "Описание систем",
        ],
    ),
    (
        "6. Интеграции и API",
        [
            "Есть ли API",
            "Есть ли выгрузки Excel/CSV",
            "Есть ли ручной обмен файлами",
            "Контакт ИТ",
            "Описание интеграций",
        ],
    ),
    (
        "7. Информационная безопасность и ПДн",
        [
            "Есть ли персональные данные",
            "Можно ли обезличить данные",
            "Требуется ли NDA",
            "NDA подписан",
            "Разрешена облачная обработка",
            "Какие ПДн могут встречаться",
            "Требования ИБ",
            "Требования по ПДн",
        ],
    ),
    (
        "8. Экономика процесса",
        [
            "Заявок в месяц",
            "Заявок в неделю",
            "Сотрудников участвует",
            "Среднее время обработки заявки",
            "Часов в месяц на процесс",
            "Стоимость часа сотрудника / команды",
            "Потери от ошибок",
            "Потери от задержек / просрочек",
            "Какой эффект от автоматизации считается значимым",
        ],
    ),
    (
        "9. Материалы и файлы",
        [
            "Excel-журнал заявок",
            "Регламент",
            "Описание систем",
            "Описание интеграций/API",
            "Политики ИБ",
            "NDA",
            "Метрики процесса",
        ],
    ),
    (
        "10. Контакты для уточнений",
        [
            "Контакт по процессу",
            "Контакт по данным",
            "Контакт по ИБ / ПДн",
            "Финансовый / экономический контакт",
        ],
    ),
    (
        "11. Открытые вопросы",
        [
            "Вопросы, комментарии, ограничения",
        ],
    ),
    (
        "12. Подтверждение",
        [
            "Данные можно использовать для диагностики",
            "Ответственный со стороны клиента",
            "Ограничения на использование данных",
        ],
    ),
]

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


def _extract_input_pack_id(saved_input_pack: Any) -> int:
    if isinstance(saved_input_pack, int):
        return saved_input_pack

    if isinstance(saved_input_pack, dict):
        return int(saved_input_pack["id"])

    if hasattr(saved_input_pack, "keys") and "id" in saved_input_pack.keys():
        return int(saved_input_pack["id"])

    return int(saved_input_pack)


def _save_uploaded_files(
    files: list[FileStorage],
    diagnostic_run_id: int,
    input_pack_id: int,
) -> None:
    upload_dir = UPLOAD_ROOT / str(diagnostic_run_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    for uploaded_file in files:
        if not uploaded_file or not uploaded_file.filename:
            continue

        original_filename = uploaded_file.filename

        if not _is_allowed_file(original_filename):
            continue

        safe_name = secure_filename(original_filename)

        if not safe_name:
            continue

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


def _build_input_pack_payload() -> dict[str, Any]:
    return {
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


def _load_raw_payload(input_pack: Any) -> dict[str, Any]:
    if input_pack is None:
        return {}

    raw_payload = input_pack["raw_payload"]

    if not raw_payload:
        return {}

    if isinstance(raw_payload, dict):
        return raw_payload

    return json.loads(raw_payload)


def _flatten_payload(
    payload: dict[str, Any],
    prefix: str = "",
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []

    for key, value in payload.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict):
            rows.extend(_flatten_payload(value, full_key))
            continue

        label = FIELD_LABELS.get(full_key, full_key)

        if isinstance(value, list):
            clean_value = ", ".join(str(item) for item in value if item)
        elif value is None or value == "":
            clean_value = "не указано"
        else:
            clean_value = str(value)

        rows.append((label, clean_value))

    return rows


def _docx_paragraph(text: str, bold: bool = False) -> str:
    safe_text = escape(text)

    if bold:
        return (
            "<w:p>"
            "<w:r>"
            "<w:rPr><w:b/></w:rPr>"
            f"<w:t>{safe_text}</w:t>"
            "</w:r>"
            "</w:p>"
        )

    return (
        "<w:p>"
        "<w:r>"
        f"<w:t>{safe_text}</w:t>"
        "</w:r>"
        "</w:p>"
    )

def _build_blank_docx(
    output_path: Path,
    diagnostic_run: Any,
) -> None:
    try:
        from docx import Document
        from docx.enum.section import WD_SECTION
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
        from docx.shared import Inches, Pt, RGBColor
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Для генерации DOCX установите зависимость: pip install python-docx"
        ) from exc

    document = Document()

    section = document.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)

    styles = document.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(10)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("Diagnostic Input Pack Form")
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = RGBColor(17, 24, 39)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run("AIha Consulting")
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(80, 95, 120)

    document.add_paragraph()

    info_table = document.add_table(rows=4, cols=2)
    info_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    info_table.style = "Table Grid"

    info_rows = [
        ("Diagnostic ID", str(diagnostic_run["id"])),
        ("Компания", diagnostic_run["company"] or "не указано"),
        ("Контакт", diagnostic_run["contact_name"] or "не указано"),
        ("Email", diagnostic_run["contact_email"] or "не указано"),
    ]

    for row_index, (label, value) in enumerate(info_rows):
        cells = info_table.rows[row_index].cells
        cells[0].text = label
        cells[1].text = value

        for cell in cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.name = "Arial"
                    run.font.size = Pt(9)

        for run in cells[0].paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = RGBColor(17, 24, 39)

    document.add_paragraph()

    intro = document.add_paragraph()
    intro.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = intro.add_run(
        "Заполните форму внутри компании. Её можно использовать для согласования "
        "с владельцем процесса, ИТ, ИБ и финансовым блоком. После подготовки "
        "ответы можно перенести в онлайн-форму или отправить документ специалисту AIha Consulting."
    )
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(57, 65, 80)

    document.add_paragraph()

    for section_title, fields in BLANK_FORM_SECTIONS:
        heading = document.add_paragraph()
        heading_run = heading.add_run(section_title)
        heading_run.bold = True
        heading_run.font.size = Pt(14)
        heading_run.font.color.rgb = RGBColor(17, 24, 39)

        table = document.add_table(rows=1, cols=2)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        header_cells = table.rows[0].cells
        header_cells[0].text = "Поле"
        header_cells[1].text = "Ответ / комментарий"

        for cell in header_cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True
                    run.font.name = "Arial"
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(255, 255, 255)

        for field in fields:
            row = table.add_row()
            row.cells[0].text = field
            row.cells[1].text = ""

            for cell in row.cells:
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.name = "Arial"
                        run.font.size = Pt(9)

        for row in table.rows:
            row.height = Pt(26)

        document.add_paragraph()

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer.add_run("AIha Consulting · Diagnostic Input Pack")
    footer_run.font.size = Pt(8)
    footer_run.font.color.rgb = RGBColor(102, 112, 133)

    document.save(output_path)

def _build_submitted_docx(
    output_path: Path,
    diagnostic_run: Any,
    payload: dict[str, Any],
) -> None:
    rows = _flatten_payload(payload)

    paragraphs = [
        _docx_paragraph("Diagnostic Input Pack — AIha Consulting", bold=True),
        _docx_paragraph(f"Diagnostic ID: {diagnostic_run['id']}"),
        _docx_paragraph(f"Компания: {diagnostic_run['company'] or 'не указано'}"),
        _docx_paragraph(f"Контакт: {diagnostic_run['contact_name'] or 'не указано'}"),
        _docx_paragraph(f"Email: {diagnostic_run['contact_email'] or 'не указано'}"),
        _docx_paragraph(f"Дата выгрузки: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"),
        _docx_paragraph(""),
        _docx_paragraph("Заполненные данные", bold=True),
    ]

    for label, value in rows:
        paragraphs.append(_docx_paragraph(label, bold=True))
        paragraphs.append(_docx_paragraph(value))
        paragraphs.append(_docx_paragraph(""))

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
    xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <w:body>
        {''.join(paragraphs)}
        <w:sectPr>
            <w:pgSz w:w="11906" w:h="16838"/>
            <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
        </w:sectPr>
    </w:body>
</w:document>
"""

    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

    rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship
        Id="rId1"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
        Target="word/document.xml"/>
</Relationships>
"""

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types_xml)
        docx.writestr("_rels/.rels", rels_xml)
        docx.writestr("word/document.xml", document_xml)

def _make_temp_output_path(suffix: str) -> Path:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return Path(path)

def _find_pdf_font() -> Path | None:
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None

def _build_blank_pdf(
    output_path: Path,
    diagnostic_run: Any,
) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
            PageBreak,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Для генерации PDF установите зависимость: pip install reportlab"
        ) from exc

    font_path = _find_pdf_font()

    if font_path is None:
        raise RuntimeError(
            "Не найден TTF-шрифт для генерации PDF. "
            "На Windows обычно подходит C:/Windows/Fonts/arial.ttf."
        )

    pdfmetrics.registerFont(TTFont("AIhaFont", str(font_path)))

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Diagnostic Input Pack Form — AIha Consulting",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "AIhaTitle",
        parent=styles["Title"],
        fontName="AIhaFont",
        fontSize=22,
        leading=26,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#111827"),
        spaceAfter=8,
    )

    subtitle_style = ParagraphStyle(
        "AIhaSubtitle",
        parent=styles["Normal"],
        fontName="AIhaFont",
        fontSize=11,
        leading=15,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#505f78"),
        spaceAfter=18,
    )

    section_style = ParagraphStyle(
        "AIhaSection",
        parent=styles["Heading2"],
        fontName="AIhaFont",
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#111827"),
        spaceBefore=18,
        spaceAfter=10,
    )

    normal_style = ParagraphStyle(
        "AIhaNormal",
        parent=styles["Normal"],
        fontName="AIhaFont",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#394150"),
        alignment=TA_LEFT,
    )

    small_style = ParagraphStyle(
        "AIhaSmall",
        parent=styles["Normal"],
        fontName="AIhaFont",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#667085"),
        alignment=TA_CENTER,
    )

    story = []

    story.append(Paragraph("Diagnostic Input Pack Form", title_style))
    story.append(Paragraph("AIha Consulting", subtitle_style))

    info_table = Table(
        [
            ["Diagnostic ID", str(diagnostic_run["id"])],
            ["Компания", diagnostic_run["company"] or "не указано"],
            ["Контакт", diagnostic_run["contact_name"] or "не указано"],
            ["Email", diagnostic_run["contact_email"] or "не указано"],
        ],
        colWidths=[45 * mm, 115 * mm],
    )

    info_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "AIhaFont"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f7")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#111827")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d5dd")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )

    story.append(info_table)
    story.append(Spacer(1, 12))

    story.append(
        Paragraph(
            "Заполните форму внутри компании. Её можно использовать для согласования "
            "с владельцем процесса, ИТ, ИБ и финансовым блоком. После подготовки "
            "ответы можно перенести в онлайн-форму или отправить документ специалисту AIha Consulting.",
            normal_style,
        )
    )

    story.append(Spacer(1, 12))

    for section_index, (section_title, fields) in enumerate(BLANK_FORM_SECTIONS, start=1):
        story.append(Paragraph(section_title, section_style))

        table_data = [
            [
                Paragraph("Поле", normal_style),
                Paragraph("Ответ / комментарий", normal_style),
            ]
        ]

        for field in fields:
            table_data.append(
                [
                    Paragraph(field, normal_style),
                    Paragraph(" ", normal_style),
                ]
            )

        section_table = Table(
            table_data,
            colWidths=[58 * mm, 102 * mm],
            repeatRows=1,
        )

        section_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), "AIhaFont"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d5dd")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ]
            )
        )

        story.append(section_table)
        story.append(Spacer(1, 10))

        if section_index in {4, 8}:
            story.append(PageBreak())

    story.append(Spacer(1, 16))
    story.append(Paragraph("AIha Consulting · Diagnostic Input Pack", small_style))

    doc.build(story)


def _build_submitted_pdf(
    output_path: Path,
    diagnostic_run: Any,
    payload: dict[str, Any],
) -> None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Для генерации PDF установите зависимость: pip install reportlab"
        ) from exc

    font_path = _find_pdf_font()

    if font_path is None:
        raise RuntimeError(
            "Не найден TTF-шрифт для генерации PDF. "
            "На Windows обычно подходит C:/Windows/Fonts/arial.ttf."
        )

    pdfmetrics.registerFont(TTFont("AIhaFont", str(font_path)))

    c = canvas.Canvas(str(output_path), pagesize=A4)
    width, height = A4

    left = 48
    top = height - 52
    line_height = 16
    y = top

    def draw_line(text: str, bold: bool = False) -> None:
        nonlocal y

        if y < 64:
            c.showPage()
            c.setFont("AIhaFont", 10)
            y = top

        c.setFont("AIhaFont", 12 if bold else 10)

        max_chars = 95
        text = text or ""

        chunks = [
            text[index : index + max_chars]
            for index in range(0, len(text), max_chars)
        ] or [""]

        for chunk in chunks:
            if y < 64:
                c.showPage()
                c.setFont("AIhaFont", 10)
                y = top

            c.drawString(left, y, chunk)
            y -= line_height

    draw_line("Diagnostic Input Pack — AIha Consulting", bold=True)
    draw_line(f"Diagnostic ID: {diagnostic_run['id']}")
    draw_line(f"Компания: {diagnostic_run['company'] or 'не указано'}")
    draw_line(f"Контакт: {diagnostic_run['contact_name'] or 'не указано'}")
    draw_line(f"Email: {diagnostic_run['contact_email'] or 'не указано'}")
    draw_line(f"Дата выгрузки: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    draw_line("")
    draw_line("Заполненные данные", bold=True)
    draw_line("")

    rows = _flatten_payload(payload)

    for label, value in rows:
        draw_line(label, bold=True)
        draw_line(value)
        draw_line("")

    c.save()


@diagnostic_bp.route("/input-pack/<token>", methods=["GET", "POST"])
def input_pack(token: str):
    diagnostic_run = get_diagnostic_run_by_token(token)

    if diagnostic_run is None:
        return render_template(
            "consulting/diagnostic_input_pack_invalid.html",
            site_links=get_site_links(),
        ), 404

    if request.method == "POST":
        payload = _build_input_pack_payload()

        saved_input_pack = save_client_input_pack(
            diagnostic_run_id=diagnostic_run["id"],
            payload=payload,
        )

        input_pack_id = _extract_input_pack_id(saved_input_pack)

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
        diagnostic=diagnostic_run,
        token=token,
        download_docx_url=url_for(
            "diagnostic.download_input_pack_template",
            token=token,
            file_format="docx",
        ),
        download_pdf_url=url_for(
            "diagnostic.download_input_pack_template",
            token=token,
            file_format="pdf",
        ),
    )

@diagnostic_bp.route("/input-pack/<token>/download/<file_format>")
def download_input_pack_template(token: str, file_format: str):
    diagnostic_run = get_diagnostic_run_by_token(token)

    if diagnostic_run is None:
        abort(404)

    file_format = file_format.lower().strip()

    if file_format not in {"docx", "pdf"}:
        abort(404)

    suffix = f".{file_format}"
    output_path = _make_temp_output_path(suffix)

    try:
        if file_format == "docx":
            _build_blank_docx(
                output_path=output_path,
                diagnostic_run=diagnostic_run,
            )
            download_name = "AIha_Diagnostic_Input_Pack_Form_v1.docx"
            mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            _build_blank_pdf(
                output_path=output_path,
                diagnostic_run=diagnostic_run,
            )
            download_name = "AIha_Diagnostic_Input_Pack_Form_v1.pdf"
            mimetype = "application/pdf"

    except RuntimeError as exc:
        output_path.unlink(missing_ok=True)
        return str(exc), 500

    response = send_file(
        output_path,
        as_attachment=True,
        download_name=download_name,
        mimetype=mimetype,
    )

    response.call_on_close(lambda: output_path.unlink(missing_ok=True))

    return response


@diagnostic_bp.route("/input-pack/<token>/submitted")
def input_pack_submitted(token: str):
    diagnostic_run = get_diagnostic_run_by_token(token)

    if diagnostic_run is None:
        abort(404)

    return render_template(
        "consulting/diagnostic_input_pack_submitted.html",
        site_links=get_site_links(),
        diagnostic_run=diagnostic_run,
        diagnostic=diagnostic_run,
        token=token,
        download_docx_url=url_for(
            "diagnostic.download_submitted_input_pack",
            token=token,
            file_format="docx",
        ),
        download_pdf_url=url_for(
            "diagnostic.download_submitted_input_pack",
            token=token,
            file_format="pdf",
        ),
    )


@diagnostic_bp.route("/input-pack/<token>/submitted/download/<file_format>")
def download_submitted_input_pack(token: str, file_format: str):
    diagnostic_run = get_diagnostic_run_by_token(token)

    if diagnostic_run is None:
        abort(404)

    file_format = file_format.lower().strip()

    if file_format not in {"docx", "pdf"}:
        abort(404)

    input_pack = get_latest_input_pack(diagnostic_run["id"])

    if input_pack is None:
        return "Заполненная форма не найдена", 404

    payload = _load_raw_payload(input_pack)

    suffix = f".{file_format}"
    output_path = _make_temp_output_path(suffix)

    try:
        if file_format == "docx":
            _build_submitted_docx(
                output_path=output_path,
                diagnostic_run=diagnostic_run,
                payload=payload,
            )
            download_name = f"AIha_Diagnostic_Input_Pack_Submitted_{diagnostic_run['id']}.docx"
            mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            _build_submitted_pdf(
                output_path=output_path,
                diagnostic_run=diagnostic_run,
                payload=payload,
            )
            download_name = f"AIha_Diagnostic_Input_Pack_Submitted_{diagnostic_run['id']}.pdf"
            mimetype = "application/pdf"

    except RuntimeError as exc:
        output_path.unlink(missing_ok=True)
        return str(exc), 500

    response = send_file(
        output_path,
        as_attachment=True,
        download_name=download_name,
        mimetype=mimetype,
    )

    response.call_on_close(lambda: output_path.unlink(missing_ok=True))

    return response
