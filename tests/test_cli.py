import json
import asyncio
from argparse import Namespace

from emers.cli import (
    DEFAULT_DEVICES,
    _edit_device,
    _remove_device,
    _run_combined,
    _select_or_configure_device,
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

    assert manager.device == {"device_type": "mock"}
    assert manager.workspace == tmp_path.resolve()


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
        "device_type": "tapo",
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
        "device_type": "tapo",
        "device_ip": "192.168.1.20",
        "tapo_user": "new@example.com",
        "tapo_password": "existing-secret",
    }


def test_run_combined_manages_measurement_and_monitor(tmp_path, monkeypatch):
    events = []

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

    assert (tmp_path / "measurements" / "MockPlug" / "combined-test").is_dir()
    assert events == [
        ("monitor", {"host": "127.0.0.1", "port": 5000, "debug": False}),
    ]
    manifests = list(
        (tmp_path / "measurements" / "MockPlug" / "combined-test" / ".emers" / "runs").glob(
            "*/run.json"
        )
    )
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["status"] == "completed"
