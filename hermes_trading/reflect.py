"""Deterministic and Hermes-driven strategy reflection."""

from __future__ import annotations

import argparse
import json
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from .score import trade_metrics


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return payload


def _read_jsonl(path: Path, limit: int = 25) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows[-limit:]


def _write_yaml_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(dict(payload), sort_keys=False), encoding="utf-8"
    )
    temporary.replace(path)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")


def _set_path(payload: dict[str, Any], dotted_path: str, value: Any) -> Any:
    parts = dotted_path.split(".")
    cursor: dict[str, Any] = payload
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            raise ValueError(f"Cannot set {dotted_path}: {part} is not a mapping")
        cursor = child
    old_value = cursor[parts[-1]]
    cursor[parts[-1]] = value
    return old_value


def _next_version(current: Any) -> str:
    number = int(str(current)) + 1
    return f"{number:02d}"


def reflect_fallback(state_dir: str | Path) -> dict[str, Any]:
    """Apply one deterministic change and persist its complete audit trail."""
    state = Path(state_dir)
    strategy_path = state / "strategy.yaml"
    goal = _read_yaml(state / "goal.yaml")
    previous = _read_yaml(strategy_path)
    trades = _read_jsonl(state / "trades.jsonl", limit=25)
    metrics = trade_metrics(trades)

    current_version = int(str(previous.get("version", "01")))
    updated = deepcopy(previous)

    if metrics["max_drawdown"] > float(goal["max_drawdown"]):
        variable = "stop_loss_pct"
        old_value = float(previous["stop_loss_pct"])
        new_value = round(max(0.1, old_value - 0.2), 2)
        reason = (
            f"Max drawdown {metrics['max_drawdown']:.4f} exceeded "
            f"limit {float(goal['max_drawdown']):.4f}; tighten risk."
        )
        prediction = "Lower drawdown; entry frequency unchanged."
    elif metrics["realised_return"] < float(goal["target_return_30d"]):
        variable = "entry.threshold"
        old_value = float(previous["entry"]["threshold"])
        new_value = min(99.0, old_value + 2.0)
        if isinstance(previous["entry"]["threshold"], int):
            new_value = int(new_value)
        reason = (
            f"Realised return {metrics['realised_return']:.4f} missed "
            f"target {float(goal['target_return_30d']):.4f}; admit more setups."
        )
        prediction = "Higher trade frequency with unchanged per-trade risk."
    else:
        variable = "entry.threshold"
        old_value = float(previous["entry"]["threshold"])
        new_value = max(1.0, old_value - 1.0)
        if isinstance(previous["entry"]["threshold"], int):
            new_value = int(new_value)
        reason = "Return and drawdown goals were met; increase entry selectivity."
        prediction = "Fewer but higher-quality entries."

    if new_value == old_value:
        raise RuntimeError(f"Reflection would not change {variable}")

    _set_path(updated, variable, new_value)
    updated["version"] = _next_version(previous.get("version", "01"))

    history_path = state / "history" / f"v{current_version:04d}.yaml"
    _write_yaml_atomic(history_path, previous)
    _write_yaml_atomic(strategy_path, updated)

    hypothesis = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "fallback",
        "from_version": str(previous.get("version", "01")),
        "to_version": updated["version"],
        "variable": variable,
        "old_value": old_value,
        "new_value": new_value,
        "reason": reason,
        "predicted_score_direction": "up",
        "prediction": prediction,
        "metrics": metrics,
        "one_variable_only": True,
    }
    _append_jsonl(state / "hypotheses.jsonl", hypothesis)
    return hypothesis


_ALLOWED_HERMES_VARIABLES: dict[str, tuple[float, float]] = {
    "entry.threshold": (1.0, 99.0),
    "stop_loss_pct": (0.1, 25.0),
    "position_size_r": (0.1, 5.0),
}


def _parse_hypothesis(output: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and {"variable", "new_value", "reason"} <= value.keys():
            return value
    raise ValueError("Hermes did not return a valid one-variable JSON hypothesis")


def _value_at_path(payload: Mapping[str, Any], dotted_path: str) -> Any:
    value: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise ValueError(f"Unknown strategy variable {dotted_path!r}")
        value = value[part]
    return value


def reflect_hermes(
    state_dir: str | Path,
    *,
    hermes_command: str = "hermes",
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Ask Hermes for one structured hypothesis, validate it, and apply it."""
    state = Path(state_dir)
    strategy_path = state / "strategy.yaml"
    goal = _read_yaml(state / "goal.yaml")
    previous = _read_yaml(strategy_path)
    trades = _read_jsonl(state / "trades.jsonl", limit=25)
    metrics = trade_metrics(trades)

    prompt = (
        "You are reflecting on a long-only Binance Global spot paper strategy. "
        "Change exactly ONE variable. The risk_reward field is locked at 2.0. "
        "Allowed variables: entry.threshold, stop_loss_pct, position_size_r. "
        "Return one JSON object only with keys variable, new_value, reason, "
        "prediction, confidence.\n\n"
        f"GOAL:\n{yaml.safe_dump(goal, sort_keys=False)}\n"
        f"CURRENT_STRATEGY:\n{yaml.safe_dump(previous, sort_keys=False)}\n"
        f"LATEST_25_CLOSED_TRADES:\n{json.dumps(trades, ensure_ascii=False)}\n"
        f"METRICS:\n{json.dumps(metrics)}"
    )
    completed = runner(
        [hermes_command, "chat", "-q", prompt],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    proposed = _parse_hypothesis(str(completed.stdout))
    variable = str(proposed["variable"])
    if variable not in _ALLOWED_HERMES_VARIABLES:
        raise ValueError(f"Hermes proposed forbidden variable {variable!r}")
    if isinstance(proposed["new_value"], bool) or not isinstance(
        proposed["new_value"], (int, float)
    ):
        raise ValueError("Hermes new_value must be numeric")

    new_value = float(proposed["new_value"])
    minimum, maximum = _ALLOWED_HERMES_VARIABLES[variable]
    if not minimum <= new_value <= maximum:
        raise ValueError(
            f"Hermes new_value for {variable} must be within [{minimum}, {maximum}]"
        )
    old_value = _value_at_path(previous, variable)
    if isinstance(old_value, int) and new_value.is_integer():
        new_value = int(new_value)
    if new_value == old_value:
        raise ValueError(f"Hermes proposed no change for {variable}")

    updated = deepcopy(previous)
    _set_path(updated, variable, new_value)
    updated["version"] = _next_version(previous.get("version", "01"))
    current_version = int(str(previous.get("version", "01")))
    _write_yaml_atomic(state / "history" / f"v{current_version:04d}.yaml", previous)
    _write_yaml_atomic(strategy_path, updated)

    confidence = float(proposed.get("confidence", 0.0))
    hypothesis = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "hermes",
        "from_version": str(previous.get("version", "01")),
        "to_version": updated["version"],
        "variable": variable,
        "old_value": old_value,
        "new_value": new_value,
        "reason": str(proposed["reason"]),
        "prediction": str(proposed.get("prediction", "Score should improve.")),
        "predicted_score_direction": "up",
        "confidence": max(0.0, min(1.0, confidence)),
        "metrics": metrics,
        "one_variable_only": True,
    }
    _append_jsonl(state / "hypotheses.jsonl", hypothesis)
    return hypothesis


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--fallback", action="store_true")
    modes.add_argument("--hermes", action="store_true")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "state",
    )
    args = parser.parse_args(argv)
    result = (
        reflect_fallback(args.state_dir)
        if args.fallback
        else reflect_hermes(args.state_dir)
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
