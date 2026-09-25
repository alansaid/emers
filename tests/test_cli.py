import json
import asyncio
from argparse import Namespace
from pathlib import Path

import pytest

from emers.cli import (
    DEFAULT_DEVICES,
    _edit_device,
    _remove_device,
    _run_combined,
    _select_or_configure_device,
    build_parser,
    init_workspace,
    main,
)
from emers.measurement import MeasurementManager


def test_init_workspace_creates_expected_files(tmp_path):
    init_workspace(tmp_path)

    assert json.loads((tmp_path / "settings.json").read_text()) == DEFAULT_DEVICES
    assert (tmp_path / "monitor_settings.json").is_file()
    assert (tmp_path / "measurements").is_dir()
    assert (tmp_path / "report").is_dir()


def test_init_workspace_does_not_overwrite_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text('{"custom": {"device_type": "mock"}}')

    init_workspace(tmp_path)

    assert json.loads(settings.read_text()) == {"custom": {"device_type": "mock"}}


def test_manager_loads_configuration_from_workspace(tmp_path):
    init_workspace(tmp_path)

    manager = MeasurementManager("MockPlug", workspace=tmp_path)

    assert manager.source_settings == {"provider": "mock"}
    assert manager.device == manager.source_settings
    assert manager.workspace == tmp_path.resolve()


def test_manager_uses_ordered_fallbacks_from_source_configuration(tmp_path):
    init_workspace(tmp_path)
    settings = {
        "primary": {
            "provider": "mock",
            "fallbacks": [
                {"provider": "zeus", "gpu_indices": [0]},
                "codecarbon",
            ],
        }
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings))

    manager = MeasurementManager("primary", workspace=tmp_path)

    assert manager.fallbacks == settings["primary"]["fallbacks"]
    assert manager.manifest["measurement"]["fallback_chain"] == [
        {"provider": "zeus"},
        {"provider": "codecarbon"},
    ]


def test_mock_manager_writes_measurements(tmp_path):
    init_workspace(tmp_path)
    manager = MeasurementManager(
        "MockPlug",
        experiment_name="test-run",
        polling_rate=0.001,
        workspace=tmp_path,
    )

    async def record_briefly():
        task = asyncio.create_task(manager.log_data())
        await asyncio.sleep(0.02)
        manager.stop_event.set()
        await task

    asyncio.run(record_briefly())

    logs = list((tmp_path / "measurements" / "MockPlug" / "test-run").glob("*.csv"))
    assert len(logs) == 1
    assert len(logs[0].read_text().splitlines()) > 1


def test_measure_accepts_custom_config_without_monitor_settings(tmp_path, monkeypatch):
    config = tmp_path / "devices.json"
    config.write_text(json.dumps(DEFAULT_DEVICES))

    async def stop_immediately(self):
        return None

    monkeypatch.setattr(MeasurementManager, "log_data", stop_immediately)
    main([
        "--workspace", str(tmp_path),
        "measure", "--device", "MockPlug", "--config", "devices.json",
    ])


def test_source_and_legacy_device_options_share_destination():
    parser = build_parser()

    assert parser.parse_args(["measure", "--source", "new"]).source == "new"
    assert parser.parse_args(["measure", "--device", "old"]).source == "old"
    assert (
        parser.parse_args(
            ["measure", "--source", "main", "--fallback-device", "backup"]
        ).fallback_sources
        == ["backup"]
    )


@pytest.mark.parametrize("command", ["dashboard", "monitor"])
def test_dashboard_command_and_monitor_alias_use_same_options(command):
    args = build_parser().parse_args(
        [command, "--host", "0.0.0.0", "--port", "5050", "--debug"]
    )

    assert args.command == command
    assert args.host == "0.0.0.0"
    assert args.port == 5050
    assert args.debug is True


@pytest.mark.parametrize("command", ["dashboard", "monitor"])
def test_dashboard_command_and_monitor_alias_run_same_dashboard(
    command, tmp_path, monkeypatch
):
    calls = []
    init_workspace(tmp_path)
    monkeypatch.setattr(
        "emers.dashboard.run", lambda **kwargs: calls.append(kwargs)
    )

    main([
        "--workspace",
        str(tmp_path),
        command,
        "--host",
        "0.0.0.0",
        "--port",
        "5050",
    ])

    assert calls == [{
        "host": "0.0.0.0",
        "port": 5050,
        "debug": False,
        "workspace": tmp_path.resolve(),
    }]


