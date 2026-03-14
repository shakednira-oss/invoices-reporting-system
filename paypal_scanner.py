import pandas as pd
from datetime import datetime


COMPLETED_STATUSES = ["completed", "cleared", "אושר", "הושלם"]
PAYMENT_TYPES = ["payment", "תשלום", "purchase", "רכישה", "debit", "חיוב"]


def find_column(df, candidates):
    for col in df.columns:
        for c in candidates:
            if c.lower() in col.lower():
                return col
    return None


def parse_paypal_csv(csv_file, start_date, end_date):
    """
    Parse a PayPal CSV export.
    Returns list of dicts: {business, date_str, amount, currency}
    """
    try:
        df = pd.read_csv(csv_file, encoding="utf-8-sig")
    except Exception:
        try:
            df = pd.read_csv(csv_file, encoding="utf-8")
        except Exception:
            return []

    date_col = find_column(df, ["date", "תאריך"])
    name_col = find_column(df, ["name", "שם"])
    status_col = find_column(df, ["status", "סטטוס", "state"])
    type_col = find_column(df, ["type", "סוג"])
    amount_col = find_column(df, ["amount", "סכום", "gross"])
    currency_col = find_column(df, ["currency", "מטבע"])

    if not date_col:
        return []

    transactions = []

    for _, row in df.iterrows():
        try:
            raw_date = str(row[date_col])
            try:
                tx_date = pd.to_datetime(raw_date, dayfirst=True)
            except Exception:
                continue

            if tx_date.date() < start_date.date() or tx_date.date() > end_date.date():
                continue

            if status_col:
                status = str(row[status_col]).lower()
                if not any(s in status for s in COMPLETED_STATUSES):
                    continue

            if type_col:
                tx_type = str(row[type_col]).lower()
                if not any(t in tx_type for t in PAYMENT_TYPES):
                    continue

            business = str(row[name_col]).strip() if name_col else "PayPal"
            if not business or business in ("nan", ""):
                business = "PayPal"

            amount = str(row[amount_col]).strip() if amount_col else ""
            currency = str(row[currency_col]).strip() if currency_col else ""

            transactions.append({
                "business": business,
                "date_str": tx_date.strftime("%Y-%m-%d"),
                "amount": amount,
                "currency": currency,
            })
        except Exception:
            continue

    return transactions


def build_receipt_text(tx):
    return (
        f"PayPal Receipt\n"
        f"==============\n"
        f"Date:     {tx['date_str']}\n"
        f"Vendor:   {tx['business']}\n"
        f"Amount:   {tx['amount']} {tx['currency']}\n"
    )
