import os
import re
from datetime import datetime


def sanitize(name):
    return re.sub(r'[<>:"/\\|?*\n\r\t]', "_", name).strip()


def save_invoice(content, business_name, date_str, base_folder, extension="pdf"):
    try:
        invoice_date = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        invoice_date = datetime.today()

    month_folder = invoice_date.strftime("%Y-%m")
    folder_path = os.path.join(base_folder, month_folder)
    os.makedirs(folder_path, exist_ok=True)

    safe_business = sanitize(business_name) or "Unknown"
    date_part = invoice_date.strftime("%Y-%m-%d")
    filename = f"{safe_business}_{date_part}.{extension}"
    filepath = os.path.join(folder_path, filename)

    counter = 1
    while os.path.exists(filepath):
        filename = f"{safe_business}_{date_part}_{counter}.{extension}"
        filepath = os.path.join(folder_path, filename)
        counter += 1

    if isinstance(content, bytes):
        with open(filepath, "wb") as f:
            f.write(content)
    else:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

    return filepath
