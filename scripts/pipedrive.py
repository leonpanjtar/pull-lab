#!/usr/bin/env python3
"""Read-only Pipedrive REST helper for the weekly loop.

Credentials, in order:
  1. A network secret on the cloud environment that adds the header
     x-api-token for salesqueze.pipedrive.com (token never enters the container).
  2. PIPEDRIVE_API_TOKEN from an env var or the gitignored .env in the repo root.
PIPEDRIVE_DOMAIN defaults to "salesqueze". Never commit the token.
The host salesqueze.pipedrive.com must be allowed by the environment's network
(the personal "pull-lab" cloud environment allows it).

Credit floor: Pipedrive's daily token budget (header
x-daily-ratelimit-token-remaining, ~300k/day) is shared with the live
WhatsApp SDR app. This helper refuses to make calls once the remaining
budget drops below 50,000 (20,000 after 23:00 local). GET only.

Usage:
  python3 scripts/pipedrive.py deals --status lost --days 90
  python3 scripts/pipedrive.py deals --status won --days 90
  python3 scripts/pipedrive.py notes --deal 1234
  python3 scripts/pipedrive.py budget
Output is JSON on stdout; the remaining budget goes to stderr.
"""
import argparse, datetime as dt, json, os, sys, urllib.parse, urllib.request
from pathlib import Path

FLOOR_DAY, FLOOR_NIGHT = 50_000, 20_000
_remaining = None


def _load_env():
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    token = os.getenv("PIPEDRIVE_API_TOKEN", "").strip()
    domain = os.getenv("PIPEDRIVE_DOMAIN", "").strip().replace(".pipedrive.com", "")
    return token, domain or "salesqueze"


TOKEN, DOMAIN = _load_env()
BASE = f"https://{DOMAIN}.pipedrive.com/api/v1"


def _floor():
    return FLOOR_NIGHT if dt.datetime.now().hour >= 23 else FLOOR_DAY


def get(path, **params):
    global _remaining
    if _remaining is not None and _remaining < _floor():
        sys.exit(f"Stopped: daily Pipedrive budget {_remaining} is under the floor {_floor()}")
    url = f"{BASE}/{path.lstrip('/')}?{urllib.parse.urlencode(params)}"
    headers = {"x-api-token": TOKEN} if TOKEN else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        rem = r.headers.get("x-daily-ratelimit-token-remaining")
        if rem is not None:
            _remaining = int(rem)
        return json.load(r)


def paged(path, limit=500, max_pages=10, stop=None, **params):
    out, start = [], 0
    for _ in range(max_pages):
        body = get(path, start=start, limit=limit, **params)
        items = body.get("data") or []
        out.extend(items)
        if stop and items and stop(items[-1]):
            break
        more = (body.get("additional_data") or {}).get("pagination", {})
        if not more.get("more_items_in_collection"):
            break
        start = more.get("next_start", start + limit)
    return out


def deals(status, days):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    field = {"won": "won_time", "lost": "lost_time"}.get(status, "add_time")
    rows = paged("deals", status=status, sort=f"{field} DESC",
                 stop=lambda d: (d.get(field) or "")[:10] < since)
    keep = ("id", "title", "status", "value", "currency", "stage_id", "add_time",
            "won_time", "lost_time", "lost_reason", "org_name", "person_name", "owner_name")
    return [{k: d.get(k) for k in keep} for d in rows if (d.get(field) or "")[:10] >= since]


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deals"); d.add_argument("--status", default="all_not_deleted"); d.add_argument("--days", type=int, default=90)
    n = sub.add_parser("notes"); n.add_argument("--deal", type=int, required=True)
    sub.add_parser("budget")
    a = ap.parse_args()
    if a.cmd == "deals":
        res = deals(a.status, a.days)
    elif a.cmd == "notes":
        res = [{k: x.get(k) for k in ("id", "add_time", "content")} for x in paged("notes", deal_id=a.deal, max_pages=2)]
    else:
        res = {"user": (get("users/me").get("data") or {}).get("name")}
    print(json.dumps(res, indent=1, ensure_ascii=False))
    print(f"pipedrive budget remaining: {_remaining}", file=sys.stderr)


if __name__ == "__main__":
    main()
