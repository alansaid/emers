"""Command-line interface for EMERS."""

import argparse
import asyncio
import getpass
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

DEVICE_TYPES = (
    ("mock", "Mock plug"),
    ("shelly", "Shelly Plug Plus S"),
    ("tapo", "TP-Link Tapo P115"),
)


def _write_json_if_missing(path, value):
    if path.exists():
        print(f"Keeping existing {path}")
        return
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(f"Created {path}")


def _prompt_choice(prompt, choices):
    """Prompt until the user selects one of the supplied (value, label) pairs."""
    print(prompt)
    for index, (_, label) in enumerate(choices, start=1):
        print(f"  {index}. {label}")

    while True:
        answer = input(f"Choose [1-{len(choices)}]: ").strip()
        try:
            return choices[int(answer) - 1][0]
        except (ValueError, IndexError):
            print(f"Please enter a number from 1 to {len(choices)}.")


def _prompt_value(label, *, default=None, secret=False, hide_default=False):
    if default is None:
        suffix = ""
    elif hide_default:
        suffix = " [press Enter to keep current]"
    else:
        suffix = f" [{default}]"
    prompt = f"{label}{suffix}: "
    reader = getpass.getpass if secret else input

    while True:
        value = reader(prompt).strip()
        if value:
            return value
        if default is not None:
            return default
        print(f"{label} is required.")


def _prompt_confirmation(prompt):
    while True:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("Please enter yes or no.")


def _load_devices(config_path):
    try:
        devices = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {config_path}: {exc}") from exc
    if not isinstance(devices, dict):
        raise SystemExit(f"Invalid configuration in {config_path}: expected a JSON object.")
    return devices


def _save_devices(config_path, devices):
    config_path.write_text(json.dumps(devices, indent=2) + "\n", encoding="utf-8")
    config_path.chmod(0o600)


def _configure_device(devices, config_path):
    print("\nConfigure a new device")
    while True:
        name = _prompt_value("Device name")
        if name in {".", ".."} or "/" in name or "\\" in name:
            print("Device name cannot be a path.")
        elif name in devices:
            print(f"A device named {name!r} already exists.")
        else:
            break

    device_type = _prompt_choice("Device type:", DEVICE_TYPES)
    device = {"device_type": device_type}
    if device_type in {"shelly", "tapo"}:
        device["device_ip"] = _prompt_value("Device IP address")
    if device_type == "shelly":
        device["device_id"] = _prompt_value("Device ID", default="0")
    elif device_type == "tapo":
        device["tapo_user"] = _prompt_value("Tapo account username")
        device["tapo_password"] = _prompt_value("Tapo account password", secret=True)

    devices[name] = device
    _save_devices(config_path, devices)
    print(f"Saved {name!r} to {config_path}.")
    return name


def _valid_device_name(name, devices, current_name=None):
    if name in {".", ".."} or "/" in name or "\\" in name:
        print("Device name cannot be a path.")
        return False
    if name != current_name and name in devices:
        print(f"A device named {name!r} already exists.")
        return False
    return True


def _edit_device(name, devices, config_path):
    current = devices[name]
    device_type = current.get("device_type")
    print(f"\nEdit {name!r} ({device_type or 'unknown type'})")

    while True:
        new_name = _prompt_value("Device name", default=name)
        if _valid_device_name(new_name, devices, current_name=name):
            break

    updated = dict(current)
    if device_type in {"shelly", "tapo"}:
        updated["device_ip"] = _prompt_value(
            "Device IP address", default=current.get("device_ip")
        )
    if device_type == "shelly":
        updated["device_id"] = _prompt_value(
            "Device ID", default=current.get("device_id", "0")
        )
    elif device_type == "tapo":
        updated["tapo_user"] = _prompt_value(
            "Tapo account username", default=current.get("tapo_user")
        )
        updated["tapo_password"] = _prompt_value(
            "Tapo account password",
            default=current.get("tapo_password"),
            secret=True,
            hide_default=True,
        )

    if new_name != name:
        del devices[name]
    devices[new_name] = updated
    _save_devices(config_path, devices)
    print(f"Saved {new_name!r} to {config_path}.")
    if new_name != name:
        print(f"Existing measurement files remain under the previous name {name!r}.")
    return new_name


def _remove_device(devices, config_path, name=None):
    if name is None:
        choices = [(device_name, device_name) for device_name in devices]
        choices.append((None, "Cancel"))
        name = _prompt_choice("Select a device to remove:", choices)
    if name is None or not _prompt_confirmation(f"Remove {name!r} from {config_path.name}?"):
        print("No device removed.")
        return False

    del devices[name]
    _save_devices(config_path, devices)
    print(f"Removed {name!r} from {config_path}.")
    print("Existing measurement files were retained.")
    return True


