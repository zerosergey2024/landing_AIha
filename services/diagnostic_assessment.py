from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from db import get_db_connection
from services.ai_agent import run_agent_with_prompt
from services.diagnostic_normalizer import normalize_input_pack
from services.diagnostics import (
    DIAGNOSTIC_STATUS_D001_COMPLETED,
    DIAGNOSTIC_STATUS_D001_RUNNING,
    save_d001_result,
    update_diagnostic_status,
)


D001_AGENT_PROMPT_NAME = "diagnostic_assessment"
MIN_INDUSTRIAL_FILLED_FIELDS = 20


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
        "d003_result",
        "d004_result",
        "final_result",
        "final_output",
        "diagnostic_report",
        "commercial_proposal",
    }

    for key in keys_to_remove:
        diagnostic_run.pop(key, None)

    return diagnostic_run


def _validate_raw_payload_for_d001(
    input_pack_id: int,
    raw_payload: dict[str, Any],
) -> None:
    if not raw_payload:
        raise ValueError(
            f"raw_payload is empty for diagnostic input pack id={input_pack_id}"
        )

    brief_type = raw_payload.get("brief_type")

    if brief_type != "industrial_ai":
        return

    industrial_ai = raw_payload.get("industrial_ai")

    if not isinstance(industrial_ai, dict):
        raise ValueError(
            f"industrial_ai object is missing in raw_payload for input_pack id={input_pack_id}"
        )

    filled_fields = _count_non_empty(industrial_ai)

    if filled_fields < MIN_INDUSTRIAL_FILLED_FIELDS:
        raise ValueError(
            "Industrial AI Brief is too sparse for D-001: "
            f"input_pack_id={input_pack_id}, "
            f"industrial_non_empty={filled_fields}. "
            "Submit a filled Industrial AI Brief before running D-001."
        )


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
              AND is_active = 1
              AND brief_type = 'industrial_ai'
              AND raw_payload IS NOT NULL
              AND TRIM(raw_payload) != ''
            ORDER BY
                COALESCE(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (diagnostic_run_id,),
        ).fetchone()

        if row is None:
            row = conn.execute(
                """
                SELECT *
                FROM diagnostic_input_packs
                WHERE diagnostic_run_id = ?
                  AND is_active = 1
                  AND brief_type = 'diagnostic_input_pack'
                  AND raw_payload IS NOT NULL
                  AND TRIM(raw_payload) != ''
                ORDER BY
                    COALESCE(updated_at, created_at) DESC,
                    id DESC
                LIMIT 1
                """,
                (diagnostic_run_id,),
            ).fetchone()

    if row is None:
        raise ValueError(
            "Active diagnostic input pack with raw_payload not found "
            f"for diagnostic_run_id={diagnostic_run_id}"
        )

    return row


