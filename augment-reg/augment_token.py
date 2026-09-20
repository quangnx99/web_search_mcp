#!/usr/bin/env python3
"""
Lay "session CLI" (accessToken + tenantURL) cho auggie tu tai khoan da dang ky.

Tai hien dung flow `auggie login` cua CLI @augmentcode/auggie:

    1. Sinh PKCE (code_verifier + code_challenge S256) va state
    2. Mo http://127.0.0.1:<port>/callback lam redirect_uri (giong CLI)
    3. Mo authorize URL tren camoufox voi session da dang nhap:
         https://auth.augmentcode.com/authorize
           ?response_type=code&client_id=auggie-cli
           &code_challenge=<S256>&state=<state>&redirect_uri=<loopback>
    4. Callback tra ve `code` + `tenant_url`
    5. POST {tenant_url}/token (grant_type=authorization_code) -> access_token
    6. Luu {"accessToken", "tenantURL", "scopes": ["email"]} ra sessions/<email>.json

Ket qua dung dinh dang `auggie token print` va ~/.augment/session.json.
Cung cap `get_cli_session(page)` de augment_reg.py goi lai sau khi dang ky.

Cach dung:
    py -3.12 augment-reg/augment_token.py --email auggie...@uberip.com
    py -3.12 augment-reg/augment_token.py --state augment-reg/states/x@y.com.json
    py -3.12 augment-reg/augment_token.py --all --only-missing
"""

import argparse
import base64
import hashlib
import json
import secrets
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from camoufox.sync_api import Camoufox

# ---------------------------------------------------------------- constants
AUTH_URL = "https://auth.augmentcode.com"
CLIENT_ID = "auggie-cli"
SCOPES = ["email"]

BASE_DIR = Path(__file__).parent
STATE_DIR = BASE_DIR / "states"
SESSION_DIR = BASE_DIR / "sessions"
SCREEN_DIR = BASE_DIR / "screens"


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def shot(page, name: str):
    SCREEN_DIR.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(SCREEN_DIR / f"{name}.png"))
    except Exception:
        pass


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_pkce() -> tuple[str, str, str]:
    verifier = b64url(secrets.token_bytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge, b64url(secrets.token_bytes(8))


# ---------------------------------------------------------------- callback server
class CallbackServer:
    """HTTP server loopback nhan redirect tu Auth0 (giong CLI auggie)."""

    def __init__(self):
        self.done = threading.Event()
        self.result: dict | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                outer.result = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Authentication successful</h1>"
                    b"<p>You can close this window and return to the terminal.</p>"
                )
                outer.done.set()

            def log_message(self, *args):  # tat log ra stderr
                pass

        self._srv = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._srv.server_address[1]
        self.redirect_uri = f"http://127.0.0.1:{self.port}/callback"
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        try:
            self._srv.shutdown()
            self._srv.server_close()
        except Exception:
            pass


# ---------------------------------------------------------------- auth flow
def build_authorize_url(challenge: str, state: str, redirect_uri: str, method: str | None) -> str:
    params = {
        "response_type": "code",
        "code_challenge": challenge,
        "client_id": CLIENT_ID,
        "state": state,
        "redirect_uri": redirect_uri,
    }
    if method:
        params["code_challenge_method"] = method
    return f"{AUTH_URL}/authorize?{urlencode(params)}"


