import os
from datetime import datetime
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas

from reportlab.lib.utils import ImageReader

from app.core.config import settings

REPORTS_DIR = os.path.join(settings.LOCAL_STORAGE_PATH, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "logo-icon.png")

INK = colors.HexColor("#1B1B1B")
NAVY = colors.HexColor("#183B66")
GREEN = colors.HexColor("#64A844")
GREY = colors.HexColor("#888888")


def draw_header(c, width, height, subtitle: str, generated_at: str):
    """Shared navy header band with the Udhayam logo, used by both report types below."""
    c.setFillColor(NAVY)
    c.rect(0, height - 32 * mm, width, 32 * mm, fill=1, stroke=0)

    if os.path.exists(LOGO_PATH):
        logo_size = 20 * mm
        logo_x, logo_y = 18 * mm, height - 27 * mm
        pad = 2 * mm
        c.setFillColor(colors.white)
        c.roundRect(logo_x - pad, logo_y - pad, logo_size + 2 * pad, logo_size + 2 * pad, 2.5 * mm, fill=1, stroke=0)
        c.drawImage(
            ImageReader(LOGO_PATH), logo_x, logo_y, width=logo_size, height=logo_size,
            mask="auto", preserveAspectRatio=True,
        )
        text_x = 18 * mm + logo_size + 5 * mm + pad
    else:
        text_x = 18 * mm

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(text_x, height - 16 * mm, "UDHAYAM MFI")
    c.setFont("Helvetica", 11)
    c.drawString(text_x, height - 24 * mm, subtitle)
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 18 * mm, height - 24 * mm, generated_at)


