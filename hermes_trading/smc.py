"""Deterministic Smart Money Concepts analysis over closed OHLCV candles."""

from __future__ import annotations

from statistics import median
from typing import Any, Mapping, Sequence

Candle = Mapping[str, Any]


def _number(candle: Candle, key: str) -> float:
    return float(candle[key])


def detect_swings(
    candles: Sequence[Candle], *, pivot_length: int = 2
) -> dict[str, list[dict[str, float | int]]]:
    """Return confirmed pivot highs/lows without using unclosed future bars."""
    if pivot_length < 1:
        raise ValueError("pivot_length must be at least 1")
    if len(candles) < pivot_length * 2 + 1:
        return {"highs": [], "lows": []}

    highs: list[dict[str, float | int]] = []
    lows: list[dict[str, float | int]] = []
    for index in range(pivot_length, len(candles) - pivot_length):
        high = _number(candles[index], "high")
        low = _number(candles[index], "low")
        neighbours = [
            candles[position]
            for position in range(index - pivot_length, index + pivot_length + 1)
            if position != index
        ]
        if all(high > _number(item, "high") for item in neighbours):
            highs.append({"index": index, "price": high})
        if all(low < _number(item, "low") for item in neighbours):
            lows.append({"index": index, "price": low})
    return {"highs": highs, "lows": lows}


def detect_fair_value_gaps(candles: Sequence[Candle]) -> list[dict[str, Any]]:
    """Detect three-candle ICT-style fair value gaps."""
    gaps: list[dict[str, Any]] = []
    for index in range(2, len(candles)):
        first = candles[index - 2]
        third = candles[index]
        first_high = _number(first, "high")
        first_low = _number(first, "low")
        third_high = _number(third, "high")
        third_low = _number(third, "low")
        if third_low > first_high:
            gaps.append(
                {
                    "index": index,
                    "direction": "bullish",
                    "lower": first_high,
                    "upper": third_low,
                }
            )
        elif third_high < first_low:
            gaps.append(
                {
                    "index": index,
                    "direction": "bearish",
                    "lower": third_high,
                    "upper": first_low,
                }
            )
    return gaps


def find_bullish_order_block(candles: Sequence[Candle]) -> dict[str, Any] | None:
    """Find the latest bearish candle preceding bullish displacement and a break."""
    if len(candles) < 4:
        return None
    bodies = [abs(_number(c, "close") - _number(c, "open")) for c in candles]
    baseline = max(median(bodies), 1e-12)
    latest: dict[str, Any] | None = None
    for index in range(3, len(candles)):
        candle = candles[index]
        body = _number(candle, "close") - _number(candle, "open")
        prior_high = max(_number(item, "high") for item in candles[max(0, index - 3) : index])
        if body < baseline * 1.5 or _number(candle, "close") <= prior_high:
            continue
        for block_index in range(index - 1, max(-1, index - 5), -1):
            candidate = candles[block_index]
            if _number(candidate, "close") < _number(candidate, "open"):
                latest = {
                    "index": block_index,
                    "low": _number(candidate, "low"),
                    "high": _number(candidate, "open"),
                    "displacement_index": index,
                }
                break
    return latest


def _aggregate(candles: Sequence[Candle], factor: int) -> list[dict[str, float]]:
    aggregated: list[dict[str, float]] = []
    for start in range(0, len(candles), factor):
        group = candles[start : start + factor]
        if len(group) != factor:
            continue
        aggregated.append(
            {
                "open": _number(group[0], "open"),
                "high": max(_number(item, "high") for item in group),
                "low": min(_number(item, "low") for item in group),
                "close": _number(group[-1], "close"),
            }
        )
    return aggregated


def _trend_bias(candles: Sequence[Candle]) -> str:
    if len(candles) < 3:
        return "neutral"
    closes = [_number(candle, "close") for candle in candles]
    period = min(8, len(closes))
    alpha = 2.0 / (period + 1.0)
    ema = closes[0]
    for close in closes[1:]:
        ema = close * alpha + ema * (1.0 - alpha)
    if closes[-1] > ema and closes[-1] > closes[max(0, len(closes) - period)]:
        return "bullish"
    if closes[-1] < ema and closes[-1] < closes[max(0, len(closes) - period)]:
        return "bearish"
    return "neutral"


def trend_bias(candles: Sequence[Candle]) -> str:
    """Public trend direction for a candle series: bullish / bearish / neutral."""
    return _trend_bias(candles)


def _market_structure(swings: dict[str, list[dict[str, float | int]]]) -> str:
    highs = swings["highs"]
    lows = swings["lows"]
    if len(highs) < 2 or len(lows) < 2:
        return "neutral"
    higher_high = float(highs[-1]["price"]) > float(highs[-2]["price"])
    higher_low = float(lows[-1]["price"]) > float(lows[-2]["price"])
    lower_high = float(highs[-1]["price"]) < float(highs[-2]["price"])
    lower_low = float(lows[-1]["price"]) < float(lows[-2]["price"])
    if higher_high and higher_low:
        return "bullish"
    if lower_high and lower_low:
        return "bearish"
    return "neutral"


