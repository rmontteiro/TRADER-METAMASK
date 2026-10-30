#!/usr/bin/env python3
"""Tickets, the user's yes, fills and positions for trader-metamask. Standard library only.

This tool never signs and never sends a transaction. In live mode the user
does the swap in MetaMask with their own hands; this tool tells them exactly
what to type and check, then verifies the result on chain from the tx hash.

  show       each pending order as a ticket. READY only when plan.py check
             passed THIS exact order today (fingerprint) and nobody decided yet.
             In live mode the ticket is a step-by-step MetaMask checklist.
  approve    records the user's explicit "sim"/"yes" for ONE order, word for
             word. Refuses "sim pra todos", "ok", "pode ser", a yes with doubt
             in it, or one naming another token. In paper mode it then fills
             the order at the live price (minus slippage and gas).
  decline    records a no.
  result     (live) records what happened in MetaMask: --tx 0x... is checked
             on chain (status, sender, tokens received), or --status rejected.
  positions  open positions at the live price, against their stop and target.
             Prints STOP HIT / TARGET HIT. MetaMask has no stop orders, so
             someone has to look.
  journal    trades and P&L, today's risk used.

    python3 ticket.py show
    python3 ticket.py approve --index 1 --reply "sim"
    python3 ticket.py result --index 1 --tx 0xabc... [--got 1234.5]
    python3 ticket.py positions
    python3 ticket.py journal
"""

import argparse
import json
import re
import sys

import common as C
import plan as P

YES = {"sim", "s", "yes", "y"}
BATCH = {"todos", "todas", "tudo", "ambos", "ambas", "all", "every", "everything", "both", "rest",
         "resto", "demais", "cada"}
DOUBT = {"nao", "não", "no", "not", "espera", "espere", "aguarda", "talvez", "mas", "porem", "porém",
         "depois", "cancela", "cancele", "se", "if", "maybe", "wait", "but", "later", "hold", "?"}
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _decided():
    return {e["fingerprint"]: e for e in C.load("state/journal.json", [])
            if e.get("fingerprint") and e.get("status") in
            ("approved", "declined", "executed", "filled", "rejected", "failed")}


def _get(index):
    for o in C.load("orders.json", []):
        if o["id"] == index:
            return o
    raise C.Fail("no order %d in crypto/orders.json" % index)


def _ready(o, day):
    r = o.get("risk")
    if not r:
        return False, "not checked: run plan.py check"
    if r["fingerprint"] != o["fingerprint"] or r["fingerprint"] != C.fingerprint(o):
        return False, "changed after the check: run plan.py check"
    if r["date"] != day:
        return False, "checked on %s, not today: run plan.py check" % r["date"]
    if r["verdict"] != "PASS":
        return False, "BLOCKED by the risk check"
    if o["fingerprint"] in _decided():
        return False, "already decided (%s)" % _decided()[o["fingerprint"]]["status"]
    return True, "READY"


def metamask_steps(o):
    ch = C.CHAINS[o["chain"]]
    pay, get = o["sell_token"], o["buy_token"]
    addr = lambda t: "the native coin (no contract)" if t["address"] == C.NATIVE else t["address"]
    steps = [
        "Open MetaMask and switch the network to %s (chain id %d). Check the network name at the top." % (
            o["chain"].capitalize(), ch["id"]),
        "Open Swap (the Swap button in the extension, or portfolio.metamask.io/swap typed by hand; "
        "never a link from a message or an ad).",
        "From: %s. Amount: %.6g." % (pay["symbol"], o["amount_in"]),
        "To: paste the contract %s and check MetaMask shows %s. Do not pick the token by its name "
        "in the list: lookalikes use the same name." % (addr(get), get["symbol"]),
        "Settings (gear) > Slippage > Custom: %.2f%%." % o["slippage_pct"],
        "Before confirming, the quote must say you receive at least %.6g %s. Less than that: "
        "stop and tell Claude, the price moved." % (o["min_out"], get["symbol"]),
    ]
    if pay["address"] != C.NATIVE:
        steps.append("If MetaMask asks for a spending cap (approval) for %s, set it to exactly %.6g, "
                     "not unlimited." % (pay["symbol"], o["amount_in"]))
    steps += [
        "In the confirmation window, check the network fee is near %s and the site is "
        "metamask.io / portfolio.metamask.io. Then you, not Claude, click Confirm." % C.money(o["gas_usd_swap"]),
        "When it shows Confirmed, copy the transaction hash (Activity > the swap > Copy "
        "transaction ID) and paste it here.",
    ]
    return steps


