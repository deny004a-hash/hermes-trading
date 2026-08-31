"""On-chain context adapter with a free Solana fallback."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from . import SCHEMA_VERSION, SchemaError


def parse_chain_tvl(payload: Any, chain: str) -> dict[str, Any]:
    if not isinstance(payload, list) or not payload:
        raise SchemaError("Chain TVL payload must be a non-empty list")
    try:
        latest = max(payload, key=lambda row: int(row["date"]))
        return {
            "schema_version": SCHEMA_VERSION,
            "source": "defillama",
            "chain": chain,
            "metric": "tvl_usd",
            "timestamp": int(latest["date"]),
            "tvl_usd": float(latest["tvl"]),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaError(f"Malformed chain TVL payload: {exc}") from exc


def _parse_glassnode(payload: Any, asset: str) -> dict[str, Any]:
    if not isinstance(payload, list) or not payload:
        raise SchemaError("Glassnode payload must be a non-empty list")
    try:
        latest = payload[-1]
        return {
            "schema_version": SCHEMA_VERSION,
            "source": "glassnode",
            "asset": asset,
            "metric": "market_cap_usd",
            "timestamp": int(latest["t"]),
            "value": float(latest["v"]),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaError(f"Malformed Glassnode payload: {exc}") from exc


async def fetch(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Fetch premium Glassnode data when configured, else free DefiLlama TVL."""
    asset = os.getenv("HERMES_ASSET", "SOL/USDT").split("/", 1)[0].upper()
    key = os.getenv("GLASSNODE_API_KEY", "").strip()
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        if key:
            response = await http.get(
                "https://api.glassnode.com/v1/metrics/market/marketcap_usd",
                params={"a": asset, "api_key": key, "i": "24h"},
            )
            response.raise_for_status()
            return _parse_glassnode(response.json(), asset)

        chain = os.getenv("HERMES_CHAIN", "Solana")
        response = await http.get(
            f"https://api.llama.fi/v2/historicalChainTvl/{chain}"
        )
        response.raise_for_status()
        return parse_chain_tvl(response.json(), chain)
    finally:
        if owns_client:
            await http.aclose()
