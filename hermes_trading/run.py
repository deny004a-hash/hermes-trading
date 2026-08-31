"""CLI entrypoint for the Binance Global spot paper worker."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

import yaml

from .loop import run_loop

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return value


def ensure_state(state_dir: Path) -> None:
    """Initialize an empty Railway volume from immutable image seeds."""
    state_dir.mkdir(parents=True, exist_ok=True)
    seed_dir = Path(
        os.getenv("HERMES_STATE_SEED_DIR", str(PROJECT_ROOT / "state_seed"))
    )
    for name in ("goal.yaml", "strategy.yaml", "watchlist.yaml"):
        target = state_dir / name
        seed = seed_dir / name
        if not target.exists():
            if not seed.exists():
                if name == "watchlist.yaml":
                    continue
                raise FileNotFoundError(f"Missing state file and seed: {target}")
            shutil.copy2(seed, target)
    history_dir = state_dir / "history"
    history_dir.mkdir(exist_ok=True)
    strategy_path = state_dir / "strategy.yaml"
    strategy_seed = seed_dir / "strategy.yaml"
    if strategy_path.exists() and strategy_seed.exists():
        current = _read_yaml(strategy_path)
        seeded = _read_yaml(strategy_seed)
        current_version = int(str(current.get("version", "0")))
        seeded_version = int(str(seeded.get("version", "0")))
        if seeded_version > current_version:
            shutil.copy2(
                strategy_path,
                history_dir / f"v{current_version:04d}.yaml",
            )
            shutil.copy2(strategy_seed, strategy_path)
    watchlist_path = state_dir / "watchlist.yaml"
    watchlist_seed = seed_dir / "watchlist.yaml"
    if watchlist_path.exists() and watchlist_seed.exists():
        current_watchlist = _read_yaml(watchlist_path)
        seeded_watchlist = _read_yaml(watchlist_seed)
        current_watchlist_version = int(str(current_watchlist.get("version", "0")))
        seeded_watchlist_version = int(str(seeded_watchlist.get("version", "0")))
        if seeded_watchlist_version > current_watchlist_version:
            shutil.copy2(watchlist_seed, watchlist_path)
    for name in ("trades.jsonl", "hypotheses.jsonl"):
        (state_dir / name).touch(exist_ok=True)


def resolve_asset(goal: Mapping[str, Any], override: str | None) -> str:
    asset = str(override or goal.get("asset", "")).upper()
    parts = asset.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("Asset must use CCXT notation such as SOL/USDT")
    return asset


def resolve_assets(
    goal: Mapping[str, Any], override: str | None, state_dir: Path
) -> list[str]:
    """Load the verified watchlist unless one CLI asset explicitly overrides it."""
    if override:
        return [resolve_asset(goal, override)]

    watchlist_name = goal.get("watchlist_file")
    if watchlist_name:
        watchlist = _read_yaml(state_dir / str(watchlist_name))
        raw_assets = watchlist.get("assets")
        if not isinstance(raw_assets, list) or not raw_assets:
            raise ValueError("watchlist.yaml must contain a non-empty assets list")
        assets = [resolve_asset({"asset": value}, None) for value in raw_assets]
        unique = list(dict.fromkeys(assets))
        if len(unique) != len(assets):
            raise ValueError("watchlist.yaml contains duplicate assets")
        return unique

    return [resolve_asset(goal, None)]


def validate_initial_mode(mode: str, risk_accepted: str) -> str:
    """Keep the onboarding deployment paper-only regardless of stray flags."""
    normalized = mode.strip().lower()
    if normalized != "paper":
        raise RuntimeError(
            "Initial worker is paper-only; live execution requires a separate "
            "post-evaluation implementation and explicit approval."
        )
    if risk_accepted.strip().lower() not in {"false", "0", "no", "off", ""}:
        raise RuntimeError(
            "HERMES_TRADING_I_ACCEPT_RISK must remain false in paper-only mode"
        )
    return normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", help="Override the asset in state/goal.yaml")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.getenv("HERMES_STATE_DIR", str(PROJECT_ROOT / "state"))),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("HERMES_LOOP_INTERVAL_SECONDS", "60")),
    )
    parser.add_argument("--once", action="store_true", help="Run one verified cycle")
    return parser


def main(argv: list[str] | None = None) -> None:
    _load_dotenv(PROJECT_ROOT / ".env")
    args = build_parser().parse_args(argv)
    ensure_state(args.state_dir)
    goal = _read_yaml(args.state_dir / "goal.yaml")
    assets = resolve_assets(goal, args.asset, args.state_dir)
    validate_initial_mode(
        os.getenv("HERMES_TRADING_MODE", "paper"),
        os.getenv("HERMES_TRADING_I_ACCEPT_RISK", "false"),
    )
    asyncio.run(
        run_loop(
            assets=assets,
            state_dir=args.state_dir,
            interval_seconds=args.interval,
            once=args.once,
        )
    )


if __name__ == "__main__":
    main()
