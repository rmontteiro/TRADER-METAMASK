#!/usr/bin/env python3
"""Setup scanner and charts for trader-metamask. Standard library only.

Reads candles (open, high, low, close, volume) of DEX pools from
GeckoTerminal's free API and looks for three long setups:

  breakout   the close clears the highest high of the previous 20 candles,
             on volume at least 1.5x its 20-candle average, above the EMA50.
  pullback   an uptrend (EMA20 over a rising EMA50) where price dipped to the
             EMA20 in the last 3 candles and closed back above it, RSI 40-60.
  trend      close over EMA20 over EMA50, RSI 55-70, not stretched (less than
             2 ATR above the EMA20).

Each setup comes with a stop under the structure (swing low or EMA, minus a
slice of ATR) and a target of at least 2.5R (or the recent high if further).
Candidates also need the config's liquidity, volume and pool age, and the
best ones go through the same token safety check as market.py token. Every
candidate gets an SVG chart (candles, EMA20/50, volume, entry, stop, target)
and the run writes crypto/charts/scan.html with all of them.

    python3 scanner.py scan --chain base                       # trending + top-volume pools
    python3 scanner.py scan --chain ethereum --tf day --limit 20
    python3 scanner.py scan --chain base --tokens 0xabc...,0xdef...
    python3 scanner.py chart --chain base --token 0xabc...     # one chart, with or without a setup

A setup is a pattern in past prices, not a prediction. The plan still has to
pass plan.py check and the user's "sim".
"""

import argparse
import datetime
import html
import json
import os
import sys
import time

import common as C
import market as M

GT = "https://api.geckoterminal.com/api/v2"
GT_NET = {"ethereum": "eth", "base": "base", "arbitrum": "arbitrum", "optimism": "optimism",
          "polygon": "polygon_pos", "bsc": "bsc", "linea": "linea"}
TF = {"5m": ("minute", 5), "15m": ("minute", 15), "hour": ("hour", 1), "4h": ("hour", 4),
      "day": ("day", 1)}
SKIP_BASE = ({"WETH", "ETH", "WBTC", "CBBTC", "WBNB", "BNB", "WPOL", "POL", "WMATIC", "MATIC",
              "STETH", "WSTETH", "CBETH", "RETH", "WEETH", "EZETH", "PAXG", "XAUT", "PYUSD", "USDE",
              "SUSDE", "FDUSD", "TUSD", "USDS", "FRAX", "LUSD", "GHO", "CRVUSD", "USD0", "USDT0", "EURC"}
             | C.STABLES)
_last_call = [0.0]


def gt(path):
    # GeckoTerminal's free tier allows about 30 calls a minute.
    wait = 2.1 - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    try:
        return C.http_json(GT + path)
    except C.Fail as e:
        if "429" not in str(e):
            raise
    # Rate limited: back off and try twice more.
    for pause in (20, 45):
        time.sleep(pause)
        _last_call[0] = time.time()
        try:
            return C.http_json(GT + path)
        except C.Fail as e:
            if "429" not in str(e):
                raise
    raise C.Fail("GeckoTerminal rate limit (HTTP 429); wait a minute and run again")


# ---------- indicators ----------

def ema(xs, n):
    k, out, e = 2 / (n + 1), [], None
    for x in xs:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(closes)):
        if i > n:
            ag = (ag * (n - 1) + gains[i - 1]) / n
            al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr(c, n=14):
    trs = [c[0]["h"] - c[0]["l"]]
    for i in range(1, len(c)):
        trs.append(max(c[i]["h"] - c[i]["l"], abs(c[i]["h"] - c[i - 1]["c"]), abs(c[i]["l"] - c[i - 1]["c"])))
    return ema(trs, n)


# ---------- data ----------

def candles(chain_key, pool, tf, limit=200):
    period, agg = TF[tf]
    d = gt("/networks/%s/pools/%s/ohlcv/%s?aggregate=%d&limit=%d&currency=usd&token=base" % (
        GT_NET[chain_key], pool, period, agg, limit))
    rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    out = [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
            "v": float(r[5])} for r in rows]
    out.sort(key=lambda x: x["t"])
    secs = {"minute": 60, "hour": 3600, "day": 86400}[period] * agg
    if out and out[-1]["t"] + secs > time.time():
        out.pop()  # the candle still forming: its volume and close are partial
    return out


