#!/usr/bin/env python3
"""Read-only market and wallet data for trader-metamask. Standard library only.

  search   find a token by name or ticker on DexScreener and list every
           contract that uses that ticker, so the real one can be told
           apart from impostors (same ticker, different address).
  token    safety check of one token contract: GoPlus (honeypot, taxes,
           owner powers, mint, pause, blacklist), honeypot.is (a simulated
           buy and sell, on ethereum/base/bsc) and DexScreener (liquidity,
           volume, pair age). Prints OK, CAUTION or BLOCK with reasons and
           records the verdict in crypto/state/tokens.json, which plan.py
           check requires.
  price    USD price of a token (DefiLlama, then DexScreener).
  wallet   balances of a PUBLIC wallet address (native coin plus the known
           stablecoins and any --tokens), valued in USD. Writes
           crypto/portfolio.json. Never needs, and never accepts, a key.
  gas      current gas price and the rough USD cost of one swap.

    python3 market.py search PEPE
    python3 market.py token --chain ethereum --address 0x6982...1933
    python3 market.py price --chain base --token USDC
    python3 market.py wallet --chain base --address 0xYourPublicAddress --tokens 0x...
    python3 market.py gas --chain arbitrum
"""

import argparse
import json
import sys
import time
import urllib.parse

import common as C


CG_PLATFORM = {"ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum-one",
               "optimism": "optimistic-ethereum", "polygon": "polygon-pos",
               "bsc": "binance-smart-chain", "linea": "linea"}


def coingecko_official(query):
    """Contracts CoinGecko lists for the top-ranked coin whose ticker is the query.
    Returns (coin id, {chain: address}) or (None, {}). Best effort: CoinGecko rate-limits."""
    try:
        coins = C.http_json("https://api.coingecko.com/api/v3/search?query=" +
                            urllib.parse.quote(query)).get("coins") or []
        coins = [c for c in coins if (c.get("symbol") or "").upper() == query.upper()]
        coins.sort(key=lambda c: c.get("market_cap_rank") or 10 ** 9)
        if not coins:
            return None, {}
        cid = coins[0]["id"]
        plat = C.http_json("https://api.coingecko.com/api/v3/coins/%s?localization=false&tickers=false"
                           "&market_data=false&community_data=false&developer_data=false" % cid
                           ).get("platforms") or {}
        out = {ch: plat[p] for ch, p in CG_PLATFORM.items() if plat.get(p)}
        return cid, out
    except C.Fail:
        return None, {}


