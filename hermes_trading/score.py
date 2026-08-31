"""Trade scoring against the operator's numeric goal."""

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Mapping


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def trade_metrics(trades: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """Return compounded realised return, max drawdown and trade Sharpe."""
    returns = [float(trade.get("return_pct", 0.0)) for trade in trades]
    if not returns:
        return {"realised_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for result in returns:
        equity *= 1.0 + result
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    realised_return = equity - 1.0
    if len(returns) < 2:
        sharpe = 0.0
    else:
        volatility = statistics.stdev(returns)
        sharpe = (
            statistics.mean(returns) / volatility * math.sqrt(len(returns))
            if volatility > 0
            else (10.0 if statistics.mean(returns) > 0 else 0.0)
        )

    return {
        "realised_return": realised_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
    }


def score(trades: Iterable[Mapping[str, Any]], goal: Mapping[str, Any]) -> float:
    """Score closed trades on return, drawdown and Sharpe in ``[-1, 1]``."""
    trade_list = list(trades)
    if not trade_list:
        return 0.0

    metrics = trade_metrics(trade_list)
    failure_floor = float(goal.get("failure_below", -0.04))
    if metrics["realised_return"] < failure_floor:
        depth = (failure_floor - metrics["realised_return"]) / max(
            abs(failure_floor), 1e-12
        )
        return float(_clip(-0.8 - 0.2 * depth))

    target = max(float(goal["target_return_30d"]), 1e-12)
    max_drawdown = max(float(goal["max_drawdown"]), 1e-12)
    min_sharpe = max(float(goal["min_sharpe"]), 1e-12)

    return_component = _clip(metrics["realised_return"] / target)
    drawdown_component = _clip(1.0 - 2.0 * metrics["max_drawdown"] / max_drawdown)
    sharpe_component = _clip(metrics["sharpe"] / min_sharpe)

    composite = (
        0.50 * return_component
        + 0.30 * drawdown_component
        + 0.20 * sharpe_component
    )
    return float(_clip(composite))
