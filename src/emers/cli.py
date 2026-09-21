"""Command-line interface for EMERS."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from emers.measurement import MeasurementManager


DEFAULT_DEVICES = {
    "MockPlug": {
        "device_type": "mock"
    }
}

DEFAULT_MONITOR_SETTINGS = {
    "cost_per_kwh": 0.3,
    "currency": "USD",
    "gco2e_per_kwh": 258,
    "gco2e_per_kilometer_car": 108.1,
}


def _write_json_if_missing(path, value):
    if path.exists():
        print(f"Keeping existing {path}")
        return
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(f"Created {path}")


def init_workspace(workspace):
    """Create a ready-to-run EMERS workspace without overwriting files."""
    workspace.mkdir(parents=True, exist_ok=True)
    _write_json_if_missing(workspace / "settings.json", DEFAULT_DEVICES)
    _write_json_if_missing(workspace / "monitor_settings.json", DEFAULT_MONITOR_SETTINGS)
    for directory in ("measurements", "report"):
        path = workspace / directory
        path.mkdir(exist_ok=True)
        print(f"Ready {path}")


async def _measure(args, workspace):
    manager = MeasurementManager(
        device_name=args.device,
        experiment_name=args.experiment,
        polling_rate=args.polling_rate,
        log_interval=args.log_interval,
        workspace=workspace,
        config=args.config,
    )
    print(
        f"Running measurement for {args.device} with polling rate "
        f"{args.polling_rate}s and log interval {args.log_interval}s."
    )
    try:
        await manager.log_data()
    except KeyboardInterrupt:
        print("Stopped measurement.")


def _require_files(workspace, names):
    missing = [name for name in names if not (workspace / name).is_file()]
    if missing:
        names = ", ".join(missing)
        raise SystemExit(
            f"Missing {names} in {workspace}. Run 'emers --workspace {workspace} init' first."
        )


def build_parser():
    parser = argparse.ArgumentParser(
        prog="emers",
        description="Measure and inspect experiment energy consumption.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Project directory for configuration, measurements, and reports (default: current directory).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize an EMERS workspace.")

    measure = subparsers.add_parser("measure", help="Continuously record measurements.")
    measure.add_argument("--device", required=True, help="Device name from settings.json.")
    measure.add_argument("--experiment", default="continuous", help="Experiment output directory name.")
    measure.add_argument("--polling-rate", type=float, default=0.5, help="Seconds between readings.")
    measure.add_argument("--log-interval", type=int, default=300, help="Seconds between log rotations.")
    measure.add_argument("--config", default="settings.json", help="Device configuration JSON file.")

    monitor = subparsers.add_parser("monitor", help="Start the monitoring web application.")
    monitor.add_argument("--host", default="127.0.0.1")
    monitor.add_argument("--port", type=int, default=5000)
    monitor.add_argument("--debug", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    workspace = args.workspace.expanduser().resolve()

    if args.command == "init":
        init_workspace(workspace)
        return

    if args.command == "measure":
        config_path = Path(args.config).expanduser()
        if not config_path.is_absolute():
            config_path = workspace / config_path
        if not config_path.is_file():
            raise SystemExit(
                f"Missing configuration {config_path}. "
                f"Run 'emers --workspace {workspace} init' first."
            )
        asyncio.run(_measure(args, workspace))
        return

    _require_files(workspace, ("settings.json", "monitor_settings.json"))
    # The monitor currently uses project-relative paths. Import it only after
    # selecting the workspace so imports never depend on the installed package directory.
    os.chdir(workspace)
    from emers.monitor import run

    run(host=args.host, port=args.port, debug=args.debug)