def cmd_search(a):
    out = C.http_json("https://api.dexscreener.com/latest/dex/search?q=" +
                      urllib.parse.quote(a.query))
    pairs = out.get("pairs") or []
    want = {C.CHAINS[c]["dexscreener"]: c for c in C.CHAINS}
    rows = {}
    for p in pairs:
        if p.get("chainId") not in want:
            continue
        b = p.get("baseToken") or {}
        key = (p["chainId"], b.get("address", "").lower())
        liq = (p.get("liquidity") or {}).get("usd") or 0
        r = rows.setdefault(key, {"chain": want[p["chainId"]], "symbol": b.get("symbol"),
                                  "name": b.get("name"), "address": b.get("address"),
                                  "liquidity_usd": 0, "volume_24h": 0, "pairs": 0,
                                  "oldest_pair": None})
        r["liquidity_usd"] += liq
        r["volume_24h"] += (p.get("volume") or {}).get("h24") or 0
        r["pairs"] += 1
        created = p.get("pairCreatedAt")
        if created and (r["oldest_pair"] is None or created < r["oldest_pair"]):
            r["oldest_pair"] = created
    rows = sorted(rows.values(), key=lambda r: r["liquidity_usd"], reverse=True)[:a.limit]
    if a.chain:
        rows = [r for r in rows if r["chain"] == a.chain]
    cid, official = coingecko_official(a.query)
    for r in rows:
        r["coingecko_official"] = (official.get(r["chain"], "").lower() == (r["address"] or "").lower())
    if a.json:
        print(json.dumps({"coingecko_id": cid, "official": official, "contracts": rows}, indent=2))
        return
    if cid:
        print("CoinGecko's top coin with ticker %s is %r. Its official contracts on supported chains:" % (
            a.query.upper(), cid))
        for ch, ad in official.items():
            print("  %-9s %s" % (ch, ad))
        missing = sorted({r["chain"] for r in rows} - set(official))
        if missing:
            print("  NOT on: %s. Tokens with this ticker there are other projects." % ", ".join(missing))
        print()
    if not rows:
        print("Nothing found for %r on the supported EVM chains." % a.query)
        return
    print("Contracts using %r (deepest liquidity first). Same ticker does NOT mean same token:" % a.query)
    for r in rows:
        age = "?"
        if r["oldest_pair"]:
            age = "%dd" % ((time.time() * 1000 - r["oldest_pair"]) / 86400000)
        print("  %-9s %-8s %-42s liq %-14s vol24h %-14s age %-6s %s(%s)" % (
            r["chain"], r["symbol"], r["address"], C.money(r["liquidity_usd"]),
            C.money(r["volume_24h"]), age, "OFFICIAL " if r["coingecko_official"] else "", r["name"]))
    syms = {}
    for r in rows:
        syms.setdefault((r["chain"], (r["symbol"] or "").upper()), []).append(r)
    dupes = [k for k, v in syms.items() if len(v) > 1]
    if dupes:
        print("\nWARNING: more than one contract uses the same ticker on %s. Confirm the address "
              "on the project's official site or CoinGecko before trading." %
              ", ".join("%s/%s" % k for k in dupes))
    print("\nNext: market.py token --chain <chain> --address <address>")


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def check_token(chain_key, addr, cfg):
    ch = C.CHAINS[chain_key]
    known = addr.lower() in {v.lower() for v in C.KNOWN.get(chain_key, {}).values()}
    reasons_block, reasons_caution, facts = [], [], {}

    # GoPlus
    try:
        gp = C.http_json("https://api.gopluslabs.io/api/v1/token_security/%s?contract_addresses=%s"
                         % (ch["goplus"], addr.lower()))
        g = (gp.get("result") or {}).get(addr.lower())
    except C.Fail as e:
        g = None
        reasons_caution.append("GoPlus unavailable (%s)" % e)
    if g:
        facts["name"] = g.get("token_name")
        facts["symbol"] = g.get("token_symbol")
        facts["holders"] = g.get("holder_count")
        buy_tax, sell_tax = _f(g.get("buy_tax")) * 100, _f(g.get("sell_tax")) * 100
        facts["buy_tax_pct"], facts["sell_tax_pct"] = round(buy_tax, 2), round(sell_tax, 2)
        flag = lambda k: g.get(k) == "1"
        if flag("is_honeypot"):
            reasons_block.append("GoPlus: honeypot (you can buy but not sell)")
        if flag("cannot_sell_all"):
            reasons_block.append("GoPlus: cannot sell all tokens")
        if flag("cannot_buy"):
            reasons_block.append("GoPlus: cannot buy")
        if flag("hidden_owner"):
            reasons_block.append("GoPlus: hidden owner")
        if flag("selfdestruct"):
            reasons_block.append("GoPlus: contract can self-destruct")
        if flag("can_take_back_ownership"):
            reasons_block.append("GoPlus: ownership can be taken back")
        if flag("owner_change_balance"):
            reasons_block.append("GoPlus: owner can change balances")
        if g.get("is_open_source") == "0":
            reasons_block.append("GoPlus: contract source not verified")
        if max(buy_tax, sell_tax) > cfg["max_token_tax_pct"]:
            reasons_block.append("tax %.1f%% buy / %.1f%% sell, over max_token_tax_pct %s%%" %
                                 (buy_tax, sell_tax, cfg["max_token_tax_pct"]))
        elif max(buy_tax, sell_tax) > 0:
            reasons_caution.append("token tax %.1f%% buy / %.1f%% sell" % (buy_tax, sell_tax))
        owner = (g.get("owner_address") or "").lower()
        renounced = owner in ("", "0x0000000000000000000000000000000000000000",
                              "0x000000000000000000000000000000000000dead")
        facts["owner_renounced"] = renounced
        for k, msg in (("is_mintable", "owner can mint new tokens"),
                       ("transfer_pausable", "transfers can be paused"),
                       ("is_blacklisted", "contract has a blacklist"),
                       ("slippage_modifiable", "tax can be changed"),
                       ("personal_slippage_modifiable", "tax can be changed per wallet"),
                       ("trading_cooldown", "trading cooldown"),
                       ("is_proxy", "upgradeable proxy (code can change)"),
                       ("external_call", "makes external calls")):
            if flag(k):
                # Most of these powers are harmless once ownership is renounced.
                # Known stablecoins and wrapped coins are run by their issuer on purpose.
                soft = renounced or known or k == "is_proxy"
                (reasons_caution if soft else reasons_block).append(
                    "GoPlus: %s%s" % (msg, "" if renounced else ", and the owner is active"))
        if (g.get("is_in_cex") or {}).get("listed") == "1":
            facts["cex"] = (g.get("is_in_cex") or {}).get("cex_list")
    elif g is None and not reasons_caution:
        reasons_caution.append("GoPlus has no data for this contract")

    # honeypot.is: a simulated buy and sell
    if ch["honeypot"]:
        try:
            hp = C.http_json("https://api.honeypot.is/v2/IsHoneypot?address=%s&chainID=%d"
                             % (addr, ch["id"]))
            if (hp.get("honeypotResult") or {}).get("isHoneypot"):
                reasons_block.append("honeypot.is: simulated sell failed (honeypot)")
            sim = hp.get("simulationResult") or {}
            if sim:
                facts["sim_sell_tax_pct"] = sim.get("sellTax")
                if _f(sim.get("sellTax")) > cfg["max_token_tax_pct"]:
                    reasons_block.append("honeypot.is: simulated sell tax %s%%" % sim.get("sellTax"))
            risk = (hp.get("summary") or {}).get("risk")
            facts["honeypot_is_risk"] = risk
            if risk in ("high", "very_high", "honeypot"):
                reasons_block.append("honeypot.is risk: %s" % risk)
            elif risk == "medium":
                reasons_caution.append("honeypot.is risk: medium")
        except C.Fail as e:
            reasons_caution.append("honeypot.is unavailable (%s)" % e)

    # DexScreener: liquidity, volume, age
    try:
        ds = C.http_json("https://api.dexscreener.com/latest/dex/tokens/" + addr)
        pairs = [p for p in (ds.get("pairs") or []) if p.get("chainId") == ch["dexscreener"]]
    except C.Fail as e:
        pairs = []
        reasons_caution.append("DexScreener unavailable (%s)" % e)
    if pairs:
        liq = sum((p.get("liquidity") or {}).get("usd") or 0 for p in pairs)
        vol = sum((p.get("volume") or {}).get("h24") or 0 for p in pairs)
        oldest = min([p["pairCreatedAt"] for p in pairs if p.get("pairCreatedAt")] or [0])
        top = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        facts.update({"liquidity_usd": round(liq), "volume_24h_usd": round(vol),
                      "change_24h_pct": (top.get("priceChange") or {}).get("h24"),
                      "fdv_usd": top.get("fdv"), "top_pair": top.get("url")})
        facts.setdefault("symbol", (top.get("baseToken") or {}).get("symbol"))
        if liq < cfg["min_liquidity_usd"] and not known:
            reasons_block.append("liquidity %s, under min_liquidity_usd %s" %
                                 (C.money(liq), C.money(cfg["min_liquidity_usd"])))
        if oldest:
            age_days = (time.time() * 1000 - oldest) / 86400000
            facts["pair_age_days"] = round(age_days, 1)
            if age_days < cfg["min_pair_age_days"]:
                reasons_block.append("oldest pool is %.1f days old, under min_pair_age_days %s" %
                                     (age_days, cfg["min_pair_age_days"]))
        if vol < cfg["min_volume_24h_usd"] and not known:
            reasons_block.append("24h volume %s, under min_volume_24h_usd %s (hard to get out at a fair price)"
                                 % (C.money(vol), C.money(cfg["min_volume_24h_usd"])))
        if liq and vol / liq > 10:
            reasons_caution.append("24h volume is %.0fx liquidity (wash trading or a pump?)" % (vol / liq))
    elif "DexScreener unavailable" not in " ".join(reasons_caution):
        reasons_block.append("no DEX pool found on %s" % chain_key)

    try:
        facts["price_usd"] = C.token_price(chain_key, addr)
    except C.Fail:
        pass
    if known:
        facts["known_token"] = True
    verdict = "BLOCK" if reasons_block else ("CAUTION" if reasons_caution else "OK")
    return {"chain": chain_key, "address": addr, "verdict": verdict, "block": reasons_block,
            "caution": reasons_caution, "facts": facts, "checked_at": C.now().isoformat()}


