"""Reliable async worker loop and paper-trading engine."""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from typing import Sequence
from uuid import uuid4

import yaml

from .adapters import SchemaError, require_schema
from .smc import analyze_smc
from .smc import trend_bias as _smc_trend_bias
from .smc import _trend_bias as _smc_trend_bias_internal
from .smc import _aggregate as _smc_aggregate


def btc_trend_gate(candles_3m: Sequence[Mapping[str, Any]]) -> str:
    """Return gate decision from multi-timeframe BTC trend.
    
    Logic: if 3m is bearish but higher timeframes (15m, 1h, 4h, 1d) are 
    predominantly bullish, treat as pullback → return 'bullish' (gate open).
    Only return 'bearish' (gate closed) when majority of HTFs are also bearish.
    """
    if len(candles_3m) < 480:
        # Not enough data for 1d aggregate; fall back to single TF
        return _smc_trend_bias(candles_3m)
    
    # Aggregate 3m base candles to higher timeframes
    bias_3m = _smc_trend_bias_internal(candles_3m)
    bias_15m = _smc_trend_bias_internal(_smc_aggregate(candles_3m, 5))   # 3m * 5 = 15m
    bias_1h  = _smc_trend_bias_internal(_smc_aggregate(candles_3m, 20))  # 3m * 20 = 1h
    bias_4h  = _smc_trend_bias_internal(_smc_aggregate(candles_3m, 80))  # 3m * 80 = 4h
    bias_1d  = _smc_trend_bias_internal(_smc_aggregate(candles_3m, 480)) # 3m * 480 = 1d
    
    # Weight: 3m=1, 15m=2, 1h=3, 4h=4, 1d=5 (higher TFs get more weight)
    bullish_score = 0
    bearish_score = 0
    for bias, weight in [
        (bias_3m, 1), (bias_15m, 2), (bias_1h, 3), (bias_4h, 4), (bias_1d, 5)
    ]:
        if bias == "bullish":
            bullish_score += weight
        elif bias == "bearish":
            bearish_score += weight
    
    # Gate open if bullish score >= bearish score (pullback tolerance)
    return "bullish" if bullish_score >= bearish_score else "bearish"


class CircuitOpenError(RuntimeError):
    """Raised after an adapter reaches its consecutive-failure threshold."""


class CircuitBreaker:
    def __init__(self, threshold: int = 5) -> None:
        if threshold < 1:
            raise ValueError("threshold must be at least 1")
        self.threshold = threshold
        self.failures: defaultdict[str, int] = defaultdict(int)

    def record_success(self, name: str) -> None:
        self.failures[name] = 0

    def record_failure(self, name: str) -> None:
        self.failures[name] += 1
        if self.failures[name] >= self.threshold:
            raise CircuitOpenError(
                f"Adapter {name!r} circuit opened after {self.failures[name]} failures"
            )


