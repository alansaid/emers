"""Command-line interface for EMERS."""

import argparse
import getpass
import json
from pathlib import Path
from time import sleep

from emers.session import Run


DEFAULT_SOURCES = {
    "MockPlug": {
        "provider": "mock"
    }
}
DEFAULT_DEVICES = DEFAULT_SOURCES

DEFAULT_MONITOR_SETTINGS = {
    "cost_per_kwh": 0.3,
    "currency": "USD",
    "gco2e_per_kwh": 258,
    "gco2e_per_kilometer_car": 108.1,
}

PROVIDER_TYPES = (
    ("mock", "Mock plug"),
    ("shelly", "Shelly Plug Plus S"),
    ("tapo", "TP-Link Tapo P115"),
    ("codecarbon", "CodeCarbon software estimate"),
    ("zeus", "Zeus hardware counters"),
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


def _prompt_optional(label, default=None):
    suffix = f" [{default}]" if default else " [optional]"
    value = input(f"{label}{suffix}: ").strip()
    return value or default or ""


def _parse_indices(value):
    if not value:
        return None
    try:
        return [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("Indices must be comma-separated integers") from exc


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
    print("\nConfigure a new measurement source")
    while True:
        name = _prompt_value("Source name")
        if name in {".", ".."} or "/" in name or "\\" in name:
            print("Source name cannot be a path.")
        elif name in devices:
            print(f"A source named {name!r} already exists.")
        else:
            break

    device_type = _prompt_choice("Provider type:", PROVIDER_TYPES)
    device = {"provider": device_type}
    if device_type in {"shelly", "tapo"}:
        device["device_ip"] = _prompt_value("Device IP address")
    if device_type == "shelly":
        device["device_id"] = _prompt_value("Device ID", default="0")
    elif device_type == "tapo":
        device["tapo_user"] = _prompt_value("Tapo account username")
        device["tapo_password"] = _prompt_value("Tapo account password", secret=True)
    elif device_type == "codecarbon":
        device["tracking_mode"] = _prompt_choice(
            "Tracking scope:",
            (("machine", "Entire machine"), ("process", "Current process")),
        )
        device["measure_power_secs"] = float(
            _prompt_value("Measurement interval in seconds", default="15")
        )
        country = _prompt_optional("Country ISO code (3 letters, e.g. SWE)")
        if country:
            device["country_iso_code"] = country.upper()
    elif device_type == "zeus":
        device["measure_power_secs"] = float(
            _prompt_value("Measurement interval in seconds", default="1")
        )
        gpu_indices = _parse_indices(
            _prompt_optional("GPU indices (comma-separated; blank means all)")
        )
        cpu_indices = _parse_indices(
            _prompt_optional("CPU package indices (comma-separated; blank means all)")
        )
        if gpu_indices is not None:
            device["gpu_indices"] = gpu_indices
        if cpu_indices is not None:
            device["cpu_indices"] = cpu_indices

    devices[name] = device
    _save_devices(config_path, devices)
    print(f"Saved {name!r} to {config_path}.")
    return name


def _valid_device_name(name, devices, current_name=None):
    if name in {".", ".."} or "/" in name or "\\" in name:
        print("Source name cannot be a path.")
        return False
    if name != current_name and name in devices:
        print(f"A source named {name!r} already exists.")
        return False
    return True


def _edit_device(name, devices, config_path):
    current = devices[name]
    device_type = current.get("provider") or current.get("device_type")
    print(f"\nEdit {name!r} ({device_type or 'unknown type'})")

    while True:
        new_name = _prompt_value("Source name", default=name)
        if _valid_device_name(new_name, devices, current_name=name):
            break

    updated = dict(current)
    updated["provider"] = device_type
    updated.pop("device_type", None)
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
    elif device_type == "codecarbon":
        updated["tracking_mode"] = _prompt_choice(
            "Tracking scope:",
            (("machine", "Entire machine"), ("process", "Current process")),
        )
        updated["measure_power_secs"] = float(
            _prompt_value(
                "Measurement interval in seconds",
                default=str(current.get("measure_power_secs", 15)),
            )
        )
        country = _prompt_optional(
            "Country ISO code (3 letters, e.g. SWE)",
            default=current.get("country_iso_code"),
        )
        if country:
            updated["country_iso_code"] = country.upper()
        else:
            updated.pop("country_iso_code", None)
    elif device_type == "zeus":
        updated["measure_power_secs"] = float(
            _prompt_value(
                "Measurement interval in seconds",
                default=str(current.get("measure_power_secs", 1)),
            )
        )
        gpu_default = ",".join(map(str, current.get("gpu_indices", [])))
        cpu_default = ",".join(map(str, current.get("cpu_indices", [])))
        gpu_indices = _parse_indices(
            _prompt_optional("GPU indices (comma-separated; blank means all)", gpu_default)
        )
        cpu_indices = _parse_indices(
            _prompt_optional(
                "CPU package indices (comma-separated; blank means all)", cpu_default
            )
        )
        if gpu_indices is None:
            updated.pop("gpu_indices", None)
        else:
            updated["gpu_indices"] = gpu_indices
        if cpu_indices is None:
            updated.pop("cpu_indices", None)
        else:
            updated["cpu_indices"] = cpu_indices

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
        name = _prompt_choice("Select a source to remove:", choices)
    if name is None or not _prompt_confirmation(f"Remove {name!r} from {config_path.name}?"):
        print("No source removed.")
        return False

    del devices[name]
    _save_devices(config_path, devices)
    print(f"Removed {name!r} from {config_path}.")
    print("Existing measurement files were retained.")
    return True


def _edit_or_remove_device(devices, config_path):
    choices = [(name, name) for name in devices]
    choices.append((None, "Cancel"))
    name = _prompt_choice("Select a source:", choices)
    if name is None:
        return

    action = _prompt_choice(
        f"What would you like to do with {name!r}?",
        (("edit", "Edit source"), ("remove", "Remove source"), ("cancel", "Cancel")),
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
            description = settings.get("provider") or settings.get(
                "device_type", "unknown provider"
            )
            if settings.get("device_ip"):
                description += f", {settings['device_ip']}"
            choices.append((("source", name), f"{name} ({description})"))
        choices.append((("configure", None), "Configure a new source"))
        if devices:
            choices.append((("manage", None), "Edit or remove a configured source"))

        action, name = _prompt_choice("Select a measurement source:", choices)
        if action == "source":
            return name
        if action == "configure":
            return _configure_device(devices, config_path)
        _edit_or_remove_device(devices, config_path)


def init_workspace(workspace):
    """Create a ready-to-run EMERS workspace without overwriting files."""
    workspace.mkdir(parents=True, exist_ok=True)
    _write_json_if_missing(workspace / "settings.json", DEFAULT_SOURCES)
    _write_json_if_missing(workspace / "monitor_settings.json", DEFAULT_MONITOR_SETTINGS)
    for directory in ("measurements", "report"):
        path = workspace / directory
        path.mkdir(exist_ok=True)
        print(f"Ready {path}")


def _run_options(args):
    parameters = {}
    for assignment in getattr(args, "param", []) or []:
        if "=" not in assignment:
            raise SystemExit(f"Invalid --param {assignment!r}; expected KEY=VALUE")
        key, value = assignment.split("=", 1)
        try:
            parameters[key] = json.loads(value)
        except json.JSONDecodeError:
            parameters[key] = value
    return {
        "failure_policy": getattr(args, "failure_policy", "fallback"),
        "fallbacks": getattr(args, "fallback_sources", None),
        "max_failures": getattr(args, "max_failures", 3),
        "request_timeout": getattr(args, "request_timeout", 10.0),
        "retry_backoff": getattr(args, "retry_backoff", 1.0),
        "max_retry_backoff": getattr(args, "max_retry_backoff", 30.0),
        "parameters": parameters,
        "tags": getattr(args, "tag", []) or [],
        "benchmark_release": getattr(args, "benchmark_release", None),
    }


def _selected_source(args):
    """Return the canonical CLI source, accepting old programmatic namespaces."""
    return getattr(args, "source", None) or getattr(args, "device", None)


def _experiment_run(args, workspace):
    return Run(
        args.experiment,
        source=_selected_source(args),
        polling_rate=args.polling_rate,
        log_interval=args.log_interval,
        workspace=workspace,
        config=args.config,
        **_run_options(args),
    )


def _measure(args, workspace):
    experiment_run = _experiment_run(args, workspace)
    source = _selected_source(args)
    print(
        f"Running measurement for {source} with polling rate "
        f"{args.polling_rate}s and log interval {args.log_interval}s."
    )
    stopped = False
    with experiment_run:
        try:
            while experiment_run.manager.is_running:
                sleep(0.25)
        except KeyboardInterrupt:
            stopped = True
    if stopped:
        print("Stopped measurement.")


def _run_combined(args, workspace, monitor_runner=None):
    """Run measurement in the background and the dashboard in the foreground."""
    experiment_run = _experiment_run(args, workspace)
    source = _selected_source(args)
    measurement_dir = workspace / "measurements" / source / args.experiment
    measurement_dir.mkdir(parents=True, exist_ok=True)

    if monitor_runner is None:
        from emers.dashboard import run as monitor_runner

    print(f"Starting measurement for {source}.")
    print(f"Dashboard: http://{args.host}:{args.port}/")
    with experiment_run:
        try:
            monitor_runner(
                host=args.host,
                port=args.port,
                debug=args.debug,
                workspace=workspace,
            )
        except KeyboardInterrupt:
            pass


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

    validate = subparsers.add_parser(
        "validate", help="Validate manifests, events, and measurement CSV files."
    )
    validate.add_argument(
        "--run", type=Path, help="Validate one run directory or run.json file."
    )
    validate.add_argument(
        "--strict-benchmark",
        action="store_true",
        help="Require an immutable benchmark release and clean provenance.",
    )
    validate.add_argument(
        "--pretty", action="store_true", help="Pretty-print the JSON result."
    )

    report = subparsers.add_parser(
        "report", help="Generate a standalone HTML report from recorded run artifacts."
    )
    report_selection = report.add_mutually_exclusive_group()
    report_selection.add_argument(
        "--run",
        help="Run ID, unique ID prefix, run directory, or run.json path (default: latest).",
    )
    report_selection.add_argument(
        "--all", action="store_true", help="Generate one report for every run."
    )
    report.add_argument(
        "--output",
        type=Path,
        help="Output HTML file or directory (default: report/<run-id>/report.html).",
    )
    report.add_argument("--cost-per-kwh", type=float)
    report.add_argument("--currency")
    report.add_argument("--gco2e-per-kwh", type=float)
    report.add_argument("--gco2e-per-kilometer-car", type=float)
    report.add_argument(
        "--smoothness",
        type=int,
        default=1,
        help="Rolling-window size for optional smoothed chart traces (default: 1).",
    )

    def add_measurement_options(command_parser):
        command_parser.add_argument(
            "--source",
            "--device",
            dest="source",
            help=(
                "Measurement source name from settings.json. If omitted, choose or "
                "configure one interactively. --device is a compatibility alias."
            ),
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
            "--config", default="settings.json", help="Source configuration JSON file."
        )
        command_parser.add_argument(
            "--failure-policy",
            choices=("fallback", "continue", "fail"),
            default="fallback",
            help=(
                "On provider failure: try configured fallbacks, keep retrying with "
                "gaps, or fail the run (default: fallback)."
            ),
        )
        command_parser.add_argument(
            "--fallback-source",
            "--fallback-device",
            dest="fallback_sources",
            action="append",
            default=None,
            help=(
                "Ordered fallback source or provider name; repeat for a chain. "
                "Defaults to the source configuration, then CodeCarbon. "
                "--fallback-device is a compatibility alias."
            ),
        )
        command_parser.add_argument(
            "--max-failures",
            type=int,
            default=3,
            help="Consecutive runtime failures before fallback/failure (default: 3).",
        )
        command_parser.add_argument(
            "--request-timeout",
            type=float,
            default=10.0,
            help="Seconds allowed for one provider request (default: 10).",
        )
        command_parser.add_argument(
            "--retry-backoff",
            type=float,
            default=1.0,
            help="Initial retry delay in seconds (default: 1).",
        )
        command_parser.add_argument(
            "--max-retry-backoff",
            type=float,
            default=30.0,
            help="Maximum retry delay in seconds (default: 30).",
        )
        command_parser.add_argument(
            "--param",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="Record an experiment parameter; repeatable.",
        )
        command_parser.add_argument(
            "--tag", action="append", default=[], help="Attach a run tag; repeatable."
        )
        command_parser.add_argument(
            "--benchmark-release",
            help="Record the immutable benchmark release or dataset version.",
        )

    measure = subparsers.add_parser("measure", help="Continuously record measurements.")
    add_measurement_options(measure)

    def add_dashboard_options(command_parser):
        command_parser.add_argument("--host", default="127.0.0.1")
        command_parser.add_argument("--port", type=int, default=5000)
        command_parser.add_argument("--debug", action="store_true")

    dashboard = subparsers.add_parser(
        "dashboard", help="Start the EMERS dashboard."
    )
    add_dashboard_options(dashboard)

    monitor = subparsers.add_parser(
        "monitor", help="Compatibility alias for 'dashboard'."
    )
    add_dashboard_options(monitor)

    run = subparsers.add_parser("run", help="Measure and run the dashboard together.")
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

    if args.command == "validate":
        from emers.validation import validate_workspace

        report = validate_workspace(
            workspace,
            run=args.run,
            strict_benchmark=args.strict_benchmark,
        )
        print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
        return 0 if report["valid"] else 1

    if args.command == "report":
        if args.smoothness < 1:
            raise SystemExit("--smoothness must be at least 1")
        from emers.reporting import generate_reports

        try:
            reports = generate_reports(
                workspace,
                run=args.run,
                all_runs=args.all,
                output=args.output,
                cost_per_kwh=args.cost_per_kwh,
                currency=args.currency,
                gco2e_per_kwh=args.gco2e_per_kwh,
                gco2e_per_kilometer_car=args.gco2e_per_kilometer_car,
                smoothness=args.smoothness,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        for report_path in reports:
            print(report_path)
        return 0

    if args.command in {"measure", "run"}:
        config_path = Path(args.config).expanduser()
        if not config_path.is_absolute():
            config_path = workspace / config_path
        if not config_path.is_file():
            raise SystemExit(
                f"Missing configuration {config_path}. "
                f"Run 'emers --workspace {workspace} init' first."
            )
        if args.source is None:
            args.source = _select_or_configure_device(config_path)
        if args.command == "measure":
            _measure(args, workspace)
        else:
            _require_files(workspace, ("monitor_settings.json",))
            _run_combined(args, workspace)
        return

    _require_files(workspace, ("settings.json", "monitor_settings.json"))
    from emers.dashboard import run

    run(host=args.host, port=args.port, debug=args.debug, workspace=workspace)