def _get_attachments_for_input_pack(input_pack_id: int) -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                input_pack_id,
                file_type,
                original_filename,
                stored_filename,
                file_path,
                uploaded_at
            FROM diagnostic_attachments
            WHERE input_pack_id = ?
            ORDER BY id ASC
            """,
            (input_pack_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def validate_d001_input(diagnostic_run_id: int) -> None:
    input_pack = _get_latest_input_pack_row(diagnostic_run_id)
    raw_payload = _load_json_object(input_pack["raw_payload"], "raw_payload")

    _validate_raw_payload_for_d001(
        input_pack_id=int(input_pack["id"]),
        raw_payload=raw_payload,
    )

def _safe_string(value: Any, max_len: int = 240) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if len(text) > max_len:
        return text[:max_len] + "..."

    return text


def _read_csv_preview(file_path: str, max_rows: int = 20) -> dict[str, Any]:
    path = Path(file_path)

    result: dict[str, Any] = {
        "kind": "csv_table_preview",
        "file_path": str(path),
        "exists": path.exists(),
        "columns": [],
        "sample_rows": [],
        "row_count_previewed": 0,
        "encoding": None,
        "error": None,
    }

    if not path.exists():
        result["error"] = "file_not_found"
        return result

    last_error: Exception | None = None

    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                reader = csv.DictReader(file)

                result["columns"] = [
                    _safe_string(column)
                    for column in (reader.fieldnames or [])
                ]

                for index, row in enumerate(reader):
                    if index >= max_rows:
                        break

                    result["sample_rows"].append(
                        {
                            _safe_string(key): _safe_string(value)
                            for key, value in row.items()
                        }
                    )

                result["row_count_previewed"] = len(result["sample_rows"])
                result["encoding"] = encoding
                return result

        except Exception as exc:
            last_error = exc

    result["error"] = str(last_error) if last_error else "csv_read_failed"
    return result


def _read_xlsx_preview(file_path: str, max_rows: int = 20) -> dict[str, Any]:
    path = Path(file_path)

    result: dict[str, Any] = {
        "kind": "xlsx_table_preview",
        "file_path": str(path),
        "exists": path.exists(),
        "sheets": [],
        "error": None,
    }

    if not path.exists():
        result["error"] = "file_not_found"
        return result

    try:
        from openpyxl import load_workbook
    except Exception as exc:
        result["error"] = f"openpyxl_not_available: {exc}"
        return result

    def normalize_cell(value: Any) -> str:
        return _safe_string(value, max_len=120).strip()

    def header_score(row_values: list[Any]) -> int:
        """
        Ищем наиболее похожую строку на заголовок таблицы.
        Это нужно, потому что Excel-лист может начинаться с названия отчёта,
        комментариев или пустых строк.
        """
        cells = [
            normalize_cell(value).lower()
            for value in row_values
            if normalize_cell(value)
        ]

        if not cells:
            return 0

        known_header_terms = {
            "event_id",
            "downtime_event_id",
            "event_date",
            "line_id",
            "line_name",
            "equipment_id",
            "equipment_name",
            "downtime_start",
            "downtime_end",
            "start_time",
            "end_time",
            "duration_min",
            "duration_minutes",
            "downtime_minutes",
            "reason_code",
            "reason_description",
            "downtime_reason",
            "source_system",
            "cost_per_hour_rub",
            "estimated_loss_rub",
            "comment",
            "corrective_action",
            "id события",
            "id линии",
            "id оборудования",
            "начало простоя",
            "конец простоя",
            "длительность",
            "причина простоя",
            "стоимость часа",
            "потери",
        }

        score = 0

        for cell in cells:
            if cell in known_header_terms:
                score += 5

            if any(term in cell for term in known_header_terms):
                score += 3

            if "_" in cell:
                score += 1

            if len(cell) <= 40:
                score += 1

        # Много заполненных ячеек в строке — хороший признак строки заголовков.
        if len(cells) >= 5:
            score += 5

        # Одна длинная ячейка похожа на заголовок отчёта, а не на header таблицы.
        if len(cells) == 1 and len(cells[0]) > 40:
            score -= 10

        return score

    try:
        workbook = load_workbook(
            filename=path,
            read_only=True,
            data_only=True,
        )

        for worksheet in workbook.worksheets[:5]:
            preview_rows = list(
                worksheet.iter_rows(
                    min_row=1,
                    max_row=min(worksheet.max_row or 1, max_rows + 15),
                    values_only=True,
                )
            )

            if not preview_rows:
                result["sheets"].append(
                    {
                        "sheet_name": worksheet.title,
                        "max_row": worksheet.max_row,
                        "max_column": worksheet.max_column,
                        "header_row_index": None,
                        "columns": [],
                        "sample_rows": [],
                        "row_count_previewed": 0,
                    }
                )
                continue

            best_header_index = 0
            best_score = -999

            for index, row_values in enumerate(preview_rows[:15]):
                score = header_score(list(row_values))

                if score > best_score:
                    best_score = score
                    best_header_index = index

            header_row = preview_rows[best_header_index]

            columns = [
                normalize_cell(value) if normalize_cell(value) else f"column_{index + 1}"
                for index, value in enumerate(header_row)
            ]

            sample_rows: list[dict[str, str]] = []

            data_rows = preview_rows[best_header_index + 1:]

            for row in data_rows:
                if len(sample_rows) >= max_rows:
                    break

                row_dict: dict[str, str] = {}

                for col_index, value in enumerate(row):
                    if col_index >= len(columns):
                        continue

                    column_name = columns[col_index]
                    row_dict[column_name] = _safe_string(value)

                if any(value.strip() for value in row_dict.values()):
                    sample_rows.append(row_dict)

            result["sheets"].append(
                {
                    "sheet_name": worksheet.title,
                    "max_row": worksheet.max_row,
                    "max_column": worksheet.max_column,
                    "header_row_index": best_header_index + 1,
                    "header_detection_score": best_score,
                    "columns": columns,
                    "sample_rows": sample_rows,
                    "row_count_previewed": len(sample_rows),
                }
            )

        workbook.close()
        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result


def _read_xls_preview(file_path: str, max_rows: int = 20) -> dict[str, Any]:
    path = Path(file_path)

    result: dict[str, Any] = {
        "kind": "xls_table_preview",
        "file_path": str(path),
        "exists": path.exists(),
        "sheets": [],
        "error": None,
    }

    if not path.exists():
        result["error"] = "file_not_found"
        return result

    try:
        import pandas as pd
    except Exception as exc:
        result["error"] = f"pandas_not_available: {exc}"
        return result

    try:
        sheets = pd.read_excel(
            path,
            sheet_name=None,
            nrows=max_rows,
        )

        for sheet_name, dataframe in list(sheets.items())[:5]:
            columns = [
                _safe_string(column)
                for column in dataframe.columns.tolist()
            ]

            sample_rows: list[dict[str, str]] = []

            for _, row in dataframe.head(max_rows).iterrows():
                row_dict: dict[str, str] = {}

                for column in dataframe.columns:
                    row_dict[_safe_string(column)] = _safe_string(row[column])

                if any(value.strip() for value in row_dict.values()):
                    sample_rows.append(row_dict)

            result["sheets"].append(
                {
                    "sheet_name": str(sheet_name),
                    "columns": columns,
                    "sample_rows": sample_rows,
                    "row_count_previewed": len(sample_rows),
                }
            )

        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result


def _read_docx_preview(file_path: str, max_chars: int = 12000) -> dict[str, Any]:
    path = Path(file_path)

    result: dict[str, Any] = {
        "kind": "docx_text_preview",
        "file_path": str(path),
        "exists": path.exists(),
        "text_excerpt": "",
        "paragraph_count_previewed": 0,
        "error": None,
    }

    if not path.exists():
        result["error"] = "file_not_found"
        return result

    try:
        from docx import Document
    except Exception as exc:
        result["error"] = f"python_docx_not_available: {exc}"
        return result

    try:
        document = Document(path)

        parts: list[str] = []

        for paragraph in document.paragraphs:
            text = paragraph.text.strip()

            if not text:
                continue

            parts.append(text)

            if len("\n".join(parts)) >= max_chars:
                break

        text_excerpt = "\n".join(parts)

        result["text_excerpt"] = text_excerpt[:max_chars]
        result["paragraph_count_previewed"] = len(parts)

        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result


def _read_pdf_preview(
    file_path: str,
    max_pages: int = 8,
    max_chars: int = 12000,
) -> dict[str, Any]:
    path = Path(file_path)

    result: dict[str, Any] = {
        "kind": "pdf_text_preview",
        "file_path": str(path),
        "exists": path.exists(),
        "text_excerpt": "",
        "pages_previewed": 0,
        "error": None,
    }

    if not path.exists():
        result["error"] = "file_not_found"
        return result

    try:
        from pypdf import PdfReader
    except Exception as exc:
        result["error"] = f"pypdf_not_available: {exc}"
        return result

    try:
        reader = PdfReader(str(path))
        parts: list[str] = []

        page_count = len(reader.pages)
        pages_to_read = min(page_count, max_pages)

        for page_index in range(pages_to_read):
            page = reader.pages[page_index]

            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""

            text = text.strip()

            if text:
                parts.append(f"--- page {page_index + 1} ---\n{text}")

            if len("\n\n".join(parts)) >= max_chars:
                break

        text_excerpt = "\n\n".join(parts)

        result["text_excerpt"] = text_excerpt[:max_chars]
        result["pages_previewed"] = pages_to_read

        if not result["text_excerpt"]:
            result["error"] = "no_text_layer_or_scanned_pdf"

        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result


def _collect_preview_columns_and_text(
    preview: dict[str, Any],
) -> tuple[list[str], list[str]]:
    columns: list[str] = []
    text_parts: list[str] = []

    kind = preview.get("kind")

    if kind == "csv_table_preview":
        columns.extend(preview.get("columns") or [])

        for row in preview.get("sample_rows") or []:
            text_parts.append(json.dumps(row, ensure_ascii=False))

    elif kind in {"xlsx_table_preview", "xls_table_preview"}:
        for sheet in preview.get("sheets") or []:
            columns.extend(sheet.get("columns") or [])

            for row in sheet.get("sample_rows") or []:
                text_parts.append(json.dumps(row, ensure_ascii=False))

    elif kind in {"docx_text_preview", "pdf_text_preview"}:
        text_parts.append(preview.get("text_excerpt") or "")

    return columns, text_parts


def _detect_attachment_signals(preview: dict[str, Any]) -> dict[str, Any]:
    columns, text_parts = _collect_preview_columns_and_text(preview)

    normalized_columns = set()

    for column in columns:
        text = str(column).strip().lower()

        if not text:
            continue

        # Не считаем случайные числовые значения заголовками колонок.
        if text.replace(".", "", 1).isdigit():
            continue

        # Не тащим слишком длинные описательные строки в detected_columns.
        if len(text) > 80:
            continue

        normalized_columns.add(text)

    full_text = "\n".join(text_parts).lower()

    def has_column(*names: str) -> bool:
        return any(name.lower() in normalized_columns for name in names)

    def has_text(*terms: str) -> bool:
        return any(term.lower() in full_text for term in terms)

    event_id_present = (
        has_column("event_id", "downtime_event_id", "id события")
        or has_text("event_id", "id события", "идентификатор события")
    )

    line_id_present = (
        has_column("line_id", "production_line_id", "id линии")
        or has_text("line_id", "id линии", "линия")
    )

    equipment_id_present = (
        has_column("equipment_id", "asset_id", "machine_id", "id оборудования")
        or has_text("equipment_id", "id оборудования", "оборудование")
    )

    start_timestamp_present = (
        has_column("downtime_start", "start_time", "started_at", "начало простоя")
        or has_text("downtime_start", "start_time", "начало простоя")
    )

    end_timestamp_present = (
        has_column("downtime_end", "end_time", "ended_at", "конец простоя")
        or has_text("downtime_end", "end_time", "конец простоя")
    )

    duration_present = (
        has_column("duration_min", "duration_minutes", "downtime_minutes", "длительность")
        or has_text("duration_min", "duration_minutes", "длительность простоя")
    )

    reason_present = (
        has_column("reason_code", "reason_description", "downtime_reason", "причина простоя")
        or has_text("reason_code", "reason_description", "причина простоя", "причины простоев")
    )

    cost_present = (
        has_column("cost_per_hour_rub", "estimated_loss_rub", "loss_rub", "стоимость часа")
        or has_text("cost_per_hour_rub", "estimated_loss_rub", "стоимость часа", "baseline", "потери")
    )

    downtime_log_detected = any(
        [
            event_id_present,
            start_timestamp_present,
            end_timestamp_present,
            duration_present,
            reason_present,
        ]
    )

    all_minimum_d002_fields_present = all(
        [
            event_id_present,
            line_id_present,
            equipment_id_present,
            start_timestamp_present,
            end_timestamp_present,
            duration_present,
            reason_present,
        ]
    )

    return {
        "downtime_log_detected": downtime_log_detected,
        "event_id_present": event_id_present,
        "line_id_present": line_id_present,
        "equipment_id_present": equipment_id_present,
        "start_timestamp_present": start_timestamp_present,
        "end_timestamp_present": end_timestamp_present,
        "duration_present": duration_present,
        "reason_present": reason_present,
        "cost_present": cost_present,
        "all_minimum_d002_fields_present": all_minimum_d002_fields_present,
        "detected_columns": sorted(normalized_columns),
    }


def _build_attachment_data_previews(
    attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []

    for attachment in attachments:
        original_filename = attachment.get("original_filename") or ""
        file_path = attachment.get("file_path") or ""
        suffix = Path(original_filename or file_path).suffix.lower()

        item: dict[str, Any] = {
            "attachment_id": attachment.get("id"),
            "input_pack_id": attachment.get("input_pack_id"),
            "original_filename": original_filename,
            "stored_filename": attachment.get("stored_filename"),
            "file_type": attachment.get("file_type"),
            "uploaded_at": attachment.get("uploaded_at"),
            "file_path": file_path,
            "suffix": suffix,
            "preview": None,
            "detected_signals": None,
        }

        if not file_path:
            item["preview"] = {
                "kind": "missing_file_path",
                "error": "attachment_file_path_is_empty",
            }
            item["detected_signals"] = _detect_attachment_signals(item["preview"])
            previews.append(item)
            continue

        if suffix in {".xlsx", ".xlsm"}:
            preview = _read_xlsx_preview(file_path)

        elif suffix == ".xls":
            preview = _read_xls_preview(file_path)

        elif suffix == ".csv":
            preview = _read_csv_preview(file_path)

        elif suffix == ".docx":
            preview = _read_docx_preview(file_path)

        elif suffix == ".pdf":
            preview = _read_pdf_preview(file_path)

        else:
            preview = {
                "kind": "unsupported_preview",
                "file_path": file_path,
                "exists": Path(file_path).exists(),
                "error": "unsupported_file_type",
                "supported_formats": [
                    ".xlsx",
                    ".xlsm",
                    ".xls",
                    ".csv",
                    ".docx",
                    ".pdf",
                ],
            }

        item["preview"] = preview
        item["detected_signals"] = _detect_attachment_signals(preview)

        previews.append(item)

    return previews

def _build_attachment_evidence_summary(
    attachment_data_previews: list[dict[str, Any]],
) -> dict[str, Any]:
    field_sources: dict[str, list[str]] = {
        "event_id": [],
        "line_id": [],
        "equipment_id": [],
        "start_timestamp": [],
        "end_timestamp": [],
        "duration": [],
        "downtime_reason": [],
        "cost_or_loss": [],
    }

    downtime_log_files: list[str] = []
    technical_regulation_files: list[str] = []
    commission_act_files: list[str] = []
    detected_columns_by_file: list[dict[str, Any]] = []

    for item in attachment_data_previews:
        filename = (
            item.get("original_filename")
            or item.get("stored_filename")
            or f"attachment_{item.get('attachment_id')}"
        )

        preview = item.get("preview") or {}
        signals = item.get("detected_signals") or {}

        if signals.get("downtime_log_detected"):
            downtime_log_files.append(filename)

        if signals.get("event_id_present"):
            field_sources["event_id"].append(filename)

        if signals.get("line_id_present"):
            field_sources["line_id"].append(filename)

        if signals.get("equipment_id_present"):
            field_sources["equipment_id"].append(filename)

        if signals.get("start_timestamp_present"):
            field_sources["start_timestamp"].append(filename)

        if signals.get("end_timestamp_present"):
            field_sources["end_timestamp"].append(filename)

        if signals.get("duration_present"):
            field_sources["duration"].append(filename)

        if signals.get("reason_present"):
            field_sources["downtime_reason"].append(filename)

        if signals.get("cost_present"):
            field_sources["cost_or_loss"].append(filename)

        detected_columns = signals.get("detected_columns") or []

        if detected_columns:
            detected_columns_by_file.append(
                {
                    "filename": filename,
                    "detected_columns": detected_columns,
                }
            )

        text_excerpt = ""

        if preview.get("kind") in {"docx_text_preview", "pdf_text_preview"}:
            text_excerpt = (preview.get("text_excerpt") or "").lower()

        if any(
            term in text_excerpt
            for term in [
                "технический регламент",
                "регламент фиксации",
                "регламент остановок",
                "порядок фиксации",
            ]
        ):
            technical_regulation_files.append(filename)

        if any(
            term in text_excerpt
            for term in [
                "акт комиссии",
                "комиссия",
                "расследование причин",
                "выводы комиссии",
            ]
        ):
            commission_act_files.append(filename)

    minimum_downtime_export_fields_present = all(
        [
            bool(field_sources["event_id"]),
            bool(field_sources["line_id"]),
            bool(field_sources["equipment_id"]),
            bool(field_sources["start_timestamp"]),
            bool(field_sources["end_timestamp"]),
            bool(field_sources["duration"]),
            bool(field_sources["downtime_reason"]),
        ]
    )

    return {
        "downtime_log_files": sorted(set(downtime_log_files)),
        "technical_regulation_files": sorted(set(technical_regulation_files)),
        "commission_act_files": sorted(set(commission_act_files)),
        "field_sources": {
            key: sorted(set(value))
            for key, value in field_sources.items()
        },
        "minimum_downtime_export_fields_present": minimum_downtime_export_fields_present,
        "detected_columns_by_file": detected_columns_by_file,
        "d001_interpretation_rules": {
            "if_minimum_downtime_export_fields_present": [
                "Do not say that event_id, line_id, equipment_id, start/end timestamps, duration or downtime reason are missing.",
                "Treat identifiers and timestamps as present in the test export.",
                "Remaining questions should be about representativeness, stability of the export format, data quality, timezone, completeness, security approval and baseline confirmation.",
                "Data readiness may remain MEDIUM because the export is test evidence, but missing-data wording must not contradict the attachment evidence.",
            ],
            "economics_rule": [
                "If cost_per_hour_rub or estimated_loss_rub is present, treat economy as partially evidenced.",
                "Still require finance confirmation of cost per downtime hour, monthly baseline and target effect.",
            ],
            "pdf_word_rule": [
                "Use PDF/DOCX text as evidence for commission conclusions, technical regulation, downtime cause classification and process documentation.",
                "If technical_regulation_files is not empty, treat the attachments as evidence that a technical regulation / технический регламент or equivalent downtime recording procedure exists.",
                "Do not treat PDF/DOCX as structured event logs unless table fields are explicitly detected.",
            ],
        },
    }

def _replace_markdown_table_row(
    markdown: str,
    row_label: str,
    replacement_row: str,
) -> str:
    lines = markdown.splitlines()

    for index, line in enumerate(lines):
        if line.lstrip().startswith("|") and row_label in line:
            lines[index] = replacement_row

    return "\n".join(lines)


def _collect_evidence_files(evidence_summary: dict[str, Any]) -> str:
    files: set[str] = set()

    for filename in evidence_summary.get("downtime_log_files", []) or []:
        files.add(str(filename))

    for filename in evidence_summary.get("commission_act_files", []) or []:
        files.add(str(filename))

    for filenames in (evidence_summary.get("field_sources", {}) or {}).values():
        for filename in filenames or []:
            files.add(str(filename))

    if not files:
        return "приложенные файлы"

    return ", ".join(sorted(files))


def _postprocess_d001_with_attachment_evidence(
    markdown: str,
    evidence_summary: dict[str, Any] | None,
) -> str:
    """
    Детерминированная защита D-001 от противоречия с evidence summary.

    Если вложения содержат минимальный набор полей downtime export,
    D-001 не должен писать, что event_id, line_id, equipment_id,
    start/end timestamps, duration или downtime reason отсутствуют.
    """

    if not evidence_summary:
        return markdown

    if not evidence_summary.get("minimum_downtime_export_fields_present"):
        return markdown

    evidence_files = _collect_evidence_files(evidence_summary)

    result = markdown

    result = _replace_markdown_table_row(
        result,
        "Главный вывод",
        "| Главный вывод           | Проект можно продолжать с ограничениями: минимальный набор идентификаторов и временных меток представлен в тестовой выгрузке; требуется подтвердить репрезентативность выгрузки, стабильность ID, timezone/формат timestamp, качество данных, baseline и правила ИБ. |",
    )

    result = _replace_markdown_table_row(
        result,
        "Идентификаторы объектов",
        f"| Идентификаторы объектов     | YES_WITH_VALIDATION           | event_id, line_id, equipment_id представлены в тестовой выгрузке и приложенных материалах: {evidence_files} | Подтвердить стабильность ID в реальных системах; ID наряда ремонта — опционально, если требуется связка с ТОиР | LOW/MEDIUM       |",
    )

    result = _replace_markdown_table_row(
        result,
        "Временные метки",
        f"| Временные метки             | YES_WITH_VALIDATION           | start_time, end_time, duration_min представлены в тестовой выгрузке и приложенных материалах: {evidence_files} | Подтвердить timezone, единый формат timestamp, полноту периода и отсутствие систематических пропусков | LOW/MEDIUM       |",
    )

    result = _replace_markdown_table_row(
        result,
        "MUST      | Подтвердить ID события простоя",
        "| MUST      | Подтвердить стабильность ID события простоя в реальных системах | Для проверки связности выгрузки при промышленном использовании | Руководитель производства / владелец данных |",
    )

    result = _replace_markdown_table_row(
        result,
        "MUST      | Уточнить временные метки событий",
        "| MUST      | Подтвердить timezone, формат timestamp и полноту периода выгрузки | Для корректного расчёта длительности простоев и сопоставления событий | Руководитель производства / владелец данных |",
    )

    replacements = {
        "Проект можно продолжать с ограничениями, требуется уточнение идентификаторов и временных меток, а также согласование правил ИБ.": (
            "Проект можно продолжать с ограничениями: минимальный набор идентификаторов и временных меток представлен в тестовой выгрузке; требуется подтвердить репрезентативность выгрузки, стабильность ID, timezone/формат timestamp, качество данных, baseline и правила ИБ."
        ),
        "требуется уточнение идентификаторов и временных меток": (
            "требуется подтверждение репрезентативности выгрузки, стабильности ID, timezone/формата timestamp и правил ИБ"
        ),
        "необходимо уточнить идентификаторы объектов и временные метки": (
            "необходимо подтвердить репрезентативность выгрузки, стабильность ID, timezone/формат timestamp и качество данных"
        ),
        "необходимо уточнить идентификаторы объектов и временные метки, а также согласовать правила ИБ": (
            "необходимо подтвердить репрезентативность выгрузки, стабильность ID, timezone/формат timestamp, качество данных и согласовать правила ИБ"
        ),
        "получить ограниченную обезличенную выгрузку данных, проверить идентификаторы и временные метки": (
            "использовать ограниченную обезличенную тестовую выгрузку данных, подтвердить стабильность идентификаторов, timezone/формат timestamp и полноту периода"
        ),
        "ID события, ID наряда ремонта": (
            "стабильность ID события в реальных системах; ID наряда ремонта — опционально для связки с ТОиР"
        ),
        "Начало и конец простоя | Временные метки событий": (
            "start_time, end_time, duration_min | Подтвердить timezone, формат timestamp и полноту периода"
        ),
        "Подтвердить ID события простоя": (
            "Подтвердить стабильность ID события простоя в реальных системах"
        ),
        "Уточнить временные метки событий": (
            "Подтвердить timezone, формат timestamp и полноту периода выгрузки"
        ),
        "Доступны данные о событиях простоев, частично подтверждены ID и временные метки": (
            "Тестовая выгрузка и приложенные материалы содержат события простоев, event_id, line_id, equipment_id, start_time, end_time, duration_min и причины простоев; требуется подтвердить репрезентативность, стабильность формата и качество данных"
        ),
        "частично подтверждены ID и временные метки": (
            "представлены ID и временные метки в тестовой выгрузке; требуется подтверждение стабильности формата"
        ),
        "необходимость подтверждения ID, временных меток и согласования правил ИБ": (
            "необходимость подтверждения стабильности ID, timezone/формата timestamp, полноты периода, качества данных и согласования правил ИБ"
        ),
        "подтверждение наличия всех обязательных идентификаторов и временных меток": (
            "подтверждение стабильности обязательных идентификаторов, timezone/формата timestamp, полноты периода и качества данных"
        ),
        "ID оборудования, ID линии, ID события и временные метки относятся к готовности данных": (
            "ID оборудования, ID линии, ID события и временные метки представлены в тестовой выгрузке; для D-002 требуется подтвердить стабильность формата, полноту периода и качество данных"
        ),
        "Однако необходимо уточнить идентификаторы объектов и временные метки, а также согласовать правила ИБ.": (
            "Минимальные идентификаторы и временные метки представлены в тестовой выгрузке; необходимо подтвердить репрезентативность, стабильность ID, timezone/формат timestamp, качество данных и согласовать правила ИБ."
        ),
        "Необходимо подтвердить ID события, оборудования и линии, а также временные метки.": (
            "ID события, оборудования, линии и временные метки представлены в тестовой выгрузке; необходимо подтвердить стабильность ID, timezone/формат timestamp и полноту периода."
        ),
        "Невозможность связать событие простоя с оборудованием, линией, временной меткой, длительностью и причиной в единой выгрузке.": (
            "Риск нестабильной связки события простоя с оборудованием, линией, временной меткой, длительностью и причиной при переходе от тестовой выгрузки к промышленному регулярному экспорту."
        ),
    }

    for old_text, new_text in replacements.items():
        result = result.replace(old_text, new_text)

    evidence_note = f"""