async def fetch_with_retry(
    name: str,
    fetcher: Callable[[], Awaitable[Mapping[str, Any]]],
    breaker: CircuitBreaker,
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> Mapping[str, Any]:
    """Fetch an adapter with bounded exponential retries and schema validation."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            payload = await fetcher()
            require_schema(payload)
            breaker.record_success(name)
            return payload
        except SchemaError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await sleep(base_delay * (2**attempt))

    breaker.record_failure(name)
    assert last_error is not None
    raise last_error


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def calculate_rsi(closes: list[float], period: int = 14) -> float:
    """Calculate a simple RSI over the latest closed candles."""
    if len(closes) < period + 1:
        raise ValueError(f"RSI({period}) requires at least {period + 1} closes")
    deltas = [new - old for old, new in zip(closes[-period - 1 : -1], closes[-period:])]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def evaluate_entry(
   candles: list[Mapping[str, Any]],
   strategy: Mapping[str, Any],
   htf_candles: Mapping[str, list[Mapping[str, Any]]] | None = None,
   btc_trend_3m: str | None = None,
) -> dict[str, Any]:
    """Require full SMC confluence and bullish price action for entry."""
    if len(candles) < 20:
        raise ValueError("Full entry analysis requires at least 20 closed candles")
    entry = strategy["entry"]
    if entry.get("direction") != "long":
        raise ValueError("Spot worker supports long direction only")
    # Indicator check removed: strategy now supports any indicator via entry.indicator,
    # but for this worker we only require SMC + price action + trend filters.
    # RSI is no longer a mandatory signal.

    current, previous = candles[-1], candles[-2]
    bullish_engulfing = (
        float(current["close"]) > float(current["open"])
        and float(current["open"]) <= float(previous["close"])
        and float(current["close"]) >= float(previous["open"])
    )
    body = abs(float(current["close"]) - float(current["open"]))
    lower_wick = min(float(current["open"]), float(current["close"])) - float(
        current["low"]
    )
    bullish_rejection = bool(
        float(current["close"]) > float(current["open"])
        and lower_wick >= max(body * 1.5, 1e-12)
    )
    bullish_price_action = bullish_engulfing or bullish_rejection
    smc = analyze_smc(candles, htf_candles)
    # threshold variable kept for compatibility but unused; RSI threshold removed
    threshold = float(entry.get("threshold", 0))

    btc_gate_ok = btc_trend_3m != "bearish"
    entry_tf_trend = smc["mtf_bias"] != "bearish"
    signals = {
        # "rsi": float(rsi),  # removed
        # "rsi_threshold": threshold,  # removed
        "bullish_price_action": bullish_price_action,
        "bullish_engulfing": bullish_engulfing,
        "bullish_rejection": bullish_rejection,
        "smc_long_setup": bool(smc["long_setup"]),
        "market_structure": smc["market_structure"],
        "bos_bullish": bool(smc["bos_bullish"]),
        "choch_bullish": bool(smc["choch_bullish"]),
        "liquidity_sweep": bool(smc["sell_side_liquidity_sweep"]),
        "order_block_mitigated": bool(smc["order_block_mitigated"]),
        "fvg_mitigated": bool(smc["fvg_mitigated"]),
        "in_discount_zone": bool(smc["in_discount_zone"]),
        "mtf_bias": smc["mtf_bias"],
        "bias_1h": smc["bias_1h"],
        "bias_4h": smc["bias_4h"],
        "bias_1d": smc["bias_1d"],
        "btc_trend_3m": btc_trend_3m,
        "btc_gate_ok": btc_gate_ok,
        "entry_tf_trend": entry_tf_trend,
        "smc_confluence_count": int(smc["smc_confluence_count"]),
        "confluence_count": int(bullish_price_action)
        + int(smc["smc_confluence_count"]),
    }
    return {
        "enter": bool(
            btc_gate_ok
            and entry_tf_trend
            # and rsi <= threshold  # removed
            and bullish_price_action
            and bool(smc["long_setup"])
        ),
        "signals": signals,
    }


def create_paper_position(
    *,
    asset: str,
    price: float,
    equity: float,
    strategy: Mapping[str, Any],
    opened_at: str | None = None,
    signals: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a long-only spot paper position using percentage risk sizing."""
    if price <= 0 or equity <= 0:
        raise ValueError("price and equity must be positive")

    stop_pct = float(strategy["stop_loss_pct"]) / 100.0
    risk_fraction = float(strategy["position_size_r"]) / 100.0
    reward_to_risk = float(strategy.get("risk_reward", 2.0))
    if stop_pct <= 0 or risk_fraction <= 0 or reward_to_risk < 1.0:
        raise ValueError("risk settings must be positive and reward/risk must be >= 1")

    risk_quote = equity * risk_fraction
    stop_distance = price * stop_pct
    quantity = min(risk_quote / stop_distance, equity / price)

    return {
        "schema_version": 1,
        "position_id": str(uuid4()),
        "asset": asset,
        "market_type": "spot",
        "mode": "paper",
        "side": "long",
        "opened_at": opened_at or utc_now(),
        "entry_price": float(price),
        "stop_loss": float(price * (1.0 - stop_pct)),
        "take_profit": float(price * (1.0 + stop_pct * reward_to_risk)),
        "quantity": float(quantity),
        "risk_quote": float(risk_quote),
        "strategy_version": str(strategy.get("version", "01")),
        "signals": dict(signals or {}),
    }


def settle_paper_position(
    position: Mapping[str, Any],
    *,
    exit_price: float,
    exit_reason: str,
    closed_at: str | None,
    equity_before: float,
    fee_rate: float = 0.001,
) -> dict[str, Any]:
    """Settle a long paper position, including fees on both sides."""
    entry_price = float(position["entry_price"])
    quantity = float(position["quantity"])
    gross_pnl = (float(exit_price) - entry_price) * quantity
    entry_fee = entry_price * quantity * fee_rate
    exit_fee = float(exit_price) * quantity * fee_rate
    fees = entry_fee + exit_fee
    net_pnl = gross_pnl - fees
    equity_after = equity_before + net_pnl

    return {
        **dict(position),
        "trade_id": str(position["position_id"]),
        "closed_at": closed_at or utc_now(),
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "gross_pnl_quote": float(gross_pnl),
        "fees_quote": float(fees),
        "net_pnl_quote": float(net_pnl),
        "return_pct": float(net_pnl / equity_before),
        "equity_before": float(equity_before),
        "equity_after": float(equity_after),
    }


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")


def _paper_state(state_dir: Path, goal: Mapping[str, Any]) -> dict[str, Any]:
    path = state_dir / "paper_state.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise SchemaError("paper_state.json schema mismatch")
        return value
    return {
        "schema_version": 1,
        "equity": float(goal.get("initial_balance_usd", 10.0)),
        "position": None,
        "closed_trades": 0,
    }


def _default_adapter_fetchers(
    asset: str,
) -> dict[str, Callable[[], Awaitable[Mapping[str, Any]]]]:
    from .adapters import macro, news, onchain, price

    return {
        "price": lambda: price.fetch(asset),
        "onchain": onchain.fetch,
        "news": news.fetch,
        "macro": macro.fetch,
    }


def choose_best_asset(decisions: Mapping[str, Mapping[str, Any]]) -> str | None:
    """Pick an enterable asset by confluence, then lower RSI, then symbol."""
    candidates = [
        (asset, decision)
        for asset, decision in decisions.items()
        if bool(decision.get("enter"))
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            -int(item[1].get("signals", {}).get("confluence_count", 0)),
            float(item[1].get("signals", {}).get("rsi", 100.0)),
            item[0],
        )
    )
    return candidates[0][0]


