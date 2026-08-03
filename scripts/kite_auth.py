"""
Kite Connect authentication — daily token refresh, semi-automated.

Credentials live in ../data/secrets/kite_secrets.json (git-ignored, 600
perms) — api_key, api_secret, totp_secret. NEVER the account password.
Zerodha provides no programmatic password-login endpoint (by design, to
prevent scripted logins) — Kite Connect's request_token can ONLY come from
an actual browser login, so full automation of this step is not possible
without storing the account password and reverse-engineering Zerodha's web
login page (unofficial, fragile, real risk to the collaborator's account
standing — considered and REJECTED, see memory
kite-connect-live-feed-2026-08). The password is typed directly into
Zerodha's own page by a human, never touched by this code.

RECOMMENDED DAILY COMMAND:
    python kite_auth.py refresh
Opens the login page in a browser automatically, prints a fresh TOTP code
right when needed, then waits for you to paste back the request_token from
the redirected URL and exchanges it immediately. About 30 seconds of manual
work (Client ID + password + the printed TOTP), everything else automatic.

Lower-level commands (same two steps, split apart — used by `refresh`
internally, or standalone for scripting/debugging):
    python kite_auth.py login                    # prints URL + TOTP
    python kite_auth.py exchange REQUEST_TOKEN    # exchanges for access_token

Either way, the access_token is cached to
../data/secrets/kite_access_token.json (git-ignored) — this is what
live_quotes.py actually reads. Zerodha access tokens expire ~24h (typically
at market open the next day), so this needs re-running daily.
"""
import json
import os
import sys

import pyotp
from kiteconnect import KiteConnect

SECRETS_PATH = "../data/secrets/kite_secrets.json"
TOKEN_PATH = "../data/secrets/kite_access_token.json"


def load_secrets():
    if not os.path.exists(SECRETS_PATH):
        sys.exit(f"ABORT: no secrets file at {SECRETS_PATH} — see kite_auth.py docstring")
    with open(SECRETS_PATH) as f:
        s = json.load(f)
    missing = [k for k in ("api_key", "api_secret", "totp_secret") if not s.get(k)]
    if missing:
        sys.exit(f"ABORT: secrets file missing {missing}")
    return s


def current_totp(totp_secret):
    return pyotp.TOTP(totp_secret).now()


def cmd_login():
    s = load_secrets()
    kite = KiteConnect(api_key=s["api_key"])
    print("1. Open this URL in a browser:\n")
    print(f"   {kite.login_url()}\n")
    print("2. Log in with the account's Client ID + PASSWORD (typed live —")
    print("   never stored by this script), then enter this TOTP code when asked:\n")
    print(f"   TOTP: {current_totp(s['totp_secret'])}")
    print("   (valid ~30s — if it expires before you get there, re-run this command)\n")
    print("3. After login, the browser redirects to a URL containing")
    print("   'request_token=XXXX'. Copy just that token value, then run:\n")
    print("   python kite_auth.py exchange <request_token>")


def cmd_refresh():
    """Semi-automated daily refresh: everything EXCEPT the password keystroke
    is automatic. Opens the login page in the default browser, prints a
    fresh TOTP right when it's needed, then waits for the human to paste
    back the request_token from the redirected URL and exchanges it
    immediately. This is the recommended daily command — 'login'+'exchange'
    stay available separately for scripting/debugging.

    Deliberately NOT further automated: Kite Connect's request_token can
    only come from an actual browser login, and Zerodha provides no
    programmatic password-login endpoint (by design, to prevent scripted
    logins) — see kite_auth.py's module docstring and memory
    kite-connect-live-feed-2026-08 for why storing the account password to
    script around this was considered and rejected."""
    import webbrowser
    s = load_secrets()
    kite = KiteConnect(api_key=s["api_key"])
    url = kite.login_url()

    print(f"Opening login page in your browser:\n  {url}\n")
    opened = webbrowser.open(url)
    if not opened:
        print("(couldn't auto-open a browser — copy the URL above manually)\n")

    print("Log in with the account's Client ID + PASSWORD, then enter this TOTP")
    print("code when asked (regenerate by re-running this command if it expires):\n")
    print(f"   TOTP: {current_totp(s['totp_secret'])}\n")
    print("After login, the browser redirects to a URL containing 'request_token=...'.")
    token = input("Paste the request_token here: ").strip()
    if not token:
        sys.exit("ABORT: no token entered")
    if "request_token=" in token:
        # tolerate pasting the whole redirected URL instead of just the token
        token = token.split("request_token=")[1].split("&")[0]
    cmd_exchange(token)


def cmd_exchange(request_token):
    s = load_secrets()
    kite = KiteConnect(api_key=s["api_key"])
    try:
        data = kite.generate_session(request_token, api_secret=s["api_secret"])
    except Exception as e:
        sys.exit(f"ABORT: token exchange failed — {e}\n"
                 f"(request_token is single-use and expires in minutes — if this "
                 f"is stale, run 'python kite_auth.py login' again for a fresh one)")
    access_token = data["access_token"]
    out = {"access_token": access_token, "generated_for_client": data.get("user_id")}
    with open(TOKEN_PATH, "w") as f:
        json.dump(out, f, indent=2)
    os.chmod(TOKEN_PATH, 0o600)
    print(f"Access token saved to {TOKEN_PATH} for client {data.get('user_id')}.")
    print("This token is valid until ~market open tomorrow — re-run the login+exchange")
    print("flow daily (can be automated later once this manual path is confirmed working).")


def get_access_token():
    """For other scripts (live_quotes.py) to load the cached token. Returns
    None if no token has been generated yet or the file is missing."""
    if not os.path.exists(TOKEN_PATH):
        return None
    with open(TOKEN_PATH) as f:
        return json.load(f).get("access_token")


def get_kite_client():
    """Returns a ready-to-use authenticated KiteConnect instance, or None if
    no access token is cached yet (caller should fall back to yfinance)."""
    token = get_access_token()
    if not token:
        return None
    s = load_secrets()
    kite = KiteConnect(api_key=s["api_key"])
    kite.set_access_token(token)
    return kite


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("login", "exchange", "refresh"):
        sys.exit("usage: python kite_auth.py refresh          (recommended daily command)\n"
                 "       python kite_auth.py login\n"
                 "       python kite_auth.py exchange <request_token>")
    if sys.argv[1] == "refresh":
        cmd_refresh()
    elif sys.argv[1] == "login":
        cmd_login()
    else:
        if len(sys.argv) < 3:
            sys.exit("usage: python kite_auth.py exchange <request_token>")
        cmd_exchange(sys.argv[2])