def cmd_token(a):
    chain_key, _ = C.chain(a.chain)
    sym, addr = C.resolve_token(chain_key, a.address)
    if addr == C.NATIVE:
        print("%s is the chain's native coin: no contract to check. OK." % sym)
        return
    cfg = C.config()
    res = check_token(chain_key, addr, cfg)
    store = C.load("state/tokens.json", {})
    store["%s:%s" % (chain_key, addr.lower())] = res
    C.save("state/tokens.json", store)
    if a.json:
        print(json.dumps(res, indent=2))
        return
    f = res["facts"]
    print("TOKEN %s (%s) on %s" % (f.get("symbol", "?"), f.get("name", "?"), chain_key))
    print("  contract   %s" % addr)
    print("  explorer   %s/token/%s" % (C.CHAINS[chain_key]["explorer"], addr))
    if "price_usd" in f:
        print("  price      $%s   24h %s%%" % (C.price_str(f["price_usd"]), f.get("change_24h_pct")))
    if "liquidity_usd" in f:
        print("  liquidity  %s   volume 24h %s   pool age %s days" % (
            C.money(f.get("liquidity_usd", 0)), C.money(f.get("volume_24h_usd", 0)),
            f.get("pair_age_days", "?")))
    if "buy_tax_pct" in f:
        print("  tax        buy %s%%  sell %s%%   holders %s   owner renounced: %s" % (
            f["buy_tax_pct"], f["sell_tax_pct"], f.get("holders"), f.get("owner_renounced")))
    if f.get("cex"):
        print("  listed on  %s" % ", ".join(f["cex"]))
    print("  VERDICT    %s" % res["verdict"])
    for r in res["block"]:
        print("    BLOCK    %s" % r)
    for r in res["caution"]:
        print("    caution  %s" % r)
    print("  (a clean check lowers scam risk; it does not make the token a good trade)")


