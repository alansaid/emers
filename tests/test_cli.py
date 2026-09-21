import json
import asyncio

from emers.cli import DEFAULT_DEVICES, init_workspace, main
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