def _pool_row(p, included=None):
    a = p["attributes"]
    base_id = p["relationships"]["base_token"]["data"]["id"]
    addr = base_id.split("_", 1)[1]
    sym = a["name"].split(" / ")[0].strip()
    return {"pool": a["address"], "name": a["name"], "symbol": sym, "address": addr,
            "liquidity_usd": float(a.get("reserve_in_usd") or 0),
            "volume_24h_usd": float((a.get("volume_usd") or {}).get("h24") or 0),
            "change_24h_pct": float((a.get("price_change_percentage") or {}).get("h24") or 0),
            "created": a.get("pool_created_at"), "price_usd": float(a.get("base_token_price_usd") or 0)}


def candidates(chain_key, tokens=None):
    net = GT_NET[chain_key]
    rows = []
    if tokens:
        for t in tokens:
            d = gt("/networks/%s/tokens/%s/pools?page=1" % (net, t))
            pools = [_pool_row(p) for p in d.get("data") or []]
            pools = [p for p in pools if p["address"].lower() == t.lower()] or pools
            if pools:
                rows.append(max(pools, key=lambda p: p["liquidity_usd"]))
        return rows
    for path in ("/networks/%s/trending_pools?page=1" % net,
                 "/networks/%s/pools?page=1&sort=h24_volume_usd_desc" % net):
        try:
            rows += [_pool_row(p) for p in gt(path).get("data") or []]
        except C.Fail as e:
            print("warning: %s" % e, file=sys.stderr)
    # One pool per token: the deepest.
    best = {}
    for r in rows:
        k = r["address"].lower()
        if k not in best or r["liquidity_usd"] > best[k]["liquidity_usd"]:
            best[k] = r
    return list(best.values())


def prefilter(rows, cfg, explicit):
    keep, dropped = [], []
    now = time.time()
    for r in rows:
        why = None
        if r["symbol"].upper() in SKIP_BASE and not explicit:
            why = "base asset or stablecoin"
        elif r["liquidity_usd"] < cfg["min_liquidity_usd"]:
            why = "liquidity %s" % C.money(r["liquidity_usd"])
        elif r["volume_24h_usd"] < cfg["min_volume_24h_usd"]:
            why = "volume %s" % C.money(r["volume_24h_usd"])
        elif r["created"]:
            age = (now - datetime.datetime.fromisoformat(r["created"].replace("Z", "+00:00")).timestamp()) / 86400
            r["pool_age_days"] = round(age, 1)
            if age < cfg["min_pair_age_days"]:
                why = "pool %.1f days old" % age
        (dropped if why else keep).append((r, why))
    return [r for r, _ in keep], [(r, w) for r, w in dropped]


# ---------- setups ----------

