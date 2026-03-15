import streamlit as st
import json
import io
import zipfile
from datetime import date, datetime
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build as gsheets_build
from streamlit_oauth import OAuth2Component

from gmail_scanner import scan_account_with_creds
from paypal_scanner import parse_paypal_csv, build_receipt_text
from invoice_parser import parse_invoice

SCOPES = "https://www.googleapis.com/auth/gmail.readonly"
SHEET_ID = "1qRW8jKq0QkG5mZF1c_rpUrt7WgkC9TOHjuAYTYlOEDA"


def log_user(email):
    try:
        creds_info = json.loads(st.secrets["SHEETS_CREDENTIALS"])
        creds = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        service = gsheets_build("sheets", "v4", credentials=creds)
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range="Sheet1!A:A",
            valueInputOption="RAW",
            body={"values": [[email]]}
        ).execute()
    except Exception:
        pass
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL = "https://accounts.google.com/o/oauth2/token"
REVOKE_URL = "https://accounts.google.com/o/oauth2/revoke"


# ── Page setup ────────────────────────────────────────────────────────────────

st.set_page_config(page_title="מערכת סריקת חשבוניות", page_icon="🧾", layout="wide")
st.title("🧾 מערכת סריקת חשבוניות")
st.caption("פותח על ידי נירה שקד באמצעות Claude Code")

# ── OAuth helper ──────────────────────────────────────────────────────────────

def get_oauth_component():
    creds = json.loads(st.secrets["GMAIL_CREDENTIALS"])
    client = creds["web"]
    return OAuth2Component(
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        authorize_endpoint=AUTHORIZE_URL,
        token_endpoint=TOKEN_URL,
        refresh_token_endpoint=TOKEN_URL,
    )

def build_gmail_creds(token_data):
    return Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=TOKEN_URL,
        client_id=json.loads(st.secrets["GMAIL_CREDENTIALS"])["web"]["client_id"],
        client_secret=json.loads(st.secrets["GMAIL_CREDENTIALS"])["web"]["client_secret"],
        scopes=[SCOPES],
    )


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ הגדרות")

    st.subheader("🔑 מפתח Anthropic API")
    anthropic_key = st.text_input("הזיני את מפתח ה-API שלך", type="password",
                                   help="ניתן להשיג בכתובת console.anthropic.com")
    if not anthropic_key:
        st.warning("נדרש מפתח API כדי להפעיל את הסריקה")

    redirect_uri = st.secrets["REDIRECT_URI"]

    st.subheader("📧 חשבונות Gmail")
    oauth2 = get_oauth_component()

    # Gmail 1
    gmail1 = st.text_input("Gmail ראשון")
    token1 = None
    if gmail1:
        if f"token_{gmail1}" in st.session_state:
            st.success(f"✅ {gmail1} — מחובר")
            if st.button(f"🔓 התנתק", key="logout1"):
                del st.session_state[f"token_{gmail1}"]
                st.rerun()
            token1 = st.session_state[f"token_{gmail1}"]
        else:
            result = oauth2.authorize_button(
                name=f"🔗 התחבר עם Google",
                redirect_uri=redirect_uri,
                scope=SCOPES,
                key="oauth1",
                extras_params={"prompt": "consent", "access_type": "offline", "login_hint": gmail1},
                use_container_width=True,
            )
            if result and "token" in result:
                st.session_state[f"token_{gmail1}"] = result["token"]
                log_user(gmail1)
                st.rerun()

    # Gmail 2
    gmail2 = st.text_input("Gmail שני (אופציונלי)")
    token2 = None
    if gmail2:
        if f"token_{gmail2}" in st.session_state:
            st.success(f"✅ {gmail2} — מחובר")
            if st.button(f"🔓 התנתק", key="logout2"):
                del st.session_state[f"token_{gmail2}"]
                st.rerun()
            token2 = st.session_state[f"token_{gmail2}"]
        else:
            result = oauth2.authorize_button(
                name=f"🔗 התחבר עם Google",
                redirect_uri=redirect_uri,
                scope=SCOPES,
                key="oauth2",
                extras_params={"prompt": "consent", "access_type": "offline", "login_hint": gmail2},
                use_container_width=True,
            )
            if result and "token" in result:
                st.session_state[f"token_{gmail2}"] = result["token"]
                log_user(gmail2)
                st.rerun()

    st.subheader("💳 PayPal")
    paypal_file = st.file_uploader("העלי קובץ CSV מ-PayPal", type=["csv"])
    st.caption("ב-PayPal: Activity → Statements → Download → CSV")