def exchange_code(tenant_url: str, code: str, verifier: str, redirect_uri: str) -> str:
    base = tenant_url if tenant_url.endswith("/") else tenant_url + "/"
    r = requests.post(
        base + "token",
        json={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        headers={"Content-Type": "application/json", "User-Agent": "augment-reg-tool/1.0"},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"token endpoint {r.status_code}: {r.text[:300]}")
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError(f"token endpoint khong tra access_token: {r.text[:300]}")
    return token


CONSENT_FORM = 'form[action="/confirm-account"]'
CONSENT_SELECTORS = (
    'button[type="submit"]:has-text("Continue")',
    'button[value="accept"]',
    'button:has-text("Accept")',
    'button:has-text("Authorize")',
    'button:has-text("Allow")',
)

_SUBMIT_JS = """() => {
    const form = document.querySelector('form[action="/confirm-account"]');
    if (!form) return false;
    const btn = form.querySelector('button[type="submit"]');
    if (!btn) return false;
    btn.click();
    return true;
}"""

MAX_PRESSES = 8
_dumped = False


def _dump_page(page, tag: str):
    """Log nut + URL de tien sua selector khi Augment doi UI."""
    try:
        btns = page.eval_on_selector_all(
            "button, input[type=submit]",
            "els => els.map(e => ({"
            "  type: e.type || '', text: (e.innerText || '').trim().slice(0, 40)"
            "}))",
        )
        log(f"  [{tag}] nut: {btns}")
    except Exception as e:
        log(f"  [{tag}] khong dump duoc nut: {e}")


def _press_continue(page) -> bool:
    """Bam 'Continue' tren trang consent.

    Trang consent la <form method=POST action=/confirm-account> voi hidden input
    (csrf_token, action=continue, oauth_params, oauth_params_signature). Click qua
    Playwright hay bi chan boi overlay/script anti-abuse (Verisoul/Stytch), nen goi
    thang btn.click() trong page de browser submit form that.
    """
    try:
        if page.evaluate(_SUBMIT_JS):
            return True
    except Exception as e:
        msg = str(e)
        # Form vua submit -> context bi huy: nghia la da bam thanh cong
        if "Execution context was destroyed" in msg or "navigation" in msg.lower():
            return True
        log(f"    (JS submit loi: {type(e).__name__}: {msg[:90]})")
        return False

    # Form khong con (Augment doi UI) -> thu click truc tiep
    for sel in CONSENT_SELECTORS:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                log(f"    bam consent qua selector: {sel}")
                btn.first.click(timeout=5000, no_wait_after=True)
                return True
        except Exception:
            pass
    return False


def _wait_for_callback(page, srv: CallbackServer, timeout_s: int, prefix: str):
    """Bam Continue khi trang consent hien ra, cho toi khi callback ve.

    Vong lap theo THOI GIAN (khong theo so lan): chuoi redirect cua Auth0 cong voi
    script anti-abuse (Verisoul/Stytch) co the lam trang consent xuat hien rat muon
    (30-60s), nen phai poll lien tuc thay vi thu vai lan roi ngoi cho.
    """
    global _dumped
    deadline = time.time() + timeout_s
    started = time.time()
    presses = 0
    last_url = ""
    while time.time() < deadline:
        if srv.done.is_set():
            return
        url = page.url
        if url != last_url:
            last_url = url
            log(f"    url: {url[:110]}")
            if "127.0.0.1" in url:
                srv.done.wait(timeout=10)  # dang redirect ve loopback
                continue
        if presses < MAX_PRESSES and _press_continue(page):
            presses += 1
            log(f"    bam Continue (lan {presses})")
            if presses == 1:
                shot(page, f"{prefix}_01_consent")
            srv.done.wait(timeout=8)  # cho POST /confirm-account + redirect
            continue
        if not _dumped and time.time() - started > 20:
            _dumped = True
            log("    (chua thay nut Continue - dump trang de debug)")
            _dump_page(page, "consent")
        srv.done.wait(timeout=1)

    shot(page, f"{prefix}_02_timeout")
    raise TimeoutError(f"Khong nhan duoc callback sau {timeout_s}s (URL: {page.url[:120]})")


def _attempt(page, verifier, challenge, state, srv: CallbackServer, method, prefix: str, wait_s: int):
    srv.done.clear()
    srv.result = None
    log(f"  mo authorize (pkce_method={method or 'mac dinh'})")
    page.goto(
        build_authorize_url(challenge, state, srv.redirect_uri, method),
        wait_until="domcontentloaded",
        timeout=90000,
    )
    page.wait_for_timeout(2500)
    shot(page, f"{prefix}_00_authorize")

    _wait_for_callback(page, srv, wait_s, prefix)

    res = srv.result or {}
    if res.get("error"):
        raise RuntimeError(f"{res['error']}: {res.get('error_description', '')}")
    if res.get("state") != state:
        raise RuntimeError("state khong khop - co the bi CSRF")
    if not res.get("code"):
        raise RuntimeError(f"callback khong co code: {res}")
    tenant_url = res.get("tenant_url") or ""
    if not urlparse(tenant_url).hostname or not urlparse(tenant_url).hostname.endswith(".augmentcode.com"):
        raise RuntimeError(f"tenant_url khong hop le: {tenant_url!r}")

    log(f"  callback OK, tenant_url = {tenant_url}")
    log("  doi code lay access token...")
    token = exchange_code(tenant_url, res["code"], verifier, srv.redirect_uri)
    return {"accessToken": token, "tenantURL": tenant_url, "scopes": SCOPES}


def get_cli_session(page, prefix: str = "token", wait_s: int = 300) -> dict:
    """Chay flow OAuth CLI tren `page` (da dang nhap), tra ve session dict."""
    global _dumped
    verifier, challenge, state = make_pkce()
    srv = CallbackServer()
    srv.start()
    log(f"  loopback server: {srv.redirect_uri}")
    try:
        last_err = None
        # CLI khong gui code_challenge_method; thu y nguyen truoc, fallback S256
        for method in (None, "S256"):
            _dumped = False
            try:
                return _attempt(page, verifier, challenge, state, srv, method, prefix, wait_s)
            except Exception as e:
                last_err = e
                log(f"  !!! that bai ({method or 'mac dinh'}): {e}")
                page.wait_for_timeout(1500)
        raise last_err or RuntimeError("Khong lay duoc session CLI")
    finally:
        srv.stop()


def save_session(email: str, session: dict) -> Path:
    SESSION_DIR.mkdir(exist_ok=True)
    out = SESSION_DIR / f"{email}.json"
    out.write_text(json.dumps(session, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------- cli
def resolve_state_file(email: str | None, state: str | None) -> Path:
    if state:
        p = Path(state)
        if not p.is_absolute():
            p = BASE_DIR / p
        if not p.exists():
            sys.exit(f"Khong thay file state: {p}")
        return p
    if not email:
        sys.exit("Can --email hoac --state.")
    p = STATE_DIR / f"{email}.json"
    if not p.exists():
        sys.exit(f"Khong thay state cho {email}: {p}")
    return p


def run(email: str | None, state: str | None, headless: bool, wait_s: int = 300) -> dict:
    state_file = resolve_state_file(email, state)
    email = email or state_file.stem
    log(f"Tai khoan: {email}")

    with Camoufox(humanize=True, geoip=True, headless=headless) as browser:
        session = _session_for(browser, state_file, email, wait_s, prefix="token")

    out = save_session(email, session)
    print("\n===== SESSION CLI =====")
    print(json.dumps(session, indent=2))
    print(f"\nDa luu : augment-reg/sessions/{out.name}")
    print("Dung   : export AUGMENT_SESSION_AUTH='<json tren>'")
    return session


def _session_for(browser, state_file: Path, email: str, wait_s: int, prefix: str) -> dict:
    ctx = browser.new_context(storage_state=str(state_file))
    try:
        page = ctx.new_page()
        page.set_default_timeout(45000)
        return get_cli_session(page, prefix=prefix, wait_s=wait_s)
    finally:
        ctx.close()


def run_all(headless: bool, wait_s: int, only_missing: bool) -> None:
    """Lay session cho moi file trong states/ (dung khi mot so account bi thieu token)."""
    states = sorted(STATE_DIR.glob("*.json"))
    if not states:
        sys.exit(f"Khong co file state nao trong {STATE_DIR}")
    todo = []
    for st in states:
        out = SESSION_DIR / st.name
        if only_missing and out.exists():
            log(f"Bo qua {st.stem} (da co session)")
            continue
        todo.append(st)
    log(f"Can lay session cho {len(todo)}/{len(states)} tai khoan")

    ok = 0
    with Camoufox(humanize=True, geoip=True, headless=headless) as browser:
        for i, st in enumerate(todo, 1):
            email = st.stem
            log(f"--- [{i}/{len(todo)}] {email}")
            try:
                session = _session_for(browser, st, email, wait_s, prefix=f"tok{i}")
                save_session(email, session)
                log(f"  OK {session['accessToken'][:16]}... @ {session['tenantURL']}")
                ok += 1
            except Exception as e:
                log(f"  !!! {email}: {e}")
    log(f"Xong: {ok}/{len(todo)} thanh cong")


def main():
    ap = argparse.ArgumentParser(description="Lay accessToken + tenantURL cho auggie CLI")
    ap.add_argument("--email", help="email tai khoan (tim trong augment-reg/states/)")
    ap.add_argument("--state", help="duong dan truc tiep toi file storage state")
    ap.add_argument("--all", action="store_true", help="lay session cho moi account trong states/")
    ap.add_argument("--only-missing", action="store_true", help="voi --all: bo qua account da co session")
    ap.add_argument("--headless", action="store_true", help="chay an cua so")
    ap.add_argument("--wait", type=int, default=300, help="giay cho callback sau khi bam Continue (mac dinh 300)")
    args = ap.parse_args()
    try:
        if args.all:
            run_all(args.headless, args.wait, args.only_missing)
        else:
            run(args.email, args.state, args.headless, args.wait)
    except KeyboardInterrupt:
        sys.exit("\nDung boi nguoi dung.")
    except Exception as e:
        sys.exit(f"\nLOI: {e}")


if __name__ == "__main__":
    main()