def generate_breakdown_pdf(tenant_name: str, group_by: str, rows: list[dict], outstanding_rows: list[dict] | None = None) -> str:
    """
    Detailed, transaction-level A4-landscape report — one row per actual
    payment (never a rolled-up total), sectioned/sorted by group_by, showing
    exactly who paid (the specific individual or group member), on-time or
    late, who collected it, and when. Followed by a highlighted section
    listing every overdue installment still unpaid — including, for group
    loans, exactly which member hasn't paid.
    """
    outstanding_rows = outstanding_rows or []
    filename = f"breakdown-{group_by}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.pdf"
    file_path = os.path.join(REPORTS_DIR, filename)
    c = canvas.Canvas(file_path, pagesize=landscape(A4))
    width, height = landscape(A4)

    label = {"day": "Day-wise", "week": "Week-wise", "month": "Month-wise", "employee": "Employee-wise", "branch": "Branch-wise"}.get(group_by, group_by)
    draw_header(c, width, height, f"{label} Collections — {tenant_name} ({len(rows)} paid, {len(outstanding_rows)} outstanding)", datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC"))

    group_col_label = {"day": "DATE", "week": "WEEK", "month": "MONTH", "employee": "EMPLOYEE", "branch": "BRANCH"}.get(group_by, "GROUP")
    col_x = [14, 38, 62, 112, 132, 165, 195, 220, 248, 272]
    headers = [group_col_label, "PAY DATE", "PAID BY", "TYPE", "GROUP", "EMPLOYEE", "LOAN #", "RECEIPT #", "STATUS", "AMOUNT"]

    def draw_section_title(y, text):
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(NAVY)
        c.drawString(14 * mm, y, text)
        return y - 7 * mm

    def draw_table_header(y):
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(GREY)
        for x, h in zip(col_x, headers):
            c.drawString(x * mm, y, h)
        return y - 6 * mm

    y = height - 40 * mm
    y = draw_section_title(y, f"Collected ({len(rows)} payments)")
    y = draw_table_header(y)

    total_amount = 0
    c.setFont("Helvetica", 8)
    for row in rows:
        if y < 18 * mm:
            c.showPage()
            y = height - 20 * mm
            y = draw_table_header(y)
            c.setFont("Helvetica", 8)
        c.setFillColor(INK)
        values = [
            row["group_label"], row["date"], row["payer_name"][:20], row["payer_type"],
            row["group_name"][:18], row["employee_name"][:16], row["loan_number"], row["receipt_number"],
            row["status"], f"Rs. {row['amount']:,.2f}",
        ]
        for x, val in zip(col_x, values):
            if val == row.get("status") and "Late" in str(val):
                c.setFillColor(colors.HexColor("#C0392B"))
            else:
                c.setFillColor(INK)
            c.drawString(x * mm, y, str(val))
        total_amount += row["amount"]
        y -= 5.5 * mm

    y -= 3 * mm
    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.line(14 * mm, y, width - 14 * mm, y)
    y -= 7 * mm
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(NAVY)
    c.drawString(14 * mm, y, f"TOTAL COLLECTED — {len(rows)} payments")
    c.drawString(272 * mm, y, f"Rs. {total_amount:,.2f}")
    y -= 14 * mm

    # ---- Outstanding / Not Paid — highlighted section ----
    if y < 40 * mm:
        c.showPage()
        y = height - 20 * mm
    c.setFillColor(colors.HexColor("#FBE4E1"))
    c.rect(10 * mm, y - 2 * mm, width - 20 * mm, 10 * mm, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#C0392B"))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(14 * mm, y + 1 * mm, f"⚠ Outstanding / Not Paid ({len(outstanding_rows)} overdue installments)")
    y -= 12 * mm

    out_col_x = [14, 45, 78, 128, 150, 185, 215, 250]
    out_headers = [group_col_label, "DUE DATE", "OWES", "TYPE", "GROUP", "EMPLOYEE", "DAYS LATE", "AMOUNT DUE"]
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(GREY)
    for x, h in zip(out_col_x, out_headers):
        c.drawString(x * mm, y, h)
    y -= 6 * mm

    total_outstanding = 0
    c.setFont("Helvetica", 8)
    for row in outstanding_rows:
        if y < 18 * mm:
            c.showPage()
            y = height - 20 * mm
        c.setFillColor(colors.HexColor("#FBE4E1"))
        c.rect(10 * mm, y - 1.3 * mm, width - 20 * mm, 5.3 * mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#C0392B"))
        values = [
            row["group_label"], row["due_date"], row["payer_name"][:20], row["payer_type"],
            row["group_name"][:18], row["employee_name"][:16], f"{row['days_overdue']}d", f"Rs. {row['amount_due']:,.2f}",
        ]
        for x, val in zip(out_col_x, values):
            c.drawString(x * mm, y, str(val))
        total_outstanding += row["amount_due"]
        y -= 5.5 * mm

    y -= 3 * mm
    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.line(14 * mm, y, width - 14 * mm, y)
    y -= 7 * mm
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(colors.HexColor("#C0392B"))
    c.drawString(14 * mm, y, f"TOTAL OUTSTANDING — {len(outstanding_rows)} unpaid")
    c.drawString(250 * mm, y, f"Rs. {total_outstanding:,.2f}")

    c.setFillColor(GREY)
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(width / 2, 10 * mm, "Generated by Udhayam MFI")

    c.showPage()
    c.save()
    return file_path


def generate_branch_report_pdf(tenant_name: str, summary: dict, par: dict, recent_loans: list, recent_payments: list) -> str:
    """Generates a printable A4 branch/tenant performance report. Returns the file path."""
    filename = f"report-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.pdf"
    file_path = os.path.join(REPORTS_DIR, filename)
    c = canvas.Canvas(file_path, pagesize=A4)
    width, height = A4

    # Header band
    draw_header(c, width, height, f"Performance Report — {tenant_name}", datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC"))

    y = height - 45 * mm

    def section_title(text):
        nonlocal y
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(18 * mm, y, text)
        c.setStrokeColor(GREEN)
        c.setLineWidth(2)
        c.line(18 * mm, y - 3 * mm, 60 * mm, y - 3 * mm)
        y -= 12 * mm

    def kv_row(label, value):
        nonlocal y
        c.setFillColor(GREY)
        c.setFont("Helvetica", 9)
        c.drawString(18 * mm, y, label)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 11)
        c.drawRightString(width - 18 * mm, y, value)
        y -= 8 * mm

    section_title("Summary")
    kv_row("Total Disbursed", f"Rs. {summary['total_disbursed']:,.2f}")
    kv_row("Total Collected", f"Rs. {summary['total_collected']:,.2f}")
    kv_row("Collection Efficiency", f"{summary['collection_efficiency_pct']}%")
    kv_row("Active Loans", str(summary['active_loans']))
    kv_row("Closed Loans", str(summary['closed_loans']))
    kv_row("Overdue Installments", str(summary['overdue_installments']))
    kv_row("Overdue Amount Outstanding", f"Rs. {summary['overdue_amount']:,.2f}")

    y -= 6 * mm
    section_title("Portfolio at Risk (by days past due)")
    for label, key in [("1–30 days", "1-30"), ("31–60 days", "31-60"), ("61–90 days", "61-90"), ("90+ days", "90+")]:
        kv_row(label, f"Rs. {par.get(key, 0):,.2f}")

    y -= 6 * mm
    section_title("Recent Loans")
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(GREY)
    c.drawString(18 * mm, y, "LOAN #")
    c.drawString(70 * mm, y, "CUSTOMER")
    c.drawString(130 * mm, y, "AMOUNT")
    c.drawString(165 * mm, y, "STATUS")
    y -= 6 * mm
    c.setFont("Helvetica", 9)
    c.setFillColor(INK)
    for l in recent_loans[:8]:
        c.drawString(18 * mm, y, l["loan_number"])
        c.drawString(70 * mm, y, l["customer"][:22])
        c.drawString(130 * mm, y, f"Rs. {l['amount']:,.0f}")
        c.drawString(165 * mm, y, l["status"].replace("_", " "))
        y -= 6 * mm
    if not recent_loans:
        c.setFillColor(GREY)
        c.drawString(18 * mm, y, "No loans yet")
        y -= 6 * mm

    y -= 8 * mm
    section_title("Recent Payments")
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(GREY)
    c.drawString(18 * mm, y, "RECEIPT #")
    c.drawString(80 * mm, y, "METHOD")
    c.drawString(140 * mm, y, "AMOUNT")
    y -= 6 * mm
    c.setFont("Helvetica", 9)
    c.setFillColor(INK)
    for p in recent_payments[:8]:
        c.drawString(18 * mm, y, p["receipt_number"])
        c.drawString(80 * mm, y, p["method"])
        c.drawString(140 * mm, y, f"Rs. {p['amount']:,.0f}")
        y -= 6 * mm
    if not recent_payments:
        c.setFillColor(GREY)
        c.drawString(18 * mm, y, "No payments yet")

    c.setFillColor(GREY)
    c.setFont("Helvetica-Oblique", 8)
    c.drawCentredString(width / 2, 12 * mm, "Generated by Udhayam MFI")

    c.showPage()
    c.save()
    return file_path