def ticket_text(o, day):
    ok, why = _ready(o, day)
    lines = [P.describe(o), "  STATUS     %s" % why]
    if ok and o["mode"] == "live":
        lines.append("  IN METAMASK (live: real money, cannot be undone):")
        lines += ["    %d. %s" % (i, s) for i, s in enumerate(metamask_steps(o), 1)]
    elif ok:
        lines.append("  PAPER: a yes simulates the swap at the live price; nothing touches your wallet.")
    if ok:
        lines.append('  Reply "sim" to %s this one order, or "nao".' % (
            "confirm and get the steps for" if o["mode"] == "live" else "simulate"))
    return "\n".join(lines)


def cmd_show(a):
    day = C.today(a.now)
    orders = [o for o in C.load("orders.json", []) if a.index in (None, o["id"])]
    if not orders:
        print("No orders. Plan one with plan.py buy / plan.py sell, then plan.py check.")
    for o in orders:
        print(ticket_text(o, day))
        print()


def _norm(s):
    return re.findall(r"[\wãõáéíóúç?]+", s.lower())


def valid_yes(reply, o, others):
    words = _norm(reply)
    if not words or words[0] not in YES:
        return False, "the reply does not start with sim/yes"
    if BATCH & set(words):
        return False, "a yes for several orders is not accepted; one order per yes"
    if DOUBT & set(words) or "?" in reply:
        return False, "the reply has a doubt or a condition in it"
    mine = (o["traded"]["symbol"] or "").lower()
    for x in others:
        sym = (x["traded"]["symbol"] or "").lower()
        if sym and sym != mine and sym in words:
            return False, "the reply names %s, which is a different order" % sym.upper()
    return True, ""


def _journal_add(entry):
    j = C.load("state/journal.json", [])
    j.append(entry)
    C.save("state/journal.json", j)


def _entry(o, status, **kw):
    e = {"date": C.today(), "at": C.now().isoformat(), "order_id": o["id"],
         "fingerprint": o["fingerprint"], "mode": o["mode"], "chain": o["chain"], "side": o["side"],
         "token": o["traded"], "usd": o["usd"], "max_loss_usd": o.get("max_loss_usd"), "status": status}
    e.update(kw)
    return e


def cmd_approve(a):
    o = _get(a.index)
    ok, why = _ready(o, C.today(a.now))
    if not ok:
        raise C.Fail("order %d is not READY: %s" % (o["id"], why))
    others = [x for x in C.load("orders.json", []) if x["id"] != o["id"]]
    good, reason = valid_yes(a.reply, o, others)
    if not good:
        print("Not recorded: %s. Ask again for this one order." % reason)
        sys.exit(1)
    _journal_add(_entry(o, "approved", reply=a.reply))
    print("Recorded your yes for order %d, word for word: %r" % (o["id"], a.reply))
    if o["mode"] == "paper":
        _paper_fill(o)
    else:
        print("\nNow, in MetaMask (you sign, not Claude):")
        for i, s in enumerate(metamask_steps(o), 1):
            print("  %d. %s" % (i, s))
        print("\nThen: ticket.py result --index %d --tx <hash>" % o["id"])


def cmd_decline(a):
    o = _get(a.index)
    _journal_add(_entry(o, "declined", reply=a.reply or "no"))
    print("Recorded: no to order %d." % o["id"])


def _positions():
    return C.load("state/positions.json", {})


def _book(o, qty_tok, price, usd_spent=None, usd_got=None):
    """Update positions (and paper cash) after a fill."""
    pos = _positions()
    key = "%s:%s:%s" % (o["mode"], o["chain"], o["traded"]["address"].lower())
    p = pos.get(key, {"symbol": o["traded"]["symbol"], "chain": o["chain"], "mode": o["mode"],
                      "address": o["traded"]["address"], "qty": 0.0, "cost_usd": 0.0,
                      "realized_usd": 0.0})
    realized = 0.0
    if o["side"] == "buy":
        p["qty"] += qty_tok
        p["cost_usd"] += usd_spent
        p["avg_price"] = p["cost_usd"] / p["qty"]
        p["stop_price"], p["target_price"] = o.get("stop_price"), o.get("target_price")
        p["opened"] = p.get("opened") or C.now().isoformat()
    else:
        avg = p.get("avg_price") or price
        sold = min(qty_tok, p["qty"]) if p["qty"] else qty_tok
        realized = usd_got - sold * avg
        p["realized_usd"] += realized
        p["cost_usd"] = max(0.0, p["cost_usd"] - sold * avg)
        p["qty"] = max(0.0, p["qty"] - sold)
    if p["qty"] <= 1e-12:
        p["closed"] = C.now().isoformat()
    pos[key] = p
    C.save("state/positions.json", pos)
    if o["mode"] == "paper":
        book = C.load("state/paper.json", {"cash_usd": C.config()["budget_usd"]})
        book["cash_usd"] += (usd_got or 0) - (usd_spent or 0) - o["gas_usd_swap"]
        C.save("state/paper.json", book)
    return realized


