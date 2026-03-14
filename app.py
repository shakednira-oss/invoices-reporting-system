import streamlit as st
import os
import json
from datetime import date, datetime
from pathlib import Path

from gmail_scanner import scan_account
from paypal_scanner import parse_paypal_csv, build_receipt_text
from invoice_parser import parse_invoice
from file_manager import save_invoice

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"gmail1": "", "gmail2": "", "base_folder": str(Path.home() / "Invoices")}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


cfg = load_config()

st.set_page_config(page_title="Nira's Invoice Scanner", page_icon="🧾", layout="wide")
st.title("🧾 מערכת סריקת חשבוניות")

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ הגדרות")

    st.subheader("תיקיית שמירה")
    base_folder = st.text_input("נתיב לתיקייה", value=cfg.get("base_folder", ""))

    st.subheader("📧 חשבונות Gmail")
    gmail1 = st.text_input("Gmail ראשון", value=cfg.get("gmail1", ""))
    gmail2 = st.text_input("Gmail שני (אופציונלי)", value=cfg.get("gmail2", ""))

    if st.button("💾 שמור הגדרות"):
        save_config({"gmail1": gmail1, "gmail2": gmail2, "base_folder": base_folder})
        st.success("נשמר!")

    st.subheader("💳 PayPal")
    paypal_file = st.file_uploader("העלי קובץ CSV מ-PayPal", type=["csv"])
    st.caption("ב-PayPal: Activity → Statements → Download → CSV")

# ── Date range ───────────────────────────────────────────────────────────────
st.subheader("📅 טווח תאריכים")
col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("מתאריך", value=date(2024, 1, 1))
with col2:
    end_date = st.date_input("עד תאריך", value=date.today())

if start_date > end_date:
    st.error("תאריך התחלה חייב להיות לפני תאריך הסיום.")
    st.stop()

# ── Run button ────────────────────────────────────────────────────────────────
run = st.button("🔍 בצע סריקה", type="primary", use_container_width=True)
st.markdown("""
<style>
div.stButton > button[kind="primary"] {
    background-color: #28a745;
    border-color: #28a745;
    color: white;
}
div.stButton > button[kind="primary"]:hover {
    background-color: #218838;
    border-color: #1e7e34;
}
</style>
""", unsafe_allow_html=True)

if run:
    if not gmail1 and not paypal_file:
        st.error("יש להזין לפחות חשבון Gmail אחד או קובץ PayPal.")
        st.stop()

    if not base_folder:
        st.error("יש לבחור תיקיית שמירה.")
        st.stop()

    os.makedirs(base_folder, exist_ok=True)

    # Create a unique subfolder for this scan run to avoid duplicates
    run_timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    scan_folder = os.path.join(base_folder, f"scan_{run_timestamp}")
    os.makedirs(scan_folder, exist_ok=True)

    saved_files = []
    manual_links = []
    errors = []

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    # ── Gmail ─────────────────────────────────────────────────────────────────
    accounts = [a for a in [gmail1, gmail2] if a.strip()]

    for account in accounts:
        st.info(f"🔄 סורק {account}...")

        progress_bar = st.progress(0, text=f"סורק {account}...")

        def update_progress(current, total, acc=account):
            pct = int((current / total) * 100) if total else 100
            progress_bar.progress(pct, text=f"סורק {acc}: {current}/{total}")

        try:
            pdfs, links = scan_account(account, start_dt, end_dt, update_progress)
            manual_links.extend(links)

            progress_bar.progress(100, text=f"✅ {account} — נמצאו {len(pdfs)} קבצי PDF, {len(links)} קישורים לטיפול ידני")

            for item in pdfs:
                try:
                    if not item["bytes"]:
                        errors.append(f"{item['subject']}: קובץ PDF ריק, דולג")
                        continue
                    business, date_str, is_invoice = parse_invoice(pdf_bytes=item["bytes"])
                    if not is_invoice:
                        errors.append(f"{item['subject']}: דולג — לא זוהה כחשבונית")
                        continue
                    # Use email metadata as fallback if Claude couldn't extract
                    if business == "Unknown":
                        business = item.get("sender_name", "Unknown")
                    if date_str == datetime.today().strftime("%Y-%m-%d"):
                        date_str = item.get("email_date", date_str)
                    path = save_invoice(item["bytes"], business, date_str, scan_folder, "pdf")
                    saved_files.append({"קובץ": os.path.basename(path), "עסק": business,
                                        "תאריך": date_str, "מקור": account})
                except Exception as e:
                    errors.append(f"{item['subject']}: {e}")

        except Exception as e:
            st.error(f"שגיאה בחיבור ל-{account}: {e}")

    # ── PayPal ────────────────────────────────────────────────────────────────
    if paypal_file:
        st.info("🔄 מעבד קובץ PayPal...")
        try:
            transactions = parse_paypal_csv(paypal_file, start_dt, end_dt)
            for tx in transactions:
                receipt_text = build_receipt_text(tx)
                path = save_invoice(receipt_text, tx["business"], tx["date_str"],
                                    scan_folder, "txt")
                saved_files.append({"קובץ": os.path.basename(path), "עסק": tx["business"],
                                    "תאריך": tx["date_str"], "מקור": "PayPal"})
            st.success(f"✅ PayPal — נמצאו {len(transactions)} עסקאות")
        except Exception as e:
            st.error(f"שגיאה בקובץ PayPal: {e}")

    # ── Results ───────────────────────────────────────────────────────────────
    st.divider()
    st.subheader(f"✅ נשמרו {len(saved_files)} חשבוניות")

    if saved_files:
        st.dataframe(saved_files, use_container_width=True)
        st.success(f"הקבצים נשמרו ב: {scan_folder}")

    if manual_links:
        st.divider()
        st.subheader(f"⚠️ {len(manual_links)} מיילים לבדיקה ידנית")
        st.caption("אלה מיילים ללא קובץ מצורף — לחצי על הקישור כדי לפתוח את המייל ב-Gmail:")
        for l in manual_links:
            direct = l.get("url", l.get("gmail_url", ""))
            gmail = l.get("gmail_url", direct)
            links_str = f"[פתח חשבונית]({direct})"
            if gmail != direct:
                links_str += f" | [פתח מייל ב-Gmail]({gmail})"
            st.markdown(f"📧 **{l['subject']}** | {l['date']} | {l['account']} | {links_str}")

    if errors:
        with st.expander(f"⚠️ {len(errors)} שגיאות"):
            for e in errors:
                st.text(e)
