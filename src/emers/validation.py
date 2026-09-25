"""Machine-readable validation for EMERS run artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
from math import isnan
from collections import defaultdict
from pathlib import Path


V2_REQUIRED_COLUMNS = {
    "timestamp",
    "timestamp_unit",
    "timestamp_monotonic",
    "request_started_at",
    "request_ended_at",
    "request_started_monotonic",
    "request_ended_monotonic",
    "acquisition_latency_seconds",
    "requested_polling_interval_seconds",
    "effective_polling_interval_seconds",
    "achieved_polling_interval_seconds",
    "current_draw",
    "current_draw_unit",
    "total_draw",
    "total_draw_unit",
    "source",
    "provider",
    "measurement_method",
    "measurement_scope",
    "segment_id",
    "segment_index",
    "run_id",
}


def _issue(collection, code, message, **context):
    collection.append({"code": code, "message": message, **context})


def _float(value):
    if value is None or (isinstance(value, str) and value == ""):
        return None
    try:
        if isnan(value):
            return None
    except TypeError:
        pass
    return float(value)


def _contains_sensitive_key(value):
    sensitive = ("password", "secret", "token", "credential", "api_key", "user")
    if isinstance(value, dict):
        return any(
            any(part in str(key).lower() for part in sensitive)
            or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _read_events(path, run_id, errors, require_run_id=False):
    events = []
    if not path.is_file():
        _issue(errors, "events_missing", "events.jsonl is missing", path=str(path))
        return events
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            _issue(
                errors,
                "event_invalid_json",
                str(exc),
                path=str(path),
                line=line_number,
            )
            continue
        if require_run_id and event.get("run_id") is None:
            _issue(
                errors,
                "event_run_id_missing",
                "Event has no run_id",
                line=line_number,
            )
        elif event.get("run_id") not in (None, run_id):
            _issue(
                errors,
                "event_run_id_mismatch",
                "Event run_id does not match manifest",
                line=line_number,
            )
        events.append(event)
    return events


def _validate_rows(rows, manifest, errors, warnings):
    run_id = manifest.get("run_id")
    schema_version = manifest.get("schema_version", 1)
    segments = {
        segment.get("id"): segment
        for segment in manifest.get("measurement", {}).get("segments", [])
    }
    by_segment = defaultdict(list)
    previous_monotonic = {}

    for location, row in rows:
        if row.get("run_id") != run_id:
            continue
        if schema_version >= 2:
            missing = sorted(V2_REQUIRED_COLUMNS - row.keys())
            if missing:
                _issue(
                    errors,
                    "csv_columns_missing",
                    f"Missing columns: {', '.join(missing)}",
                    location=location,
                )
                continue
            if row["current_draw_unit"] != "W" or row["total_draw_unit"] != "kWh":
                _issue(
                    errors,
                    "unit_mismatch",
                    "Expected current_draw=W and total_draw=kWh",
                    location=location,
                )
            try:
                start_wall = _float(row["request_started_at"])
                end_wall = _float(row["request_ended_at"])
                start_mono = _float(row["request_started_monotonic"])
                end_mono = _float(row["request_ended_monotonic"])
                latency = _float(row["acquisition_latency_seconds"])
                sample_mono = _float(row["timestamp_monotonic"])
                if None in (start_wall, end_wall, start_mono, end_mono, latency, sample_mono):
                    raise ValueError("required timing value is empty")
                if end_wall < start_wall or end_mono < start_mono or latency < 0:
                    raise ValueError("request timing is negative or reversed")
                if abs((end_mono - start_mono) - latency) > 1e-6:
                    raise ValueError("latency differs from monotonic request duration")
                requested = _float(row["requested_polling_interval_seconds"])
                effective = _float(row["effective_polling_interval_seconds"])
                achieved = _float(row["achieved_polling_interval_seconds"])
                if requested is None or effective is None or requested <= 0 or effective <= 0:
                    raise ValueError("requested/effective polling intervals must be positive")
                if achieved is not None and achieved < 0:
                    raise ValueError("achieved polling interval cannot be negative")
            except (TypeError, ValueError) as exc:
                _issue(
                    errors,
                    "timing_invalid",
                    str(exc),
                    location=location,
                )
                continue
            segment_id = row["segment_id"]
            if segment_id not in segments:
                _issue(
                    errors,
                    "segment_unknown",
                    f"CSV references unknown segment {segment_id!r}",
                    location=location,
                )
            else:
                segment = segments[segment_id]
                expected = {
                    "provider": segment.get("provider"),
                    "measurement_method": segment.get("method"),
                    "measurement_scope": segment.get("scope"),
                }
                for field, expected_value in expected.items():
                    if expected_value is not None and row.get(field) != expected_value:
                        _issue(
                            errors,
                            "segment_provenance_mismatch",
                            f"CSV {field} does not match segment manifest",
                            location=location,
                            segment_id=segment_id,
                        )
            if segment_id in previous_monotonic and sample_mono < previous_monotonic[segment_id]:
                _issue(
                    errors,
                    "monotonic_order_invalid",
                    "Sample monotonic timestamps moved backwards",
                    location=location,
                )
            previous_monotonic[segment_id] = sample_mono
        by_segment[row.get("segment_id", "legacy")].append(row)

    expected_samples = manifest.get("measurement", {}).get("coverage", {}).get("sample_count")
    actual_samples = sum(len(items) for items in by_segment.values())
    if schema_version >= 2 and actual_samples == 0:
        _issue(errors, "samples_missing", "Run contains no measurement samples")
    if expected_samples is not None and expected_samples != actual_samples:
        _issue(
            errors,
            "sample_count_mismatch",
            f"Manifest records {expected_samples} samples but CSV contains {actual_samples}",
        )
    for segment_id, segment in segments.items():
        expected = segment.get("sample_count")
        actual = len(by_segment.get(segment_id, []))
        if expected is not None and expected != actual:
            _issue(
                errors,
                "segment_sample_count_mismatch",
                f"Segment records {expected} samples but CSV contains {actual}",
                segment_id=segment_id,
            )

    for segment_id, segment_rows in by_segment.items():
        if not segment_rows or segment_rows[0].get("provider") != "tapo":
            continue
        totals = [_float(row.get("total_draw")) for row in segment_rows]
        totals = [value for value in totals if value is not None]
        if not totals:
            continue
        resolution = 0.001
        try:
            details = json.loads(segment_rows[0].get("misc") or "{}")
            resolution = float(
                details.get("cumulative_energy_resolution_kwh", resolution)
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
        observed_delta = max(totals) - min(totals)
        if observed_delta <= resolution:
            _issue(
                warnings,
                "tapo_resolution_insufficient",
                "Observed cumulative-energy change is not above the declared Tapo "
                "resolution; use the reference analyzer for interval energy",
                segment_id=segment_id,
                observed_delta_kwh=observed_delta,
                resolution_kwh=resolution,
            )
    return actual_samples


def validate_manifest(
    manifest_path,
    strict_benchmark=False,
    *,
    manifest=None,
    rows=None,
):
    """Validate one run, optionally using artifact data loaded by a RunStore."""

    manifest_path = Path(manifest_path)
    errors = []
    warnings = []
    if manifest is None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _issue(errors, "manifest_invalid", str(exc), path=str(manifest_path))
            return {"path": str(manifest_path), "valid": False, "errors": errors, "warnings": warnings}

    for field in ("schema_version", "run_id", "experiment", "source", "measurement"):
        if field not in manifest:
            _issue(errors, "manifest_field_missing", f"Missing manifest field {field!r}")
    schema_version = manifest.get("schema_version", 1)
    if schema_version < 2:
        _issue(
            warnings,
            "legacy_schema",
            "Schema predates explicit timing, units, and coverage evidence",
        )
    measurement = manifest.get("measurement", {})
    sensitive_areas = [measurement.get("calibration", {})]
    for segment in measurement.get("segments", []):
        sensitive_areas.extend(
            (segment.get("configuration", {}), segment.get("calibration", {}))
        )
    if any(_contains_sensitive_key(area) for area in sensitive_areas):
        _issue(errors, "credentials_present", "Manifest contains a credential-like key")
    if not manifest.get("software", {}).get("emers_version"):
        _issue(errors, "emers_version_missing", "EMERS version is missing")

    metadata = manifest.get("metadata", {})
    environment = metadata.get("environment", {})
    if not metadata.get("emers_code_sha256"):
        _issue(errors, "emers_code_hash_missing", "EMERS package code hash is missing")
    if not environment.get("packages_sha256"):
        _issue(errors, "environment_hash_missing", "Python environment hash is missing")
    elif environment.get("packages"):
        calculated = hashlib.sha256(
            "\n".join(environment["packages"]).encode("utf-8")
        ).hexdigest()
        if calculated != environment["packages_sha256"]:
            _issue(
                errors,
                "environment_hash_mismatch",
                "Python package list does not match packages_sha256",
            )
    git = metadata.get("git")
    if not git or not git.get("commit"):
        target = errors if strict_benchmark else warnings
        _issue(target, "commit_missing", "Git commit was not recorded")
    elif git.get("dirty"):
        target = errors if strict_benchmark else warnings
        _issue(target, "dirty_worktree", "Run used a dirty Git worktree")
    emers_git = metadata.get("emers_git")
    if emers_git and emers_git.get("dirty"):
        target = errors if strict_benchmark else warnings
        _issue(target, "dirty_emers_source", "EMERS source tree was dirty")
    if not metadata.get("benchmark_release"):
        target = errors if strict_benchmark else warnings
        _issue(target, "benchmark_release_missing", "Immutable benchmark release was not recorded")

    for segment in manifest.get("measurement", {}).get("segments", []):
        if segment.get("scope") != "whole_system_wall":
            continue
        calibration = segment.get("calibration") or {}
        required = ("reference_meter", "reference_uncertainty_percent")
        missing = [field for field in required if calibration.get(field) in (None, "")]
        if missing:
            target = errors if strict_benchmark else warnings
            _issue(
                target,
                "calibration_metadata_missing",
                f"Whole-system segment lacks: {', '.join(missing)}",
                segment_id=segment.get("id"),
            )

    run_id = manifest.get("run_id")
    run_dir = manifest_path.parent
    events = _read_events(
        run_dir / "events.jsonl", run_id, errors, require_run_id=schema_version >= 2
    )
    if len(manifest_path.parents) < 4:
        _issue(
            errors,
            "manifest_location_invalid",
            "Manifest is not under <experiment>/.emers/runs/<run-id>/run.json",
        )
        experiment_dir = manifest_path.parent
    else:
        experiment_dir = manifest_path.parents[3]
    if rows is None:
        rows = []
        for csv_path in sorted(experiment_dir.glob("*.csv")):
            try:
                with csv_path.open(newline="", encoding="utf-8") as file:
                    for line_number, row in enumerate(csv.DictReader(file), 2):
                        rows.append((f"{csv_path}:{line_number}", row))
            except (OSError, csv.Error) as exc:
                _issue(errors, "csv_invalid", str(exc), path=str(csv_path))
    sample_count = _validate_rows(rows, manifest, errors, warnings)

    coverage = manifest.get("measurement", {}).get("coverage")
    if schema_version >= 2 and not manifest.get("measurement", {}).get(
        "source_configuration_sha256"
    ):
        _issue(errors, "configuration_hash_missing", "Source configuration hash is missing")
    if schema_version >= 2 and not coverage:
        _issue(errors, "coverage_missing", "Derived coverage metrics are missing")
    elif coverage:
        ratio = coverage.get("coverage_ratio")
        if ratio is not None and not 0 <= ratio <= 1:
            _issue(errors, "coverage_invalid", "coverage_ratio must be between 0 and 1")
        if coverage.get("gap_count") != len(manifest.get("measurement", {}).get("gaps", [])):
            _issue(errors, "gap_count_mismatch", "Coverage gap count does not match gap records")
        duration = coverage.get("duration_seconds")
        covered = coverage.get("covered_seconds")
        gap_seconds = coverage.get("gap_seconds")
        if None not in (duration, covered, gap_seconds):
            if abs(float(duration) - float(covered) - float(gap_seconds)) > 1e-6:
                _issue(
                    errors,
                    "coverage_arithmetic_invalid",
                    "covered_seconds + gap_seconds must equal duration_seconds",
                )

    return {
        "path": str(manifest_path),
        "run_id": run_id,
        "schema_version": schema_version,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "statistics": {"events": len(events), "samples": sample_count},
    }


def validate_workspace(workspace, run=None, strict_benchmark=False):
    workspace = Path(workspace).expanduser().resolve()
    if run:
        candidate = Path(run).expanduser().resolve()
        manifests = [candidate / "run.json" if candidate.is_dir() else candidate]
    else:
        manifests = sorted(workspace.glob("measurements/*/*/.emers/runs/*/run.json"))
    results = [
        validate_manifest(path, strict_benchmark=strict_benchmark)
        for path in manifests
    ]
    errors = sum(len(result["errors"]) for result in results)
    warnings = sum(len(result["warnings"]) for result in results)
    if not manifests:
        errors += 1
    return {
        "schema": "emers-validation-v1",
        "workspace": str(workspace),
        "valid": errors == 0,
        "summary": {
            "runs": len(results),
            "valid_runs": sum(result["valid"] for result in results),
            "errors": errors,
            "warnings": warnings,
        },
        "errors": (
            [{"code": "no_runs", "message": "No run manifests were found"}]
            if not manifests
            else []
        ),
        "runs": results,
    }
