"""Kiểm tra một session trong `sessions/` có thật sự dùng được với API Augment không.

Gọi thẳng các endpoint mà auggie CLI / Context Engine dùng, tất cả đều là
`POST {tenantURL}/<endpoint>` với header `Authorization: Bearer <accessToken>`:

    get-models                     -> xác nhận danh tính (email, tenant_name)
    get-billing-summary            -> plan + số credit còn lại
    chat                           -> LLM text (chạy được cả trên free plan)
    agents/codebase-retrieval      -> Context Engine (chặn bởi credit)
    checkpoint-blobs               -> hạ tầng indexing đã bật chưa
    agents/list-remote-tools       -> các remote tool tenant được dùng

Cách dùng:
    py -3.12 augment-reg/probe.py                       # tất cả session
    py -3.12 augment-reg/probe.py <email>               # một session
"""

import json
import sys
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
SESS = BASE / "sessions"

TIMEOUT = 60


def post(session: dict, endpoint: str, body: dict | None = None) -> tuple[int, str]:
    url = session["tenantURL"].rstrip("/") + "/" + endpoint
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": "Bearer " + session["accessToken"],
                "Content-Type": "application/json",
                "User-Agent": "augment-reg-probe/1.0",
                "x-request-id": "augment-reg-probe",
                "x-request-session-id": "augment-reg-probe",
            },
            json=body or {},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        return 0, str(e)
    return r.status_code, r.text


def probe(path: Path) -> None:
    session = json.loads(path.read_text(encoding="utf-8"))
    print(f"\n=== {path.stem}  ({session['tenantURL']}) ===")

    code, text = post(session, "get-models")
    if code == 200:
        user = json.loads(text).get("user", {})
        print(f"  identity    : {user.get('email')} | tenant_name: {user.get('tenant_name')}")
    else:
        print(f"  identity    : FAILED {code} {text[:120]}")

    code, text = post(session, "get-billing-summary")
    if code == 200:
        b = json.loads(text)
        remaining = b.get("amount_remaining")
        print(
            f"  plan        : {b.get('plan_name')} | remaining: {remaining}"
            f" | cycle_end: {b.get('billing_cycle_end_date_iso')}"
        )
    else:
        print(f"  plan        : FAILED {code} {text[:120]}")

    code, text = post(session, "checkpoint-blobs", {"checkpoint_id": ""})
    print(f"  indexing    : {code} {text[:130]}")

    code, text = post(session, "chat", {"message": "say hi"})
    if code == 200:
        reply = json.loads(text).get("text", "").strip().replace("\n", " ")
        print(f"  chat        : 200 {reply[:100]!r}")
    else:
        print(f"  chat        : {code} {text[:130]}")

    code, text = post(session, "agents/codebase-retrieval", {"query": "probe"})
    print(f"  retrieval   : {code} {text[:130]}")

    code, text = post(session, "agents/list-remote-tools")
    print(f"  remote tools: {code} {text[:130]}")


def main() -> None:
    if len(sys.argv) > 1:
        files = [SESS / f"{sys.argv[1]}.json"]
    else:
        files = sorted(SESS.glob("*.json"))

    if not files or not any(f.exists() for f in files):
        sys.exit(f"Khong co session nao trong {SESS}")

    for f in files:
        if f.exists():
            probe(f)


if __name__ == "__main__":
    main()