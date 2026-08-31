"""Free global crypto-market regime adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from . import SCHEMA_VERSION, SchemaError


def parse_global_market(payload: Any) -> dict[str, Any]:
    try:
        data = payload["data"]
        result = {
            "schema_version": SCHEMA_VERSION,
            "source": "coingecko",
            "total_market_cap_usd": float(data["total_market_cap"]["usd"]),
            "btc_dominance_pct": float(data["market_cap_percentage"]["btc"]),
            "market_cap_change_24h_pct": float(
                data["market_cap_change_percentage_24h_usd"]
            ),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        return result
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaError(f"Malformed global market payload: {exc}") from exc


async def fetch(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Fetch global market-cap context from CoinGecko's public endpoint."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        response = await http.get("https://api.coingecko.com/api/v3/global")
        response.raise_for_status()
        return parse_global_market(response.json())
    finally:
        if owns_client:
            await http.aclose()