async def run_cycle(
    *,
    asset: str,
    state_dir: str | Path,
    breaker: CircuitBreaker,
    adapter_fetchers: Mapping[
        str, Callable[[], Awaitable[Mapping[str, Any]]]
    ]
    | None = None,
    btc_trend_3m: str | None = None,
) -> dict[str, Any]:
    """Run one complete data, decision, paper-execution and heartbeat cycle."""
    state = Path(state_dir)
    goal = _read_yaml(state / "goal.yaml")
    strategy = _read_yaml(state / "strategy.yaml")
    if goal.get("market_type") != "spot" or goal.get("mode") != "paper":
        raise RuntimeError("This worker cycle is locked to spot paper mode")

    fetchers = dict(adapter_fetchers or _default_adapter_fetchers(asset))
    expected = {"price", "onchain", "news", "macro"}
    if set(fetchers) != expected:
        raise ValueError(f"adapter_fetchers must contain exactly {sorted(expected)}")

    names = list(fetchers)
    payloads = await asyncio.gather(
        *(
            fetch_with_retry(name, fetchers[name], breaker, attempts=3)
            for name in names
        )
    )
    data = dict(zip(names, payloads))
    price_data = data["price"]
    candles_value = price_data.get("candles")
    if not isinstance(candles_value, list) or len(candles_value) < 16:
        raise SchemaError("Price adapter must provide at least 16 candles")
    candles = candles_value
    latest = candles[-1]

    paper = _paper_state(state, goal)
    equity = float(paper["equity"])
    position = paper.get("position")
    closed_trade: dict[str, Any] | None = None
    opened_position: dict[str, Any] | None = None
    decision: dict[str, Any] = {"enter": False, "signals": {}}

    if isinstance(position, Mapping):
        stop = float(position["stop_loss"])
        target = float(position["take_profit"])
        if float(latest["low"]) <= stop:
            closed_trade = settle_paper_position(
                position,
                exit_price=stop,
                exit_reason="stop_loss",
                closed_at=utc_now(),
                equity_before=equity,
                fee_rate=float(os.getenv("PAPER_FEE_RATE", "0.001")),
            )
        elif float(latest["high"]) >= target:
            closed_trade = settle_paper_position(
                position,
                exit_price=target,
                exit_reason="take_profit",
                closed_at=utc_now(),
                equity_before=equity,
                fee_rate=float(os.getenv("PAPER_FEE_RATE", "0.001")),
            )

        if closed_trade is not None:
            equity = float(closed_trade["equity_after"])
            paper["position"] = None
            paper["closed_trades"] = int(paper.get("closed_trades", 0)) + 1
            _append_jsonl(state / "trades.jsonl", closed_trade)

    if paper.get("position") is None and closed_trade is None:
        decision = evaluate_entry(candles, strategy, btc_trend_3m=btc_trend_3m)
        if decision["enter"]:
            opened_position = create_paper_position(
                asset=asset,
                price=float(latest["close"]),
                equity=equity,
                strategy=strategy,
                opened_at=utc_now(),
                signals=decision["signals"],
            )
            paper["position"] = opened_position

    paper["equity"] = equity
    paper["updated_at"] = utc_now()
    _write_json_atomic(state / "paper_state.json", paper)

    heartbeat = {
        "schema_version": 1,
        "timestamp": utc_now(),
        "status": "ok",
        "asset": asset,
        "exchange": "binance",
        "exchange_region": "global",
        "market_type": "spot",
        "mode": "paper",
        "strategy_version": str(strategy.get("version", "01")),
        "equity": equity,
        "last_price": float(price_data.get("last_price", latest["close"])),
        "open_position": paper.get("position") is not None,
        "closed_trades": int(paper.get("closed_trades", 0)),
        "decision": decision,
        "adapter_sources": {
            name: str(payload.get("source", "unknown"))
            for name, payload in data.items()
        },
    }
    _write_json_atomic(state / "heartbeat.json", heartbeat)
    return {
        "heartbeat": heartbeat,
        "closed_trade": closed_trade,
        "opened_position": opened_position,
    }


