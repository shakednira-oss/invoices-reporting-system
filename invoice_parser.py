import os
import io
import pdfplumber
import anthropic
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def extract_text_from_pdf(pdf_bytes):
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() or ""
        return text.strip()
    except Exception:
        return ""


def parse_invoice(pdf_bytes=None, email_text=None):
    """Extract business name and date from invoice. Returns (business_name, date_str)."""
    if pdf_bytes:
        text = extract_text_from_pdf(pdf_bytes)
    elif email_text:
        text = email_text
    else:
        return "Unknown", datetime.today().strftime("%Y-%m-%d")

    if not text.strip():
        return "Unknown", datetime.today().strftime("%Y-%m-%d")

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Extract the following from this invoice or receipt text:\n"
                        "1. Business or company name (the seller/vendor, not the buyer)\n"
                        "2. Invoice or receipt date\n\n"
                        "Return ONLY in this exact format (no extra text):\n"
                        "BUSINESS: [name]\n"
                        "DATE: [YYYY-MM-DD]\n\n"
                        "If you cannot find the business name, write Unknown.\n"
                        f"If you cannot find the date, write {datetime.today().strftime('%Y-%m-%d')}.\n\n"
                        f"Text:\n{text[:3000]}"
                    ),
                }
            ],
        )

        result = response.content[0].text
        business = "Unknown"
        date_str = datetime.today().strftime("%Y-%m-%d")

        for line in result.splitlines():
            if line.startswith("BUSINESS:"):
                business = line.replace("BUSINESS:", "").strip()
            elif line.startswith("DATE:"):
                date_str = line.replace("DATE:", "").strip()

        return business, date_str

    except Exception:
        return "Unknown", datetime.today().strftime("%Y-%m-%d")
