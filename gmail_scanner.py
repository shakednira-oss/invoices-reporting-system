import os
import base64
import pickle
import re
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials", "gmail_credentials.json")

INVOICE_KEYWORDS = [
    "invoice", "receipt", "billing", "tax invoice",
    "חשבונית", "קבלה", "שובר", "ארנונה", "חשבון",
    "אישור תשלום", "אישור הזמנה", "אישור רכישה",
]

INVOICE_LINK_KEYWORDS = [
    "invoice", "receipt", "billing", "payment", "download", "חשבונית", "קבלה",
]


def get_token_path(account_email):
    safe = account_email.replace("@", "_at_").replace(".", "_")
    return os.path.join(BASE_DIR, "credentials", f"token_{safe}.pickle")


def authenticate(account_email):
    token_path = get_token_path(account_email)
    creds = None

    if os.path.exists(token_path):
        with open(token_path, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "wb") as f:
            pickle.dump(creds, f)

    return build("gmail", "v1", credentials=creds)


def build_query(start_date, end_date):
    after = start_date.strftime("%Y/%m/%d")
    before = end_date.strftime("%Y/%m/%d")
    # Use simple keywords without subject: prefix - Gmail API handles Hebrew better this way
    english_kw = ["invoice", "receipt", "billing"]
    hebrew_kw = ["חשבונית", "קבלה", "שובר", "ארנונה"]
    all_kw = " OR ".join([f'"{k}"' for k in english_kw + hebrew_kw])
    return f"({all_kw}) after:{after} before:{before}"


def get_attachment_bytes(service, msg_id, att_id):
    att = service.users().messages().attachments().get(
        userId="me", messageId=msg_id, id=att_id
    ).execute()
    return base64.urlsafe_b64decode(att["data"] + "==")


SKIP_DOMAINS = ["unsubscribe", "mailto", "tracking", "open.php", "pixel", "click."]

def extract_links(text):
    urls = re.findall(r"https?://[^\s<>\"'{}|\\^`\[\]]+", text)
    return [u for u in urls if not any(s in u.lower() for s in SKIP_DOMAINS)]


def decode_body(data):
    try:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
    except Exception:
        return ""


def collect_parts(parts, service, msg_id):
    """Recursively collect PDF attachments and body text from message parts."""
    attachments = []
    body_text = ""

    for part in parts:
        mime = part.get("mimeType", "")
        filename = part.get("filename", "")
        sub_parts = part.get("parts", [])

        if sub_parts:
            sub_atts, sub_text = collect_parts(sub_parts, service, msg_id)
            attachments.extend(sub_atts)
            body_text += sub_text
        elif mime == "application/pdf" or filename.lower().endswith(".pdf"):
            att_id = part["body"].get("attachmentId")
            if att_id:
                try:
                    pdf_bytes = get_attachment_bytes(service, msg_id, att_id)
                    attachments.append((pdf_bytes, filename))
                except Exception:
                    pass
        elif mime in ("text/plain", "text/html"):
            data = part["body"].get("data", "")
            if data:
                body_text += decode_body(data)

    return attachments, body_text


def scan_account(account_email, start_date, end_date, progress_callback=None):
    """
    Scan one Gmail account.
    Returns:
        pdf_list: list of dicts {bytes, filename, subject, date, account}
        link_list: list of dicts {subject, date, url, account}
    """
    service = authenticate(account_email)
    query = build_query(start_date, end_date)

    result = service.users().messages().list(
        userId="me", q=query, maxResults=500
    ).execute()
    messages = result.get("messages", [])
    total = len(messages)
    print(f"[DEBUG] {account_email}: query={query!r}, found {total} messages")

    pdf_list = []
    link_list = []

    for i, ref in enumerate(messages):
        if progress_callback:
            progress_callback(i + 1, total)

        try:
            msg = service.users().messages().get(
                userId="me", id=ref["id"], format="full"
            ).execute()
        except Exception:
            continue

        subject = ""
        date_str = ""
        sender = ""
        for h in msg["payload"].get("headers", []):
            if h["name"] == "Subject":
                subject = h["value"]
            elif h["name"] == "Date":
                date_str = h["value"]
            elif h["name"] == "From":
                sender = h["value"]

        # Parse email date for fallback
        email_date = None
        try:
            from email.utils import parsedate_to_datetime
            email_date = parsedate_to_datetime(date_str).strftime("%Y-%m-%d")
        except Exception:
            email_date = datetime.today().strftime("%Y-%m-%d")

        # Extract sender name (before the email address)
        sender_name = sender.split("<")[0].strip().strip('"') or "Unknown"

        # Build direct Gmail link
        gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{ref['id']}"

        payload = msg["payload"]
        parts = payload.get("parts", [])

        if parts:
            atts, body_text = collect_parts(parts, service, ref["id"])
        else:
            atts = []
            raw = payload["body"].get("data", "")
            body_text = decode_body(raw) if raw else ""

        if atts:
            for pdf_bytes, filename in atts:
                pdf_list.append({
                    "bytes": pdf_bytes,
                    "filename": filename,
                    "subject": subject,
                    "date": date_str,
                    "email_date": email_date,
                    "sender_name": sender_name,
                    "account": account_email,
                })
        else:
            # No PDF — add to manual list only if subject looks like an invoice
            subject_lower = subject.lower()
            invoice_subject_kw = [
                "חשבונית", "קבלה", "שובר", "ארנונה",
                "invoice", "receipt", "billing",
            ]
            if any(k in subject_lower for k in invoice_subject_kw):
                # Try to find a direct invoice link in the body
                direct_link = gmail_link
                if body_text:
                    INVOICE_URL_KW = [
                        "invoice", "receipt", "order", "billing", "download",
                        "חשבונית", "קבלה", "הורד", "צפה",
                        "view", "statement", "document",
                    ]
                    candidate_links = extract_links(body_text)
                    for url in candidate_links:
                        if any(k in url.lower() for k in INVOICE_URL_KW):
                            direct_link = url
                            break

                link_list.append({
                    "subject": subject,
                    "date": email_date,
                    "url": direct_link,
                    "gmail_url": gmail_link,
                    "account": account_email,
                })

    return pdf_list, link_list
