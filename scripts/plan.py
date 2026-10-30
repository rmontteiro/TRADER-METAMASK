#!/usr/bin/env python3
"""Trade plans and the risk gate for trader-metamask. Standard library only.

  buy     plans a swap from a stablecoin (or the native coin) into a token:
          live price, stop and target, the size your max loss allows
          (counting slippage and gas both ways), the minimum you must
          receive, and the gas. Appends it to crypto/orders.json.
  sell    plans a swap out of a token you hold (an exit), for all or part.
  check   the risk gate. Checks every pending order against
          crypto/config.json and today's journal and marks it PASS or BLOCK
          with the reasons. Only a PASS can become a ticket.
  clear   empties crypto/orders.json.

    python3 plan.py buy --chain base --token 0x... --with USDC --stop-pct 8 --target-pct 16 --why "..."
    python3 plan.py buy --chain ethereum --token 0x... --usd 150 --stop 0.0000045 --target 0.0000060
    python3 plan.py sell --chain base --token 0x... --all --for USDC --why "hit target"
    python3 plan.py check
    python3 plan.py check --json

Nothing here signs or sends anything.
"""

import argparse
import json
import math
import sys

import common as C


def _token_side(chain_key, token):
    sym, addr = C.resolve_token(chain_key, token)
    if sym is None:
        try:
            sym, _ = C.erc20_meta(chain_key, addr)
        except C.Fail:
            sym = addr[:8]
    return {"symbol": sym, "address": addr}


def _slippage(cfg, chain_key, addr, given):
    if given is not None:
        return float(given)
    tok = C.load("state/tokens.json", {}).get("%s:%s" % (chain_key, addr.lower()), {})
    liq = (tok.get("facts") or {}).get("liquidity_usd") or 0
    known = addr == C.NATIVE or addr.lower() in {v.lower() for v in C.KNOWN.get(chain_key, {}).values()}
    return 0.5 if known or liq >= 5_000_000 else min(1.0, cfg["max_slippage_pct"])


def cmd_buy(a):
    cfg = C.config()
    chain_key, ch = C.chain(a.chain)
    buy = _token_side(chain_key, a.token)
    pay = _token_side(chain_key, a.with_token)
    if buy["address"] == pay["address"]:
        raise C.Fail("buying and paying with the same token")
    price = C.token_price(chain_key, buy["address"])
    pay_price = C.token_price(chain_key, pay["address"])
    stop = a.stop if a.stop is not None else (price * (1 - a.stop_pct / 100) if a.stop_pct else None)
    target = a.target if a.target is not None else (price * (1 + a.target_pct / 100) if a.target_pct else None)
    if stop is not None and stop >= price:
        raise C.Fail("the stop (%s) must be under the price now (%s)" % (C.price_str(stop), C.price_str(price)))
    if target is not None and target <= price:
        raise C.Fail("the target (%s) must be over the price now (%s)" % (C.price_str(target), C.price_str(price)))
    slip = _slippage(cfg, chain_key, buy["address"], a.slippage)
    gas, _ = C.gas_cost_usd(chain_key)
    gas_round_trip = gas * 2 + gas * 50000 / ch["gas_units_swap"]  # buy, sell, one approval

    # Size: the loss if the stop is hit, counting slippage on the way in and
    # out and the gas, must fit max_loss_per_trade_usd.
    cap = min(cfg["max_trade_usd"], a.usd) if a.usd else cfg["max_trade_usd"]
    note = "your max_trade_usd %s" % C.money(cfg["max_trade_usd"])
    if a.usd and a.usd < cfg["max_trade_usd"]:
        note = "the amount you asked for"
    if stop is not None:
        loss_frac = (price - stop) / price + 2 * slip / 100
        by_loss = (cfg["max_loss_per_trade_usd"] - gas_round_trip) / loss_frac
        if by_loss <= 0:
            raise C.Fail("gas alone (%s) is over your max loss per trade" % C.money(gas_round_trip))
        if by_loss < cap:
            cap, note = by_loss, "your max loss per trade %s at the stop" % C.money(cfg["max_loss_per_trade_usd"])
    usd = math.floor(cap * 100) / 100
    amount_in = usd / pay_price
    expect_out = usd / price
    min_out = expect_out * (1 - slip / 100)
    max_loss = None
    if stop is not None:
        max_loss = usd * ((price - stop) / price + 2 * slip / 100) + gas_round_trip
    rr = None
    if stop is not None and target is not None:
        rr = (target - price) / (price - stop)
    order = {
        "id": None, "created": C.now(a.now).isoformat(), "mode": cfg["mode"], "chain": chain_key,
        "chain_id": ch["id"], "side": "buy", "sell_token": pay, "buy_token": buy,
        "traded": buy, "price_at_plan": price, "usd": usd, "amount_in": round(amount_in, 8),
        "expected_out": expect_out, "min_out": min_out, "slippage_pct": slip,
        "stop_price": stop, "target_price": target, "reward_risk": rr,
        "gas_usd_swap": round(gas, 4), "gas_usd_round_trip": round(gas_round_trip, 4),
        "max_loss_usd": round(max_loss, 2) if max_loss is not None else None,
        "sized_by": note, "why": a.why or "", "risk": None,
    }
    _append(order)


