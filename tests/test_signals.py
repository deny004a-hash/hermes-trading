import hermes_trading.loop as loop
from hermes_trading.loop import evaluate_entry


def _candle(index: int, open_: float, high: float, low: float, close: float) -> dict:
    return {
        "open_time_ms": index * 60_000,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100.0,
        "close_time_ms": index * 60_000 + 59_999,
        "trade_count": 10,
    }


def _oversold_with_bullish_engulfing() -> list[dict]:
    candles = []
    price = 140.0
    for index in range(29):
        candles.append(_candle(index, price, price + 0.4, price - 1.4, price - 1.0))
        price -= 1.0
    candles.append(_candle(29, price - 0.5, price + 2.0, price - 1.0, price + 1.5))
    return candles


def _smc(long_setup: bool) -> dict:
    return {
        "market_structure": "bullish",
        "last_swing_high": {"index": 20, "price": 125.0},
        "last_swing_low": {"index": 25, "price": 111.0},
        "bos_bullish": True,
        "choch_bullish": False,
        "sell_side_liquidity_sweep": True,
        "bullish_order_block": {"index": 24, "low": 110.0, "high": 112.0},
        "order_block_mitigated": True,
        "bullish_fvg": {"index": 26, "lower": 111.0, "upper": 112.0},
        "fvg_mitigated": False,
        "dealing_range_high": 125.0,
        "dealing_range_low": 110.0,
        "equilibrium": 117.5,
        "in_discount_zone": True,
        "bias_1h": "bullish",
        "bias_4h": "bullish",
        "bias_1d": "neutral",
        "mtf_bias": "bullish",
        "smc_confluence_count": 5,
        "long_setup": long_setup,
    }


def test_entry_requires_full_smc_and_bullish_price_action(monkeypatch):
    monkeypatch.setattr(loop, "analyze_smc", lambda candles, htf=None: _smc(True))

    decision = evaluate_entry(
        _oversold_with_bullish_engulfing(),
        {"entry": {"indicator": "none", "threshold": 0, "direction": "long"}},
    )

    assert decision["enter"] is True
    # RSI removed from strategy
    assert decision["signals"]["bullish_price_action"] is True
    assert decision["signals"]["smc_long_setup"] is True
    assert decision["signals"]["order_block_mitigated"] is True
    assert decision["signals"]["mtf_bias"] == "bullish"


def test_entry_is_rejected_when_full_smc_setup_is_incomplete(monkeypatch):
    monkeypatch.setattr(loop, "analyze_smc", lambda candles, htf=None: _smc(False))

    decision = evaluate_entry(
        _oversold_with_bullish_engulfing(),
        {"entry": {"indicator": "none", "threshold": 0, "direction": "long"}},
    )

    assert decision["signals"]["bullish_price_action"] is True
    assert decision["signals"]["smc_long_setup"] is False
    assert decision["enter"] is False