def analyze_smc(
    candles: Sequence[Candle],
    htf_candles: Mapping[str, Sequence[Candle]] | None = None,
) -> dict[str, Any]:
    """Build a non-repainting SMC confluence model for a spot long setup."""
    if len(candles) < 20:
        raise ValueError("Full SMC analysis requires at least 20 closed candles")

    current = candles[-1]
    previous = candles[-2]
    confirmed = candles[:-1]
    swings = detect_swings(confirmed, pivot_length=2)
    structure_before = _market_structure(swings)
    last_swing_high = swings["highs"][-1] if swings["highs"] else None
    last_swing_low = swings["lows"][-1] if swings["lows"] else None

    bos_bullish = bool(
        last_swing_high
        and _number(current, "close") > float(last_swing_high["price"])
        and _number(previous, "close") <= float(last_swing_high["price"])
    )
    choch_bullish = bool(bos_bullish and structure_before == "bearish")
    sell_side_sweep = bool(
        last_swing_low
        and _number(current, "low") < float(last_swing_low["price"])
        and _number(current, "close") > float(last_swing_low["price"])
    )

    order_block = find_bullish_order_block(confirmed)
    order_block_mitigated = bool(
        order_block
        and int(order_block["index"]) < len(candles) - 1
        and _number(current, "low") <= float(order_block["high"])
        and _number(current, "close") >= (float(order_block["low"]) + float(order_block["high"])) / 2.0
    )

    bullish_gaps = [
        gap for gap in detect_fair_value_gaps(confirmed) if gap["direction"] == "bullish"
    ]
    active_gap: dict[str, Any] | None = None
    for gap in reversed(bullish_gaps):
        filled_before_current = any(
            _number(candle, "low") <= float(gap["lower"])
            for candle in confirmed[int(gap["index"]) + 1 :]
        )
        if not filled_before_current:
            active_gap = gap
            break
    fvg_mitigated = bool(
        active_gap
        and _number(current, "low") <= float(active_gap["upper"])
        and _number(current, "close") >= float(active_gap["lower"])
    )

    if last_swing_high and last_swing_low:
        range_high = float(last_swing_high["price"])
        range_low = float(last_swing_low["price"])
        if range_low > range_high:
            range_low, range_high = range_high, range_low
    else:
        recent = candles[-20:]
        range_high = max(_number(candle, "high") for candle in recent)
        range_low = min(_number(candle, "low") for candle in recent)
    equilibrium = (range_high + range_low) / 2.0
    in_discount_zone = _number(current, "close") <= equilibrium

    # Multi-timeframe bias: prefer real HTF candles when provided; otherwise
    # aggregate the entry-timeframe series (1h = 4x, 4h = 16x, 1d = 96x 15m).
    if htf_candles:
        def _htf_bias(key: str) -> str:
            rows = htf_candles.get(key) or []
            return _trend_bias(rows) if len(rows) >= 3 else "neutral"

        bias_1h = _htf_bias("1h")
        bias_4h = _htf_bias("4h")
        bias_1d = _htf_bias("1d")
    else:
        bias_1h = _trend_bias(_aggregate(candles, 4))
        bias_4h = _trend_bias(_aggregate(candles, 16))
        bias_1d = _trend_bias(_aggregate(candles, 96))
    biases = {bias_1h, bias_4h, bias_1d}
    if biases == {"bullish"}:
        mtf_bias = "bullish"
    elif biases == {"bearish"}:
        mtf_bias = "bearish"
    elif "bearish" in biases and "bullish" not in biases:
        mtf_bias = "bearish"
    elif "bullish" in biases and "bearish" not in biases:
        mtf_bias = "bullish"
    else:
        mtf_bias = "neutral"

    structure_trigger = bos_bullish or choch_bullish or sell_side_sweep
    point_of_interest = order_block_mitigated or fvg_mitigated
    confluence_count = sum(
        int(value)
        for value in (
            bos_bullish,
            choch_bullish,
            sell_side_sweep,
            order_block_mitigated,
            fvg_mitigated,
            in_discount_zone,
            mtf_bias == "bullish",
        )
    )
    long_setup = bool(
        structure_trigger
        and point_of_interest
        and in_discount_zone
        and mtf_bias != "bearish"
    )

    return {
        "market_structure": structure_before,
        "last_swing_high": last_swing_high,
        "last_swing_low": last_swing_low,
        "bos_bullish": bos_bullish,
        "choch_bullish": choch_bullish,
        "sell_side_liquidity_sweep": sell_side_sweep,
        "bullish_order_block": order_block,
        "order_block_mitigated": order_block_mitigated,
        "bullish_fvg": active_gap,
        "fvg_mitigated": fvg_mitigated,
        "dealing_range_high": range_high,
        "dealing_range_low": range_low,
        "equilibrium": equilibrium,
        "in_discount_zone": in_discount_zone,
        "bias_1h": bias_1h,
        "bias_4h": bias_4h,
        "bias_1d": bias_1d,
        "mtf_bias": mtf_bias,
        "smc_confluence_count": confluence_count,
        "long_setup": long_setup,
    }
