import json

import pytest
import yaml

import hermes_trading.loop as loop
from hermes_trading.loop import CircuitBreaker, run_watchlist_cycle


def _candles(confirmed: bool, strong: bool = False) -> list[dict]:
    candles = []
    price = 120.0
    for index in range(15):
        candles.append(
            {
                "open_time_ms": index * 60_000,
                "open": price,
                "high": price + 0.5,
                "low": price - 1.5,
                "close": price - 1.0,
                "volume": 100.0,
                "close_time_ms": index * 60_000 + 59_999,
                "trade_count": 10,
            }
        )
        price -= 1.0
    if confirmed:
        candles.append(
            {
                "open_time_ms": 900_000,
                "open": 105.0 if strong else 106.0,
                "high": 107.5,
                "low": 100.0,
                "close": 107.0 if strong else 104.0,
                "volume": 150.0,
                "close_time_ms": 959_999,
                "trade_count": 20,
            }
        )
    else:
        candles.append(
            {
                "open_time_ms": 900_000,
                "open": 105.0,
                "high": 105.5,
                "low": 103.5,
                "close": 104.0,
                "volume": 100.0,
                "close_time_ms": 959_999,
                "trade_count": 10,
            }
        )
    return candles


def _make_price_fetcher(payloads: dict[str, list[dict]]):
    """Create a price fetcher that returns multi-TF compatible structure."""
    def make_price_fetcher(asset: str):
        async def fetch():
            candles = payloads[asset]
            # Return multi-TF structure compatible with new fetch_price_multi
            if asset == "BTC/USDT":
                # BTC needs 3m candles for gate
                return {
                    "schema_version": 1,
                    "source": "binance",
                    "asset": asset,
                    "timeframe": "3m",
                    "last_price": candles[-1]["close"],
                    "candles": candles,
                }
            return {
                "schema_version": 1,
                "source": "binance",
                "asset": asset,
                "timeframe": "15m",
                "last_price": candles[-1]["close"],
                "candles": candles,
            }

        return fetch
    return make_price_fetcher


async def _context_fetch():
    return {"schema_version": 1, "source": "fixture"}


@pytest.mark.asyncio
async def test_watchlist_cycle_opens_only_strongest_signal(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    goal = {
        "initial_balance_usd": 10.0,
        "target_return_30d": 0.3478489153,
        "max_drawdown": 0.08,
        "min_sharpe": 1.5,
        "failure_below": -0.04,
        "market_type": "spot",
        "mode": "paper",
    }
    strategy = {
        "version": "01",
        "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"},
        "stop_loss_pct": 2.0,
        "position_size_r": 0.5,
        "risk_reward": 2.0,
    }
    (state_dir / "goal.yaml").write_text(yaml.safe_dump(goal), encoding="utf-8")
    (state_dir / "strategy.yaml").write_text(yaml.safe_dump(strategy), encoding="utf-8")

    payloads = {
        "SOL/USDT": _candles(True, strong=True),
        "ETH/USDT": _candles(True, strong=False),
        "BTC/USDT": _candles(False),
    }

    def fake_evaluate_entry(candles, strategy, htf_candles=None, btc_trend_3m=None):
        last = candles[-1]
        if last["close"] == 107.0:
            return {"enter": True, "signals": {"confluence_count": 7, "rsi": 20.0}}
        if last["high"] == 107.5:
            return {"enter": True, "signals": {"confluence_count": 5, "rsi": 24.0}}
        return {"enter": False, "signals": {"confluence_count": 0, "rsi": 30.0}}

    monkeypatch.setattr(loop, "evaluate_entry", fake_evaluate_entry)

    result = await run_watchlist_cycle(
        assets=list(payloads),
        state_dir=state_dir,
        breaker=CircuitBreaker(),
        price_fetchers={asset: _make_price_fetcher(payloads)(asset) for asset in payloads},
        context_fetchers={
            "onchain": _context_fetch,
            "news": _context_fetch,
            "macro": _context_fetch,
        },
    )

    paper = json.loads((state_dir / "paper_state.json").read_text(encoding="utf-8"))
    heartbeat = json.loads((state_dir / "heartbeat.json").read_text(encoding="utf-8"))
    assert paper["position"]["asset"] == "SOL/USDT"
    assert result["selected_asset"] == "SOL/USDT"
    assert heartbeat["scanned_assets"] == 3
    assert heartbeat["selected_asset"] == "SOL/USDT"


@pytest.mark.asyncio
async def test_watchlist_cycle_blocks_entries_when_btc_gate_is_bearish(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    goal = {
        "initial_balance_usd": 10.0,
        "market_type": "spot",
        "mode": "paper",
    }
    strategy = {
        "version": "04",
        "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"},
        "stop_loss_pct": 2.0,
        "position_size_r": 0.5,
        "risk_reward": 2.0,
    }
    (state_dir / "goal.yaml").write_text(yaml.safe_dump(goal), encoding="utf-8")
    (state_dir / "strategy.yaml").write_text(yaml.safe_dump(strategy), encoding="utf-8")

    payloads = {
        "SOL/USDT": _candles(True, strong=True),
        "BTC/USDT": _candles(False),
    }

    def fake_evaluate_entry(candles, strategy, htf_candles=None, btc_trend_3m=None):
        if btc_trend_3m == "bearish":
            return {"enter": False, "signals": {"confluence_count": 0, "rsi": 30.0}}
        return {"enter": True, "signals": {"confluence_count": 7, "rsi": 20.0}}

    monkeypatch.setattr(loop, "evaluate_entry", fake_evaluate_entry)

    result = await run_watchlist_cycle(
        assets=list(payloads),
        state_dir=state_dir,
        breaker=CircuitBreaker(),
        price_fetchers={asset: _make_price_fetcher(payloads)(asset) for asset in payloads},
        context_fetchers={
            "onchain": _context_fetch,
            "news": _context_fetch,
            "macro": _context_fetch,
        },
    )

    paper = json.loads((state_dir / "paper_state.json").read_text(encoding="utf-8"))
    assert paper["position"] is None
    assert result["heartbeat"]["eligible_assets"] == []