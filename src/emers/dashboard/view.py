"""Run-oriented view models for the EMERS dashboard."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from emers.artifacts import RunArtifact, RunStore


@dataclass(frozen=True)
class RunDashboardData:
    artifact: RunArtifact
    manifest: dict
    readings: pd.DataFrame
    summary: tuple[dict, ...]
    segments: tuple[dict, ...]
    gaps: tuple[dict, ...]
    validation: dict
    validation_issues: tuple[dict, ...]


def _display(value, default="—"):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return round(value, 4)
    return value


def run_options(store: RunStore) -> list[dict]:
    """Return human-readable options for every discovered run."""

    options = []
    for run in store.runs():
        try:
            manifest = store.read_manifest(run)
            status = manifest.get("status", "unknown")
            created = manifest.get("created_at", "")
        except (OSError, ValueError):
            status = "invalid manifest"
            created = ""
        timestamp = f" · {created}" if created else ""
        options.append({
            "label": (
                f"{run.experiment} · {run.source} · {run.run_id[:8]} · "
                f"{status}{timestamp}"
            ),
            "value": str(run.manifest_path),
        })
    return options


def load_run_dashboard_data(
    store: RunStore, run: RunArtifact | str
) -> RunDashboardData:
    """Load the manifest, measurements, and validation state for one run."""

    artifact = store.run(run)
    manifest = store.read_manifest(artifact)
    readings = store.read_run(artifact)
    measurement = manifest.get("measurement", {})
    coverage = measurement.get("coverage") or {}
    summary_values = (
        ("Run ID", manifest.get("run_id", artifact.run_id)),
        ("Experiment", manifest.get("experiment", artifact.experiment)),
        ("Source", manifest.get("source", artifact.source)),
        ("Run status", manifest.get("status")),
        ("Measurement status", measurement.get("status")),
        ("Started", manifest.get("run_started_at") or manifest.get("created_at")),
        ("Ended", manifest.get("run_ended_at") or manifest.get("measurement_ended_at")),
        ("Samples", coverage.get("sample_count")),
        ("Coverage", (
            f"{float(coverage['coverage_ratio']) * 100:.2f}%"
            if coverage.get("coverage_ratio") is not None
            else None
        )),
        ("Gap time (s)", coverage.get("gap_seconds")),
        ("Fallback used", measurement.get("fallback_used")),
        ("Benchmark release", manifest.get("metadata", {}).get("benchmark_release")),
    )
    summary = tuple(
        {"Field": field, "Value": _display(value)} for field, value in summary_values
    )

    segments = []
    for segment in measurement.get("segments", []):
        achieved = segment.get("achieved_polling_interval_seconds") or {}
        segments.append({
            "Segment": segment.get("id"),
            "Provider": segment.get("provider"),
            "Method": segment.get("method"),
            "Scope": segment.get("scope"),
            "Status": segment.get("status"),
            "Fallback": _display(segment.get("fallback")),
            "Samples": segment.get("sample_count"),
            "Requested interval (s)": _display(segment.get(
                "requested_polling_interval_seconds"
            )),
            "Mean achieved interval (s)": _display(achieved.get("mean")),
            "Started": segment.get("started_at"),
            "Ended": segment.get("ended_at"),
        })

    gaps = []
    for index, gap in enumerate(measurement.get("gaps", []), 1):
        gaps.append({
            "Gap": index,
            "Provider": gap.get("provider"),
            "Started": gap.get("started_at_iso") or gap.get("started_at"),
            "Ended": gap.get("ended_at_iso") or gap.get("ended_at"),
            "Duration (s)": _display(gap.get("duration_seconds")),
            "Cause": gap.get("cause") or gap.get("reason"),
            "Resolution": gap.get("resolution"),
        })

    validation = store.validate_run(
        artifact, manifest=manifest, readings=readings
    )
    issues = tuple(
        {
            "Severity": severity,
            "Code": issue.get("code"),
            "Message": issue.get("message"),
        }
        for severity, collection in (
            ("Error", validation.get("errors", [])),
            ("Warning", validation.get("warnings", [])),
        )
        for issue in collection
    )
    return RunDashboardData(
        artifact=artifact,
        manifest=manifest,
        readings=readings,
        summary=summary,
        segments=tuple(segments),
        gaps=tuple(gaps),
        validation=validation,
        validation_issues=issues,
    )