def _paper_fill(o):
    price = C.token_price(o["chain"], o["traded"]["address"])
    worse = 1 + o["slippage_pct"] / 200 if o["side"] == "buy" else 1 - o["slippage_pct"] / 200
    fill = price * worse  # assume half the allowed slippage goes against us
    if o["side"] == "buy":
        qty = o["usd"] / fill
        _book(o, qty, fill, usd_spent=o["usd"])
        _journal_add(_entry(o, "filled", price=fill, amount_out=qty))
        print("PAPER FILL: bought %.6g %s at $%s for %s (+ gas %s). Stop $%s, target $%s." % (
            qty, o["traded"]["symbol"], C.price_str(fill), C.money(o["usd"]), C.money(o["gas_usd_swap"]),
            C.price_str(o.get("stop_price")), C.price_str(o.get("target_price"))))
        print("MetaMask has no stop orders: check with ticket.py positions.")
    else:
        got = o["amount_in"] * fill
        r = _book(o, o["amount_in"], fill, usd_got=got)
        _journal_add(_entry(o, "filled", price=fill, amount_out=got, realized_usd=round(r, 2)))
        print("PAPER FILL: sold %.6g %s at $%s for %s. Realized P&L %s." % (
            o["amount_in"], o["traded"]["symbol"], C.price_str(fill), C.money(got), C.money(r)))


def cmd_result(a):
    o = _get(a.index)
    if o["mode"] != "live":
        raise C.Fail("order %d is a paper order; it was filled when approved" % o["id"])
    d = _decided().get(o["fingerprint"])
    if not d or d["status"] != "approved":
        raise C.Fail("order %d has no recorded yes; nothing to record" % o["id"])
    if a.status in ("rejected", "failed"):
        _journal_add(_entry(o, a.status, note=a.note))
        print("Recorded: order %d %s. A retry needs plan.py check and a new yes." % (o["id"], a.status))
        return
    if not a.tx or not re.match(r"^0x[0-9a-fA-F]{64}$", a.tx):
        raise C.Fail("pass --tx with the 0x... transaction hash (66 characters)")
    rc = C.rpc(o["chain"], "eth_getTransactionReceipt", [a.tx])
    if rc is None:
        raise C.Fail("no receipt yet on %s for that hash (pending, or the wrong network)" % o["chain"])
    sender = rc.get("from")
    if int(rc.get("status", "0x0"), 16) != 1:
        _journal_add(_entry(o, "failed", tx=a.tx, note="reverted on chain"))
        print("The transaction REVERTED on chain (gas was spent, no swap). Recorded as failed. "
              "Explorer: %s/tx/%s" % (C.CHAINS[o["chain"]]["explorer"], a.tx))
        return
    got = a.got
    out_tok = o["buy_token"]["address"]
    if got is None and out_tok != C.NATIVE:
        raw = 0
        for lg in rc.get("logs", []):
            t = lg.get("topics") or []
            if (lg.get("address", "").lower() == out_tok.lower() and len(t) == 3 and t[0] == TRANSFER
                    and t[2][-40:].lower() == sender[2:].lower()):
                raw += int(lg["data"], 16)
        if raw:
            _, dec = C.erc20_meta(o["chain"], out_tok)
            got = raw / 10 ** dec
    if got is None:
        raise C.Fail("could not read the amount received from the receipt; pass --got <amount>")
    if got < o["min_out"] * 0.999:
        print("WARNING: received %.6g, under the planned minimum %.6g." % (got, o["min_out"]))
    gas_wei = int(rc.get("gasUsed", "0x0"), 16) * int(rc.get("effectiveGasPrice", "0x0"), 16)
    if o["side"] == "buy":
        price = o["usd"] / got
        _book(o, got, price, usd_spent=o["usd"])
        _journal_add(_entry(o, "executed", tx=a.tx, wallet=sender, amount_out=got, price=price,
                            gas_native=gas_wei / 1e18))
        print("EXECUTED (verified on chain): bought %.6g %s at about $%s. Wallet %s." % (
            got, o["traded"]["symbol"], C.price_str(price), sender))
        print("Stop $%s, target $%s. MetaMask will not sell for you: watch with ticket.py positions." % (
            C.price_str(o.get("stop_price")), C.price_str(o.get("target_price"))))
    else:
        usd_got = got * C.token_price(o["chain"], out_tok)
        r = _book(o, o["amount_in"], usd_got / o["amount_in"], usd_got=usd_got)
        _journal_add(_entry(o, "executed", tx=a.tx, wallet=sender, amount_out=got,
                            realized_usd=round(r, 2), gas_native=gas_wei / 1e18))
        print("EXECUTED (verified on chain): sold %.6g %s for %.6g %s. Realized P&L about %s." % (
            o["amount_in"], o["traded"]["symbol"], got, o["buy_token"]["symbol"], C.money(r)))
    print("Explorer: %s/tx/%s" % (C.CHAINS[o["chain"]]["explorer"], a.tx))


