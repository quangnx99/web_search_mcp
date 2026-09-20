#!/usr/bin/env python3
"""
Dang ky tai khoan Augment Code (Cosmos) tu dong.

Luong thuc te cua Augment (Auth0 Universal Login, passwordless):

    1. cosmos.augmentcode.com          -> bam "Sign up"
    2. /u/signup/identifier            -> nhap "Email address" (input[name=email])
    3. /u/login/passwordless-email-challenge -> hien "Enter the code"
       -> lay code 6 so tu inbox mail.tm
    4. Nhap code -> /auth/continue     -> hien hCaptcha "Additional verification required"
    5. Tick checkbox hCaptcha -> cho backend xu ly (co the vai phut) -> trang onboarding
    6. Tai hien flow `auggie login` (OAuth PKCE client_id=auggie-cli) de lay
       accessToken + tenantURL -> sessions/<email>.json
    7. Luu tai khoan vao accounts.json + cookies vao states/<email>.json

Luu y: Augment KHONG dat mat khau - dang nhap bang code gui ve email.

Yeu cau (Python 3.12):
    py -3.12 -m pip install camoufox requests
    py -3.12 -m camoufox fetch

Cach dung:
    py -3.12 augment-reg/augment_reg.py
    py -3.12 augment-reg/augment_reg.py --headless
    py -3.12 augment-reg/augment_reg.py --wait 300
    py -3.12 augment-reg/augment_reg.py --no-cli-token
    py -3.12 augment-reg/augment_reg.py --email xxx@uberip.com --mail-pass "..."
"""

import argparse
import json
import random
import re
import string
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from camoufox.sync_api import Camoufox

from augment_token import get_cli_session, save_session as save_cli_session

# ---------------------------------------------------------------- constants
MAIL_API = "https://api.mail.tm"
COSMOS_URL = "https://cosmos.augmentcode.com/"

BASE_DIR = Path(__file__).parent
ACC_FILE = BASE_DIR / "accounts.json"
SCREEN_DIR = BASE_DIR / "screens"
STATE_DIR = BASE_DIR / "states"

UA = {"User-Agent": "augment-reg-tool/1.0"}