# ── Date range ────────────────────────────────────────────────────────────────

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
    if not anthropic_key:
        st.error("יש להזין מפתח Anthropic API.")
        st.stop()

    accounts = []
    if token1:
        accounts.append((gmail1, build_gmail_creds(token1)))
    if token2:
        accounts.append((gmail2, build_gmail_creds(token2)))

    if not accounts and not paypal_file:
        st.error("יש להתחבר לפחות לחשבון Gmail אחד או להעלות קובץ PayPal.")
        st.stop()

    saved_files = []
    manual_links = []
    errors = []

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    # ── Gmail ─────────────────────────────────────────────────────────────────
    for account, creds in accounts:
        st.info(f"🔄 סורק {account}...")
        progress_bar = st.progress(0, text=f"סורק {account}...")

        def update_progress(current, total, acc=account):
            pct = int((current / total) * 100) if total else 100
            progress_bar.progress(pct, text=f"סורק {acc}: {current}/{total}")

        try:
            pdfs, links = scan_account_with_creds(creds, account, start_dt, end_dt, update_progress)
            manual_links.extend(links)
            progress_bar.progress(100, text=f"✅ {account} — נמצאו {len(pdfs)} קבצי PDF, {len(links)} קישורים")

            for item in pdfs:
                try:
                    if not item["bytes"]:
                        errors.append(f"{item['subject']}: קובץ PDF ריק, דולג")
                        continue
                    business, date_str, is_invoice = parse_invoice(pdf_bytes=item["bytes"], api_key=anthropic_key)
                    if not is_invoice:
                        errors.append(f"{item['subject']}: דולג — לא זוהה כחשבונית")
                        continue
                    if business == "Unknown":
                        business = item.get("sender_name", "Unknown")
                    if date_str == datetime.today().strftime("%Y-%m-%d"):
                        date_str = item.get("email_date", date_str)

                    try:
                        month = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m")
                    except Exception:
                        month = "unknown"

                    safe_business = business.replace("/", "_").replace("\\", "_")
                    filename = f"{month}/{safe_business}_{date_str}.pdf"
                    saved_files.append({
                        "name": filename,
                        "bytes": item["bytes"],
                        "עסק": business,
                        "תאריך": date_str,
                        "מקור": account,
                    })
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
                try:
                    month = datetime.strptime(tx["date_str"], "%Y-%m-%d").strftime("%Y-%m")
                except Exception:
                    month = "unknown"
                safe_business = tx["business"].replace("/", "_").replace("\\", "_")
                filename = f"{month}/{safe_business}_{tx['date_str']}.txt"
                saved_files.append({
                    "name": filename,
                    "bytes": receipt_text.encode("utf-8"),
                    "עסק": tx["business"],
                    "תאריך": tx["date_str"],
                    "מקור": "PayPal",
                })
            st.success(f"✅ PayPal — נמצאו {len(transactions)} עסקאות")
        except Exception as e:
            st.error(f"שגיאה בקובץ PayPal: {e}")

    # ── Results ───────────────────────────────────────────────────────────────
    st.divider()
    st.subheader(f"✅ נמצאו {len(saved_files)} חשבוניות")

    if saved_files:
        table = [{"קובץ": f["name"].split("/")[-1], "עסק": f["עסק"],
                  "תאריך": f["תאריך"], "מקור": f["מקור"]} for f in saved_files]
        st.dataframe(table, use_container_width=True)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in saved_files:
                zf.writestr(f["name"], f["bytes"])
        zip_buffer.seek(0)

        st.download_button(
            label="📥 הורד את כל החשבוניות (ZIP)",
            data=zip_buffer,
            file_name=f"invoices_{datetime.now().strftime('%Y-%m-%d')}.zip",
            mime="application/zip",
            use_container_width=True,
        )

    if manual_links:
        st.divider()
        st.subheader(f"⚠️ {len(manual_links)} מיילים לבדיקה ידנית")
        st.caption("אלה מיילים ללא קובץ מצורף — לחצי על הקישור כדי לפתוח את המייל ב-Gmail:")
        for l in manual_links:
            direct = l.get("url", l.get("gmail_url", ""))
            gmail_url = l.get("gmail_url", direct)
            links_str = f"[פתח חשבונית]({direct})"
            if gmail_url != direct:
                links_str += f" | [פתח מייל ב-Gmail]({gmail_url})"
            st.markdown(f"📧 **{l['subject']}** | {l['date']} | {l['account']} | {links_str}")

    if errors:
        with st.expander(f"⚠️ {len(errors)} דילוגים"):
            for e in errors:
                st.text(e)
