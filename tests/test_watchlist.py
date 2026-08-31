from pathlib import Path

import yaml


def test_watchlist_is_loaded_unless_cli_asset_overrides(tmp_path):
    from hermes_trading.run import resolve_assets

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "watchlist.yaml").write_text(
        yaml.safe_dump({"assets": ["SOL/USDT", "BTC/USDT", "ETH/USDT"]}),
        encoding="utf-8",
    )
    goal = {"asset": "SOL/USDT", "watchlist_file": "watchlist.yaml"}

    assert resolve_assets(goal, None, state_dir) == [
        "SOL/USDT",
        "BTC/USDT",
        "ETH/USDT",
    ]
    assert resolve_assets(goal, "XRP/USDT", state_dir) == ["XRP/USDT"]


def test_best_signal_uses_confluence_then_lower_rsi():
    from hermes_trading.loop import choose_best_asset

    decisions = {
        "SOL/USDT": {"enter": True, "signals": {"confluence_count": 2, "rsi": 18.0}},
        "BTC/USDT": {"enter": True, "signals": {"confluence_count": 3, "rsi": 29.0}},
        "ETH/USDT": {"enter": True, "signals": {"confluence_count": 3, "rsi": 22.0}},
        "XRP/USDT": {"enter": False, "signals": {"confluence_count": 4, "rsi": 10.0}},
    }

    assert choose_best_asset(decisions) == "ETH/USDT"
