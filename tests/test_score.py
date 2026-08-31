from hermes_trading.score import score


def test_score_is_high_when_return_drawdown_and_sharpe_beat_goal():
    trades = [
        {"return_pct": 0.08},
        {"return_pct": 0.07},
        {"return_pct": 0.06},
        {"return_pct": 0.08},
        {"return_pct": 0.07},
    ]
    goal = {
        "target_return_30d": 0.3478489153,
        "max_drawdown": 0.08,
        "min_sharpe": 1.5,
        "failure_below": -0.04,
    }

    result = score(trades, goal)

    assert 0.8 <= result <= 1.0


def test_score_is_steeply_negative_below_failure_floor():
    trades = [{"return_pct": -0.05}]
    goal = {
        "target_return_30d": 0.3478489153,
        "max_drawdown": 0.08,
        "min_sharpe": 1.5,
        "failure_below": -0.04,
    }

    result = score(trades, goal)

    assert result <= -0.8
