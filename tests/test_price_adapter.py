import pytest

from hermes_trading.adapters import SchemaError
from hermes_trading.adapters.price import parse_klines


def test_binance_klines_are_parsed_into_versioned_ohlcv():
    rows = [
        [
            1_700_000_000_000,
            "10.0",
            "11.0",
            "9.5",
            "10.5",
            "123.4",
            1_700_000_059_999,
            "0",
            42,
            "0",
            "0",
            "0",
        ]
    ]

    result = parse_klines(rows, "SOL/USDT")

    assert result["schema_version"] == 1
    assert result["source"] == "binance"
    assert result["asset"] == "SOL/USDT"
    assert result["last_price"] == 10.5
    assert result["candles"][0]["volume"] == 123.4


def test_malformed_binance_kline_raises_schema_error():
    with pytest.raises(SchemaError):
        parse_klines([[1, "10.0"]], "SOL/USDT")