async def run_watchlist_cycle(
    *,
    assets: list[str],
    state_dir: str | Path,
    breaker: CircuitBreaker,
    price_fetchers: Mapping[
        str, Callable[[], Awaitable[Mapping[str, Any]]]
    ]
    | None = None,
    context_fetchers: Mapping[
        str, Callable[[], Awaitable[Mapping[str, Any]]]
    ]
    | None = None,
) -> dict[str, Any]:
    """Scan every watchlist asset and process only the strongest valid signal."""
    if not assets:
        raise ValueError("assets must not be empty")
    if len(set(assets)) != len(assets):
        raise ValueError("assets must not contain duplicates")

    from .adapters import macro, news, onchain, price

    entry_tf = os.getenv("HERMES_PRICE_TIMEFRAME", "15m")

    prices = dict(
        price_fetchers
        or {asset: (lambda asset=asset: price.fetch(asset)) for asset in assets}
    )
    if set(prices) != set(assets):
        raise ValueError("price_fetchers must contain exactly one fetcher per asset")
    contexts = dict(
        context_fetchers
        or {"onchain": onchain.fetch, "news": news.fetch, "macro": macro.fetch}
    )
    if set(contexts) != {"onchain", "news", "macro"}:
        raise ValueError("context_fetchers must contain onchain, news and macro")

    semaphore = asyncio.Semaphore(
        max(1, int(os.getenv("HERMES_MAX_PARALLEL_PRICE_FETCHES", "8")))
    )

    async def fetch_price_multi(asset: str) -> Mapping[str, Any]:
        """Fetch multi-TF klines for an asset and return entry TF (15m) candles + HTF map."""
        async with semaphore:
            if price_fetchers is None:
                # Production: use fetch_klines_multi
                multi_payload = await fetch_with_retry(
                    f"price:{asset}:multi",
                    lambda: price.fetch_klines_multi(asset),
                    breaker,
                    attempts=3,
                )
                # New format: single payload with 'timeframes' key containing all TFs
                timeframes_data = multi_payload.get("timeframes", {})
                if not timeframes_data:
                    # Fallback: use the payload itself as 15m
                    timeframes_data = {"15m": multi_payload}
            else:
                # Tests: use injected single-TF fetcher (backward compat)
                single = await fetch_with_retry(
                    f"price:{asset}", prices[asset], breaker, attempts=3
                )
                timeframes_data = {single.get("timeframe", "15m"): single}
            
            # Extract entry timeframe candles (15m default) for evaluate_entry
            entry_candles = timeframes_data.get(entry_tf, {}).get("candles", [])
            if not entry_candles:
                # Fallback to any available timeframe
                for tf_data in timeframes_data.values():
                    if tf_data.get("candles"):
                        entry_candles = tf_data["candles"]
                        break
            
            # Get last price from entry TF or first available
            last_price = timeframes_data.get(entry_tf, {}).get("last_price")
            if last_price is None:
                for tf_data in timeframes_data.values():
                    if tf_data.get("last_price") is not None:
                        last_price = tf_data["last_price"]
                        break
            if last_price is None and entry_candles:
                last_price = entry_candles[-1]["close"]
            elif last_price is None:
                last_price = 0
            
            return {
                "asset": asset,
                "entry_candles": entry_candles,
                "htf_candles": timeframes_data,  # all timeframes for SMC MTF analysis
                "last_price": last_price,
            }

    price_multi_results = await asyncio.gather(
        *(fetch_price_multi(asset) for asset in assets)
    )
    price_multi = {r["asset"]: r for r in price_multi_results}
    
    # Also fetch context data
    context_values = await asyncio.gather(
        *(
            fetch_with_retry(name, contexts[name], breaker, attempts=3)
            for name in ("onchain", "news", "macro")
        )
    )
    context_data = dict(zip(("onchain", "news", "macro"), context_values))

    # BTC master gate: multi-timeframe BTC trend
    btc_multi = price_multi.get("BTC/USDT", {})
    btc_gate_candles = btc_multi.get("htf_candles", {}).get("3m", {}).get("candles", [])
    btc_gate = (
        btc_trend_gate(btc_gate_candles)
        if btc_gate_candles
        else None
    )

    state = Path(state_dir)
    goal = _read_yaml(state / "goal.yaml")
    strategy = _read_yaml(state / "strategy.yaml")
    decisions: dict[str, dict[str, Any]] = {}
    for asset, multi_data in price_multi.items():
        entry_candles = multi_data.get("entry_candles", [])
        htf_candles_raw = multi_data.get("htf_candles", {})
        # Extract only candle lists for analyze_smc
        htf_candles = {}
        for tf, tf_data in htf_candles_raw.items():
            if isinstance(tf_data, dict) and "candles" in tf_data:
                htf_candles[tf] = tf_data["candles"]
            elif isinstance(tf_data, list):
                htf_candles[tf] = tf_data
        if not isinstance(entry_candles, list) or len(entry_candles) < 16:
            raise SchemaError(f"Price adapter for {asset} must provide 16+ entry candles")
        decisions[asset] = evaluate_entry(
            entry_candles, strategy, htf_candles=htf_candles, btc_trend_3m=btc_gate
        )

    paper = _paper_state(state, goal)
    position = paper.get("position")
    delisted_trade: dict[str, Any] | None = None
    if isinstance(position, Mapping):
        selected_asset = str(position["asset"])
        if selected_asset not in price_multi:
            # The position's asset left the watchlist (e.g. sharia delisting):
            # settle it at the latest market price and continue scanning.
            delisted_payload = await fetch_with_retry(
                f"price:{selected_asset}",
                lambda selected_asset=selected_asset: price.fetch(selected_asset),
                breaker,
                attempts=3,
            )
            delisted_candles = delisted_payload.get("candles")
            if not isinstance(delisted_candles, list) or not delisted_candles:
                raise SchemaError(
                    f"Price adapter for delisted {selected_asset} returned no candles"
                )
            delisted_trade = settle_paper_position(
                position,
                exit_price=float(delisted_candles[-1]["close"]),
                exit_reason="removed_from_watchlist",
                closed_at=utc_now(),
                equity_before=float(paper["equity"]),
                fee_rate=float(os.getenv("PAPER_FEE_RATE", "0.001")),
            )
            paper["equity"] = float(delisted_trade["equity_after"])
            paper["position"] = None
            paper["closed_trades"] = int(paper.get("closed_trades", 0)) + 1
            _append_jsonl(state / "trades.jsonl", delisted_trade)
            _write_json_atomic(state / "paper_state.json", paper)
            selected_asset = choose_best_asset(decisions) or assets[0]
    else:
        selected_asset = choose_best_asset(decisions) or assets[0]

    def cached(payload: Mapping[str, Any]) -> Callable[[], Awaitable[Mapping[str, Any]]]:
        async def fetch_cached() -> Mapping[str, Any]:
            return payload

        return fetch_cached

    selected_multi = price_multi[selected_asset]
    # Build a proper price payload with schema_version for run_cycle
    entry_tf = os.getenv("HERMES_PRICE_TIMEFRAME", "15m")
    price_payload = {
        "schema_version": 1,
        "source": "binance",
        "asset": selected_asset,
        "timeframe": entry_tf,
        "last_price": selected_multi["last_price"],
        "candles": selected_multi["entry_candles"],
    }
    selected_fetchers = {
        "price": cached(price_payload),
        **{name: cached(payload) for name, payload in context_data.items()},
    }
    result = await run_cycle(
        asset=selected_asset,
        state_dir=state,
        breaker=breaker,
        adapter_fetchers=selected_fetchers,
        btc_trend_3m=btc_gate,
    )
    if delisted_trade is not None and result.get("closed_trade") is None:
        result["closed_trade"] = delisted_trade
    eligible_assets = [asset for asset, decision in decisions.items() if decision["enter"]]
    heartbeat = result["heartbeat"]
    heartbeat.update(
        {
            "scanned_assets": len(assets),
            "eligible_assets": eligible_assets,
            "selected_asset": selected_asset,
            "max_concurrent_positions": 1,
        }
    )
    _write_json_atomic(state / "heartbeat.json", heartbeat)
    result.update(
        {
            "selected_asset": selected_asset,
            "decisions": decisions,
            "heartbeat": heartbeat,
        }
    )
    return result


