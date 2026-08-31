"""Build the halal-only watchlist: ShariaQuant HALAL verdicts x Binance USDT spot pairs.

Screening source: shariaquant.com/halalcrypto (fetched 2026-08-29).
Policy: include ONLY coins with a clear HALAL verdict. Every DOUBTFUL or
HARAM verdict is excluded. Coins where independent sources disagree
(e.g. privacy coins, meme coins) are treated as doubtful and excluded.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS_FILE = ROOT / "state" / "binance_usdt_symbols.json"
WATCHLIST = ROOT / "state" / "watchlist.yaml"

# Existing watchlist members verified HALAL by the screening source.
KEEP_EXISTING = [
    "RVN", "BCH", "GRT", "FET", "ZEN", "RUNE", "NEAR", "ADA", "ALGO",
    "XRP", "AXS", "SOL", "LINK", "QTUM", "RAY", "AVAX", "BTC", "EGLD",
    "ETH", "LTC", "STX", "BAT", "TRX", "MINA",
]

# BNB is DOUBTFUL in the screening, but an open paper position still
# references it; the worker requires the position asset to stay on the
# watchlist. Remove it once that position closes.
TEMPORARY = ["BNB"]

# New additions: clear HALAL verdict, real utility, not in a disputed
# category (privacy / meme / gambling / interest-lending / derivatives).
NEW_ADDITIONS = [
    "SUI", "APT", "SEI", "TIA", "ICP", "OP", "ARB", "STRK", "ZK", "POL",
    "KAS", "FLR", "XDC", "TAO", "RENDER", "FIL", "AKT", "ATH", "IMX",
    "ZRO", "DYM", "MAV", "ONE", "VIRTUAL", "QNT", "WLD", "PI", "TON",
    "UNI", "AERO", "XLM", "XTZ", "DOT", "HBAR", "VET",
]


def main() -> None:
    lines = SYMBOLS_FILE.read_text(encoding="utf-8").splitlines()
    binance_pairs = set(json.loads(lines[1]))

    final: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for base in KEEP_EXISTING + TEMPORARY + NEW_ADDITIONS:
        if base in seen:
            continue
        seen.add(base)
        if base in binance_pairs:
            final.append(base)
        else:
            missing.append(base)

    content = [
        "# Halal-screened watchlist (shariaquant.com verdicts, fetched 2026-08-29)",
        "# Policy: HALAL verdicts only; doubtful/haram assets are excluded.",
        "# BNB stays ONLY until the open paper position closes (screening: doubtful).",
        "version: 2",
        "source: halal_screened_shariaquant",
        "exchange: binance",
        "exchange_region: global",
        "quote_asset: USDT",
        "selection: strongest_valid_signal",
        "max_concurrent_positions: 1",
        "assets:",
    ]
    content += [f"  - {base}/USDT" for base in final]
    WATCHLIST.write_text("\n".join(content) + "\n", encoding="utf-8")
    print(f"watchlist_assets={len(final)}")
    print(f"missing_from_binance={missing}")


if __name__ == "__main__":
    main()
