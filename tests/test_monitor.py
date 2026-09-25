import asyncio
import json
from pathlib import Path

import pandas as pd

from emers.dashboard import create_dashboard
from emers.dashboard.app import _render_table
from emers.dashboard.app import update_run_dashboard as canonical_update_run_dashboard
from emers.artifacts import RunStore
from emers.measurement import MeasurementManager
from emers.monitor import calculate_cost, make_scatters, update_run_dashboard


def _component_with_id(node, component_id):
    if node is None or isinstance(node, (str, int, float, bool)):
        return None
    if isinstance(node, (list, tuple)):
        for child in node:
            result = _component_with_id(child, component_id)
            if result is not None:
                return result
        return None
    if getattr(node, "id", None) == component_id:
        return node
    return _component_with_id(getattr(node, "children", None), component_id)


def _workspace(path, source):
    (path / "measurements" / source / "experiment").mkdir(parents=True)
    (path / "report").mkdir()
    (path / "monitor_settings.json").write_text(json.dumps({
        "cost_per_kwh": 0.3,
        "currency": "USD",
        "gco2e_per_kwh": 258,
        "gco2e_per_kilometer_car": 108.1,
    }))
    (path / "settings.json").write_text(json.dumps({source: {"provider": "mock"}}))


def test_dashboard_uses_semantic_html_tables():
    rendered = _render_table([{"Field": "Samples", "Value": 4}])

    assert type(rendered.children).__name__ == "Table"


def test_monitor_module_delegates_to_canonical_dashboard():
    assert update_run_dashboard is canonical_update_run_dashboard


def test_dashboard_does_not_combine_mixed_measurement_scopes():
    readings = pd.DataFrame({
        "timestamp": [1.0, 2.0, 3.0, 4.0],
        "current_draw": [10.0, 10.0, 5.0, 5.0],
        "total_draw": [1.0, 1.1, 0.0, 0.05],
        "source": ["tapo", "tapo", "codecarbon", "codecarbon"],
        "provider": ["tapo", "tapo", "codecarbon", "codecarbon"],
        "measurement_scope": [
            "whole_system_wall",
            "whole_system_wall",
            "cpu_gpu_ram",
            "cpu_gpu_ram",
        ],
        "segment_id": ["segment-001", "segment-001", "segment-002", "segment-002"],
    })

    result = make_scatters({"experiment": readings}, smoothness=1, autosize=True)

    assert len(result["scatters"]) == 2
    assert result["power_by_experiment"]["experiment"] is None
    assert result["total_power"] is None
    assert "mixed measurement scopes" in calculate_cost(None, 1, "USD", 1, 1)[
        "Total Energy Consumption (kWh)"
    ]


def test_dashboard_factory_is_bound_to_explicit_workspace(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    _workspace(first, "plug-one")
    _workspace(second, "plug-two")
    monkeypatch.chdir(unrelated)

    first_app = create_dashboard(first)
    second_app = create_dashboard(second)

    assert Path.cwd() == unrelated
    first_dropdown = _component_with_id(first_app.layout, "plug_dropdown")
    second_dropdown = _component_with_id(second_app.layout, "plug_dropdown")
    assert first_app.emers_workspace == first.resolve()
    assert second_app.emers_workspace == second.resolve()
    assert [option["label"] for option in first_dropdown.options] == ["plug-one"]
    assert [option["label"] for option in second_dropdown.options] == ["plug-two"]
    assert _component_with_id(first_app.layout, "run_dropdown") is not None
    assert len(first_app.callback_map) == 7
    assert len(second_app.callback_map) == 7
    assert first_app.server.test_client().get("/").status_code == 200


def test_run_dashboard_surfaces_segments_coverage_and_validation(tmp_path):
    _workspace(tmp_path, "plug")
    manager = MeasurementManager(
        "plug",
        experiment_name="run-view",
        polling_rate=0.001,
        workspace=tmp_path,
    )

    async def collect():
        task = asyncio.create_task(manager.log_data())
        await asyncio.sleep(0.02)
        manager.stop_event.set()
        await task

    asyncio.run(collect())
    store = RunStore(tmp_path)
    result = update_run_dashboard(
        str(manager.manifest_path),
        0.3,
        "USD",
        258,
        108.1,
        1,
        [],
        store=store,
    )

    summary, segments, _, gaps, _, gap_message, validation_status = result[:7]
    assert {item["Field"]: item["Value"] for item in summary}["Run ID"] == manager.run_id
    assert segments[0]["Provider"] == "mock"
    assert gaps == []
    assert gap_message == "No measurement gaps recorded."
    assert "validation is provisional" in validation_status
    assert result[8]
    assert len(result[10].data) == 1
    assert len(result[11].data) == 1

    update_run_dashboard(
        str(manager.manifest_path),
        0.3,
        "USD",
        258,
        108.1,
        1,
        [],
        store=store,
    )
    cache = store.cache_info()
    assert cache["csv_full_reads"] == 1
    assert cache["csv_hits"] >= 1
    assert cache["manifest_hits"] >= 1
