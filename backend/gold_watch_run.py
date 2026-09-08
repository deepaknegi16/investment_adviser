#!/usr/bin/env python
"""Run one GOLDBEES gold-watch sweep from cron.

    cd backend && .venv/bin/python gold_watch_run.py            # alert if material
    cd backend && .venv/bin/python gold_watch_run.py --digest   # always email
    cd backend && .venv/bin/python gold_watch_run.py --dry-run  # research, no email
    cd backend && .venv/bin/python gold_watch_run.py --test-email

Suggested crontab (market-hours sweep + a Friday digest):
    30 9,15 * * 1-5  cd /path/backend && .venv/bin/python gold_watch_run.py >> gold_watch.log 2>&1
    45 18   * * 5    cd /path/backend && .venv/bin/python gold_watch_run.py --digest >> gold_watch.log 2>&1
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.db import init_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="GOLDBEES gold-factor news watch")
    ap.add_argument("--digest", action="store_true", help="email regardless of materiality")
    ap.add_argument("--dry-run", action="store_true", help="research only, never email")
    ap.add_argument("--test-email", action="store_true", help="send an SMTP test and exit")
    ap.add_argument("--json", action="store_true", help="dump the full result as JSON")
    ap.add_argument("--factors", help="comma-separated factor buckets to limit the sweep")
    args = ap.parse_args()

    init_db()
    from app import notify
    from app.agents.gold_alerts import run_and_alert

    stamp = dt.datetime.now().isoformat(timespec="seconds")

    if args.test_email:
        try:
            print(f"[{stamp}] {notify.send_email('[GoldBeES] Alert channel test', 'Alerts are wired up correctly.')}")
            return 0
        except Exception as e:
            print(f"[{stamp}] test email FAILED: {type(e).__name__}: {e}")
            return 1

    cfg = notify.config_status()
    if not cfg["configured"] and not args.dry_run:
        print(f"[{stamp}] warning: email not configured (missing {', '.join(cfg['missing'])}) "
              f"— the sweep will still run and store results.")

    if args.dry_run:
        import app.notify as n
        n.send_email = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[assignment]
            n.EmailNotConfigured("--dry-run: email suppressed")
        )

    buckets = [b.strip() for b in args.factors.split(",")] if args.factors else None
    result = run_and_alert(force_email=args.digest and not args.dry_run, buckets=buckets)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    bias = result.get("bias", {})
    etf = (result.get("snapshot") or {}).get("etf", {})
    print(f"[{stamp}] GOLDBEES ₹{etf.get('price')} ({etf.get('chg_1d')}%) · "
          f"bias {bias.get('direction')}/{bias.get('conviction')} · "
          f"{len(result.get('events', []))} events, {len(result.get('new_event_ids', []))} new · "
          f"emailed={result.get('emailed')}")
    for r in result.get("decision", {}).get("reasons", []):
        print(f"    trigger: {r}")
    if result.get("email_error"):
        print(f"    email error: {result['email_error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
