from hermes_trading.smc import (
    analyze_smc,
    detect_fair_value_gaps,
    detect_swings,
    find_bullish_order_block,
)


def _candle(index, open_, high, low, close, volume=100.0):
    return {
        "open_time_ms": index * 60_000,
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
        "close_time_ms": index * 60_000 + 59_999,
        "trade_count": 10,
    }


def test_detect_swings_uses_confirmed_pivots():
    highs = [10, 11, 12, 16, 13, 12, 11, 12, 13]
    lows = [8, 9, 10, 11, 9, 7, 4, 8, 9]
    candles = [
        _candle(i, (high + low) / 2, high, low, (high + low) / 2)
        for i, (high, low) in enumerate(zip(highs, lows))
    ]

    swings = detect_swings(candles, pivot_length=2)

    assert swings["highs"][-1] == {"index": 3, "price": 16.0}
    assert swings["lows"][-1] == {"index": 6, "price": 4.0}


def test_detect_fair_value_gap_tracks_bullish_imbalance():
    candles = [
        _candle(0, 99, 101, 98, 100),
        _candle(1, 100, 104, 99, 103),
        _candle(2, 103, 106, 102, 105),
        _candle(3, 105, 107, 104, 106),
    ]

    gaps = detect_fair_value_gaps(candles)

    assert gaps[-1]["direction"] == "bullish"
    assert gaps[-1]["lower"] == 101.0
    assert gaps[-1]["upper"] == 102.0


def test_order_block_is_last_bearish_candle_before_displacement():
    candles = [
        _candle(0, 100, 101, 99, 100.5),
        _candle(1, 100.5, 101, 99.5, 100),
        _candle(2, 100, 100.5, 98, 98.5),
        _candle(3, 98.5, 104.5, 98.2, 104),
        _candle(4, 104, 106, 103.5, 105.5),
    ]

    block = find_bullish_order_block(candles)

    assert block is not None
    assert block["index"] == 2
    assert block["low"] == 98.0
    assert block["high"] == 100.0


def test_full_smc_analysis_exposes_institutional_components():
    candles = []
    price = 120.0
    for index in range(45):
        drift = -0.35 if index < 30 else 0.15
        close = price + drift
        candles.append(_candle(index, price, max(price, close) + 0.4, min(price, close) - 0.4, close))
        price = close

    # Bearish order block, displacement/FVG, then retrace and sell-side sweep.
    candles.extend(
        [
            _candle(45, price, price + 0.3, price - 1.2, price - 0.8),
            _candle(46, price - 0.8, price + 3.5, price - 0.9, price + 3.0),
            _candle(47, price + 3.0, price + 4.2, price + 2.6, price + 3.8),
            _candle(48, price + 3.8, price + 4.0, price + 0.2, price + 0.8),
            _candle(49, price + 0.8, price + 2.2, price - 1.4, price + 1.8),
        ]
    )

    analysis = analyze_smc(candles)

    assert set(
        [
            "bos_bullish",
            "choch_bullish",
            "sell_side_liquidity_sweep",
            "bullish_order_block",
            "bullish_fvg",
            "in_discount_zone",
            "mtf_bias",
            "smc_confluence_count",
            "long_setup",
        ]
    ).issubset(analysis)
    assert analysis["mtf_bias"] in {"bullish", "neutral", "bearish"}
    assert isinstance(analysis["smc_confluence_count"], int)