def _held(chain_key, addr):
    """What the user holds of a token: the position book, and in live mode also the last wallet snapshot."""
    cfg = C.config()
    pos = C.load("state/positions.json", {}).get("%s:%s:%s" % (cfg["mode"], chain_key, addr.lower()))
    best = (pos or {}).get("qty", 0.0)
    if cfg["mode"] == "live":
        for w in C.load("portfolio.json", {}).get("wallets", {}).values():
            if w.get("chain") != chain_key:
                continue
            for h in w.get("holdings", []):
                if h["address"].lower() == addr.lower():
                    best = max(best, h["amount"])
    return best, pos


def cmd_sell(a):
    cfg = C.config()
    chain_key, ch = C.chain(a.chain)
    sell = _token_side(chain_key, a.token)
    get = _token_side(chain_key, a.for_token)
    held, pos = _held(chain_key, sell["address"])
    qty = held if a.all else a.amount
    if not qty:
        raise C.Fail("nothing to sell: --all uses the %s book, which holds none of this token. "
                     "Pass --amount (for a paper exit of real coins, the amount market.py wallet shows)"
                     % cfg["mode"])
    price = C.token_price(chain_key, sell["address"])
    get_price = C.token_price(chain_key, get["address"])
    slip = _slippage(cfg, chain_key, sell["address"], a.slippage)
    gas, _ = C.gas_cost_usd(chain_key)
    usd = qty * price
    order = {
        "id": None, "created": C.now(a.now).isoformat(), "mode": cfg["mode"], "chain": chain_key,
        "chain_id": ch["id"], "side": "sell", "sell_token": sell, "buy_token": get,
        "traded": sell, "price_at_plan": price, "usd": round(usd, 2), "amount_in": qty,
        "expected_out": usd / get_price, "min_out": usd / get_price * (1 - slip / 100),
        "slippage_pct": slip, "stop_price": None, "target_price": None, "reward_risk": None,
        "gas_usd_swap": round(gas, 4), "held": held, "entry_price": (pos or {}).get("avg_price"),
        "why": a.why or "", "risk": None,
    }
    _append(order)


def _append(order):
    orders = C.load("orders.json", [])
    order["id"] = (max([o["id"] for o in orders] or [0]) + 1)
    order["fingerprint"] = C.fingerprint(order)
    orders.append(order)
    C.save("orders.json", orders)
    print(describe(order))
    print("\nSaved as order %d in crypto/orders.json. Next: plan.py check" % order["id"])


def describe(o):
    t, s = o["traded"]["symbol"], o["sell_token"]["symbol"]
    lines = ["ORDER %d  %s  %s on %s (%s mode)" % (o["id"], o["side"].upper(), t, o["chain"], o["mode"])]
    if o["side"] == "buy":
        lines.append("  pay        %.6g %s (about %s)" % (o["amount_in"], s, C.money(o["usd"])))
        lines.append("  receive    about %.6g %s, at least %.6g after %.2f%% slippage" % (
            o["expected_out"], t, o["min_out"], o["slippage_pct"]))
        lines.append("  price now  $%s" % C.price_str(o["price_at_plan"]))
        if o["stop_price"] is not None:
            lines.append("  stop       $%s (%.1f%% under)" % (C.price_str(o["stop_price"]),
                         (1 - o["stop_price"] / o["price_at_plan"]) * 100))
        if o["target_price"] is not None:
            lines.append("  target     $%s (%.1f%% over)" % (C.price_str(o["target_price"]),
                         (o["target_price"] / o["price_at_plan"] - 1) * 100))
        if o.get("reward_risk") is not None:
            lines.append("  reward:risk %.2f" % o["reward_risk"])
        if o.get("max_loss_usd") is not None:
            lines.append("  max loss   about %s at the stop, with slippage and gas (more in a crash or a gap)"
                         % C.money(o["max_loss_usd"]))
        lines.append("  size       %s, set by %s" % (C.money(o["usd"]), o["sized_by"]))
    else:
        lines.append("  sell       %.6g %s (about %s)" % (o["amount_in"], t, C.money(o["usd"])))
        lines.append("  receive    about %.6g %s, at least %.6g after %.2f%% slippage" % (
            o["expected_out"], o["buy_token"]["symbol"], o["min_out"], o["slippage_pct"]))
        if o.get("entry_price"):
            lines.append("  vs entry   $%s -> $%s (%+.1f%%)" % (
                C.price_str(o["entry_price"]), C.price_str(o["price_at_plan"]),
                (o["price_at_plan"] / o["entry_price"] - 1) * 100))
    lines.append("  gas        about %s per swap" % C.money(o["gas_usd_swap"]))
    if o.get("why"):
        lines.append("  why        %s" % o["why"])
    r = o.get("risk")
    if r:
        lines.append("  RISK       %s (%s)" % (r["verdict"], r["at"][:16]))
        lines += ["    BLOCK    %s" % x for x in r["block"]]
        lines += ["    warn     %s" % x for x in r["warn"]]
    return "\n".join(lines)