def analyze(c):
    """Returns indicators and the best setup (or None) on the last closed candle."""
    if len(c) < 60:
        return {"error": "only %d candles (need 60)" % len(c)}
    closes = [x["c"] for x in c]
    e20, e50, r, a = ema(closes, 20), ema(closes, 50), rsi(closes), atr(c)
    i = len(c) - 1
    px, A = closes[i], a[i]
    vol_avg = sum(x["v"] for x in c[i - 20:i]) / 20 or 1e-12
    hi20 = max(x["h"] for x in c[i - 20:i])
    swing_lo = min(x["l"] for x in c[i - 10:i + 1])
    ind = {"price": px, "ema20": e20[i], "ema50": e50[i], "rsi": r[i], "atr": A,
           "atr_pct": A / px * 100, "vol_ratio": c[i]["v"] / vol_avg, "high20": hi20,
           "ema50_rising": e50[i] > e50[i - 10], "stretch_atr": (px - e20[i]) / A if A else 0}
    setups = []
    if px > hi20 and ind["vol_ratio"] >= 1.5 and px > e50[i]:
        stop = max(hi20 - A, swing_lo)
        setups.append(("breakout", stop, "closed above the 20-candle high %s on %.1fx volume" % (
            C.price_str(hi20), ind["vol_ratio"])))
    touched = min(x["l"] for x in c[i - 2:i + 1]) <= e20[i] + 0.25 * A
    if (e20[i] > e50[i] and ind["ema50_rising"] and touched and px > e20[i]
            and r[i] is not None and 40 <= r[i] <= 60):
        stop = min(min(x["l"] for x in c[i - 4:i + 1]), e50[i]) - 0.25 * A
        setups.append(("pullback", stop, "uptrend, dipped to the EMA20 and closed back above it (RSI %.0f)" % r[i]))
    if (px > e20[i] > e50[i] and ind["ema50_rising"] and r[i] is not None and 55 <= r[i] <= 70
            and ind["stretch_atr"] < 2):
        stop = min(e50[i], swing_lo) - 0.25 * A
        setups.append(("trend", stop, "price over EMA20 over a rising EMA50, RSI %.0f, not stretched" % r[i]))
    ind["warnings"] = []
    if ind["atr_pct"] < 0.5 and abs(px - 1) < 0.03:
        return {"ind": dict(ind, warnings=["moves under 0.5% a candle near $1: a stablecoin"]), "setup": None}
    if r[i] is not None and r[i] > 75:
        ind["warnings"].append("RSI %.0f: overbought" % r[i])
    if ind["stretch_atr"] > 3:
        ind["warnings"].append("%.1f ATR above the EMA20: stretched" % ind["stretch_atr"])
    if px < e50[i]:
        ind["warnings"].append("under the EMA50: downtrend")
    best = None
    for name, stop, why in setups:
        if stop >= px or stop <= 0:
            continue
        risk = px - stop
        # 2.5R, or the recent high when it is further but still within 4R.
        target = hi20 if px + 2.5 * risk < hi20 <= px + 4 * risk else px + 2.5 * risk
        cand = {"setup": name, "entry": px, "stop": stop, "target": target,
                "stop_pct": risk / px * 100, "target_pct": (target - px) / px * 100,
                "reward_risk": (target - px) / risk, "why": why}
        rank = {"breakout": 3, "pullback": 2, "trend": 1}[name]
        if best is None or rank > {"breakout": 3, "pullback": 2, "trend": 1}[best["setup"]]:
            best = cand
    return {"ind": ind, "setup": best}


def score(row, res):
    s = res.get("setup")
    if not s:
        return -1
    ind = res["ind"]
    pts = {"breakout": 3, "pullback": 2.5, "trend": 2}[s["setup"]]
    pts += min(ind["vol_ratio"], 4) * 0.5
    pts += 1 if ind["ema50_rising"] else 0
    pts -= len(ind["warnings"])
    pts -= max(0, s["stop_pct"] - 15) / 5  # very wide stops shrink the size a lot
    return round(pts, 2)


# ---------- charts ----------

