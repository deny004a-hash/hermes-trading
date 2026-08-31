"""Binance Global public spot market-data adapter."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Sequence

import httpx

from . import SCHEMA_VERSION, SchemaError

BINANCE_API_BASE = "https://api.binance.com"


def _symbol(asset: str) -> str:
    parts = asset.upper().split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Expected a CCXT asset such as SOL/USDT, got {asset!r}")
    return "".join(parts)


def parse_klines(rows: Any, asset: str, timeframe: str | None = None) -> dict[str, Any]:
    """Convert Binance's positional kline rows into a stable named schema."""
    if not isinstance(rows, list) or not rows:
        raise SchemaError("Binance klines payload must be a non-empty list")

    candles: list[dict[str, Any]] = []
    try:
        for row in rows:
            if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) < 12:
                raise SchemaError("Binance kline row must contain at least 12 fields")
            candles.append(
                {
                    "open_time_ms": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                    "close_time_ms": int(row[6]),
                    "trade_count": int(row[8]),
                }
            )
    except SchemaError:
        raise
    except (TypeError, ValueError, IndexError) as exc:
        raise SchemaError(f"Malformed Binance kline payload: {exc}") from exc

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "binance",
        "exchange_region": "global",
        "asset": asset.upper(),
        "timeframe": timeframe or os.getenv("HERMES_PRICE_TIMEFRAME", "15m"),
        "last_price": candles[-1]["close"],
        "candles": candles,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


async def fetch(
    asset: str | None = None,
    client: httpx.AsyncClient | None = None,
    timeframe: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch live Binance Global spot klines; no API key is required."""
    chosen_asset = asset or os.getenv("HERMES_ASSET", "SOL/USDT")
    base_url = os.getenv("BINANCE_API_BASE", BINANCE_API_BASE).rstrip("/")
    chosen_timeframe = timeframe or os.getenv("HERMES_PRICE_TIMEFRAME", "15m")
    params = {
        "symbol": _symbol(chosen_asset),
        "interval": chosen_timeframe,
        "limit": int(limit or os.getenv("HERMES_PRICE_LIMIT", "100")),
    }

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        response = await http.get(f"{base_url}/api/v3/klines", params=params)
        response.raise_for_status()
        return parse_klines(response.json(), chosen_asset, chosen_timeframe)
    finally:
        if owns_client:
            await http.aclose()


async def fetch_klines_multi(
    asset: str,
    client: httpx.AsyncClient | None = None,
    timeframes: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch multiple timeframes for a single asset in parallel.
    
    Returns a single payload with schema_version containing all timeframes
    under the 'timeframes' key.
    """
    if timeframes is None:
        timeframes = ["3m", "15m", "1h", "4h", "1d"]
    base_url = os.getenv("BINANCE_API_BASE", BINANCE_API_BASE).rstrip("/")
    base_limit = int(limit or os.getenv("HERMES_PRICE_LIMIT", "100"))
    # Ensure enough 3m candles for 1d aggregate (480)
    limits = {tf: max(base_limit, 480) if tf == "3m" else base_limit for tf in timeframes}

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)

    async def fetch_one(tf: str) -> tuple[str, dict[str, Any]]:
        params = {
            "symbol": _symbol(asset),
            "interval": tf,
            "limit": limits[tf],
        }
        response = await http.get(f"{base_url}/api/v3/klines", params=params)
        response.raise_for_status()
        return tf, parse_klines(response.json(), asset, tf)

    try:
        results = await asyncio.gather(*(fetch_one(tf) for tf in timeframes))
        timeframes_data = dict(results)
        # Use 15m as the primary timeframe for the top-level payload
        primary = timeframes_data.get("15m", list(timeframes_data.values())[0])
        return {
            "schema_version": SCHEMA_VERSION,
            "source": "binance",
            "exchange_region": "global",
            "asset": asset.upper(),
            "timeframe": "multi",
            "last_price": primary["last_price"],
            "candles": primary["candles"],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "timeframes": timeframes_data,  # all timeframes keyed by interval
        }
    finally:
        if owns_client:
            await http.aclose()