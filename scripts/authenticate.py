import sys
import os
import webbrowser
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # quiet: no console to reconfigure (pythonw, redirected stream)
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

    auth_url, state = flow.authorization_url(prompt="consent", access_type="offline")

    # Save to file for easy clicking/opening
    AUTH_FILE.write_text(auth_url, encoding="utf-8")

    print("\n👉 GOOGLE AUTHORIZATION LINK GENERATED!\n", flush=True)
    print(auth_url, flush=True)
    print("\n" + "=" * 65, flush=True)
    print("🌐 Launching browser now...", flush=True)

    try:
        os.startfile(auth_url)
    except Exception:  # quiet: falls back to webbrowser.open
        webbrowser.open(auth_url)

    print("⏳ Waiting for you to sign in with your Google account and click Allow...", flush=True)
    # run_local_server builds its own URL; reuse the opened link's state or the callback fails the CSRF check
    creds = flow.run_local_server(port=8080, open_browser=False, state=state,
                                  prompt="consent", access_type="offline")

    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print("\n🎉 SUCCESS! token.json has been created!", flush=True)
    sys.path.insert(0, str(BASE_DIR))
    import gmail_engine
    if gmail_engine._after_sign_in():  # Sakura DMs a re-sign reminder 6 days from now
        print("⏰ Sakura will remind you to re-sign in 6 days.", flush=True)
    print("=" * 65, flush=True)

    if AUTH_FILE.exists():
        try:
            AUTH_FILE.unlink()
        except Exception:  # quiet: best-effort removal of a stale token file
            pass

if __name__ == "__main__":
    main()