def cmd_price(a):
    chain_key, _ = C.chain(a.chain)
    sym, addr = C.resolve_token(chain_key, a.token)
    p = C.token_price(chain_key, addr)
    print(json.dumps({"chain": chain_key, "token": sym or addr, "address": addr, "price_usd": p})
          if a.json else "%s on %s: $%s" % (sym or addr, chain_key, C.price_str(p)))


def cmd_wallet(a):
    chain_key, ch = C.chain(a.chain)
    if not C.is_addr(a.address):
        raise C.Fail("that is not a public wallet address (0x + 40 hex characters). "
                     "Never paste a private key or seed phrase anywhere.")
    holdings = []
    wei = int(C.rpc(chain_key, "eth_getBalance", [a.address, "latest"]), 16)
    holdings.append({"symbol": ch["native"], "address": C.NATIVE, "amount": wei / 1e18})
    tokens = dict(C.KNOWN.get(chain_key, {}))
    for t in filter(None, (a.tokens or "").split(",")):
        if not C.is_addr(t.strip()):
            raise C.Fail("bad token address %r" % t)
        tokens[t.strip()] = t.strip()
    for label, addr in tokens.items():
        raw = C.erc20_balance(chain_key, addr, a.address)
        if raw == 0:
            continue
        sym, dec = C.erc20_meta(chain_key, addr)
        holdings.append({"symbol": sym or label, "address": addr, "amount": raw / 10 ** dec})
    keys = []
    for h in holdings:
        h["key"] = ("coingecko:" + ch["native_cg"]) if h["address"] == C.NATIVE else \
            "%s:%s" % (chain_key, h["address"])
        keys.append(h["key"])
    px = C.prices_usd(keys)
    total = 0
    for h in holdings:
        h["price_usd"] = px.get(h.pop("key"))
        h["value_usd"] = round(h["amount"] * h["price_usd"], 2) if h["price_usd"] else None
        total += h["value_usd"] or 0
    snap = C.load("portfolio.json", {"wallets": {}})
    snap.setdefault("wallets", {})["%s:%s" % (chain_key, a.address.lower())] = {
        "chain": chain_key, "address": a.address, "holdings": holdings,
        "total_usd": round(total, 2), "as_of": C.now().isoformat()}
    C.save("portfolio.json", snap)
    if a.json:
        print(json.dumps(holdings, indent=2))
        return
    print("WALLET %s on %s (read only)" % (a.address, chain_key))
    for h in holdings:
        print("  %-8s %20s   %s" % (h["symbol"], ("%.6f" % h["amount"]).rstrip("0").rstrip("."),
                                    C.money(h["value_usd"]) if h["value_usd"] is not None else "no price"))
    print("  total    %s" % C.money(total))


def cmd_gas(a):
    chain_key, ch = C.chain(a.chain)
    usd, gwei = C.gas_cost_usd(chain_key)
    print("%s gas: %.4f gwei; one swap (~%d gas) costs about %s, plus about %s if the token "
          "needs an approval first. On L2s the L1 data fee can add more." % (
              chain_key, gwei, ch["gas_units_swap"], C.money(usd),
              C.money(usd * 50000 / ch["gas_units_swap"])))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--chain")
    s.add_argument("--limit", type=int, default=15); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("token"); s.add_argument("--chain", required=True)
    s.add_argument("--address", required=True); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_token)
    s = sub.add_parser("price"); s.add_argument("--chain", required=True)
    s.add_argument("--token", required=True); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_price)
    s = sub.add_parser("wallet"); s.add_argument("--chain", required=True)
    s.add_argument("--address", required=True); s.add_argument("--tokens")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_wallet)
    s = sub.add_parser("gas"); s.add_argument("--chain", required=True); s.set_defaults(fn=cmd_gas)
    a = p.parse_args()
    try:
        a.fn(a)
    except C.Fail as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
