"""Shared helpers for trader-metamask. Standard library only.

Nothing here can sign or send a transaction. The only network calls are
public, read-only HTTP GETs and JSON-RPC reads (eth_call, eth_getBalance,
eth_gasPrice, eth_getTransactionReceipt). There is no place in this code for
a private key or a seed phrase, on purpose.
"""

import datetime
import hashlib
import json
import os
import re
import urllib.error
import urllib.request

UA = "trader-metamask/1.0 (+read-only)"

# Public RPCs (no key) and the ids each data source uses for the chain.
CHAINS = {
    "ethereum": {"id": 1, "rpc": "https://ethereum-rpc.publicnode.com", "native": "ETH",
                 "native_cg": "ethereum", "goplus": "1", "dexscreener": "ethereum",
                 "explorer": "https://etherscan.io", "honeypot": True, "gas_units_swap": 180000},
    "base": {"id": 8453, "rpc": "https://base-rpc.publicnode.com", "native": "ETH",
             "native_cg": "ethereum", "goplus": "8453", "dexscreener": "base",
             "explorer": "https://basescan.org", "honeypot": True, "gas_units_swap": 250000},
    "arbitrum": {"id": 42161, "rpc": "https://arbitrum-one-rpc.publicnode.com", "native": "ETH",
                 "native_cg": "ethereum", "goplus": "42161", "dexscreener": "arbitrum",
                 "explorer": "https://arbiscan.io", "honeypot": False, "gas_units_swap": 600000},
    "optimism": {"id": 10, "rpc": "https://optimism-rpc.publicnode.com", "native": "ETH",
                 "native_cg": "ethereum", "goplus": "10", "dexscreener": "optimism",
                 "explorer": "https://optimistic.etherscan.io", "honeypot": False,
                 "gas_units_swap": 250000},
    "polygon": {"id": 137, "rpc": "https://polygon-bor-rpc.publicnode.com", "native": "POL",
                "native_cg": "polygon-ecosystem-token", "goplus": "137", "dexscreener": "polygon",
                "explorer": "https://polygonscan.com", "honeypot": False, "gas_units_swap": 250000},
    "bsc": {"id": 56, "rpc": "https://bsc-rpc.publicnode.com", "native": "BNB",
            "native_cg": "binancecoin", "goplus": "56", "dexscreener": "bsc",
            "explorer": "https://bscscan.com", "honeypot": True, "gas_units_swap": 200000},
    "linea": {"id": 59144, "rpc": "https://linea-rpc.publicnode.com", "native": "ETH",
              "native_cg": "ethereum", "goplus": "59144", "dexscreener": "linea",
              "explorer": "https://lineascan.build", "honeypot": False, "gas_units_swap": 250000},
}

