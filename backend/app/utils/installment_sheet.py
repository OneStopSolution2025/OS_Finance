import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from app.core.config import settings

SHEETS_DIR = os.path.join(settings.LOCAL_STORAGE_PATH, "installment-sheets")
os.makedirs(SHEETS_DIR, exist_ok=True)

LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "logo-icon.png")

INK = colors.HexColor("#1B1B1B")
NAVY = colors.HexColor("#183B66")
GREEN = colors.HexColor("#64A844")
GREY = colors.HexColor("#888888")


def generate_installment_sheet_pdf(product_name: str, sample_amount: float, product, rows: list[dict]) -> str:
    """
    A printable projected installment sheet for a loan product at a sample
    principal amount — for handing to a prospective customer to show exactly
    what their repayments would look like before they've actually applied.
    """
    filename = f"installment-sheet-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.pdf"
    file_path = os.path.join(SHEETS_DIR, filename)
    c = canvas.Canvas(file_path, pagesize=A4)
    width, height = A4

    c.setFillColor(NAVY)
    c.rect(0, height - 30 * mm, width, 30 * mm, fill=1, stroke=0)
    if os.path.exists(LOGO_PATH):
        logo_size = 18 * mm
        pad = 2 * mm
        logo_x, logo_y = 16 * mm, height - 25 * mm
        c.setFillColor(colors.white)
        c.roundRect(logo_x - pad, logo_y - pad, logo_size + 2 * pad, logo_size + 2 * pad, 2.5 * mm, fill=1, stroke=0)
        c.drawImage(ImageReader(LOGO_PATH), logo_x, logo_y, width=logo_size, height=logo_size, mask="auto", preserveAspectRatio=True)
        text_x = logo_x + logo_size + 6 * mm
    else:
        text_x = 16 * mm
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(text_x, height - 14 * mm, "UDHAYAM MFI")
    c.setFont("Helvetica", 10)
    c.drawString(text_x, height - 21 * mm, f"Projected Installment Sheet — {product_name}")

    y = height - 38 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(16 * mm, y, f"Sample principal amount: Rs. {sample_amount:,.2f}")
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 16 * mm, y, f"Generated {datetime.utcnow().strftime('%d %b %Y')}")
    y -= 6 * mm
    interest_label = product.custom_interest_label if product.interest_type.value == "other" else product.interest_type.value.capitalize()
    c.setFont("Helvetica", 9)
    c.setFillColor(GREY)
    c.drawString(16 * mm, y, f"{product.interest_rate_annual}% p.a. ({interest_label}) · {product.tenure_months} months · {product.repayment_frequency} repayments")
    y -= 10 * mm

    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(GREY)
    col_x = [16, 45, 85, 125, 160]
    headers = ["#", "DUE DATE", "PRINCIPAL", "INTEREST", "TOTAL DUE"]
    for x, h in zip(col_x, headers):
        c.drawString(x * mm, y, h)
    y -= 3 * mm
    c.line(16 * mm, y, width - 16 * mm, y)
    y -= 6 * mm

    c.setFont("Helvetica", 9)
    total_principal = total_interest = total_due = 0.0
    for row in rows:
        if y < 20 * mm:
            c.showPage()
            y = height - 20 * mm
            c.setFont("Helvetica-Bold", 9)
            c.setFillColor(GREY)
            for x, h in zip(col_x, headers):
                c.drawString(x * mm, y, h)
            y -= 3 * mm
            c.line(16 * mm, y, width - 16 * mm, y)
            y -= 6 * mm
            c.setFont("Helvetica", 9)
        c.setFillColor(INK)
        c.drawString(col_x[0] * mm, y, str(row["installment_no"]))
        c.drawString(col_x[1] * mm, y, row["due_date"])
        c.drawString(col_x[2] * mm, y, f"Rs. {row['principal_due']:,.2f}")
        c.drawString(col_x[3] * mm, y, f"Rs. {row['interest_due']:,.2f}")
        c.drawString(col_x[4] * mm, y, f"Rs. {row['total_due']:,.2f}")
        total_principal += row["principal_due"]
        total_interest += row["interest_due"]
        total_due += row["total_due"]
        y -= 6 * mm

    y -= 2 * mm
    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.line(16 * mm, y, width - 16 * mm, y)
    y -= 7 * mm
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(NAVY)
    c.drawString(col_x[0] * mm, y, "TOTAL")
    c.drawString(col_x[2] * mm, y, f"Rs. {total_principal:,.2f}")
    c.drawString(col_x[3] * mm, y, f"Rs. {total_interest:,.2f}")
    c.drawString(col_x[4] * mm, y, f"Rs. {total_due:,.2f}")

    c.setFillColor(GREY)
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(width / 2, 12 * mm, "This is a projected schedule for illustration — actual figures depend on the disbursal date and exact amount taken.")

    c.showPage()
    c.save()
    return file_path


def generate_installment_sheet_xlsx(product_name: str, sample_amount: float, product, rows: list[dict]) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = "Installment Sheet"[:31]

    ws.merge_cells("A1:E1")
    ws["A1"] = f"Udhayam MFI — Projected Installment Sheet — {product_name}"
    ws["A1"].font = Font(bold=True, size=14)
    interest_label = product.custom_interest_label if product.interest_type.value == "other" else product.interest_type.value.capitalize()
    ws["A2"] = f"Sample principal: Rs. {sample_amount:,.2f} — {product.interest_rate_annual}% p.a. ({interest_label}) — {product.tenure_months} months — {product.repayment_frequency}"
    ws["A2"].font = Font(italic=True, size=10, color="666666")

    header_row = 4
    headers = ["#", "Due Date", "Principal (Rs.)", "Interest (Rs.)", "Total Due (Rs.)"]
    fill = PatternFill(start_color="183B66", end_color="183B66", fill_type="solid")
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col, value=text)
        cell.fill = fill
        cell.font = Font(color="64A844", bold=True)
        cell.alignment = Alignment(horizontal="center")

    total_principal = total_interest = total_due = 0.0
    for i, row in enumerate(rows, start=header_row + 1):
        ws.cell(row=i, column=1, value=row["installment_no"])
        ws.cell(row=i, column=2, value=row["due_date"])
        ws.cell(row=i, column=3, value=row["principal_due"]).number_format = "#,##0.00"
        ws.cell(row=i, column=4, value=row["interest_due"]).number_format = "#,##0.00"
        ws.cell(row=i, column=5, value=row["total_due"]).number_format = "#,##0.00"
        total_principal += row["principal_due"]
        total_interest += row["interest_due"]
        total_due += row["total_due"]

    total_row = header_row + len(rows) + 1
    ws.cell(row=total_row, column=1, value="TOTAL").font = Font(bold=True)
    ws.cell(row=total_row, column=3, value=round(total_principal, 2)).font = Font(bold=True)
    ws.cell(row=total_row, column=3).number_format = "#,##0.00"
    ws.cell(row=total_row, column=4, value=round(total_interest, 2)).font = Font(bold=True)
    ws.cell(row=total_row, column=4).number_format = "#,##0.00"
    ws.cell(row=total_row, column=5, value=round(total_due, 2)).font = Font(bold=True)
    ws.cell(row=total_row, column=5).number_format = "#,##0.00"

    for col, w in zip("ABCDE", [8, 14, 16, 16, 16]):
        ws.column_dimensions[col].width = w

    filename = f"installment-sheet-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.xlsx"
    file_path = os.path.join(SHEETS_DIR, filename)
    wb.save(file_path)
    return file_path