def _todays_entries(day):
    j = C.load("state/journal.json", [])
    return [e for e in j if e.get("date") == day and e.get("side") == "buy"
            and e.get("status") in ("executed", "filled")]


def check_order(o, cfg, tokens, day, batch_seen, extra_loss, extra_trades):
    block, warn = [], []
    entry = o["side"] == "buy"
    traded = o["traded"]["address"]

    if o["chain"] not in cfg["allowed_chains"]:
        (block if entry else warn).append("chain %s is not in allowed_chains" % o["chain"])
    if o["fingerprint"] != C.fingerprint(o):
        block.append("the order was edited after it was planned; plan it again")
    if o["mode"] != cfg["mode"]:
        block.append("planned in %s mode but config is now %s; plan it again" % (o["mode"], cfg["mode"]))
    if o["slippage_pct"] > cfg["max_slippage_pct"]:
        (block if entry else warn).append("slippage %.2f%% over max_slippage_pct %s%%" % (
            o["slippage_pct"], cfg["max_slippage_pct"]))
    if o["min_out"] <= 0:
        block.append("minimum received is zero")

    # The token safety check must exist, be fresh and not BLOCK.
    if traded != C.NATIVE:
        tok = tokens.get("%s:%s" % (o["chain"], traded.lower()))
        if not tok:
            block.append("no token check yet: market.py token --chain %s --address %s" % (o["chain"], traded))
        else:
            age_h = (C.now().timestamp() - C.datetime.datetime.fromisoformat(tok["checked_at"]).timestamp()) / 3600
            if age_h > 24:
                (block if entry else warn).append("token check is %.0fh old; run market.py token again" % age_h)
            if tok["verdict"] == "BLOCK":
                (block if entry else warn).append("token check BLOCK: %s" % "; ".join(tok["block"]))
            elif tok["verdict"] == "CAUTION":
                warn.append("token check CAUTION: %s" % "; ".join(tok["caution"]))
    sym = (o["traded"]["symbol"] or "").upper()
    if sym in [b.upper() for b in cfg["blocked_tokens"]] or traded.lower() in [b.lower() for b in cfg["blocked_tokens"]]:
        (block if entry else warn).append("%s is on blocked_tokens" % sym)

    if entry:
        if o["usd"] > cfg["max_trade_usd"] + 0.01:
            block.append("size %s over max_trade_usd %s" % (C.money(o["usd"]), C.money(cfg["max_trade_usd"])))
        if cfg["require_stop"] and o.get("stop_price") is None:
            block.append("no stop (require_stop is true)")
        if o.get("max_loss_usd") is not None and o["max_loss_usd"] > cfg["max_loss_per_trade_usd"] + 0.01:
            block.append("max loss %s over max_loss_per_trade_usd %s" % (
                C.money(o["max_loss_usd"]), C.money(cfg["max_loss_per_trade_usd"])))
        rr = o.get("reward_risk")
        if rr is None:
            warn.append("no target, so no reward:risk")
        elif rr < cfg["min_reward_risk"] - 1e-6:
            block.append("reward:risk %.2f under min_reward_risk %s" % (rr, cfg["min_reward_risk"]))
        gas_pct = o["gas_usd_round_trip"] / o["usd"] * 100 if o["usd"] else 100
        if gas_pct > cfg["max_gas_pct_of_trade"]:
            block.append("gas in and out is %.1f%% of the trade, over max_gas_pct_of_trade %s%%" % (
                gas_pct, cfg["max_gas_pct_of_trade"]))
        today = _todays_entries(day)
        used = sum(e.get("max_loss_usd") or 0 for e in today) + extra_loss
        if used + (o.get("max_loss_usd") or 0) > cfg["max_daily_loss_usd"] + 0.01:
            block.append("today's risk would be %s, over max_daily_loss_usd %s" % (
                C.money(used + (o.get("max_loss_usd") or 0)), C.money(cfg["max_daily_loss_usd"])))
        if len(today) + extra_trades >= cfg["max_trades_per_day"]:
            block.append("already %d entries today (max_trades_per_day %d)" % (
                len(today) + extra_trades, cfg["max_trades_per_day"]))
        key = (o["chain"], traded.lower())
        if key in batch_seen:
            block.append("same token twice in this batch")
        batch_seen.add(key)
        # Cash: the paper book, or the last wallet snapshot.
        pay = o["sell_token"]["address"]
        if cfg["mode"] == "paper":
            cash = C.load("state/paper.json", {}).get("cash_usd", cfg["budget_usd"])
            if o["usd"] > cash:
                block.append("paper cash %s is less than %s" % (C.money(cash), C.money(o["usd"])))
        else:
            held, _ = _held(o["chain"], pay)
            if held == 0:
                block.append("no wallet snapshot shows %s on %s; run market.py wallet first" % (
                    o["sell_token"]["symbol"], o["chain"]))
            elif held < o["amount_in"]:
                block.append("wallet holds %.6g %s, the swap needs %.6g" % (
                    held, o["sell_token"]["symbol"], o["amount_in"]))
            if pay == C.NATIVE and held and held - o["amount_in"] < 0.002:
                warn.append("this would leave almost no %s for gas" % C.CHAINS[o["chain"]]["native"])
    else:
        held = o.get("held") or 0
        if held and o["amount_in"] > held * 1.0001:
            block.append("selling %.6g but only %.6g held" % (o["amount_in"], held))

    return {"verdict": "BLOCK" if block else "PASS", "block": block, "warn": warn,
            "at": C.now().isoformat(), "date": day, "fingerprint": o["fingerprint"]}


