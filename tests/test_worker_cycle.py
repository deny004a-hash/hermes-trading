import json

import pytest
import yaml

from hermes_trading.loop import CircuitBreaker, create_paper_position, run_cycle


@pytest.mark.asyncio
async def test_cycle_closes_ambiguous_candle_at_stop_and_writes_heartbeat(tmp_path):
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
    position = create_paper_position(
        asset="SOL/USDT",
        price=100.0,
        equity=10.0,
        strategy=strategy,
        opened_at="2026-08-28T09:00:00+00:00",
        signals={"rsi": 28.0},
    )
    (state_dir / "paper_state.json").write_text(
        json.dumps({"schema_version": 1, "equity": 10.0, "position": position}),
        encoding="utf-8",
    )

    candles = []
    for index in range(16):
        candles.append(
            {
                "open_time_ms": index * 60_000,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 100.0,
                "close_time_ms": index * 60_000 + 59_999,
                "trade_count": 10,
            }
        )
    candles[-1]["high"] = 105.0
    candles[-1]["low"] = 97.0

    async def price_fetch():
        return {
            "schema_version": 1,
            "source": "binance",
            "asset": "SOL/USDT",
            "last_price": 100.0,
            "candles": candles,
        }

    async def context_fetch():
        return {"schema_version": 1, "source": "fixture"}

    result = await run_cycle(
        asset="SOL/USDT",
        state_dir=state_dir,
        breaker=CircuitBreaker(),
        adapter_fetchers={
            "price": price_fetch,
            "onchain": context_fetch,
            "news": context_fetch,
            "macro": context_fetch,
        },
    )

    trade = json.loads((state_dir / "trades.jsonl").read_text(encoding="utf-8"))
    heartbeat = json.loads((state_dir / "heartbeat.json").read_text(encoding="utf-8"))
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_price"] == 98.0
    assert result["closed_trade"]["trade_id"] == trade["trade_id"]
    assert heartbeat["status"] == "ok"
    assert heartbeat["open_position"] is False


@pytest.mark.asyncio
async def test_run_cycle_forwards_btc_gate_into_entry_decision(tmp_path, monkeypatch):
    import hermes_trading.loop as loop_mod

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

    candles = [
        {
            "open_time_ms": index * 60_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
            "close_time_ms": index * 60_000 + 59_999,
            "trade_count": 10,
        }
        for index in range(16)
    ]

    async def price_fetch():
        return {
            "schema_version": 1,
            "source": "binance",
            "asset": "SOL/USDT",
            "last_price": 100.0,
            "candles": candles,
        }

    async def context_fetch():
        return {"schema_version": 1, "source": "fixture"}

    seen = {}

    def fake_evaluate_entry(candles_arg, strategy_arg, htf_candles=None, btc_trend_3m=None):
        seen["btc_trend_3m"] = btc_trend_3m
        return {
            "enter": btc_trend_3m == "bullish",
            "signals": {"rsi": 20.0, "btc_trend_3m": btc_trend_3m},
        }

    monkeypatch.setattr(loop_mod, "evaluate_entry", fake_evaluate_entry)

    fetchers = {
        "price": price_fetch,
        "onchain": context_fetch,
        "news": context_fetch,
        "macro": context_fetch,
    }

    blocked = await run_cycle(
        asset="SOL/USDT",
        state_dir=state_dir,
        breaker=CircuitBreaker(),
        adapter_fetchers=fetchers,
        btc_trend_3m="bearish",
    )
    paper = json.loads((state_dir / "paper_state.json").read_text(encoding="utf-8"))
    assert seen["btc_trend_3m"] == "bearish"
    assert paper["position"] is None
    assert blocked["heartbeat"]["open_position"] is False

    allowed = await run_cycle(
        asset="SOL/USDT",
        state_dir=state_dir,
        breaker=CircuitBreaker(),
        adapter_fetchers=fetchers,
        btc_trend_3m="bullish",
    )
    paper = json.loads((state_dir / "paper_state.json").read_text(encoding="utf-8"))
    assert paper["position"] is not None
    assert allowed["opened_position"] is not None