> Комментарий по приложенным материалам: загруженные файлы содержат минимальный набор полей для анализа простоев: event_id, line_id, equipment_id, start_time, end_time, duration_min и reason_code/reason_description. Источники: {evidence_files}. Поэтому D-001 считает эти поля представленными в тестовой выгрузке; оставшиеся ограничения относятся к репрезентативности, стабильности формата, качеству данных, timezone, baseline и правилам ИБ.
""".strip()

    if "Комментарий по приложенным материалам:" not in result and "## 2. Что уже известно" in result:
        result = result.replace(
            "## 2. Что уже известно",
            f"{evidence_note}\n\n---\n\n## 2. Что уже известно",
            1,
        )

    return result


def get_latest_input_pack_for_d001(diagnostic_run_id: int) -> dict[str, Any]:
    input_pack_row = _get_latest_input_pack_row(diagnostic_run_id)
    input_pack = dict(input_pack_row)

    raw_payload = _load_json_object(input_pack.get("raw_payload"), "raw_payload")

    _validate_raw_payload_for_d001(
        input_pack_id=int(input_pack["id"]),
        raw_payload=raw_payload,
    )

    attachments = _get_attachments_for_input_pack(int(input_pack["id"]))
    attachment_data_previews = _build_attachment_data_previews(attachments)
    attachment_evidence_summary = _build_attachment_evidence_summary(
        attachment_data_previews
    )

    return {
        "input_pack": input_pack,
        "raw_payload": raw_payload,
        "attachments": attachments,
        "attachment_data_previews": attachment_data_previews,
        "attachment_evidence_summary": attachment_evidence_summary,
    }


def ensure_normalized_payload(diagnostic_run_id: int) -> dict[str, Any]:
    data = get_latest_input_pack_for_d001(diagnostic_run_id)
    input_pack = data["input_pack"]
    raw_payload = data["raw_payload"]

    if raw_payload.get("brief_type") == "industrial_ai":
        return raw_payload

    if input_pack.get("normalized_payload"):
        return _load_json_object(
            input_pack["normalized_payload"],
            "normalized_payload",
        )

    return normalize_input_pack(int(input_pack["id"]))


def get_existing_d001_result(diagnostic_run_id: int) -> str | None:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)
    return diagnostic_run["d001_result"] or None


def build_d001_prompt_input(diagnostic_run_id: int) -> str:
    diagnostic_run = _get_diagnostic_run_row(diagnostic_run_id)
    data = get_latest_input_pack_for_d001(diagnostic_run_id)

    input_pack = data["input_pack"]
    raw_payload = data["raw_payload"]
    normalized_payload = ensure_normalized_payload(diagnostic_run_id)

    safe_diagnostic_run = _sanitize_diagnostic_run(diagnostic_run)

    safe_input_pack = {
        "id": input_pack.get("id"),
        "diagnostic_run_id": input_pack.get("diagnostic_run_id"),
        "status": input_pack.get("status"),
        "created_at": input_pack.get("created_at"),
        "updated_at": input_pack.get("updated_at"),
        "brief_type": raw_payload.get("brief_type"),
        "brief_version": raw_payload.get("brief_version"),
        "source": raw_payload.get("source"),
        "submitted_at": raw_payload.get("submitted_at"),
        "raw_payload_non_empty": _count_non_empty(raw_payload),
        "industrial_ai_non_empty": _count_non_empty(
            raw_payload.get("industrial_ai", {})
        ),
    }

    return f"""
