import os
import sys
import time
import re
from pathlib import Path
from typing import Dict, Any, List
from collections import defaultdict

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # quiet: no console to reconfigure (pythonw, redirected stream)
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from gmail_engine import (
    get_gmail_service,
    ensure_lsfm_labels,
    classify_email,
    get_label_id_for_category,
    extract_body_text
)
from googleapiclient.errors import HttpError


def fetch_all_inbox_metadata(service, max_total: int = 250) -> List[Dict[str, Any]]:
    """Fetches metadata for all messages currently in label:INBOX using paging and rate-limit pacing."""
    all_messages = []
    page_token = None

    print("📬 [1/3] Querying Gmail for all messages in Primary Inbox...")
    while True:
        try:
            resp = service.users().messages().list(
                userId="me",
                q="label:INBOX",
                maxResults=min(100, max_total - len(all_messages)),
                pageToken=page_token
            ).execute()

            msgs = resp.get("messages", [])
            all_messages.extend(msgs)
            page_token = resp.get("nextPageToken")

            if not page_token or len(all_messages) >= max_total:
                break
        except HttpError as e:
            if e.resp.status in [403, 429]:
                print("⏳ Rate limit detected, pausing 3 seconds...")
                time.sleep(3)
                continue
            raise

    total = len(all_messages)
    print(f"📦 Found {total} total messages in Primary Inbox. Fetching metadata...")

    parsed_emails = []
    for idx, m in enumerate(all_messages, start=1):
        retries = 3
        while retries > 0:
            try:
                msg_data = service.users().messages().get(
                    userId="me",
                    id=m["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date", "List-Unsubscribe", "Precedence"]
                ).execute()

                headers = {h["name"].lower(): h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
                subject = headers.get("subject", "(No Subject)")
                sender = headers.get("from", "Unknown Sender")
                date = headers.get("date", "")
                snippet = msg_data.get("snippet", "")

                parsed_emails.append({
                    "id": m["id"],
                    "thread_id": msg_data.get("threadId"),
                    "subject": subject,
                    "sender": sender,
                    "date": date,
                    "snippet": snippet,
                    "body": snippet,
                    "label_ids": msg_data.get("labelIds", []),
                    "headers": headers
                })

                if idx % 10 == 0 or idx == total:
                    print(f"   ↳ Loaded headers: {idx}/{total} emails...", end="\r", flush=True)

                # Gentle 40ms pacing to strictly adhere to Gmail quota
                time.sleep(0.04)
                break
            except HttpError as e:
                if e.resp.status in [403, 429]:
                    time.sleep(2.5)
                    retries -= 1
                else:
                    break
            except Exception as _exc:
                print(f"⚠️ [mass_cleanse.fetch_all_inbox_metadata] suppressed {type(_exc).__name__}: {_exc}", flush=True)
                break

    print(f"\n✅ Finished loading {len(parsed_emails)} emails.")
    return parsed_emails


def run_mass_cleanse():
    print("=" * 65)
    print("🌸 LE SSERAFIM AI HQ — MASS INBOX CLEANSE & ZERO SWEEP")
    print("Strategy: High-Signal Inbox (Archive Clutter, Keep Relevant)")
    print("=" * 65)

    service = get_gmail_service()
    if not service:
        print("❌ Gmail API is not authenticated! Please run test_gmail_auth.py first.")
        return

    label_map = ensure_lsfm_labels(service)
    emails = fetch_all_inbox_metadata(service, max_total=250)

    if not emails:
        print("✨ Your Primary Inbox is already at Inbox Zero!")
        return

    total = len(emails)
    print(f"\n🧠 [2/3] Classifying and sorting all {total} emails with Two-Tier AI...")

    action_groups = defaultdict(list)
    category_counts = defaultdict(int)

    noise_categories = {"PROMO_CLUTTER", "NEWSLETTER", "REJECTION"}
    archived_count = 0
    retained_count = 0

    for idx, email in enumerate(emails, start=1):
        cls = classify_email(email)
        cat = cls.get("category", "PROMO_CLUTTER")
        source = cls.get("source", "Unknown")
        summary = cls.get("summary", email["subject"])

        target_label_id = get_label_id_for_category(cat, label_map)
        is_noise = cat in noise_categories

        action_groups[(target_label_id, is_noise)].append(email["id"])
        category_counts[cat] += 1

        if is_noise:
            archived_count += 1
            status_tag = "🧹 [ARCHIVED NOISE]"
        else:
            retained_count += 1
            status_tag = "📌 [KEPT IN INBOX]"

        source_short = source[:20]
        subj_short = summary[:45]
        print(f"[{idx:3d}/{total:3d}] {status_tag} {cat:<24} | {source_short:<20} | {subj_short}")

    print(f"\n🏷️ [3/3] Applying batch updates to Gmail...")

    for (target_label_id, is_noise), msg_ids in action_groups.items():
        if not msg_ids:
            continue

        add_labels = [target_label_id] if target_label_id else []
        remove_labels = ["INBOX", "UNREAD"] if is_noise else []

        for i in range(0, len(msg_ids), 500):
            chunk = msg_ids[i:i + 500]
            body = {
                "ids": chunk,
                "addLabelIds": add_labels,
                "removeLabelIds": remove_labels
            }
            try:
                service.users().messages().batchModify(userId="me", body=body).execute()
                time.sleep(0.1)
            except Exception as e:
                print(f"⚠️ Warning updating batch: {e}")

    print("\n" + "=" * 65)
    print("🎉 MASS INBOX CLEANSE COMPLETED SUCCESSFULLY!")
    print("=" * 65)
    print(f"📊 Summary:")
    print(f"   • Total Emails Scanned: {total}")
    print(f"   • 🧹 Promotional & Newsletter Noise Archived: {archived_count}")
    print(f"   • 📌 High-Signal Items Neatly Labeled in Primary Inbox: {retained_count}")
    print("\n📂 Category Breakdown:")
    for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"   • {cat:<25}: {count:3d} emails")
    print("=" * 65)
    print("💡 Open Gmail in your browser or type '!inbox' in Discord to view your high-signal workspace!")

    return {
        "success": True,
        "total": total,
        "archived_count": archived_count,
        "retained_count": retained_count,
        "categories": dict(category_counts)
    }


if __name__ == "__main__":
    run_mass_cleanse()
