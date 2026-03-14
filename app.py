import streamlit as st
import json
import io
import zipfile
from datetime import date, datetime
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from gmail_scanner import scan_account_with_creds
from paypal_scanner import parse_paypal_csv, build_receipt_text
from invoice_parser import parse_invoice

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def get_client_config():
    return json.loads(st.secrets["GMAIL_CREDENTIALS"])


def get_redirect_uri():
    return st.secrets["REDIRECT_URI"]


def build_flow():
    return Flow.from_client_config(
        get_client_config(),
        scopes=SCOPES,
        redirect_uri=get_redirect_uri()
    )


def save_credentials(key, creds):
    st.session_state[f"creds_{key}"] = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    }


def load_credentials(key):
    data = st.session_state.get(f"creds_{key}")
    if not data:
        return None
    creds = Credentials(
        token=data["token"],
        refresh_token=data["refresh_token"],
        token_uri=data["token_uri"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data["scopes"],
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(key, creds)
        except Exception:
            return None
    return creds if creds.valid else None


# ── Handle OAuth callback ─────────────────────────────────────────────────────

if "code" in st.query_params:
    code = st.query_params["code"]
    key = st.query_params.get("state", st.session_state.get("pending_oauth_key", ""))
    if key:
        try:
            flow = build_flow()
            flow.fetch_token(code=code)
            save_credentials(key, flow.credentials)
            st.session_state.pop("pending_oauth_key", None)
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"שגיאה בהתחברות ל-Gmail: {e}")


# ── Page setup ────────────────────────────────────────────────────────────────

st.set_page_config(page_title="מערכת סריקת חשבוניות", page_icon="🧾", layout="wide")
st.title("🧾 מערכת סריקת חשבוניות")
st.caption("פותח על ידי נירה שקד באמצעות Claude Code")

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ הגדרות")

    st.subheader("🔑 מפתח Anthropic API")
    anthropic_key = st.text_input("הזיני את מפתח ה-API שלך", type="password",
                                   help="ניתן להשיג בחינם בכתובת console.anthropic.com")
    if not anthropic_key:
        st.warning("נדרש מפתח API כדי להפעיל את הסריקה")

    st.subheader("📧 חשבונות Gmail")
    gmail1 = st.text_input("Gmail ראשון")
    gmail2 = st.text_input("Gmail שני (אופציונלי)")

    for gmail in [g for g in [gmail1, gmail2] if g.strip()]:
        key = gmail.strip()
        creds = load_credentials(key)
        if creds:
            st.success(f"✅ {key} — מחובר")
            if st.button(f"🔓 התנתק מ-{key}", key=f"logout_{key}"):
                st.session_state.pop(f"creds_{key}", None)
                st.rerun()
        else:
            if st.button(f"🔗 התחבר עם Google — {key}", key=f"login_{key}"):
                st.session_state["pending_oauth_key"] = key
                flow = build_flow()
                auth_url, _ = flow.authorization_url(
                    access_type="offline",
                    prompt="consent",
                    login_hint=key,
                    state=key,
                )
                st.link_button("לחצי כאן להתחבר ל-Google", auth_url)

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
    accounts = [(g.strip(), load_credentials(g.strip())) for g in [gmail1, gmail2] if g.strip()]
    connected = [(email, creds) for email, creds in accounts if creds]

    if not anthropic_key:
        st.error("יש להזין מפתח Anthropic API.")
        st.stop()

    if not connected and not paypal_file:
        st.error("יש להתחבר לפחות לחשבון Gmail אחד או להעלות קובץ PayPal.")
        st.stop()

    saved_files = []   # {name, bytes, month}
    manual_links = []
    errors = []

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    # ── Gmail ─────────────────────────────────────────────────────────────────
    for account, creds in connected:
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

        # Create ZIP in memory
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
            gmail = l.get("gmail_url", direct)
            links_str = f"[פתח חשבונית]({direct})"
            if gmail != direct:
                links_str += f" | [פתח מייל ב-Gmail]({gmail})"
            st.markdown(f"📧 **{l['subject']}** | {l['date']} | {l['account']} | {links_str}")

    if errors:
        with st.expander(f"⚠️ {len(errors)} דילוגים"):
            for e in errors:
                st.text(e)