def cmd_positions(a):
    pos = [p for p in _positions().values() if p["qty"] > 1e-12]
    if not pos:
        print("No open positions.")
        return
    hits = 0
    for p in pos:
        px = C.token_price(p["chain"], p["address"])
        val = p["qty"] * px
        pnl = val - p["cost_usd"]
        flag = ""
        if p.get("stop_price") and px <= p["stop_price"]:
            flag, hits = "  << STOP HIT: plan the exit now (plan.py sell --all)", hits + 1
        elif p.get("target_price") and px >= p["target_price"]:
            flag, hits = "  << TARGET HIT: take profit? (plan.py sell)", hits + 1
        print("%-5s %-8s %-9s qty %.6g  avg $%s  now $%s  value %s  P&L %s (%+.1f%%)  stop $%s  target $%s%s" % (
            p["mode"], p["symbol"], p["chain"], p["qty"], C.price_str(p.get("avg_price")),
            C.price_str(px), C.money(val), C.money(pnl), pnl / p["cost_usd"] * 100 if p["cost_usd"] else 0,
            C.price_str(p.get("stop_price")), C.price_str(p.get("target_price")), flag))
    if a.json:
        print(json.dumps(pos, indent=2))
    sys.exit(3 if hits else 0)


def cmd_journal(a):
    j = C.load("state/journal.json", [])
    day = C.today(a.now)
    for e in j[-a.last:]:
        print("%s  %-5s %-9s %-4s %-8s %-9s %s%s%s" % (
            e["at"][:16], e["mode"], e["chain"], e["side"], (e["token"] or {}).get("symbol"),
            e["status"], C.money(e["usd"]),
            "  P&L %s" % C.money(e["realized_usd"]) if e.get("realized_usd") is not None else "",
            "  tx %s" % e["tx"] if e.get("tx") else ""))
    realized = sum(e.get("realized_usd") or 0 for e in j if e["status"] in ("filled", "executed"))
    used = sum(e.get("max_loss_usd") or 0 for e in j if e["date"] == day and e["side"] == "buy"
               and e["status"] in ("filled", "executed"))
    cfg = C.config()
    print("\nRealized P&L, all time: %s. Risk taken today: %s of %s. Mode: %s." % (
        C.money(realized), C.money(used), C.money(cfg["max_daily_loss_usd"]), cfg["mode"]))
    if cfg["mode"] == "paper":
        print("Paper cash: %s" % C.money(C.load("state/paper.json", {"cash_usd": cfg["budget_usd"]})["cash_usd"]))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--now", help="pin the clock, ISO time in UTC")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show"); s.add_argument("--index", type=int); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("approve"); s.add_argument("--index", type=int, required=True)
    s.add_argument("--reply", required=True); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("decline"); s.add_argument("--index", type=int, required=True)
    s.add_argument("--reply"); s.set_defaults(fn=cmd_decline)
    s = sub.add_parser("result"); s.add_argument("--index", type=int, required=True)
    s.add_argument("--tx"); s.add_argument("--got", type=float)
    s.add_argument("--status", choices=["executed", "rejected", "failed"], default="executed")
    s.add_argument("--note"); s.set_defaults(fn=cmd_result)
    s = sub.add_parser("positions"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_positions)
    s = sub.add_parser("journal"); s.add_argument("--last", type=int, default=20)
    s.set_defaults(fn=cmd_journal)
    a = p.parse_args()
    try:
        a.fn(a)
    except C.Fail as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