def _edit_or_remove_device(devices, config_path):
    choices = [(name, name) for name in devices]
    choices.append((None, "Cancel"))
    name = _prompt_choice("Select a device:", choices)
    if name is None:
        return

    action = _prompt_choice(
        f"What would you like to do with {name!r}?",
        (("edit", "Edit device"), ("remove", "Remove device"), ("cancel", "Cancel")),
    )
    if action == "edit":
        _edit_device(name, devices, config_path)
    elif action == "remove":
        _remove_device(devices, config_path, name=name)


def _select_or_configure_device(config_path):
    devices = _load_devices(config_path)
    while True:
        choices = []
        for name, settings in devices.items():
            description = settings.get("device_type", "unknown type")
            if settings.get("device_ip"):
                description += f", {settings['device_ip']}"
            choices.append((("device", name), f"{name} ({description})"))
        choices.append((("configure", None), "Configure a new device"))
        if devices:
            choices.append((("manage", None), "Edit or remove a configured device"))

        action, name = _prompt_choice("Select a device to measure:", choices)
        if action == "device":
            return name
        if action == "configure":
            return _configure_device(devices, config_path)
        _edit_or_remove_device(devices, config_path)


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
    manager = _measurement_manager(args, workspace)
    print(
        f"Running measurement for {args.device} with polling rate "
        f"{args.polling_rate}s and log interval {args.log_interval}s."
    )
    try:
        await manager.log_data()
    except KeyboardInterrupt:
        print("Stopped measurement.")


def _measurement_manager(args, workspace):
    return MeasurementManager(
        device_name=args.device,
        experiment_name=args.experiment,
        polling_rate=args.polling_rate,
        log_interval=args.log_interval,
        workspace=workspace,
        config=args.config,
    )


def _run_combined(args, workspace, monitor_runner=None):
    """Run measurement in the background and the dashboard in the foreground."""
    manager = _measurement_manager(args, workspace)
    measurement_dir = workspace / "measurements" / args.device / args.experiment
    measurement_dir.mkdir(parents=True, exist_ok=True)

    previous_directory = Path.cwd()
    try:
        os.chdir(workspace)
        if monitor_runner is None:
            from emers.monitor import run as monitor_runner

        print(f"Starting measurement for {args.device}.")
        print(f"Dashboard: http://{args.host}:{args.port}/")
        with manager:
            monitor_runner(host=args.host, port=args.port, debug=args.debug)
    finally:
        os.chdir(previous_directory)


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

    def add_measurement_options(command_parser):
        command_parser.add_argument(
            "--device",
            help="Device name from settings.json. If omitted, choose or configure one interactively.",
        )
        command_parser.add_argument(
            "--experiment", default="continuous", help="Experiment output directory name."
        )
        command_parser.add_argument(
            "--polling-rate", type=float, default=0.5, help="Seconds between readings."
        )
        command_parser.add_argument(
            "--log-interval", type=int, default=300, help="Seconds between log rotations."
        )
        command_parser.add_argument(
            "--config", default="settings.json", help="Device configuration JSON file."
        )

    measure = subparsers.add_parser("measure", help="Continuously record measurements.")
    add_measurement_options(measure)

    monitor = subparsers.add_parser("monitor", help="Start the monitoring web application.")
    monitor.add_argument("--host", default="127.0.0.1")
    monitor.add_argument("--port", type=int, default=5000)
    monitor.add_argument("--debug", action="store_true")

    run = subparsers.add_parser("run", help="Measure and run the monitoring UI together.")
    add_measurement_options(run)
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=5000)
    run.add_argument("--debug", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    workspace = args.workspace.expanduser().resolve()

    if args.command == "init":
        init_workspace(workspace)
        return

    if args.command in {"measure", "run"}:
        config_path = Path(args.config).expanduser()
        if not config_path.is_absolute():
            config_path = workspace / config_path
        if not config_path.is_file():
            raise SystemExit(
                f"Missing configuration {config_path}. "
                f"Run 'emers --workspace {workspace} init' first."
            )
        if args.device is None:
            args.device = _select_or_configure_device(config_path)
        if args.command == "measure":
            asyncio.run(_measure(args, workspace))
        else:
            _require_files(workspace, ("monitor_settings.json",))
            _run_combined(args, workspace)
        return

    _require_files(workspace, ("settings.json", "monitor_settings.json"))
    # The monitor currently uses project-relative paths. Import it only after
    # selecting the workspace so imports never depend on the installed package directory.
    os.chdir(workspace)
    from emers.monitor import run

    run(host=args.host, port=args.port, debug=args.debug)
