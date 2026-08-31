import pytest

from hermes_trading.loop import create_paper_position, settle_paper_position


def test_paper_position_uses_one_to_two_risk_reward():
    strategy = {"stop_loss_pct": 2.0, "position_size_r": 0.5, "risk_reward": 2.0}

    position = create_paper_position(
        asset="SOL/USDT",
        price=100.0,
        equity=10.0,
        strategy=strategy,
        opened_at="2026-08-28T10:00:00+00:00",
        signals={"rsi": 28.0},
    )

    assert position["side"] == "long"
    assert position["stop_loss"] == pytest.approx(98.0)
    assert position["take_profit"] == pytest.approx(104.0)
    assert position["quantity"] == pytest.approx(0.025)


def test_settled_paper_trade_deducts_entry_and_exit_fees():
    strategy = {"stop_loss_pct": 2.0, "position_size_r": 0.5, "risk_reward": 2.0}
    position = create_paper_position(
        asset="SOL/USDT",
        price=100.0,
        equity=10.0,
        strategy=strategy,
        opened_at="2026-08-28T10:00:00+00:00",
        signals={"rsi": 28.0},
    )

    trade = settle_paper_position(
        position,
        exit_price=104.0,
        exit_reason="take_profit",
        closed_at="2026-08-28T11:00:00+00:00",
        equity_before=10.0,
        fee_rate=0.001,
    )

    assert trade["gross_pnl_quote"] == pytest.approx(0.1)
    assert trade["fees_quote"] == pytest.approx(0.0051)
    assert trade["net_pnl_quote"] == pytest.approx(0.0949)
    assert trade["equity_after"] == pytest.approx(10.0949)
