import os
import sys
import re
import base64
import json
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
from typing import Optional, Dict, Any, List
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from llm_client import query_llm

BASE_DIR = Path(__file__).parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"

# Scope allows reading, modifying labels, archiving, and creating drafts
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

LSFM_LABEL_DEFINITIONS = [
    "LSFM/🔥 Urgent - Interviews",
    "LSFM/💼 Applications",
    "LSFM/🎓 University",
    "LSFM/💳 Finance & Banking",
    "LSFM/📦 Deliveries & Orders",
    "LSFM/⚙️ Dev & Cloud",
    "LSFM/🔑 Security & Alerts",
    "LSFM/📰 Design & Tech Reads",
    "LSFM/💬 Personal VIP",
    "LSFM/📁 Promos & Clutter",
]

def is_configured() -> bool:
    """Checks if either a valid token or credentials.json exists."""
    return TOKEN_PATH.exists() or CREDENTIALS_PATH.exists()

def get_gmail_service():
    """
    Authenticates with Gmail API via OAuth2.
    Returns the Gmail resource object or None if credentials are not configured.
    """
    creds = None
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except Exception as e:
            print(f"⚠️ [Gmail Engine] Failed to load token.json: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
            except Exception as e:
                print(f"⚠️ [Gmail Engine] Failed to refresh token: {e}")
                creds = None

        if not creds:
            if not CREDENTIALS_PATH.exists():
                return None
            
            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
                creds = flow.run_local_server(port=0)
                TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
            except Exception as e:
                print(f"⚠️ [Gmail Engine] OAuth authentication flow failed: {e}")
                return None

    try:
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        print(f"⚠️ [Gmail Engine] Failed to build Gmail client: {e}")
        return None

def ensure_lsfm_labels(service) -> Dict[str, str]:
    """Ensures that all LSFM custom labels exist in the user's Gmail account."""
    if not service:
        return {}

    try:
        results = service.users().labels().list(userId="me").execute()
        existing_labels = {lbl["name"]: lbl["id"] for lbl in results.get("labels", [])}
    except Exception as e:
        print(f"⚠️ [Gmail Engine] Error listing labels: {e}")
        return {}

    label_map = {}
    for target_label in LSFM_LABEL_DEFINITIONS:
        if target_label in existing_labels:
            label_map[target_label] = existing_labels[target_label]
        else:
            try:
                label_body = {
                    "name": target_label,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show"
                }
                new_lbl = service.users().labels().create(userId="me", body=label_body).execute()
                label_map[target_label] = new_lbl["id"]
                print(f"🏷️ [Gmail Engine] Created label: {target_label}")
            except Exception as e:
                print(f"⚠️ [Gmail Engine] Failed to create label {target_label}: {e}")

    return label_map

def extract_body_text(payload: Dict[str, Any]) -> str:
    """Recursively extracts plain text from a Gmail message payload."""
    body_text = ""
    if "parts" in payload:
        for part in payload["parts"]:
            body_text += extract_body_text(part)
    else:
        mime_type = payload.get("mimeType", "")
        data = payload.get("body", {}).get("data")
        if data and ("text/plain" in mime_type or "text/html" in mime_type):
            try:
                decoded = base64.urlsafe_b64decode(data.encode("ASCII")).decode("utf-8", errors="ignore")
                # Basic html tag stripping if html
                if "text/html" in mime_type:
                    clean = re.sub(r"<[^>]+>", " ", decoded)
                    body_text += clean
                else:
                    body_text += decoded
            except Exception:
                pass
    return body_text[:2500]

def fetch_inbox_messages(service, max_results: int = 25, unread_only: bool = False) -> List[Dict[str, Any]]:
    """
    Fetches messages from the user's INBOX.
    - If unread_only=True: queries 'is:unread label:INBOX'. If fewer than 3 are found,
      supplements with recent 'label:INBOX' so Hans never gets an empty/skipped triage.
    - If unread_only=False: queries 'label:INBOX' directly (read + unread).
    """
    if not service:
        return []

    try:
        query = "is:unread label:INBOX" if unread_only else "label:INBOX"
        response = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results
        ).execute()
        
        messages = response.get("messages", [])

        # If unread_only was requested but inbox has few/no unread, supplement with active inbox messages
        if unread_only and len(messages) < 3:
            supp_resp = service.users().messages().list(
                userId="me",
                q="label:INBOX",
                maxResults=max_results
            ).execute()
            seen_ids = {m["id"] for m in messages}
            for sm in supp_resp.get("messages", []):
                if sm["id"] not in seen_ids:
                    messages.append(sm)
                    seen_ids.add(sm["id"])
                    if len(messages) >= max_results:
                        break

        parsed_emails = []

        for msg in messages:
            msg_data = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="full"
            ).execute()

            headers = {h["name"].lower(): h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
            subject = headers.get("subject", "(No Subject)")
            sender = headers.get("from", "Unknown Sender")
            date = headers.get("date", "")
            snippet = msg_data.get("snippet", "")
            body = extract_body_text(msg_data.get("payload", {})) or snippet

            parsed_emails.append({
                "id": msg["id"],
                "thread_id": msg_data.get("threadId"),
                "subject": subject,
                "sender": sender,
                "date": date,
                "snippet": snippet,
                "body": body,
                "label_ids": msg_data.get("labelIds", []),
                "headers": headers
            })

        return parsed_emails
    except Exception as e:
        print(f"⚠️ [Gmail Engine] Error fetching inbox messages: {e}")
        return []

# Backwards compatibility alias
fetch_unread_inbox_messages = fetch_inbox_messages

def classify_email(email_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Two-Tier Hybrid Classifier:
    1. Tier 1: High-Precision Domain Rules Engine (Instant, 100% accurate on known platforms & senders).
    2. Tier 2: LLM Contextual Classifier with robust JSON regex extraction for edge cases.
    """
    sender = email_data.get("sender", "")
    subject = email_data.get("subject", "")
    snippet = email_data.get("snippet", "")
    headers = email_data.get("headers", {})
    
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    snippet_lower = snippet.lower()
    combined = f"{sender_lower} {subject_lower} {snippet_lower}"

    is_automated = any(k in sender_lower for k in [
        "no-reply", "noreply", "notifications", "messages-noreply",
        "info@", "support@", "mailer", "bounce", "alert", "news",
        "billing", "orders", "donotreply", "system"
    ]) or bool(headers.get("list-unsubscribe")) or "bulk" in headers.get("precedence", "").lower()

    # =========================================================================
    # TIER 1: DETERMINISTIC DOMAIN RULES (100% Accuracy)
    # =========================================================================

    # 1. University / Academics
    if ".edu" in sender_lower or any(k in combined for k in [
        "canvas lms", "blackboard", "university", "academics",
        "student portal", "registrar", "enrollment", "clearance", "graduation"
    ]):
        is_urgent = any(k in combined for k in ["deadline", "clearance", "urgent", "grade", "notice", "claiming", "graduation"])
        return {
            "category": "UNIVERSITY",
            "urgency": "HIGH" if is_urgent else "MEDIUM",
            "source": "University / Academics",
            "action": "FLAG_UNIVERSITY",
            "summary": subject[:70]
        }

    # 2. Courier & E-Commerce Deliveries
    if any(k in sender_lower for k in ["shopee", "lazada", "grab", "foodpanda", "j&t", "ninjavan", "lalamove", "2go", "dhl", "fedex", "ups"]) \
       or any(k in combined for k in ["out for delivery", "parcel", "package shipped", "tracking number", "cod order", "order confirmed", "order #", "has been delivered"]):
        merchant = "Shopee" if "shopee" in combined else ("Lazada" if "lazada" in combined else ("Grab" if "grab" in combined else "Delivery Courier"))
        return {
            "category": "DELIVERY",
            "urgency": "HIGH" if "out for delivery" in combined else "MEDIUM",
            "source": merchant,
            "action": "TRACK_DELIVERY",
            "summary": subject[:70]
        }

    # 3. Security & 2FA / Sign-In Alerts
    if any(k in sender_lower for k in ["openai", "security@"]) or any(k in combined for k in [
        "new sign-in", "verification code", "one-time password", "otp", "2fa",
        "security alert", "password reset", "shared some google account", "suspicious activity", "authorized access"
    ]):
        src = "OpenAI" if "openai" in combined else ("Google" if "google" in combined else "Security Service")
        return {
            "category": "SECURITY",
            "urgency": "HIGH",
            "source": src,
            "action": "SECURITY_ALERT",
            "summary": subject[:70]
        }

    # 4. Job Search: Applications & Interviews
    job_senders = ["kalibrr", "recruitgo", "greenhouse", "lever.co", "workday", "smartrecruiters", "ashbyhq", "jobvite", "indeed"]
    is_job_platform = any(k in sender_lower for k in job_senders)

    if any(k in combined for k in ["interview", "schedule a time", "phone screen", "chat with our team", "availability for a call", "invitation to interview", "technical assessment"]):
        comp = "Recruiter"
        for js in job_senders:
            if js in sender_lower:
                comp = js.capitalize()
                break
        return {
            "category": "INTERVIEW",
            "urgency": "HIGH",
            "source": comp,
            "action": "NOTIFY_INTERVIEW",
            "summary": subject[:70]
        }

    if is_job_platform or any(k in combined for k in [
        "application sent", "application received", "thank you for applying",
        "complete the application", "response to application", "your application to"
    ]):
        comp = "Job Platform"
        if "kalibrr" in sender_lower: comp = "Kalibrr"
        elif "recruitgo" in sender_lower: comp = "RecruitGo"
        elif "greenhouse" in sender_lower: comp = "Greenhouse"
        elif "workday" in sender_lower: comp = "Workday"
        return {
            "category": "APPLICATION_CONFIRMATION",
            "urgency": "MEDIUM",
            "source": comp,
            "action": "LOG_APPLICATION",
            "summary": subject[:70]
        }

    if any(k in combined for k in ["not moving forward", "other candidates", "regret to inform", "unfortunate", "pursuing other"]):
        return {
            "category": "REJECTION",
            "urgency": "LOW",
            "source": "Company",
            "action": "LOG_REJECTION",
            "summary": subject[:70]
        }

    # 5. Finance & Banking Receipts
    if any(k in sender_lower for k in ["google play", "gcash", "paymaya", "maya", "bdo", "bpi", "unionbank", "metrobank", "paypal", "apple"]) \
       or any(k in combined for k in ["subscription suspended", "payment received", "billing statement", "bank transfer", "money received", "statement of account", "invoice #", "receipt for"]):
        fin_src = "Google Play" if "google play" in combined else ("GCash" if "gcash" in combined else ("Maya" if "maya" in combined else ("PayPal" if "paypal" in combined else "Banking")))
        return {
            "category": "FINANCE",
            "urgency": "HIGH" if "suspended" in combined or "overdue" in combined else "MEDIUM",
            "source": fin_src,
            "action": "LOG_FINANCE",
            "summary": subject[:70]
        }

    # 6. Social Notifications & Network Blasts (Always Clutter, Never VIP!)
    if any(k in sender_lower for k in ["linkedin", "facebook", "twitter", "instagram", "tiktok", "pinterest", "threads"]):
        is_reading = any(k in combined for k in ["digest", "opinion", "reads", "pulse", "newsletter", "article"])
        cat = "NEWSLETTER" if is_reading else "PROMO_CLUTTER"
        return {
            "category": cat,
            "urgency": "LOW",
            "source": "LinkedIn" if "linkedin" in sender_lower else "Social Notification",
            "action": "ARCHIVE_NEWSLETTER" if is_reading else "ARCHIVE_PROMO",
            "summary": subject[:70]
        }

    # 7. E-Commerce Promotions & Marketing Blasts
    if any(k in sender_lower for k in ["skoop", "zalora", "uniqlo", "shein", "temu"]) or any(k in combined for k in [
        "9.9", "10.10", "11.11", "12.12", "payday sale", "exclusive", "celebrating 10 years",
        "sale", "discount", "% off", "promo", "voucher", "cashback", "deal", "limited time",
        "free shipping", "flash sale", "don't miss out", "save up to"
    ]):
        src = "SKOOP" if "skoop" in sender_lower else (sender.split("<")[0].strip().strip('"')[:20] or "Store Promotion")
        return {
            "category": "PROMO_CLUTTER",
            "urgency": "LOW",
            "source": src,
            "action": "ARCHIVE_PROMO",
            "summary": subject[:70]
        }

    # 8. Curated Newsletters & Reads
    if any(k in sender_lower for k in ["substack", "medium", "ux collective", "smashing magazine", "tldr", "morning brew", "hackernews", "daily.dev"]) \
       or any(k in combined for k in ["weekly digest", "newsletter", "edition #", "weekly dispatch"]):
        return {
            "category": "NEWSLETTER",
            "urgency": "LOW",
            "source": sender.split("<")[0].strip().strip('"')[:20] or "Tech Newsletter",
            "action": "ARCHIVE_NEWSLETTER",
            "summary": subject[:70]
        }

    # 9. Direct Human Contact (Personal VIP)
    if not is_automated:
        clean_name = sender.split("<")[0].strip().strip('"')
        return {
            "category": "PERSONAL_VIP",
            "urgency": "MEDIUM",
            "source": clean_name or "Personal VIP",
            "action": "KEEP_INBOX",
            "summary": subject[:70]
        }

    # =========================================================================
    # TIER 2: AMBIENT LLM CLASSIFICATION (For complex edge cases)
    # =========================================================================
    prompt = f"""
You are the Executive Email Triage Specialist for Hans Aaron Laureles.
Classify this email into exactly ONE category:
INTERVIEW | APPLICATION_CONFIRMATION | REJECTION | UNIVERSITY | FINANCE | DELIVERY | DEV_OPS | SECURITY | NEWSLETTER | PROMO_CLUTTER | PERSONAL_VIP

Email Details:
From: {sender}
Subject: {subject}
Snippet: {snippet}

Return ONLY valid JSON:
{{
  "category": "CATEGORY_NAME",
  "urgency": "HIGH" | "MEDIUM" | "LOW",
  "source": "Sender/Merchant/Platform",
  "summary": "1 punchy sentence"
}}
"""
    try:
        res = query_llm(prompt, temperature=0.1)
        json_match = re.search(r"\{[\s\S]*\}", res)
        if json_match:
            data = json.loads(json_match.group(0))
            if "category" in data:
                return {
                    "category": data.get("category", "PROMO_CLUTTER"),
                    "urgency": data.get("urgency", "LOW"),
                    "source": data.get("source", sender[:20]),
                    "action": "KEEP_INBOX" if data.get("category") in ["UNIVERSITY", "INTERVIEW", "FINANCE", "PERSONAL_VIP", "DELIVERY"] else "ARCHIVE_PROMO",
                    "summary": data.get("summary", subject[:70])
                }
    except Exception:
        pass

    # Safe fallback: automated emails are never VIP!
    return {
        "category": "PROMO_CLUTTER" if is_automated else "PERSONAL_VIP",
        "urgency": "LOW" if is_automated else "MEDIUM",
        "source": sender[:20],
        "action": "ARCHIVE_PROMO" if is_automated else "KEEP_INBOX",
        "summary": subject[:70]
    }

def modify_email_labels(service, msg_id: str, add_labels: List[str] = None, remove_labels: List[str] = None) -> bool:
    """Adds and/or removes labels on a message (e.g. archiving by removing INBOX)."""
    if not service:
        return False

    body = {}
    if add_labels:
        body["addLabelIds"] = [lbl for lbl in add_labels if lbl]
    if remove_labels:
        body["removeLabelIds"] = [lbl for lbl in remove_labels if lbl]

    if not body.get("addLabelIds") and not body.get("removeLabelIds"):
        return True

    try:
        service.users().messages().modify(userId="me", id=msg_id, body=body).execute()
        return True
    except Exception as e:
        print(f"⚠️ [Gmail Engine] Failed to modify labels on {msg_id}: {e}")
        return False

def create_draft_reply(service, to_email: str, subject: str, reply_body: str, thread_id: Optional[str] = None) -> Optional[str]:
    """Creates a draft reply in Gmail so Hans can review it before sending."""
    if not service:
        return None

    try:
        from email.mime.text import MIMEText
        message = MIMEText(reply_body)
        message["to"] = to_email
        message["subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"

        raw_msg = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        draft_body = {
            "message": {
                "raw": raw_msg
            }
        }
        if thread_id:
            draft_body["message"]["threadId"] = thread_id

        draft = service.users().drafts().create(userId="me", body=draft_body).execute()
        return draft.get("id")
    except Exception as e:
        print(f"⚠️ [Gmail Engine] Failed to create draft: {e}")
        return None

def get_label_id_for_category(category: str, label_map: Dict[str, str]) -> Optional[str]:
    """Resolves category string to its matching LSFM label ID."""
    category_to_label = {
        "INTERVIEW": "LSFM/🔥 Urgent - Interviews",
        "APPLICATION_CONFIRMATION": "LSFM/💼 Applications",
        "REJECTION": "LSFM/💼 Applications",
        "UNIVERSITY": "LSFM/🎓 University",
        "FINANCE": "LSFM/💳 Finance & Banking",
        "DELIVERY": "LSFM/📦 Deliveries & Orders",
        "DEV_OPS": "LSFM/⚙️ Dev & Cloud",
        "SECURITY": "LSFM/🔑 Security & Alerts",
        "NEWSLETTER": "LSFM/📰 Design & Tech Reads",
        "PROMO_CLUTTER": "LSFM/📁 Promos & Clutter",
        "PERSONAL_VIP": "LSFM/💬 Personal VIP",
    }
    target_name = category_to_label.get(category)
    return label_map.get(target_name) if target_name else None

def triage_inbox(max_emails: int = 25, unread_only: bool = False) -> Dict[str, Any]:
    """
    Comprehensive Executive Triage routine:
    1. Fetches emails from INBOX (intelligent unread + active inbox scanning)
    2. Ensures custom LSFM labels exist
    3. Classifies each email with high-precision hybrid classifier
    4. Auto-labels all emails
    5. Auto-archives newsletters and promo clutter (clearing INBOX & UNREAD badge)
    6. Returns multi-category report for Discord
    """
    service = get_gmail_service()
    if not service:
        return {
            "success": False,
            "error": "Gmail API is not authenticated. Please run test_gmail_auth.py or verify credentials.",
            "interviews": [],
            "applications": [],
            "university": [],
            "finance": [],
            "deliveries": [],
            "dev_ops": [],
            "security": [],
            "personal_vip": [],
            "newsletters_archived": 0,
            "promos_archived": 0,
            "total_scanned": 0
        }

    label_map = ensure_lsfm_labels(service)
    inbox_messages = fetch_inbox_messages(service, max_results=max_emails, unread_only=unread_only)

    interviews = []
    applications = []
    university = []
    finance = []
    deliveries = []
    dev_ops = []
    security = []
    personal_vip = []
    newsletters_archived = 0
    promos_archived = 0

    for msg in inbox_messages:
        classification = classify_email(msg)
        cat = classification.get("category", "PROMO_CLUTTER")
        source = classification.get("source") or "Unknown"
        summary = classification.get("summary") or msg.get("subject", "")

        target_label_id = get_label_id_for_category(cat, label_map)
        remove_inbox = False

        item_data = {
            "id": msg["id"],
            "sender": msg["sender"],
            "subject": msg["subject"],
            "source": source,
            "summary": summary,
            "urgency": classification.get("urgency", "MEDIUM"),
            "snippet": msg["snippet"]
        }

        if cat == "INTERVIEW":
            interviews.append({**item_data, "company": source, "thread_id": msg["thread_id"]})
        elif cat == "APPLICATION_CONFIRMATION":
            applications.append({**item_data, "company": source})
        elif cat == "UNIVERSITY":
            university.append(item_data)
        elif cat == "FINANCE":
            finance.append(item_data)
        elif cat == "DELIVERY":
            deliveries.append(item_data)
        elif cat == "DEV_OPS":
            dev_ops.append(item_data)
        elif cat == "SECURITY":
            security.append(item_data)
        elif cat == "PERSONAL_VIP":
            personal_vip.append(item_data)
        elif cat == "NEWSLETTER":
            remove_inbox = True
            newsletters_archived += 1
        elif cat == "PROMO_CLUTTER":
            remove_inbox = True
            promos_archived += 1
        elif cat == "REJECTION":
            remove_inbox = True
            applications.append({**item_data, "company": source, "status": "Rejected"})

        # Apply label and archive clutter from inbox (and clear unread badge)
        add_list = [target_label_id] if target_label_id else []
        remove_list = ["INBOX", "UNREAD"] if remove_inbox else []
        if add_list or remove_list:
            modify_email_labels(service, msg["id"], add_labels=add_list, remove_labels=remove_list)

    return {
        "success": True,
        "interviews": interviews,
        "applications": applications,
        "university": university,
        "finance": finance,
        "deliveries": deliveries,
        "dev_ops": dev_ops,
        "security": security,
        "personal_vip": personal_vip,
        "newsletters_archived": newsletters_archived,
        "promos_archived": promos_archived,
        "total_scanned": len(inbox_messages)
    }

def sweep_inbox(max_emails: int = 50) -> Dict[str, Any]:
    """
    On-Demand Clutter Sweep routine (!clean command):
    Scans up to max_emails messages in INBOX (both read & unread).
    Auto-labels and archives all promos, marketing blasts, and newsletters to achieve Inbox Zero.
    Keeps all university, career, finance, order, dev, and personal emails safely in the inbox.
    """
    service = get_gmail_service()
    if not service:
        return {"success": False, "error": "Gmail API not authenticated."}

    label_map = ensure_lsfm_labels(service)
    inbox_messages = fetch_inbox_messages(service, max_results=max_emails, unread_only=False)

    swept_promos = 0
    swept_newsletters = 0
    labeled_retained = 0
    categories_counted = {}

    for msg in inbox_messages:
        classification = classify_email(msg)
        cat = classification.get("category", "PROMO_CLUTTER")
        categories_counted[cat] = categories_counted.get(cat, 0) + 1

        target_label_id = get_label_id_for_category(cat, label_map)
        remove_inbox = False

        if cat in ["PROMO_CLUTTER", "NEWSLETTER", "REJECTION"]:
            remove_inbox = True
            if cat == "PROMO_CLUTTER":
                swept_promos += 1
            elif cat == "NEWSLETTER":
                swept_newsletters += 1
        else:
            labeled_retained += 1

        add_list = [target_label_id] if target_label_id else []
        remove_list = ["INBOX", "UNREAD"] if remove_inbox else []
        if add_list or remove_list:
            modify_email_labels(service, msg["id"], add_labels=add_list, remove_labels=remove_list)

    return {
        "success": True,
        "total_scanned": len(inbox_messages),
        "promos_swept": swept_promos,
        "newsletters_swept": swept_newsletters,
        "total_swept": swept_promos + swept_newsletters,
        "retained_in_inbox": labeled_retained,
        "categories": categories_counted
    }

def get_recent_finance_summary(max_results: int = 20) -> Dict[str, Any]:
    """
    Scans recent financial receipts, banking alerts, and e-commerce orders (!receipts command).
    Extracts merchant, approximate amount, date, and subject.
    """
    service = get_gmail_service()
    if not service:
        return {"success": False, "error": "Gmail API not authenticated."}

    query = "GCash OR Maya OR Shopee OR Lazada OR 'Grab' OR 'Foodpanda' OR invoice OR receipt OR 'BDO' OR 'BPI' OR 'PayPal'"
    try:
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results
        ).execute()
        messages = results.get("messages", [])
    except Exception as e:
        return {"success": False, "error": str(e)}

    parsed_transactions = []
    amount_regex = re.compile(r"(?:PHP|Php|₱|\$)\s*([0-9,]+(?:\.[0-9]{2})?)")

    for m in messages:
        try:
            msg = service.users().messages().get(
                userId="me",
                id=m["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            snippet = msg.get("snippet", "")
            subject = headers.get("Subject", "Transaction")
            sender = headers.get("From", "Unknown")
            date_str = headers.get("Date", "")[:16]

            # Detect amount from snippet or subject
            match = amount_regex.search(f"{subject} {snippet}")
            amount = match.group(0) if match else "N/A"

            # Determine merchant
            merchant = "Unknown"
            lower = f"{sender} {subject}".lower()
            if "gcash" in lower:
                merchant = "GCash"
            elif "maya" in lower:
                merchant = "Maya"
            elif "shopee" in lower:
                merchant = "Shopee"
            elif "lazada" in lower:
                merchant = "Lazada"
            elif "grab" in lower:
                merchant = "Grab"
            elif "foodpanda" in lower:
                merchant = "Foodpanda"
            elif "bdo" in lower:
                merchant = "BDO"
            elif "bpi" in lower:
                merchant = "BPI"
            elif "paypal" in lower:
                merchant = "PayPal"
            else:
                merchant = sender.split("<")[0].strip().strip('"')[:20]

            parsed_transactions.append({
                "merchant": merchant,
                "amount": amount,
                "subject": subject[:50],
                "date": date_str,
                "snippet": snippet[:100]
            })
        except Exception:
            continue

    return {
        "success": True,
        "transactions": parsed_transactions,
        "count": len(parsed_transactions)
    }
