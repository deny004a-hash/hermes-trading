import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from hermes_trading.reflect import reflect_fallback, reflect_hermes


BASE_STRATEGY = {
    "version": "01",
    "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"},
    "stop_loss_pct": 2.0,
    "position_size_r": 0.5,
    "risk_reward": 2.0,
}
BASE_GOAL = {
    "target_return_30d": 0.3478489153,
    "max_drawdown": 0.08,
    "min_sharpe": 1.5,
    "failure_below": -0.04,
    "one_variable_only": True,
}


def _write_state(state_dir: Path, trades: list[dict]) -> None:
    state_dir.mkdir()
    (state_dir / "strategy.yaml").write_text(
        yaml.safe_dump(BASE_STRATEGY, sort_keys=False), encoding="utf-8"
    )
    (state_dir / "goal.yaml").write_text(
        yaml.safe_dump(BASE_GOAL, sort_keys=False), encoding="utf-8"
    )
    (state_dir / "trades.jsonl").write_text(
        "".join(json.dumps(trade) + "\n" for trade in trades), encoding="utf-8"
    )


def test_fallback_loosens_only_entry_threshold_when_return_misses_target(tmp_path):
    state_dir = tmp_path / "state"
    _write_state(state_dir, [{"return_pct": 0.01}])

    hypothesis = reflect_fallback(state_dir)

    updated = yaml.safe_load((state_dir / "strategy.yaml").read_text(encoding="utf-8"))
    archived = yaml.safe_load(
        (state_dir / "history" / "v0001.yaml").read_text(encoding="utf-8")
    )
    logged = json.loads(
        (state_dir / "hypotheses.jsonl").read_text(encoding="utf-8").strip()
    )
    assert updated["version"] == "02"
    assert updated["entry"]["threshold"] == 32
    assert updated["stop_loss_pct"] == 2.0
    assert archived == BASE_STRATEGY
    assert hypothesis["variable"] == "entry.threshold"
    assert logged["variable"] == "entry.threshold"


def test_fallback_prioritizes_drawdown_and_changes_only_stop_loss(tmp_path):
    state_dir = tmp_path / "state"
    _write_state(state_dir, [{"return_pct": 0.05}, {"return_pct": -0.20}])

    hypothesis = reflect_fallback(state_dir)

    updated = yaml.safe_load((state_dir / "strategy.yaml").read_text(encoding="utf-8"))
    assert updated["version"] == "02"
    assert updated["entry"]["threshold"] == 30
    assert updated["stop_loss_pct"] == 1.8
    assert hypothesis["variable"] == "stop_loss_pct"


def test_hermes_mode_applies_one_whitelisted_variable_from_subprocess(tmp_path):
    state_dir = tmp_path / "state"
    _write_state(state_dir, [{"return_pct": 0.01}])
    captured_command = []

    def fake_runner(command, **kwargs):
        captured_command.extend(command)
        return SimpleNamespace(
            stdout=(
                'Analysis complete. {"variable":"position_size_r",'
                '"new_value":0.4,"reason":"Reduce risk while learning",'
                '"prediction":"Drawdown should fall","confidence":0.82}'
            )
        )

    hypothesis = reflect_hermes(state_dir, runner=fake_runner)

    updated = yaml.safe_load((state_dir / "strategy.yaml").read_text(encoding="utf-8"))
    assert captured_command[:3] == ["hermes", "chat", "-q"]
    assert updated["version"] == "02"
    assert updated["position_size_r"] == 0.4
    assert updated["entry"]["threshold"] == 30
    assert updated["stop_loss_pct"] == 2.0
    assert hypothesis["variable"] == "position_size_r"
    assert hypothesis["one_variable_only"] is True


def test_reflect_cli_runs_fallback_mode(tmp_path, capsys):
    state_dir = tmp_path / "state"
    _write_state(state_dir, [{"return_pct": 0.01}])
    from hermes_trading.reflect import main

    main(["--fallback", "--state-dir", str(state_dir)])

    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "fallback"
    assert output["variable"] == "entry.threshold"
