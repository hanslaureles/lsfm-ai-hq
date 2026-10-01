import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"

def print_setup_guide():
    print("=" * 65)
    print("🔑 GOOGLE GMAIL API — 1-TIME SETUP GUIDE FOR HANS")
    print("=" * 65)
    print("To let Sakura organize your Gmail, we need a free OAuth Client ID:")
    print("\n1. Go to Google Cloud Console:")
    print("   👉 https://console.cloud.google.com/apis/dashboard")
    print("\n2. Create a new project (e.g. 'Hans Job Copilot') or select an existing one.")
    print("\n3. Enable the Gmail API:")
    print("   👉 https://console.cloud.google.com/apis/library/gmail.googleapis.com")
    print("   Click 'Enable'.")
    print("\n4. Configure OAuth Consent Screen:")
    print("   👉 https://console.cloud.google.com/apis/credentials/consent")
    print("   • User Type: External")
    print("   • App name: 'Hans Job Copilot' (add your own Gmail address)")
    print("   • In 'Test Users', add your own Gmail address")
    print("\n5. Create OAuth Client ID:")
    print("   👉 https://console.cloud.google.com/apis/credentials")
    print("   • Click '+ CREATE CREDENTIALS' -> 'OAuth client ID'")
    print("   • Application type: 'Desktop app'")
    print("   • Name: 'LE SSERAFIM AI Desktop'")
    print("   • Click Create, then click 'DOWNLOAD JSON'")
    print(f"\n6. Rename the downloaded file to 'credentials.json' and place it in:")
    print(f"   📁 {CREDENTIALS_PATH.resolve()}")
    print("=" * 65)

def main():
    print("=" * 65)
    print("🌸 LE SSERAFIM AI HQ — GMAIL ENGINE CONNECTION TEST")
    print("=" * 65)

    if not CREDENTIALS_PATH.exists() and not TOKEN_PATH.exists():
        print("❌ 'credentials.json' not found in lsfm-swarm directory!\n")
        print_setup_guide()
        return

    print("🔍 Found credentials! Connecting to Google Gmail API...")
    try:
        from gmail_engine import get_gmail_service, ensure_lsfm_labels, fetch_unread_inbox_messages

        service = get_gmail_service()
        if not service:
            print("❌ Authentication failed or credentials missing.")
            return

        print("✅ Successfully authenticated with Gmail API!")
        print("🏷️ Verifying LSFM custom labels...")
        label_map = ensure_lsfm_labels(service)
        for name, lbl_id in label_map.items():
            print(f"   • {name} (ID: {lbl_id})")

        print("\n📬 Checking unread inbox messages...")
        unread = fetch_unread_inbox_messages(service, max_results=5)
        print(f"   Found {len(unread)} recent unread messages in INBOX.")
        for i, m in enumerate(unread, 1):
            print(f"   [{i}] From: {m['sender'][:35]} | Subject: {m['subject'][:45]}")

        print("\n🎉 GMAIL ENGINE IS READY FOR SAKURA & CHAEWON!")
        print("=" * 65)
    except Exception as e:
        print(f"❌ Error during test: {e}")

if __name__ == "__main__":
    main()
