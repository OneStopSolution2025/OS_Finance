import os
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from app.core.config import settings

XLSX_REPORTS_DIR = os.path.join(settings.LOCAL_STORAGE_PATH, "reports")
os.makedirs(XLSX_REPORTS_DIR, exist_ok=True)

CHARCOAL_FILL = PatternFill(start_color="231F20", end_color="231F20", fill_type="solid")
HEADER_FONT = Font(color="FFB600", bold=True, size=12)
LABEL_HEADERS = {"day": "Date", "week": "Week", "month": "Month", "employee": "Employee"}


def generate_breakdown_xlsx(tenant_name: str, group_by: str, rows: list[dict]) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = f"{group_by.capitalize()} Breakdown"

    ws.merge_cells("A1:C1")
    ws["A1"] = f"OS Finances — {tenant_name} — {group_by.capitalize()}-wise Collections"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Generated {datetime.utcnow().strftime('%d %b %Y, %I:%M %p UTC')}"
    ws["A2"].font = Font(italic=True, size=9, color="888888")

    header_row = 4
    headers = [LABEL_HEADERS.get(group_by, "Label"), "Amount Collected (₹)", "Payment Count"]
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col, value=text)
        cell.fill = CHARCOAL_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")

    total_amount = 0
    total_count = 0
    for i, row in enumerate(rows, start=header_row + 1):
        ws.cell(row=i, column=1, value=row["label"])
        ws.cell(row=i, column=2, value=row["amount"]).number_format = "#,##0.00"
        ws.cell(row=i, column=3, value=row["payment_count"])
        total_amount += row["amount"]
        total_count += row["payment_count"]

    total_row = header_row + len(rows) + 1
    ws.cell(row=total_row, column=1, value="TOTAL").font = Font(bold=True)
    ws.cell(row=total_row, column=2, value=round(total_amount, 2)).font = Font(bold=True)
    ws.cell(row=total_row, column=2).number_format = "#,##0.00"
    ws.cell(row=total_row, column=3, value=total_count).font = Font(bold=True)

    for col in range(1, 4):
        ws.column_dimensions[get_column_letter(col)].width = 26

    filename = f"breakdown-{group_by}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.xlsx"
    file_path = os.path.join(XLSX_REPORTS_DIR, filename)
    wb.save(file_path)
    return file_path