def svg_chart(title, c, res, tf, width=920, height=520):
    c = c[-120:]
    closes_all = [x["c"] for x in c]
    e20, e50 = ema(closes_all, 20), ema(closes_all, 50)
    s = (res or {}).get("setup")
    lo = min(x["l"] for x in c)
    hi = max(x["h"] for x in c)
    if s:
        lo, hi = min(lo, s["stop"]), max(hi, s["target"])
    pad = (hi - lo) * 0.05 or hi * 0.01
    lo, hi = lo - pad, hi + pad
    L, R, T, PH, VH = 10, 90, 34, 360, 90
    W = width - L - R
    step = W / len(c)
    y = lambda p: T + (hi - p) / (hi - lo) * PH
    vmax = max(x["v"] for x in c) or 1
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" role="img" aria-label="%s">' % (
        width, height, html.escape(title)),
        '<style>.t{font:13px system-ui,sans-serif;fill:var(--fg,#222)}.s{font:11px system-ui,sans-serif;fill:var(--muted,#666)}'
        '.g{stroke:var(--grid,#e5e5e5)}</style>',
        '<text class="t" x="%d" y="20">%s</text>' % (L, html.escape(title))]
    for k in range(5):
        p = lo + (hi - lo) * k / 4
        out.append('<line class="g" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (L, L + W, y(p), y(p)))
        out.append('<text class="s" x="%d" y="%.1f">%s</text>' % (L + W + 6, y(p) + 4, C.price_str(p)))
    for k, x in enumerate(c):
        cx = L + step * (k + 0.5)
        up = x["c"] >= x["o"]
        col = "#1a9e5a" if up else "#d0453b"
        out.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="%s"/>' % (cx, cx, y(x["h"]), y(x["l"]), col))
        top, bot = y(max(x["o"], x["c"])), y(min(x["o"], x["c"]))
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>' % (
            cx - step * 0.35, top, max(step * 0.7, 1), max(bot - top, 0.8), col))
        vh = x["v"] / vmax * (VH - 10)
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" opacity=".45"/>' % (
            cx - step * 0.35, T + PH + VH - vh + 10, max(step * 0.7, 1), vh, col))
    for series, col, name in ((e20, "#2f6fd6", "EMA20"), (e50, "#d98a1c", "EMA50")):
        pts = " ".join("%.1f,%.1f" % (L + step * (k + 0.5), y(v)) for k, v in enumerate(series) if k >= 5)
        out.append('<polyline fill="none" stroke="%s" stroke-width="1.5" points="%s"/>' % (col, pts))
    out.append('<text class="s" x="%d" y="%d" fill="#2f6fd6">EMA20</text><text class="s" x="%d" y="%d">EMA50 (laranja) · volume embaixo · velas de %s</text>' % (
        L, T + PH + VH + 26, L + 50, T + PH + VH + 26, tf))
    if s:
        for p, col, name in ((s["target"], "#1a9e5a", "alvo"), (s["entry"], "#2f6fd6", "entrada"),
                             (s["stop"], "#d0453b", "stop")):
            out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s" stroke-dasharray="6 4" stroke-width="1.5"/>' % (
                L, L + W, y(p), y(p), col))
            out.append('<text class="s" x="%d" y="%.1f" style="fill:%s">%s %s</text>' % (
                L + 4, y(p) - 4, col, name, C.price_str(p)))
    out.append("</svg>")
    return "\n".join(out)


TF_LABEL = {"5m": "5 min", "15m": "15 min", "hour": "1 h", "4h": "4 h", "day": "1 dia"}


def write_charts(items, tf, chain_key):
    d = C.path("charts")
    os.makedirs(d, exist_ok=True)
    cards = []
    for it in items:
        fn = "%s-%s.svg" % ("".join(ch for ch in it["symbol"] if ch.isalnum())[:16] or "token", it["address"][2:10])
        svg = svg_chart("%s (%s) · %s · %s" % (it["symbol"], it["address"][:10] + "…", chain_key,
                                               (it.get("setup") or {}).get("setup", "sem setup")),
                        it["_candles"], it, TF_LABEL.get(tf, tf))
        with open(os.path.join(d, fn), "w") as f:
            f.write(svg)
        it["chart"] = os.path.join(d, fn)
        s = it.get("setup")
        info = ("<b>%s</b>: %s<br>entrada %s · stop %s (−%.1f%%) · alvo %s (+%.1f%%) · R:R %.1f" % (
            s["setup"], html.escape(s["why"]), C.price_str(s["entry"]), C.price_str(s["stop"]), s["stop_pct"],
            C.price_str(s["target"]), s["target_pct"], s["reward_risk"])) if s else "sem setup"
        safety = it.get("safety")
        if safety:
            info += "<br>checagem do token: <b>%s</b> %s" % (safety["verdict"], html.escape(
                "; ".join(safety["block"] + safety["caution"])))
        cards.append('<section><h2>%s</h2><p>%s</p><p class="m">%s · liquidez %s · volume 24h %s</p>%s</section>' % (
            html.escape(it["symbol"]), info, it["address"], C.money(it["liquidity_usd"]),
            C.money(it["volume_24h_usd"]), svg))
    tf = TF_LABEL.get(tf, tf)
    page = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Scanner cripto</title>