# INPUT FOR D-001 DIAGNOSTIC ASSESSMENT AGENT

## Diagnostic Run

{json.dumps(safe_diagnostic_run, ensure_ascii=False, indent=2)}

## Selected Input Pack

{json.dumps(safe_input_pack, ensure_ascii=False, indent=2)}

## Raw Diagnostic Input Pack / Industrial AI Brief

This is the source of truth.

If `brief_type` is `industrial_ai`, analyze the `industrial_ai` object directly.
Do not treat the brief as empty if `industrial_ai` contains filled fields.
Do not let the normalized helper payload override the raw Industrial AI Brief.

{json.dumps(raw_payload, ensure_ascii=False, indent=2)}

## Normalized Diagnostic Input Pack

Use this only as a helper.
If this section conflicts with the raw payload above, the raw payload wins.

{json.dumps(normalized_payload, ensure_ascii=False, indent=2)}

## Attachments Summary

{json.dumps(data["attachments"], ensure_ascii=False, indent=2)}

## Attachments Summary

{json.dumps(data["attachments"], ensure_ascii=False, indent=2)}

## Attachment Evidence Summary

This section is a deterministic summary of parsed attachment evidence.

Use this section as the primary evidence layer for deciding whether critical data fields
are present in uploaded files.

If `minimum_downtime_export_fields_present` is true:
- do not say that event_id is missing;
- do not say that line_id is missing;
- do not say that equipment_id is missing;
- do not say that start/end timestamps are missing;
- do not say that duration is missing;
- do not say that downtime reason is missing.