async def run_loop(
    *,
    assets: list[str],
    state_dir: str | Path,
    interval_seconds: float = 60.0,
    once: bool = False,
) -> None:
    """Run forever, halting only on schema drift or an opened circuit."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if not assets:
        raise ValueError("assets must not be empty")
    os.environ["HERMES_ASSET"] = assets[0]
    breaker = CircuitBreaker(threshold=5)
    state = Path(state_dir)
    print(
        json.dumps(
            {
                "event": "boot",
                "message": "Booting hermes-trading worker",
                "primary_asset": assets[0],
                "watchlist_size": len(assets),
                "mode": "paper",
            }
        ),
        flush=True,
    )

    while True:
        started = asyncio.get_running_loop().time()
        try:
            result = await run_watchlist_cycle(
                assets=assets, state_dir=state, breaker=breaker
            )
            print(json.dumps({"event": "cycle", **result["heartbeat"]}), flush=True)
            if result["opened_position"] is not None:
                print(
                    json.dumps(
                        {"event": "paper_position_opened", **result["opened_position"]}
                    ),
                    flush=True,
                )
            if result["closed_trade"] is not None:
                print(
                    json.dumps(
                        {"event": "paper_trade_closed", **result["closed_trade"]}
                    ),
                    flush=True,
                )
        except (SchemaError, CircuitOpenError) as exc:
            heartbeat = {
                "schema_version": 1,
                "timestamp": utc_now(),
                "status": "fatal",
                "primary_asset": assets[0],
                "watchlist_size": len(assets),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            _write_json_atomic(state / "heartbeat.json", heartbeat)
            print(json.dumps({"event": "fatal", **heartbeat}), flush=True)
            raise
        except Exception as exc:
            heartbeat = {
                "schema_version": 1,
                "timestamp": utc_now(),
                "status": "degraded",
                "primary_asset": assets[0],
                "watchlist_size": len(assets),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            _write_json_atomic(state / "heartbeat.json", heartbeat)
            print(json.dumps({"event": "cycle_error", **heartbeat}), flush=True)
            if once:
                raise

        if once:
            return
        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.0, interval_seconds - elapsed))