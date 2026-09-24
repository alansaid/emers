import csv
import json
from time import sleep

from emers.cli import init_workspace
from emers.session import Run
from emers.validation import validate_workspace


def test_v2_artifacts_record_timing_units_provenance_and_validate(tmp_path):
    init_workspace(tmp_path)
    settings = {
        "source": {
            "provider": "mock",
            "calibration": {
                "reference_meter": "lab-analyzer-1",
                "reference_uncertainty_percent": 0.5,
                "calibrated_at": "2026-09-24",
            },
        }
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings))

    with Run(
        "validation",
        source="source",
        workspace=tmp_path,
        polling_rate=0.005,
        benchmark_release="benchmark-v1",
    ) as run:
        sleep(0.03)

    manifest = json.loads(run.manifest_path.read_text())
    csv_path = next(run.manager.log_base.glob("*.csv"))
    with csv_path.open(newline="") as file:
        rows = list(csv.DictReader(file))

    assert manifest["schema_version"] == 2
    assert manifest["metadata"]["benchmark_release"] == "benchmark-v1"
    assert len(manifest["metadata"]["environment"]["packages_sha256"]) == 64
    assert len(manifest["metadata"]["emers_code_sha256"]) == 64
    assert manifest["measurement"]["calibration"]["reference_uncertainty_percent"] == 0.5
    assert manifest["measurement"]["coverage"]["coverage_ratio"] == 1.0
    assert manifest["measurement"]["coverage"]["sample_count"] == len(rows)
    assert rows[0]["current_draw_unit"] == "W"
    assert rows[0]["total_draw_unit"] == "kWh"
    assert float(rows[0]["request_ended_at"]) >= float(rows[0]["request_started_at"])
    assert float(rows[0]["request_ended_monotonic"]) >= float(
        rows[0]["request_started_monotonic"]
    )
    assert float(rows[0]["acquisition_latency_seconds"]) >= 0
    assert any(row["achieved_polling_interval_seconds"] for row in rows[1:])

    report = validate_workspace(tmp_path)
    assert report["valid"] is True
    assert report["summary"]["runs"] == 1


def test_validation_rejects_inconsistent_units(tmp_path):
    init_workspace(tmp_path)
    with Run(
        "invalid-units",
        source="MockPlug",
        workspace=tmp_path,
        polling_rate=0.005,
    ) as run:
        sleep(0.02)

    csv_path = next(run.manager.log_base.glob("*.csv"))
    text = csv_path.read_text().replace(",W,", ",mW,", 1)
    csv_path.write_text(text)

    report = validate_workspace(tmp_path)

    assert report["valid"] is False
    assert any(
        error["code"] == "unit_mismatch"
        for error in report["runs"][0]["errors"]
    )


def test_validation_warns_when_tapo_resolution_is_inadequate(tmp_path):
    init_workspace(tmp_path)
    with Run(
        "tapo-resolution",
        source="MockPlug",
        workspace=tmp_path,
        polling_rate=0.005,
    ) as run:
        sleep(0.02)

    manifest = json.loads(run.manifest_path.read_text())
    manifest["measurement"]["segments"][0]["provider"] = "tapo"
    run.manifest_path.write_text(json.dumps(manifest))

    csv_path = next(run.manager.log_base.glob("*.csv"))
    with csv_path.open(newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames
        rows = list(reader)
    for row in rows:
        row["provider"] = "tapo"
        row["total_draw"] = "1.0"
        row["misc"] = json.dumps({"cumulative_energy_resolution_kwh": 0.001})
    with csv_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    report = validate_workspace(tmp_path)

    assert report["valid"] is True
    assert any(
        warning["code"] == "tapo_resolution_insufficient"
        for warning in report["runs"][0]["warnings"]
    )