You may still require confirmation of:
- export representativeness;
- stability of IDs across real systems;
- timezone and timestamp format;
- duplicates and missing values;
- completeness of the period;
- security approval;
- finance-confirmed baseline.

{json.dumps(data.get("attachment_evidence_summary", {}), ensure_ascii=False, indent=2)}

## Attachments Data Preview

This section contains parsed previews of supported attachments:
Excel/XLSX/XLSM/XLS, CSV, Word/DOCX and PDF.

For Excel/CSV files, use columns and sample rows as evidence of data availability.
For Word/PDF files, use extracted text as evidence of documented process, constraints,
baseline, security requirements, data field descriptions or export specification.

If the preview contains event_id, line_id, equipment_id, start/end timestamps,
duration and downtime reason fields, do not state that these fields are missing.
You may still require confirmation that the export is representative, complete,
stable, approved by the client and safe to process.

{json.dumps(data.get("attachment_data_previews", []), ensure_ascii=False, indent=2)}
""".strip()


def run_d001_diagnostic_assessment(
    diagnostic_run_id: int,
    force_rebuild: bool = False,
) -> str:
    existing_result = get_existing_d001_result(diagnostic_run_id)

    if existing_result and not force_rebuild:
        return existing_result

    validate_d001_input(diagnostic_run_id)

    update_diagnostic_status(
        diagnostic_run_id=diagnostic_run_id,
        status=DIAGNOSTIC_STATUS_D001_RUNNING,
    )

    prompt_input = build_d001_prompt_input(diagnostic_run_id)

    result = run_agent_with_prompt(
        agent_prompt_name=D001_AGENT_PROMPT_NAME,
        user_input=prompt_input,
    )

    d001_data = get_latest_input_pack_for_d001(diagnostic_run_id)
    result = _postprocess_d001_with_attachment_evidence(
        markdown=result,
        evidence_summary=d001_data.get("attachment_evidence_summary", {}),
    )

    save_d001_result(
        diagnostic_run_id=diagnostic_run_id,
        result=result,
    )

    update_diagnostic_status(
        diagnostic_run_id=diagnostic_run_id,
        status=DIAGNOSTIC_STATUS_D001_COMPLETED,
    )

    return result