# Well-known token contracts, so "USDC on base" does not need a search (a search
# is where impostor tokens hide). Checked on 2026-10-30.
KNOWN = {
    "ethereum": {"USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                 "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                 "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
                 "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                 "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"},
    "base": {"USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
             "WETH": "0x4200000000000000000000000000000000000006",
             "CBBTC": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"},
    "arbitrum": {"USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                 "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
                 "WETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
                 "ARB": "0x912CE59144191C1204E64559FE8253a0e49E6548"},
    "optimism": {"USDC": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
                 "WETH": "0x4200000000000000000000000000000000000006",
                 "OP": "0x4200000000000000000000000000000000000042"},
    "polygon": {"USDC": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
                "USDT": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
                "WETH": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"},
    "bsc": {"USDT": "0x55d398326f99059fF775485246999027B3197955",
            "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
            "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"},
    "linea": {"USDC": "0x176211869cA2b568f2A7D4EE941E073a821EE1ff",
              "WETH": "0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf34f"},
}
STABLES = {"USDC", "USDT", "DAI", "USDC.E", "USDBC"}
NATIVE = "native"

DEFAULT_CONFIG = {
    "mode": "paper",                 # paper = simulated fills; live = user signs in MetaMask
    "budget_usd": 1000,
    "max_trade_usd": 200,
    "max_loss_per_trade_usd": 20,
    "max_daily_loss_usd": 50,
    "max_trades_per_day": 3,
    "min_reward_risk": 2.0,
    "max_slippage_pct": 1.0,
    "max_gas_pct_of_trade": 3.0,
    "min_liquidity_usd": 250000,
    "min_volume_24h_usd": 50000,
    "min_pair_age_days": 7,
    "max_token_tax_pct": 5,
    "allowed_chains": ["ethereum", "base", "arbitrum", "optimism", "polygon", "bsc", "linea"],
    "blocked_tokens": [],
    "require_stop": True,
}

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class Fail(Exception):
    pass


def root():
    """The crypto/ data folder, relative to where the user runs the scripts."""
    path = os.environ.get("TRADER_METAMASK_DIR") or os.path.join(os.getcwd(), "crypto")
    os.makedirs(os.path.join(path, "state"), exist_ok=True)
    return path


def path(*parts):
    return os.path.join(root(), *parts)


def load(name, default):
    p = path(name)
    if not os.path.exists(p):
        return default
    with open(p) as f:
        return json.load(f)


def save(name, data):
    p = path(name)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load("config.json", {}))
    if cfg.get("mode") not in ("paper", "live"):
        raise Fail("config.json: mode must be \"paper\" or \"live\"")
    return cfg


def now(pinned=None):
    if pinned:
        return datetime.datetime.fromisoformat(pinned).replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def today(pinned=None):
    return now(pinned).date().isoformat()


def chain(name):
    key = (name or "").lower()
    if key not in CHAINS:
        raise Fail("unknown chain %r; use one of: %s" % (name, ", ".join(CHAINS)))
    return key, CHAINS[key]


def is_addr(s):
    return bool(s and ADDR_RE.match(s))


def resolve_token(chain_key, token):
    """'native', a known symbol, or a 0x address -> (symbol_or_None, address_or_'native')."""
    if not token:
        raise Fail("token missing")
    up = token.upper()
    if up in ("NATIVE", CHAINS[chain_key]["native"]):
        return CHAINS[chain_key]["native"], NATIVE
    if up in KNOWN.get(chain_key, {}):
        return up, KNOWN[chain_key][up]
    if is_addr(token):
        return None, token
    raise Fail("%r is not a known token on %s. Pass its contract address (0x...), found with "
               "market.py search and checked with market.py token. A ticker alone is not "
               "enough: anyone can deploy a token called %s." % (token, chain_key, up))


def http_json(url, data=None, timeout=15):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise Fail("HTTP %s from %s" % (e.code, url.split("?")[0]))
    except (urllib.error.URLError, TimeoutError) as e:
        raise Fail("could not reach %s (%s)" % (url.split("/")[2], e))


def rpc(chain_key, method, params):
    out = http_json(CHAINS[chain_key]["rpc"], {"jsonrpc": "2.0", "id": 1, "method": method,
                                               "params": params})
    if "error" in out:
        raise Fail("RPC %s: %s" % (method, out["error"].get("message")))
    return out.get("result")


def eth_call(chain_key, to, data):
    return rpc(chain_key, "eth_call", [{"to": to, "data": data}, "latest"])


def decode_string(hexdata):
    raw = bytes.fromhex(hexdata[2:]) if hexdata and hexdata != "0x" else b""
    if len(raw) >= 64:
        n = int.from_bytes(raw[32:64], "big")
        return raw[64:64 + n].decode(errors="replace")
    return raw.rstrip(b"\0").decode(errors="replace")


def erc20_meta(chain_key, addr):
    dec = int(eth_call(chain_key, addr, "0x313ce567") or "0x0", 16)  # decimals()
    sym = decode_string(eth_call(chain_key, addr, "0x95d89b41"))     # symbol()
    return sym, dec


def erc20_balance(chain_key, token, holder):
    data = "0x70a08231" + holder[2:].lower().rjust(64, "0")          # balanceOf(holder)
    return int(eth_call(chain_key, token, data) or "0x0", 16)


def prices_usd(keys):
    """keys like 'coingecko:ethereum' or 'base:0x...'. Returns {key: price}."""
    if not keys:
        return {}
    out = http_json("https://coins.llama.fi/prices/current/" + ",".join(keys))
    return {k: v.get("price") for k, v in out.get("coins", {}).items()}


def token_price(chain_key, addr):
    if addr == NATIVE:
        key = "coingecko:" + CHAINS[chain_key]["native_cg"]
    else:
        key = "%s:%s" % (chain_key, addr)
    p = prices_usd([key]).get(key)
    if p is None and addr != NATIVE:
        # Fall back to the deepest DexScreener pair for this token on this chain.
        pairs = http_json("https://api.dexscreener.com/latest/dex/tokens/" + addr).get("pairs") or []
        pairs = [x for x in pairs if x.get("chainId") == CHAINS[chain_key]["dexscreener"]
                 and x.get("baseToken", {}).get("address", "").lower() == addr.lower()]
        pairs.sort(key=lambda x: (x.get("liquidity") or {}).get("usd") or 0, reverse=True)
        if pairs and pairs[0].get("priceUsd"):
            p = float(pairs[0]["priceUsd"])
    if p is None:
        raise Fail("no price found for %s on %s" % (addr, chain_key))
    return float(p)


def gas_cost_usd(chain_key, units=None):
    gwei_wei = int(rpc(chain_key, "eth_gasPrice", []), 16)
    units = units or CHAINS[chain_key]["gas_units_swap"]
    native = token_price(chain_key, NATIVE)
    return gwei_wei * units / 1e18 * native, gwei_wei / 1e9


def fingerprint(order):
    keys = ("chain", "side", "sell_token", "buy_token", "amount_in", "usd", "stop_price",
            "target_price", "slippage_pct")
    blob = json.dumps({k: order.get(k) for k in keys}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def money(x):
    if 0 < abs(x) < 0.01:
        return "$%.4f" % x
    return "$%s" % format(round(x, 2), ",.2f")


def price_str(x):
    if x is None:
        return "?"
    if x >= 1:
        return "%.4f" % x
    return "%.10f" % x if x < 0.0001 else "%.6f" % x
