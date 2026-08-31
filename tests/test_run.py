import yaml
import pytest

from hermes_trading.run import ensure_state, resolve_asset, validate_initial_mode


def test_asset_defaults_to_goal_and_cli_override_wins():
    goal = {"asset": "SOL/USDT"}

    assert resolve_asset(goal, None) == "SOL/USDT"
    assert resolve_asset(goal, "BTC/USDT") == "BTC/USDT"


def test_initial_worker_refuses_live_mode_even_if_risk_flag_is_true():
    with pytest.raises(RuntimeError, match="paper-only"):
        validate_initial_mode("live", "true")


def test_initial_worker_accepts_paper_mode_with_risk_flag_false():
    assert validate_initial_mode("paper", "false") == "paper"


def test_ensure_state_promotes_newer_seed_strategy_and_archives_current(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "state"
    seed_dir = tmp_path / "seed"
    state_dir.mkdir()
    seed_dir.mkdir()
    current = {"version": "02", "entry": {"threshold": 32}}
    newer = {"version": "03", "entry": {"threshold": 34}}
    (state_dir / "goal.yaml").write_text("asset: SOL/USDT\n", encoding="utf-8")
    (state_dir / "strategy.yaml").write_text(
        yaml.safe_dump(current, sort_keys=False), encoding="utf-8"
    )
    (seed_dir / "strategy.yaml").write_text(
        yaml.safe_dump(newer, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_STATE_SEED_DIR", str(seed_dir))

    ensure_state(state_dir)

    promoted = yaml.safe_load((state_dir / "strategy.yaml").read_text(encoding="utf-8"))
    archived = yaml.safe_load(
        (state_dir / "history" / "v0002.yaml").read_text(encoding="utf-8")
    )
    assert promoted == newer
    assert archived == current