def cmd_check(a):
    cfg = C.config()
    orders = C.load("orders.json", [])
    tokens = C.load("state/tokens.json", {})
    day = C.today(a.now)
    decided = {d["fingerprint"] for d in C.load("state/journal.json", []) if d.get("fingerprint")}
    seen, extra_loss, extra_trades, out = set(), 0.0, 0, []
    for o in orders:
        if o["fingerprint"] in decided:
            continue
        r = check_order(o, cfg, tokens, day, seen, extra_loss, extra_trades)
        o["risk"] = r
        if r["verdict"] == "PASS" and o["side"] == "buy":
            extra_loss += o.get("max_loss_usd") or 0
            extra_trades += 1
        out.append(o)
    if not a.no_write:
        C.save("orders.json", orders)
    if a.json:
        print(json.dumps([{"id": o["id"], **o["risk"]} for o in out], indent=2))
        return
    if not out:
        print("No pending orders. Plan one with plan.py buy or plan.py sell.")
    for o in out:
        print(describe(o))
        print()
    print("Rules check only, not a recommendation. Next for a PASS: ticket.py show")


def cmd_clear(a):
    C.save("orders.json", [])
    print("crypto/orders.json emptied.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--now", help="pin the clock, ISO time in UTC")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("buy")
    s.add_argument("--chain", required=True); s.add_argument("--token", required=True)
    s.add_argument("--with", dest="with_token", default="USDC")
    s.add_argument("--usd", type=float); s.add_argument("--stop", type=float)
    s.add_argument("--stop-pct", type=float); s.add_argument("--target", type=float)
    s.add_argument("--target-pct", type=float); s.add_argument("--slippage", type=float)
    s.add_argument("--why"); s.set_defaults(fn=cmd_buy)
    s = sub.add_parser("sell")
    s.add_argument("--chain", required=True); s.add_argument("--token", required=True)
    s.add_argument("--for", dest="for_token", default="USDC")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--amount", type=float); g.add_argument("--all", action="store_true")
    s.add_argument("--slippage", type=float); s.add_argument("--why"); s.set_defaults(fn=cmd_sell)
    s = sub.add_parser("check"); s.add_argument("--json", action="store_true")
    s.add_argument("--no-write", action="store_true"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("clear"); s.set_defaults(fn=cmd_clear)
    a = p.parse_args()
    try:
        a.fn(a)
    except C.Fail as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