# Selector on dinh, khong phu thuoc ngon ngu (hCaptcha/Turnstile doi title theo locale)
TURNSTILE_FRAME = 'iframe[src*="challenges.cloudflare.com"]'
HCAPTCHA_CB = 'iframe[src*="frame=checkbox"]'
HCAPTCHA_CHALLENGE = 'iframe[src*="frame=challenge"]'


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- temp mail
class TempMail:
    """Inbox dung mot lan qua mail.tm."""

    def __init__(self, address: str, mail_pass: str):
        self.address = address
        self.mail_pass = mail_pass
        self.token = None

    @classmethod
    def create(cls) -> "TempMail":
        domains = requests.get(f"{MAIL_API}/domains", timeout=20).json()
        members = domains.get("hydra:member", [])
        active = [d for d in members if d.get("isActive")]
        domain = (active or members)[0]["domain"]
        prefix = "auggie" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        address = f"{prefix}@{domain}"
        mail_pass = "Mp!" + "".join(random.choices(string.ascii_letters + string.digits, k=10))
        r = requests.post(
            f"{MAIL_API}/accounts",
            json={"address": address, "password": mail_pass},
            headers=UA,
            timeout=20,
        )
        r.raise_for_status()
        # mail.tm co the chuan hoa dia chi (bo dau cham...) -> phai dung dia chi tra ve
        canonical = r.json().get("address", address)
        if canonical != address:
            log(f"  mail.tm chuan hoa dia chi: {canonical}")
        log(f"Email tam: {canonical}")
        return cls(canonical, mail_pass)

    def login(self, retries: int = 5, delay: float = 4.0):
        last = None
        for i in range(retries):
            r = requests.post(
                f"{MAIL_API}/token",
                json={"address": self.address, "password": self.mail_pass},
                headers=UA,
                timeout=20,
            )
            if r.status_code == 200:
                self.token = r.json()["token"]
                return
            last = r
            log(f"  mail.tm login lan {i + 1}: {r.status_code}, doi {delay}s...")
            time.sleep(delay)
        if last is not None:
            last.raise_for_status()
        raise RuntimeError("mail.tm login that bai")

    def _headers(self):
        if not self.token:
            self.login()
        return {"Authorization": f"Bearer {self.token}", **UA}

    def wait_verification(self, timeout_s: int = 300, poll_s: int = 5) -> dict:
        """Cho mail tu Augment, tra ve {'code': '123456', 'text': ...}."""
        log(f"Cho code trong mail tai {self.address} (toi da {timeout_s}s)...")
        deadline = time.time() + timeout_s
        seen: set[str] = set()
        while time.time() < deadline:
            try:
                msgs = requests.get(
                    f"{MAIL_API}/messages", headers=self._headers(), timeout=20
                ).json()
                for m in msgs.get("hydra:member", []):
                    mid = m["id"]
                    if mid in seen:
                        continue
                    seen.add(mid)
                    frm = (m.get("from", {}) or {}).get("address", "")
                    intro = (m.get("intro") or "").lower()
                    if "augment" not in frm.lower() and "code" not in intro and "verif" not in intro:
                        continue
                    detail = requests.get(
                        f"{MAIL_API}/messages/{mid}", headers=self._headers(), timeout=20
                    ).json()
                    text = (detail.get("text") or "") + "\n" + "\n".join(detail.get("html") or [])
                    log(f"  Mail tu {frm}: {detail.get('subject')}")
                    code = re.search(r"\b(\d{6})\b", text)
                    if code:
                        return {"code": code.group(1), "text": text}
                    log("  (mail khong co ma 6 so)")
            except Exception as e:  # transient network / rate limit
                log(f"  (mail poll: {e})")
            time.sleep(poll_s)
        raise TimeoutError("Khong nhan duoc code trong thoi gian cho.")


# ---------------------------------------------------------------- page helpers
def shot(page, name: str):
    SCREEN_DIR.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(SCREEN_DIR / f"{name}.png"), full_page=False)
    except Exception:
        pass


