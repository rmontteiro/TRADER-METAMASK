#!/usr/bin/env python3
"""Stop and target alerts for trader-metamask. Standard library only. Read only.

MetaMask has no stop or take-profit orders: a swap is at market, and nothing
sells for you when the price turns. This script is the someone-has-to-look.
It reads the open positions (crypto/state/positions.json), prices them, and
raises an alert when:

  STOP HIT     the price is at or under the stop
  TARGET HIT   the price is at or over the target
  NEAR STOP    the price is within --near-pct of the stop (default 2%)
  NO STOP      an open position has no stop at all

The same alert for the same position is raised once, then stays quiet until
the price leaves that zone and comes back (crypto/state/alerts.json), so a
schedule that runs every hour does not repeat itself. Exit code 3 means there
are new alerts, 0 means nothing new, 2 an error. It never plans, signs or
sends anything: the exit is still plan.py sell, plan.py check and the user's "sim".

    python3 alerts.py check                   # new alerts only
    python3 alerts.py check --all             # every open position with its status
    python3 alerts.py check --near-pct 3 --json
    python3 alerts.py check --notify "notify-send 'Cripto'"   # also run a command with the text
    python3 alerts.py reset                   # forget what was already alerted
"""

import argparse
import json
import shlex
import subprocess
import sys

import common as C


def status(p, px, near_pct):
    stop, target = p.get("stop_price"), p.get("target_price")
    if not stop:
        return "NO STOP"
    if px <= stop:
        return "STOP HIT"
    if target and px >= target:
        return "TARGET HIT"
    if (px - stop) / px * 100 <= near_pct:
        return "NEAR STOP"
    return "OK"


def cmd_check(a):
    pos = {k: p for k, p in C.load("state/positions.json", {}).items() if p.get("qty", 0) > 1e-12}
    seen = C.load("state/alerts.json", {})
    rows, new = [], []
    for key, p in pos.items():
        try:
            px = C.token_price(p["chain"], p["address"])
        except C.Fail as e:
            rows.append({"key": key, "symbol": p["symbol"], "status": "NO PRICE", "error": str(e)})
            continue
        st = status(p, px, a.near_pct)
        value = p["qty"] * px
        pnl = value - p.get("cost_usd", 0)
        row = {"key": key, "mode": p["mode"], "chain": p["chain"], "symbol": p["symbol"],
               "address": p["address"], "qty": p["qty"], "price": px, "avg_price": p.get("avg_price"),
               "stop_price": p.get("stop_price"), "target_price": p.get("target_price"),
               "value_usd": round(value, 2), "pnl_usd": round(pnl, 2),
               "pnl_pct": round(pnl / p["cost_usd"] * 100, 2) if p.get("cost_usd") else None,
               "status": st}
        rows.append(row)
        if st == "OK":
            seen.pop(key, None)  # back in the safe zone: the next touch alerts again
        elif seen.get(key) != st:
            seen[key] = st
            new.append(row)
    for key in list(seen):
        if key not in pos:
            seen.pop(key)  # closed positions
    if not a.dry_run:
        C.save("state/alerts.json", seen)

    lines = []
    for r in new:
        if r["status"] == "NO STOP":
            lines.append("%s %s (%s, %s): open with NO STOP. Set one or plan the exit." % (
                r["status"], r["symbol"], r["chain"], r["mode"]))
            continue
        lines.append("%s %s (%s, %s): now $%s, stop $%s, target $%s, P&L %s (%+.1f%%). %s" % (
            r["status"], r["symbol"], r["chain"], r["mode"], C.price_str(r["price"]),
            C.price_str(r["stop_price"]), C.price_str(r["target_price"]), C.money(r["pnl_usd"]),
            r["pnl_pct"] or 0,
            {"STOP HIT": "Exit: plan.py sell --chain %s --token %s --all" % (r["chain"], r["address"]),
             "TARGET HIT": "Take profit? plan.py sell --chain %s --token %s --all" % (r["chain"], r["address"]),
             "NEAR STOP": "Watch it."}[r["status"]]))
    if a.json:
        print(json.dumps({"new": new, "positions": rows}, indent=2))
    else:
        if not pos:
            print("No open positions.")
        for line in lines:
            print(line)
        if a.all:
            for r in rows:
                if "error" in r:
                    print("  %-8s %s" % (r["symbol"], r["error"]))
                    continue
                print("  %-10s %-5s %-9s $%-14s stop $%-14s target $%-14s P&L %-10s %s" % (
                    r["status"], r["mode"], r["symbol"], C.price_str(r["price"]),
                    C.price_str(r["stop_price"]), C.price_str(r["target_price"]),
                    C.money(r["pnl_usd"]), r["chain"]))
        if pos and not lines:
            print("No new alerts (%d open position%s)." % (len(pos), "" if len(pos) == 1 else "s"))
    if lines and a.notify:
        subprocess.run(shlex.split(a.notify) + ["\n".join(lines)], check=False)
    sys.exit(3 if new else 0)


def cmd_reset(a):
    C.save("state/alerts.json", {})
    print("Alert memory cleared: every current condition will alert again.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check")
    s.add_argument("--near-pct", type=float, default=2.0)
    s.add_argument("--all", action="store_true")
    s.add_argument("--json", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="do not remember the alerts")
    s.add_argument("--notify", help="command to run with the alert text as its last argument")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("reset"); s.set_defaults(fn=cmd_reset)
    a = p.parse_args()
    try:
        a.fn(a)
    except C.Fail as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
