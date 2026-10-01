import sys
import os
import webbrowser
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"
AUTH_FILE = BASE_DIR / "AUTH_URL.txt"

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

def main():
    print("=" * 65, flush=True)
    print("🌸 LE SSERAFIM AI HQ — 1-TIME GMAIL AUTHENTICATION", flush=True)
    print("=" * 65, flush=True)

    if not CREDENTIALS_PATH.exists():
        print("❌ credentials.json not found!", flush=True)
        return

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_PATH),
        SCOPES,
        redirect_uri="http://localhost:8080/"
    )

    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    # Save to file for easy clicking/opening
    AUTH_FILE.write_text(auth_url, encoding="utf-8")

    print("\n👉 GOOGLE AUTHORIZATION LINK GENERATED!\n", flush=True)
    print(auth_url, flush=True)
    print("\n" + "=" * 65, flush=True)
    print("🌐 Launching browser now...", flush=True)

    try:
        os.startfile(auth_url)
    except Exception:
        webbrowser.open(auth_url)

    print("⏳ Waiting for you to sign in with your Google account and click Allow...", flush=True)
    creds = flow.run_local_server(port=8080, open_browser=False)

    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print("\n🎉 SUCCESS! token.json has been created!", flush=True)
    print("=" * 65, flush=True)

    if AUTH_FILE.exists():
        try:
            AUTH_FILE.unlink()
        except Exception:
            pass

if __name__ == "__main__":
    main()
