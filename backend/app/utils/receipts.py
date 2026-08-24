import os
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas

from app.core.config import settings

RECEIPTS_DIR = os.path.join(settings.LOCAL_STORAGE_PATH, "receipts")
os.makedirs(RECEIPTS_DIR, exist_ok=True)

# OS2 EMS brand tokens
INK = colors.HexColor("#1B1613")
CHARCOAL = colors.HexColor("#231F20")
AMBER = colors.HexColor("#FFB600")


def generate_receipt_pdf(payment, loan, customer) -> str:
    """Generates a printable A5 payment receipt. Returns the file path."""
    file_path = os.path.join(RECEIPTS_DIR, f"{payment.receipt_number}.pdf")
    c = canvas.Canvas(file_path, pagesize=A5)
    width, height = A5

    # Header band
    c.setFillColor(CHARCOAL)
    c.rect(0, height - 25 * mm, width, 25 * mm, fill=1, stroke=0)
    c.setFillColor(AMBER)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(12 * mm, height - 12 * mm, "OS FINANCES")
    c.setFillColor(colors.white)
    c.setFont("Helvetica", 9)
    c.drawString(12 * mm, height - 18 * mm, "Payment Receipt")

    y = height - 35 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(12 * mm, y, f"Receipt No: {payment.receipt_number}")
    c.drawRightString(width - 12 * mm, y, payment.paid_at.strftime("%d-%b-%Y %I:%M %p"))

    y -= 10 * mm
    c.setFont("Helvetica", 10)
    rows = [
        ("Customer Name", customer.full_name if customer else "-"),
        ("Customer Code", customer.customer_code if customer else "-"),
        ("Loan Number", loan.loan_number),
        ("Payment Method", payment.method.value.upper()),
    ]
    for label, value in rows:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(12 * mm, y, f"{label}:")
        c.setFont("Helvetica", 9)
        c.drawString(50 * mm, y, str(value))
        y -= 7 * mm

    y -= 3 * mm
    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.line(12 * mm, y, width - 12 * mm, y)
    y -= 10 * mm

    c.setFont("Helvetica-Bold", 13)
    c.setFillColor(CHARCOAL)
    c.drawString(12 * mm, y, "Amount Received:")
    c.drawRightString(width - 12 * mm, y, f"Rs. {payment.amount:,.2f}")

    y -= 20 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Oblique", 8)
    c.drawCentredString(width / 2, y, "This is a system-generated receipt from OS Finances.")

    c.showPage()
    c.save()
    return file_path
