import os
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from app.core.config import settings

XLSX_REPORTS_DIR = os.path.join(settings.LOCAL_STORAGE_PATH, "reports")
os.makedirs(XLSX_REPORTS_DIR, exist_ok=True)

CHARCOAL_FILL = PatternFill(start_color="183B66", end_color="183B66", fill_type="solid")
HEADER_FONT = Font(color="64A844", bold=True, size=11)
GROUP_LABEL_HEADERS = {"day": "Date", "week": "Week", "month": "Month", "employee": "Employee", "branch": "Branch"}


def generate_breakdown_xlsx(tenant_name: str, group_by: str, rows: list[dict]) -> str:
    """
    One row per actual payment — never a rolled-up total. Sectioned/sorted by
    group_by, but every row still shows exactly who paid, what type (individual
    or group), who collected it, and when.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = f"{group_by.capitalize()} Breakdown"[:31]  # Excel sheet name limit

    ws.merge_cells("A1:H1")
    ws["A1"] = f"Udhayam MFI — {tenant_name} — {group_by.capitalize()}-wise Collections (detailed)"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Generated {datetime.utcnow().strftime('%d %b %Y, %I:%M %p UTC')} — {len(rows)} payments"
    ws["A2"].font = Font(italic=True, size=9, color="888888")

    header_row = 4
    group_col_label = GROUP_LABEL_HEADERS.get(group_by, "Group")
    headers = [group_col_label, "Payment Date", "Customer / Group", "Type", "Employee", "Branch", "Loan #", "Receipt #", "Amount (₹)"]
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col, value=text)
        cell.fill = CHARCOAL_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")

    total_amount = 0
    for i, row in enumerate(rows, start=header_row + 1):
        ws.cell(row=i, column=1, value=row["group_label"])
        ws.cell(row=i, column=2, value=row["date"])
        ws.cell(row=i, column=3, value=row["payer_name"])
        ws.cell(row=i, column=4, value=row["payer_type"])
        ws.cell(row=i, column=5, value=row["employee_name"])
        ws.cell(row=i, column=6, value=row["branch_name"])
        ws.cell(row=i, column=7, value=row["loan_number"])
        ws.cell(row=i, column=8, value=row["receipt_number"])
        ws.cell(row=i, column=9, value=row["amount"]).number_format = "#,##0.00"
        total_amount += row["amount"]

    total_row = header_row + len(rows) + 1
    ws.cell(row=total_row, column=1, value="TOTAL").font = Font(bold=True)
    ws.cell(row=total_row, column=8, value=f"{len(rows)} payments").font = Font(bold=True)
    ws.cell(row=total_row, column=9, value=round(total_amount, 2)).font = Font(bold=True)
    ws.cell(row=total_row, column=9).number_format = "#,##0.00"

    widths = [14, 13, 22, 11, 18, 16, 12, 14, 14]
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width

    filename = f"breakdown-{group_by}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.xlsx"
    file_path = os.path.join(XLSX_REPORTS_DIR, filename)
    wb.save(file_path)
    return file_path
