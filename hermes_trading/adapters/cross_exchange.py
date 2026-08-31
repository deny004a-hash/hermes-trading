"""Cross-exchange public price fetcher for live paper-trading arbitrage.

Uses only PUBLIC endpoints — no API key required. Pulls ticker prices
from Binance, Bybit, OKX, KuCoin simultaneously and returns a normalized
price book that the arbitrage engine compares.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping

import httpx

from . import SCHEMA_VERSION, SchemaError

BINANCE = "https://api.binance.com"
BYBIT = "https://api.bybit.com"
OKX = "https://api.okx.com"
KUCOIN = "https://api.kucoin.com"

EXCHANGES = {
    "binance": BINANCE,
    "bybit": BYBIT,
    "okx": OKX,
    "kucoin": KUCOIN,
}

# Spot withdrawal + taker fee (BNB-discounted where applicable)
# Source: each exchange's published fee schedule (mid-2025).
TAKER_FEE = {
    "binance": 0.001,      # 0.10% (regular), 0.075% with BNB
    "bybit": 0.001,
    "okx": 0.001,
    "kucoin": 0.001,
}

# Rough cross-exchange withdrawal fee (BTC: 0.0001 BTC = ~0.00125% at $80k)
# We store as percent of trade notional.
WITHDRAWAL_FEE_PCT = {
    "binance": 0.0005,
    "bybit": 0.0005,
    "okx": 0.0005,
    "kucoin": 0.0005,
}


async def _fetch_binance_tickers(client: httpx.AsyncClient) -> dict[str, float]:
    """Fetch all USDT spot tickers from Binance."""
    try:
        r = await client.get(f"{BINANCE}/api/v3/ticker/price", timeout=10.0)
        r.raise_for_status()
        data = r.json()
        return {row["symbol"]: float(row["price"]) for row in data if row["symbol"].endswith("USDT")}
    except Exception:
        return {}


async def _fetch_bybit_tickers(client: httpx.AsyncClient) -> dict[str, float]:
    try:
        r = await client.get(f"{BYBIT}/v5/market/tickers", params={"category": "spot"}, timeout=10.0)
        r.raise_for_status()
        data = r.json().get("result", {}).get("list", [])
        return {row["symbol"]: float(row["lastPrice"]) for row in data if row["symbol"].endswith("USDT")}
    except Exception:
        return {}


async def _fetch_okx_tickers(client: httpx.AsyncClient) -> dict[str, float]:
    try:
        r = await client.get(f"{OKX}/api/v5/market/tickers", params={"instType": "SPOT"}, timeout=10.0)
        r.raise_for_status()
        data = r.json().get("data", [])
        out = {}
        for row in data:
            inst = row.get("instId", "")
            if inst.endswith("-USDT"):
                sym = inst.replace("-", "").replace("USDT", "USDT")
                out[sym] = float(row["last"])
        return out
    except Exception:
        return {}


async def _fetch_kucoin_tickers(client: httpx.AsyncClient) -> dict[str, float]:
    try:
        r = await client.get(f"{KUCOIN}/api/v1/market/allTickers", timeout=10.0)
        r.raise_for_status()
        data = r.json().get("data", {}).get("ticker", [])
        return {row["symbol"]: float(row["last"]) for row in data if row["symbol"].endswith("-USDT")}
    except Exception:
        return {}


async def fetch_cross_exchange_prices() -> dict[str, Any]:
    """Fetch USDT-spot prices from all exchanges in parallel (no API key needed)."""
    async with httpx.AsyncClient() as client:
        binance_t, bybit_t, okx_t, kucoin_t = await asyncio.gather(
            _fetch_binance_tickers(client),
            _fetch_bybit_tickers(client),
            _fetch_okx_tickers(client),
            _fetch_kucoin_tickers(client),
            return_exceptions=True,
        )

    prices = {
        "binance": binance_t if isinstance(binance_t, dict) else {},
        "bybit":   bybit_t   if isinstance(bybit_t, dict)   else {},
        "okx":     okx_t     if isinstance(okx_t, dict)     else {},
        "kucoin":  kucoin_t  if isinstance(kucoin_t, dict)  else {},
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "binance+bybit+okx+kucoin",
        "fetched_at": time.time(),
        "prices": prices,
    }


def find_spatial_arbitrage(
    book: Mapping[str, Any],
    *,
    min_spread_pct: float = 0.3,
    notional_usdt: float = 100.0,
) -> list[dict[str, Any]]:
    """Find spatial arb opportunities (cross-exchange).

    For each symbol quoted on >=2 exchanges, find:
      - buy_exchange: lowest ask
      - sell_exchange: highest bid
      - spread: (sell - buy) / buy * 100

    Returns opportunities with NET profit = spread - 2*taker_fee - withdrawal_fee
    Only includes opportunities with net > 0.
    """
    if not isinstance(book, Mapping):
        raise SchemaError("price book must be a mapping")
    if book.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError("schema_version mismatch")
    prices = book.get("prices", {})
    symbols = set()
    for ex_prices in prices.values():
        symbols.update(ex_prices.keys())

    opportunities = []
    for sym in symbols:
        quotes = {ex: p[sym] for ex, p in prices.items() if sym in p and p[sym] > 0}
        if len(quotes) < 2:
            continue
        sorted_quotes = sorted(quotes.items(), key=lambda kv: kv[1])
        buy_ex, buy_px = sorted_quotes[0]
        sell_ex, sell_px = sorted_quotes[-1]
        if sell_ex == buy_ex:
            continue
        spread_pct = (sell_px - buy_px) / buy_px * 100.0

        # Cost: taker fee on each leg + withdrawal
        taker_cost = TAKER_FEE.get(buy_ex, 0.001) * 100 + TAKER_FEE.get(sell_ex, 0.001) * 100
        withdraw_cost = WITHDRAWAL_FEE_PCT.get(buy_ex, 0.0005) * 100
        net_pct = spread_pct - taker_cost - withdraw_cost
        if net_pct < 0:
            continue
        if spread_pct < min_spread_pct:
            continue

        qty = notional_usdt / buy_px
        net_profit_usdt = notional_usdt * (net_pct / 100.0)

        opportunities.append({
            "symbol": sym,
            "buy_exchange": buy_ex,
            "buy_price": buy_px,
            "sell_exchange": sell_ex,
            "sell_price": sell_px,
            "spread_pct": round(spread_pct, 4),
            "fees_pct": round(taker_cost + withdraw_cost, 4),
            "net_pct": round(net_pct, 4),
            "notional_usdt": notional_usdt,
            "net_profit_usdt": round(net_profit_usdt, 4),
            "all_quotes": quotes,
        })

    opportunities.sort(key=lambda o: o["net_pct"], reverse=True)
    return opportunities


# === TRIANGULAR ARBITRAGE (same-exchange, 3-pair cycle) ===

# 22 halal coin + USDC as bridge (USDT/USDC pair, stable-to-stable spreads are usually tiny)
HALAL_22 = {
    "ADA", "ALGO", "ARB", "AVAX", "BCH", "BTC", "DOT", "ETH", "FIL",
    "HBAR", "ICP", "LINK", "LTC", "NEAR", "POL", "QNT", "RENDER", "SOL",
    "SUI", "TRX", "XLM", "XRP",
}

# Triangular cycle structure: A -> B -> C -> A using [Coin/USDT, Coin/BTC, BTC/USDT] book.
# A cycle is profitable when:
#   qty * (A/B) * (B/C) * (C/A) > 1 + fees
# But each leg is a trade. A cleaner approach: for each coin X, build a triangular
# cycle X/USDT -> X/BTC -> BTC/USDT -> X/USDT.
#   leg1: sell X/USDT (get USDT)
#   leg2: buy BTC/USDT (spend USDT)
#   leg3: sell X/BTC (get BTC)  -- if X/BTC exists
# Net: USDT -> BTC -> (X/BTC) -> X -> USDT

def find_triangular_arbitrage(
    book: Mapping[str, Any],
    *,
    min_net_pct: float = 0.3,
    notional_usdt: float = 100.0,
) -> list[dict[str, Any]]:
    """Find triangular arb opportunities on a single exchange.

    For each exchange with sufficient book depth:
      - For each halal coin in HALAL_22 that has pairs vs USDT and vs BTC,
        build cycle: USDT -> BTC -> coin -> USDT
          leg1: buy BTC/USDT with USDT   (rate btc_usdt)
          leg2: buy coin/BTC with BTC    (rate coin_btc)
          leg3: sell coin/USDT for USDT  (rate coin_usdt)
        net = btc_usdt * coin_btc * coin_usdt  (per 1 USDT input)

    Also reverse: USDT -> coin -> BTC -> USDT
      leg1: buy coin/USDT
      leg2: sell coin/BTC for BTC
      leg3: sell BTC/USDT for USDT

    Net profit only if net > 1 + 3*taker_fee.
    """
    if not isinstance(book, Mapping):
        raise SchemaError("price book must be a mapping")
    if book.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError("schema_version mismatch")
    prices = book.get("prices", {})

    opportunities = []
    for ex, ex_prices in prices.items():
        if not ex_prices:
            continue
        fee = TAKER_FEE.get(ex, 0.001) * 3  # 3 legs
        btc_usdt = ex_prices.get("BTCUSDT")
        if not btc_usdt or btc_usdt <= 0:
            continue

        for coin in HALAL_22:
            sym_usdt = f"{coin}USDT"
            sym_btc = f"{coin}BTC"
            coin_usdt = ex_prices.get(sym_usdt)
            coin_btc = ex_prices.get(sym_btc)
            if not coin_usdt or not coin_btc or coin_usdt <= 0 or coin_btc <= 0:
                continue

            # Cycle 1: USDT -> BTC -> coin -> USDT
            # 1 USDT -> 1/btc_usdt BTC -> (1/btc_usdt)/coin_btc coin
            #        -> ((1/btc_usdt)/coin_btc) * coin_usdt USDT
            c1 = (1.0 / btc_usdt) / coin_btc * coin_usdt

            # Cycle 2: USDT -> coin -> BTC -> USDT
            # 1 USDT -> 1/coin_usdt coin -> (1/coin_usdt)*coin_btc BTC
            #        -> ((1/coin_usdt)*coin_btc) * btc_usdt USDT
            c2 = (1.0 / coin_usdt) * coin_btc * btc_usdt

            # Best cycle
            for cycle, label in [(c1, "usdt->btc->coin->usdt"), (c2, "usdt->coin->btc->usdt")]:
                gross_pct = (cycle - 1.0) * 100.0
                net_pct = gross_pct - fee * 100
                if net_pct < min_net_pct:
                    continue
                net_profit = notional_usdt * (net_pct / 100.0)
                opportunities.append({
                    "kind": "triangular",
                    "exchange": ex,
                    "coin": coin,
                    "cycle": label,
                    "btc_usdt": btc_usdt,
                    "coin_usdt": coin_usdt,
                    "coin_btc": coin_btc,
                    "gross_pct": round(gross_pct, 4),
                    "fees_pct": round(fee * 100, 4),
                    "net_pct": round(net_pct, 4),
                    "notional_usdt": notional_usdt,
                    "net_profit_usdt": round(net_profit, 4),
                })

    opportunities.sort(key=lambda o: o["net_pct"], reverse=True)
    return opportunities
