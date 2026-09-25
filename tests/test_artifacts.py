import json
from pathlib import Path

import pandas as pd

from emers.artifacts import ArtifactStore, RunStore


def _write_csv(path: Path, rows: list[tuple[float, float, float]]):
    pd.DataFrame(rows, columns=["timestamp", "current_draw", "total_draw"]).to_csv(
        path, index=False
    )


def test_store_discovers_only_measurement_artifacts(tmp_path):
    experiment = tmp_path / "measurements" / "plug-b" / "experiment-b"
    experiment.mkdir(parents=True)
    (experiment / ".emers" / "runs").mkdir(parents=True)
    _write_csv(experiment / "2.csv", [(2.0, 20.0, 0.2)])
    _write_csv(experiment / "1.csv", [(1.0, 10.0, 0.1)])
    (experiment / "notes.txt").write_text("not measurement data", encoding="utf-8")
    (tmp_path / "measurements" / ".hidden").mkdir()

    store = ArtifactStore(tmp_path)

    assert [source.name for source in store.sources()] == ["plug-b"]
    discovered = store.experiments(store.sources()[0])
    assert [item.name for item in discovered] == ["experiment-b"]
    assert [item.name for item in store.files(discovered[0])] == ["1.csv", "2.csv"]

    readings = store.read_experiment(discovered[0])
    assert readings["timestamp"].tolist() == [1.0, 2.0]


def test_store_handles_missing_workspace_and_empty_live_csv(tmp_path):
    store = ArtifactStore(tmp_path)
    assert store.sources() == ()
    assert store.read_all() == {}

    experiment = tmp_path / "measurements" / "plug" / "experiment"
    experiment.mkdir(parents=True)
    (experiment / "1.csv").touch()

    assert store.read_experiment(experiment).empty
    assert store.read_all() == {}


def test_run_store_discovers_manifests_and_filters_shared_csv_files(tmp_path):
    experiment = tmp_path / "measurements" / "plug" / "experiment"
    first_run = experiment / ".emers" / "runs" / "run-1"
    second_run = experiment / ".emers" / "runs" / "run-2"
    first_run.mkdir(parents=True)
    second_run.mkdir(parents=True)
    for run_path, run_id in ((first_run, "run-1"), (second_run, "run-2")):
        (run_path / "run.json").write_text(json.dumps({
            "run_id": run_id,
            "source": "plug",
            "experiment": "experiment",
        }))
        (run_path / "events.jsonl").write_text(
            json.dumps({"run_id": run_id, "event": "started"}) + "\n"
        )
    pd.DataFrame({
        "timestamp": [1.0, 2.0],
        "current_draw": [10.0, 20.0],
        "total_draw": [0.1, 0.2],
        "run_id": ["run-1", "run-2"],
    }).to_csv(experiment / "measurements.csv", index=False)

    store = RunStore(tmp_path)
    runs = {run.run_id: run for run in store.runs()}

    assert set(runs) == {"run-1", "run-2"}
    assert store.resolve_run("run-1") == runs["run-1"]
    assert store.resolve_run(runs["run-2"].manifest_path) == runs["run-2"]
    assert store.read_manifest(runs["run-1"])["run_id"] == "run-1"
    assert store.read_events(runs["run-1"])[0]["event"] == "started"
    assert store.read_run(runs["run-1"])["timestamp"].tolist() == [1.0]


def test_csv_cache_reuses_unchanged_data_and_reads_only_appended_rows(tmp_path):
    experiment = tmp_path / "measurements" / "plug" / "experiment"
    experiment.mkdir(parents=True)
    csv_path = experiment / "measurements.csv"
    _write_csv(csv_path, [(1.0, 10.0, 0.1)])
    store = ArtifactStore(tmp_path)

    first = store.read_file(csv_path)
    first.loc[0, "timestamp"] = -1
    unchanged = store.read_file(csv_path)

    assert unchanged["timestamp"].tolist() == [1.0]
    assert store.cache_info()["csv_full_reads"] == 1
    assert store.cache_info()["csv_hits"] == 1

    with csv_path.open("a", encoding="utf-8") as file:
        file.write("2.0,20.0")
    pending = store.read_file(csv_path)
    assert pending["timestamp"].tolist() == [1.0]

    with csv_path.open("a", encoding="utf-8") as file:
        file.write(",0.2\n")
    appended = store.read_file(csv_path)

    assert appended["timestamp"].tolist() == [1.0, 2.0]
    assert store.cache_info()["csv_incremental_reads"] == 2


def test_csv_cache_reloads_a_replaced_file(tmp_path):
    experiment = tmp_path / "measurements" / "plug" / "experiment"
    experiment.mkdir(parents=True)
    csv_path = experiment / "measurements.csv"
    replacement = experiment / "replacement.csv"
    _write_csv(csv_path, [(1.0, 10.0, 0.1)])
    store = ArtifactStore(tmp_path)
    store.read_file(csv_path)

    _write_csv(replacement, [(5.0, 50.0, 0.5), (6.0, 60.0, 0.6)])
    replacement.replace(csv_path)
    reloaded = store.read_file(csv_path)

    assert reloaded["timestamp"].tolist() == [5.0, 6.0]
    assert store.cache_info()["csv_full_reads"] == 2


def test_manifest_and_event_caches_invalidate_on_change(tmp_path):
    experiment = tmp_path / "measurements" / "plug" / "experiment"
    run_path = experiment / ".emers" / "runs" / "run-1"
    run_path.mkdir(parents=True)
    manifest_path = run_path / "run.json"
    events_path = run_path / "events.jsonl"
    manifest_path.write_text(json.dumps({"run_id": "run-1"}), encoding="utf-8")
    events_path.write_text(json.dumps({"event": "started"}) + "\n", encoding="utf-8")
    store = RunStore(tmp_path)
    run = store.runs()[0]

    manifest = store.read_manifest(run)
    manifest["run_id"] = "changed-by-caller"
    assert store.read_manifest(run)["run_id"] == "run-1"
    assert store.read_events(run) == ({"event": "started"},)
    assert store.read_events(run) == ({"event": "started"},)

    manifest_path.write_text(
        json.dumps({"run_id": "run-1", "status": "complete"}), encoding="utf-8"
    )
    with events_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"event": "completed"}) + "\n")

    assert store.read_manifest(run)["status"] == "complete"
    assert [event["event"] for event in store.read_events(run)] == [
        "started",
        "completed",
    ]
    info = store.cache_info()
    assert info["manifest_hits"] == 1
    assert info["manifest_reads"] == 2
    assert info["events_hits"] == 1
    assert info["events_full_reads"] == 1
    assert info["events_incremental_reads"] == 1