def pass_turnstile(page, timeout_s: int = 12) -> bool:
    """Tick Turnstile neu co (thuong tu pass san voi camoufox). Best-effort."""
    try:
        page.wait_for_selector(TURNSTILE_FRAME, timeout=timeout_s * 1000)
    except Exception:
        return True  # khong co challenge
    fl = page.frame_locator(TURNSTILE_FRAME)
    for _ in range(5):
        try:
            if fl.locator('input[type="checkbox"]:checked').count() > 0:
                log("  Turnstile: PASS")
                return True
            cb = fl.locator('input[type="checkbox"]')
            if cb.count() == 0:
                return True
            cb.first.click(timeout=4000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
    try:
        ok = fl.locator('input[type="checkbox"]:checked').count() > 0
    except Exception:
        ok = False
    log(f"  Turnstile: {'PASS' if ok else 'chua xac nhan (van tiep tuc)'}")
    return ok


def is_finished(page) -> bool:
    """Da ra khoi luong dang nhap -> trang onboarding / Cosmos."""
    u = page.url
    if "login.augmentcode.com" in u:
        return False
    if "/auth/continue" in u:
        return False
    return "augmentcode.com" in u


def wait_settle(page, timeout_s: int = 40, stable_ms: int = 2500) -> str:
    """Cho URL on dinh (khong doi trong stable_ms) de bo qua cac buoc redirect trung gian."""
    deadline = time.time() + timeout_s
    last = page.url
    stable_since = time.time()
    while time.time() < deadline:
        page.wait_for_timeout(500)
        if page.url != last:
            last = page.url
            stable_since = time.time()
        elif (time.time() - stable_since) * 1000 >= stable_ms:
            break
    return page.url


def hcaptcha_needs_image(page) -> bool:
    """hCaptcha da bat sang dang giai hinh anh (can nguoi giai)."""
    try:
        return page.frame_locator(HCAPTCHA_CHALLENGE).locator(".prompt-text").count() > 0
    except Exception:
        return False


def submit(page, max_retry: int = 4):
    """Bam Continue, xu ly Turnstile reset roi bam lai."""
    for i in range(max_retry):
        pass_turnstile(page, timeout_s=8)
        try:
            page.click('button[type="submit"]', timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        if "identifier" not in page.url:
            return
        log(f"  Trang con o identifier -> thu lai ({i + 1}/{max_retry})")


def handle_hcaptcha(page, wait_s: int = 300) -> bool:
    """Tick hCaptcha tren /auth/continue, roi cho redirect sang onboarding."""
    log("  Cho hCaptcha xuat hien...")
    try:
        page.wait_for_selector(HCAPTCHA_CB, timeout=45000)
    except Exception:
        log("  Khong thay hCaptcha (co the da qua luon)")
        return is_finished(page)

    page.wait_for_timeout(3000)
    try:
        page.frame_locator(HCAPTCHA_CB).locator("#checkbox").first.click(timeout=8000)
        log("  Da tick hCaptcha")
    except Exception as e:
        log(f"  Khong tick duoc hCaptcha: {e}")

    log(f"  Cho backend xu ly + redirect (toi da {wait_s}s)...")
    deadline = time.time() + wait_s
    warned = False
    last_url = ""
    while time.time() < deadline:
        if is_finished(page):
            return True
        if not warned and hcaptcha_needs_image(page):
            log("  !! hCaptcha yeu cau giai hinh anh - hay giai tay tren cua so browser")
            warned = True
        if page.url != last_url:
            last_url = page.url
            log(f"    url: {last_url[:110]}")
        page.wait_for_timeout(3000)
    return is_finished(page)


# ---------------------------------------------------------------- main flow
def run(email: str | None, mail_pass: str | None, headless: bool, wait_s: int, fetch_cli: bool = True):
    mail = TempMail(email, mail_pass) if (email and mail_pass) else TempMail.create()
    mail.login()
    log(f"mail.tm OK: {mail.address}")

    with Camoufox(humanize=True, geoip=True, headless=headless) as browser:
        # Tao context tuong minh: context do browser.new_page() tao ra khong cho mo them page
        # (can mot tab rieng cho flow OAuth lay CLI token)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.set_default_timeout(45000)

        log("B1: Mo cosmos.augmentcode.com")
        page.goto(COSMOS_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_selector('input[name="username"]', timeout=60000)
        page.wait_for_timeout(2500)
        shot(page, "00_login")

        log("B2: Chuyen sang tab Sign up")
        # The <a> khong co text va co the bi che -> uu tien lay href roi dieu huong
        signup_url = None
        try:
            href = page.locator('a[href*="/u/signup/"]').first.get_attribute("href", timeout=10000)
            if href:
                signup_url = urljoin(page.url, href)
        except Exception:
            pass
        if signup_url:
            page.goto(signup_url, wait_until="domcontentloaded", timeout=90000)
        else:
            page.click('a[href*="/u/signup/"]', timeout=15000)
        page.wait_for_selector('input[name="email"]', timeout=45000)
        page.wait_for_timeout(1500)
        shot(page, "01_signup")

        log(f"B3: Dien email signup: {mail.address}")
        page.fill('input[name="email"]', mail.address)
        page.wait_for_timeout(800)
        submit(page)
        page.wait_for_selector('input[name="code"]', timeout=60000)
        shot(page, "02_code_screen")
        log("    -> man nhap code")

        log("B4: Lay code tu mail va nhap")
        ver = mail.wait_verification(timeout_s=300)
        log(f"    code = {ver['code']}")
        page.fill('input[name="code"]', ver["code"])
        page.wait_for_timeout(800)
        submit(page)

        log("B5: Kiem tra bo sung (hCaptcha)")
        ok = handle_hcaptcha(page, wait_s=wait_s)
        if ok:
            # bo qua cac buoc redirect trung gian (/auth2/callback, /authorize/resume...)
            log(f"    -> {wait_settle(page)}")
            page.wait_for_timeout(3000)
        shot(page, "03_final")

        log("B6: Lay session CLI (accessToken + tenantURL) cho auggie")
        cli_session = None
        cli_session_file = None
        if fetch_cli and ok:
            try:
                cli_page = page.context.new_page()
                cli_page.set_default_timeout(45000)
                cli_session = get_cli_session(cli_page, prefix="cli", wait_s=wait_s)
                cli_page.close()
                cli_session_file = f"sessions/{save_cli_session(mail.address, cli_session).name}"
                log(f"  accessToken: {cli_session['accessToken'][:16]}... @ {cli_session['tenantURL']}")
            except Exception as e:
                log(f"  (khong lay duoc session CLI: {e})")
        elif fetch_cli:
            log("B6: Bo qua session CLI vi chua dang ky xong")

        state_dir = STATE_DIR
        state_dir.mkdir(exist_ok=True)
        state_file = state_dir / f"{mail.address}.json"
        try:
            page.context.storage_state(path=str(state_file))
            state_rel = f"states/{state_file.name}"
        except Exception as e:
            log(f"  (khong luu duoc cookies: {e})")
            state_rel = None

        result = {
            "augment_email": mail.address,
            "mailtm_email": mail.address,
            "mailtm_password": mail.mail_pass,
            "login_method": "passwordless (code 6 so gui ve email)",
            "final_url": page.url,
            "success": ok,
            "storage_state": state_rel,
            "cli_session": cli_session,
            "cli_session_file": cli_session_file,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        accs = json.loads(ACC_FILE.read_text(encoding="utf-8")) if ACC_FILE.exists() else []
        accs.append(result)
        ACC_FILE.write_text(json.dumps(accs, indent=2, ensure_ascii=False), encoding="utf-8")

        print("\n===== TAI KHOAN AUGMENT =====")
        print(f"Email    : {mail.address}")
        print(f"Mail tam : mail.tm (pass: {mail.mail_pass})")
        print(f"Trang thai: {'OK -> ' + page.url if ok else 'CHUA XONG -> ' + page.url}")
        if state_rel:
            print(f"Cookies  : augment-reg/{state_rel}")
        if cli_session:
            print(f"CLI token: {cli_session['accessToken']}")
            print(f"Tenant   : {cli_session['tenantURL']}")
            print(f"Session  : augment-reg/{cli_session_file}")
        print(f"Da luu   : {ACC_FILE}")
        if not ok:
            print("CANH BAO: chua ve duoc onboarding. Xem augment-reg/screens/, thu tang --wait.")


def main():
    ap = argparse.ArgumentParser(description="Dang ky tai khoan Augment Code")
    ap.add_argument("--email", help="dung lai email mail.tm co san")
    ap.add_argument("--mail-pass", help="mat khau mail.tm tuong ung")
    ap.add_argument("--headless", action="store_true", help="chay an cua so (captcha kho hon)")
    ap.add_argument("--wait", type=int, default=300, help="giay cho sau khi tick hCaptcha (mac dinh 300)")
    ap.add_argument("--no-cli-token", action="store_true", help="khong lay accessToken/tenantURL cho auggie CLI")
    args = ap.parse_args()
    if bool(args.email) != bool(args.mail_pass):
        sys.exit("--email va --mail-pass phai di cung nhau.")
    try:
        run(args.email, args.mail_pass, args.headless, args.wait, fetch_cli=not args.no_cli_token)
    except KeyboardInterrupt:
        sys.exit("\nDung boi nguoi dung.")
    except Exception as e:
        sys.exit(f"\nLOI: {e}")


if __name__ == "__main__":
    main()