<style>:root{--fg:#1d1d1f;--muted:#666;--grid:#e6e6e6;--bg:#fff}
@media (prefers-color-scheme:dark){:root{--fg:#eee;--muted:#aaa;--grid:#333;--bg:#151515}}
body{background:var(--bg);color:var(--fg);font:15px system-ui,sans-serif;margin:0 auto;max-width:960px;padding:16px}
section{border-top:1px solid var(--grid);padding:12px 0}.m{color:var(--muted);font-size:13px;word-break:break-all}svg{width:100%%;height:auto}</style>
</head><body><h1>Scanner cripto · %s · velas de %s</h1>
<p class="m">%s UTC. Um setup é um padrão no preço passado, não uma previsão. Todo trade ainda passa por plan.py check e pelo seu "sim".</p>
%s</body></html>""" % (chain_key, tf, C.now().strftime("%Y-%m-%d %H:%M"), "\n".join(cards))
    with open(os.path.join(d, "scan.html"), "w") as f:
        f.write(page)
    return os.path.join(d, "scan.html")


# ---------- commands ----------

def cmd_scan(a):
    cfg = C.config()
    chain_key, _ = C.chain(a.chain)
    toks = [t.strip() for t in (a.tokens or "").split(",") if t.strip()]
    for t in toks:
        if not C.is_addr(t):
            raise C.Fail("bad token address %r" % t)
    rows = candidates(chain_key, toks)
    rows, dropped = prefilter(rows, cfg, bool(toks))
    rows.sort(key=lambda r: r["volume_24h_usd"], reverse=True)
    rows = rows[:a.limit]
    print("Scanning %d pools on %s (%s candles); %d dropped by the liquidity/volume/age filters." % (
        len(rows), chain_key, a.tf, len(dropped)), file=sys.stderr)
    results = []
    for r in rows:
        try:
            c = candles(chain_key, r["pool"], a.tf)
        except C.Fail as e:
            print("  %s: %s" % (r["symbol"], e), file=sys.stderr)
            continue
        res = analyze(c)
        if "error" in res:
            continue
        r.update(res)
        r["_candles"] = c
        r["score"] = score(r, res)
        results.append(r)
    hits = sorted([r for r in results if r.get("setup")], key=lambda r: r["score"], reverse=True)
    # Safety check on the best few; a BLOCK drops the candidate.
    for r in hits[:a.check]:
        r["safety"] = M.check_token(chain_key, r["address"], cfg)
        store = C.load("state/tokens.json", {})
        store["%s:%s" % (chain_key, r["address"].lower())] = r["safety"]
        C.save("state/tokens.json", store)
    shown = [r for r in hits if (r.get("safety") or {}).get("verdict") != "BLOCK"]
    blocked = [r for r in hits if (r.get("safety") or {}).get("verdict") == "BLOCK"]
    page = write_charts(shown[:a.charts] or results[:3], a.tf, chain_key)
    C.save("state/scan.json", {"at": C.now().isoformat(), "chain": chain_key, "tf": a.tf,
                               "hits": [{k: v for k, v in r.items() if k != "_candles"} for r in hits]})
    if a.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "_candles"} for r in shown], indent=2))
        return
    if not shown:
        print("No setups right now on %s (%s candles) among %d pools that passed the filters." % (
            chain_key, a.tf, len(results)))
    for n, r in enumerate(shown, 1):
        s, ind = r["setup"], r["ind"]
        print("%d. %-10s %s  score %.1f  [%s]" % (n, r["symbol"], r["address"], r["score"], s["setup"].upper()))
        print("   %s" % s["why"])
        print("   entry $%s  stop $%s (-%.1f%%)  target $%s (+%.1f%%)  R:R %.1f  RSI %.0f  ATR %.1f%%" % (
            C.price_str(s["entry"]), C.price_str(s["stop"]), s["stop_pct"], C.price_str(s["target"]),
            s["target_pct"], s["reward_risk"], ind["rsi"] or 0, ind["atr_pct"]))
        print("   liquidity %s  volume 24h %s  24h %+.1f%%  pool %s days" % (
            C.money(r["liquidity_usd"]), C.money(r["volume_24h_usd"]), r["change_24h_pct"],
            r.get("pool_age_days", "?")))
        if ind["warnings"]:
            print("   warn: %s" % "; ".join(ind["warnings"]))
        sf = r.get("safety")
        print("   token check: %s" % ("%s %s" % (sf["verdict"], "; ".join(sf["caution"])) if sf
                                       else "not run yet: market.py token --chain %s --address %s" % (chain_key, r["address"])))
        print("   plan: plan.py buy --chain %s --token %s --stop %s --target %s --why \"%s %s\"" % (
            chain_key, r["address"], C.price_str(s["stop"]), C.price_str(s["target"]), s["setup"], a.tf))
    others = [r for r in results if not r.get("setup")]
    if others and a.all:
        print("\nNo setup now (trend · RSI · volume vs average · warnings):")
        for r in sorted(others, key=lambda r: r["volume_24h_usd"], reverse=True):
            ind = r["ind"]
            trend = ("up" if ind["price"] > ind["ema20"] > ind["ema50"] else
                     "down" if ind["price"] < ind["ema20"] < ind["ema50"] else "mixed")
            print("   %-10s %-6s RSI %3.0f  vol %.1fx  %s" % (r["symbol"], trend, ind["rsi"] or 0,
                                                          ind["vol_ratio"], "; ".join(ind["warnings"])))
    for r in blocked:
        print("dropped %s %s: token check BLOCK (%s)" % (r["symbol"], r["address"], "; ".join(r["safety"]["block"])))
    print("\nCharts: %s" % page)
    print("A setup is a pattern in past prices, not a prediction.")


def cmd_chart(a):
    chain_key, _ = C.chain(a.chain)
    sym, addr = C.resolve_token(chain_key, a.token)
    rows = candidates(chain_key, [addr])
    if not rows:
        raise C.Fail("no pool found on GeckoTerminal for %s on %s" % (addr, chain_key))
    r = rows[0]
    c = candles(chain_key, r["pool"], a.tf)
    res = analyze(c)
    if "error" in res:
        raise C.Fail(res["error"])
    r.update(res)
    r["_candles"] = c
    page = write_charts([r], a.tf, chain_key)
    ind, s = r["ind"], r.get("setup")
    print("%s on %s, %s candles: price $%s  EMA20 $%s  EMA50 $%s  RSI %.0f  ATR %.1f%%  volume %.1fx avg" % (
        r["symbol"], chain_key, a.tf, C.price_str(ind["price"]), C.price_str(ind["ema20"]),
        C.price_str(ind["ema50"]), ind["rsi"] or 0, ind["atr_pct"], ind["vol_ratio"]))
    if s:
        print("SETUP %s: %s. stop $%s (-%.1f%%), target $%s (+%.1f%%), R:R %.1f" % (
            s["setup"].upper(), s["why"], C.price_str(s["stop"]), s["stop_pct"], C.price_str(s["target"]),
            s["target_pct"], s["reward_risk"]))
    else:
        print("No setup on the last candle.")
    if ind["warnings"]:
        print("warn: %s" % "; ".join(ind["warnings"]))
    print("Chart: %s" % r["chart"])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--chain", required=True); s.add_argument("--tokens")
    s.add_argument("--tf", choices=list(TF), default="hour")
    s.add_argument("--limit", type=int, default=15, help="pools to pull candles for (each is one API call)")
    s.add_argument("--check", type=int, default=5, help="safety-check the best N setups")
    s.add_argument("--charts", type=int, default=8)
    s.add_argument("--all", action="store_true", help="also list the pools without a setup")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("chart")
    s.add_argument("--chain", required=True); s.add_argument("--token", required=True)
    s.add_argument("--tf", choices=list(TF), default="hour"); s.set_defaults(fn=cmd_chart)
    a = p.parse_args()
    try:
        a.fn(a)
    except C.Fail as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
