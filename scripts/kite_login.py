"""
investMITRA — Kite Semi-Automated Login
Run at 8:55 AM every morning before market opens.

What it does automatically:
  1. Opens Zerodha login in browser
  2. Waits for you to login (30 seconds)
  3. Captures the request_token from redirect
  4. Generates access_token
  5. Saves to .env.prod
  6. Starts intraday signal engine

You only need to: login on phone/browser (30 sec) and approve.

Run: python scripts/kite_login.py
"""
import os, sys, time, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv, set_key
load_dotenv('.env.prod')

from kiteconnect import KiteConnect

API_KEY    = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

if not API_KEY or not API_SECRET:
    print("ERROR: Set KITE_API_KEY and KITE_API_SECRET in .env.prod")
    sys.exit(1)

kite          = KiteConnect(api_key=API_KEY)
request_token = None


class TokenHandler(BaseHTTPRequestHandler):
    """Captures request_token from Zerodha redirect."""

    def do_GET(self):
        global request_token
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "request_token" in params:
            request_token = params["request_token"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style='font-family:sans-serif;text-align:center;padding:50px'>
                <h2 style='color:green'>&#10003; Login Successful!</h2>
                <p>investMITRA has captured your token.</p>
                <p>You can close this window.</p>
                <script>setTimeout(()=>window.close(),2000)</script>
                </body></html>
            """)
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Waiting for token...")

    def log_message(self, format, *args):
        pass  # Suppress server logs


def main():
    global request_token

    print(f"\n{'='*60}")
    print("  investMITRA — Kite Login")
    print(f"{'='*60}")
    print(f"  Time: {time.strftime('%H:%M:%S')}")
    print(f"  Opening Zerodha login...")
    print()

    # Start local server to capture redirect
    server = HTTPServer(("127.0.0.1", 5000), TokenHandler)
    server.timeout = 120  # 2 minutes timeout

    # Open login URL
    login_url = kite.login_url()
    webbrowser.open(login_url)

    print("  ➤ Login to Zerodha in the browser")
    print("  ➤ Complete 2FA (TOTP/PIN)")
    print("  ➤ Token will be captured automatically")
    print()
    print("  Waiting (120 seconds timeout)...")

    # Wait for callback
    start = time.time()
    while not request_token and time.time() - start < 120:
        server.handle_request()

    if not request_token:
        print("❌ Timeout — no token received. Try again.")
        sys.exit(1)

    # Generate session
    try:
        data         = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = data["access_token"]

        # Save to .env.prod
        set_key(".env.prod", "KITE_ACCESS_TOKEN", access_token)
        set_key(".env.prod", "KITE_LOGIN_DATE", time.strftime("%Y-%m-%d"))

        print(f"\n{'='*60}")
        print(f"  ✅ LOGIN SUCCESSFUL")
        print(f"  Token saved to .env.prod")
        print(f"  Token: {access_token[:8]}...{access_token[-4:]}")
        print(f"{'='*60}")
        print()
        print("  investMITRA intraday engine ready.")
        print("  Run: python scripts/intraday_signals.py")

    except Exception as e:
        print(f"❌ Session generation failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