def test_report_command_dispatches_run_selection_and_settings(tmp_path, monkeypatch):
    calls = []

    def fake_generate(workspace, **kwargs):
        calls.append((workspace, kwargs))
        return (tmp_path / "report.html",)

    monkeypatch.setattr("emers.reporting.generate_reports", fake_generate)

    result = main([
        "--workspace",
        str(tmp_path),
        "report",
        "--run",
        "abc123",
        "--cost-per-kwh",
        "0.42",
        "--currency",
        "EUR",
        "--smoothness",
        "3",
    ])

    assert result == 0
    assert calls == [(tmp_path.resolve(), {
        "run": "abc123",
        "all_runs": False,
        "output": None,
        "cost_per_kwh": 0.42,
        "currency": "EUR",
        "gco2e_per_kwh": None,
        "gco2e_per_kilometer_car": None,
        "smoothness": 3,
    })]


def test_interactive_measure_selects_existing_device(tmp_path, monkeypatch):
    init_workspace(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "1")

    selected = _select_or_configure_device(tmp_path / "settings.json")

    assert selected == "MockPlug"


def test_interactive_measure_configures_and_saves_tapo_device(tmp_path, monkeypatch):
    init_workspace(tmp_path)
    answers = iter([
        "2",  # Configure a new device
        "office_plug",
        "3",  # TP-Link Tapo P115
        "192.168.1.42",
        "user@example.com",
    ])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr("emers.cli.getpass.getpass", lambda _: "secret")

    selected = _select_or_configure_device(tmp_path / "settings.json")

    assert selected == "office_plug"
    devices = json.loads((tmp_path / "settings.json").read_text())
    assert devices["office_plug"] == {
        "provider": "tapo",
        "device_ip": "192.168.1.42",
        "tapo_user": "user@example.com",
        "tapo_password": "secret",
    }
    assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600


def test_remove_device_updates_settings_but_retains_measurements(tmp_path, monkeypatch):
    init_workspace(tmp_path)
    measurement = tmp_path / "measurements" / "MockPlug" / "continuous" / "reading.csv"
    measurement.parent.mkdir(parents=True)
    measurement.write_text("timestamp,current_draw,total_draw\n")
    answers = iter(["1", "yes"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    devices = dict(DEFAULT_DEVICES)

    removed = _remove_device(devices, tmp_path / "settings.json")

    assert removed is True
    assert json.loads((tmp_path / "settings.json").read_text()) == {}
    assert measurement.is_file()


def test_remove_device_can_be_cancelled(tmp_path, monkeypatch):
    init_workspace(tmp_path)
    answers = iter(["1", "no"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    devices = dict(DEFAULT_DEVICES)

    removed = _remove_device(devices, tmp_path / "settings.json")

    assert removed is False
    assert json.loads((tmp_path / "settings.json").read_text()) == DEFAULT_DEVICES


def test_edit_device_updates_fields_and_keeps_tapo_password(tmp_path, monkeypatch):
    config = tmp_path / "settings.json"
    devices = {
        "old_name": {
            "device_type": "tapo",
            "device_ip": "192.168.1.10",
            "tapo_user": "old@example.com",
            "tapo_password": "existing-secret",
        }
    }
    config.write_text(json.dumps(devices))
    answers = iter([
        "new_name",
        "192.168.1.20",
        "new@example.com",
    ])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr("emers.cli.getpass.getpass", lambda _: "")

    edited_name = _edit_device("old_name", devices, config)

    assert edited_name == "new_name"
    saved = json.loads(config.read_text())
    assert "old_name" not in saved
    assert saved["new_name"] == {
        "provider": "tapo",
        "device_ip": "192.168.1.20",
        "tapo_user": "new@example.com",
        "tapo_password": "existing-secret",
    }


def test_run_combined_manages_measurement_and_monitor(tmp_path, monkeypatch):
    events = []
    original_directory = Path.cwd()

    def fake_monitor(**kwargs):
        events.append(("monitor", kwargs))

    init_workspace(tmp_path)
    args = Namespace(
        device="MockPlug",
        experiment="combined-test",
        polling_rate=0.5,
        log_interval=300,
        config="settings.json",
        host="127.0.0.1",
        port=5000,
        debug=False,
    )

    _run_combined(args, tmp_path, monitor_runner=fake_monitor)

    assert Path.cwd() == original_directory
    assert (tmp_path / "measurements" / "MockPlug" / "combined-test").is_dir()
    assert events == [
        (
            "monitor",
            {
                "host": "127.0.0.1",
                "port": 5000,
                "debug": False,
                "workspace": tmp_path,
            },
        ),
    ]
    manifests = list(
        (tmp_path / "measurements" / "MockPlug" / "combined-test" / ".emers" / "runs").glob(
            "*/run.json"
        )
    )
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["status"] == "completed"
