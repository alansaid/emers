"""Standalone, run-oriented HTML report generation."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from emers.analysis import analyze_measurements, calculate_impact
from emers.artifacts import RunArtifact, RunStore


DEFAULT_REPORT_SETTINGS = {
    "cost_per_kwh": 0.3,
    "currency": "USD",
    "gco2e_per_kwh": 258,
    "gco2e_per_kilometer_car": 108.1,
}


def _table(rows, empty_message="None recorded."):
    if not rows:
        return f"<p class='empty'>{escape(empty_message)}</p>"
    return pd.DataFrame(rows).to_html(index=False, border=0, escape=True)


def _figures(analysis, smoothness):
    power = go.Figure()
    energy = go.Figure()
    for segment in analysis.segments:
        readings = segment.readings
        power.add_trace(go.Scatter(
            x=readings["timestamp"],
            y=readings["current_draw"],
            name=segment.label,
        ))
        energy.add_trace(go.Scatter(
            x=readings["timestamp"],
            y=readings["total_draw"],
            name=segment.label,
        ))
        if smoothness > 1:
            power.add_trace(go.Scatter(
                x=readings["timestamp"],
                y=readings["current_draw_smooth"],
                name=f"Smoothed · {segment.label}",
            ))
            energy.add_trace(go.Scatter(
                x=readings["timestamp"],
                y=readings["total_draw_smooth"],
                name=f"Smoothed · {segment.label}",
            ))
    power.update_layout(
        title="Power over time",
        xaxis_title="Time (s)",
        yaxis_title="Power (W)",
        legend={"orientation": "h"},
    )
    energy.update_layout(
        title="Cumulative energy over time",
        xaxis_title="Time (s)",
        yaxis_title="Energy (kWh)",
        legend={"orientation": "h"},
    )
    return power, energy


def _report_settings(workspace, overrides):
    settings = dict(DEFAULT_REPORT_SETTINGS)
    path = workspace / "monitor_settings.json"
    if path.is_file():
        settings.update(json.loads(path.read_text(encoding="utf-8")))
    settings.update({key: value for key, value in overrides.items() if value is not None})
    for key in ("cost_per_kwh", "gco2e_per_kwh", "gco2e_per_kilometer_car"):
        try:
            settings[key] = float(settings[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid report setting {key}: {settings[key]!r}") from exc
    if settings["cost_per_kwh"] < 0 or settings["gco2e_per_kwh"] < 0:
        raise ValueError("Cost and carbon intensity cannot be negative")
    if settings["gco2e_per_kilometer_car"] <= 0:
        raise ValueError("gco2e_per_kilometer_car must be greater than zero")
    return settings


def generate_run_report(
    workspace,
    run: RunArtifact | str | Path | None = None,
    *,
    output=None,
    cost_per_kwh=None,
    currency=None,
    gco2e_per_kwh=None,
    gco2e_per_kilometer_car=None,
    smoothness=1,
):
    """Generate a self-contained HTML report for one run and return its path."""

    workspace = Path(workspace).expanduser().resolve()
    if smoothness < 1:
        raise ValueError("smoothness must be at least 1")
    store = RunStore(workspace)
    artifact = store.resolve_run(run)
    manifest = store.read_manifest(artifact)
    readings = store.read_run(artifact)
    settings = _report_settings(workspace, {
        "cost_per_kwh": cost_per_kwh,
        "currency": currency,
        "gco2e_per_kwh": gco2e_per_kwh,
        "gco2e_per_kilometer_car": gco2e_per_kilometer_car,
    })
    analysis = analyze_measurements(
        {artifact.experiment: readings}, smoothness=smoothness
    )
    validation = store.validate_run(
        artifact, manifest=manifest, readings=readings
    )
    measurement = manifest.get("measurement", {})
    coverage = measurement.get("coverage") or {}

    summary = [{
        "Run ID": manifest.get("run_id", artifact.run_id),
        "Experiment": manifest.get("experiment", artifact.experiment),
        "Source": manifest.get("source", artifact.source),
        "Status": manifest.get("status"),
        "Started": manifest.get("run_started_at") or manifest.get("created_at"),
        "Ended": manifest.get("run_ended_at") or manifest.get("measurement_ended_at"),
        "Samples": coverage.get("sample_count"),
        "Coverage": (
            f"{float(coverage['coverage_ratio']) * 100:.2f}%"
            if coverage.get("coverage_ratio") is not None else None
        ),
        "Gap time (s)": coverage.get("gap_seconds"),
    }]
    if readings.empty:
        impact = {"Energy": "Unavailable (no samples)"}
        power_html = energy_html = "<p class='empty'>No measurement samples.</p>"
    else:
        impact = calculate_impact(
            analysis.total_energy_kwh,
            settings["cost_per_kwh"],
            settings["currency"],
            settings["gco2e_per_kwh"],
            settings["gco2e_per_kilometer_car"],
        )
        power, energy = _figures(analysis, smoothness)
        power_html = power.to_html(
            full_html=False, include_plotlyjs=True, config={"responsive": True}
        )
        energy_html = energy.to_html(
            full_html=False, include_plotlyjs=False, config={"responsive": True}
        )

    issues = [
        {"Severity": severity, "Code": item.get("code"), "Message": item.get("message")}
        for severity, collection in (
            ("Error", validation.get("errors", [])),
            ("Warning", validation.get("warnings", [])),
        )
        for item in collection
    ]
    validation_label = (
        f"Valid with {len(validation['warnings'])} warning(s)"
        if validation.get("valid")
        else f"Invalid: {len(validation['errors'])} error(s), "
             f"{len(validation['warnings'])} warning(s)"
    )
    created = datetime.now(timezone.utc).isoformat()
    html_document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EMERS report · {escape(artifact.experiment)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; color: #17211b; margin: 0 auto; max-width: 1200px; padding: 32px; }}
    h1, h2 {{ color: #075f16; }}
    .status {{ padding: 12px; background: #eef4ee; border-left: 4px solid #067b04; }}
    .meta, .empty {{ color: #52605a; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; display: block; overflow-x: auto; }}
    th, td {{ border: 1px solid #ccd5cf; padding: 8px; text-align: left; white-space: nowrap; }}
    th {{ background: #eef4ee; }}
  </style>
</head>
<body>
  <h1>EMERS run report</h1>
  <p class="meta">Generated {escape(created)}</p>
  <p class="status"><strong>Artifact validation:</strong> {escape(validation_label)}</p>
  <h2>Run summary</h2>
  {_table(summary)}
  <h2>Energy and impact</h2>
  {_table([impact])}
  <h2>Provider segments</h2>
  {_table(measurement.get('segments', []), 'No provider segments recorded.')}
  <h2>Measurement gaps</h2>
  {_table(measurement.get('gaps', []), 'No measurement gaps recorded.')}
  <h2>Validation findings</h2>
  {_table(issues, 'No validation findings.')}
  <h2>Measurements</h2>
  {power_html}
  {energy_html}
</body>
</html>
"""

    if output is None:
        report_path = workspace / "report" / artifact.run_id / "report.html"
    else:
        report_path = Path(output).expanduser()
        if not report_path.is_absolute():
            report_path = workspace / report_path
        if report_path.suffix.lower() != ".html":
            report_path = report_path / artifact.run_id / "report.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html_document, encoding="utf-8")
    return report_path.resolve()


def generate_reports(workspace, *, run=None, all_runs=False, output=None, **settings):
    """Generate one report (latest by default) or one report per discovered run."""

    workspace = Path(workspace).expanduser().resolve()
    store = RunStore(workspace)
    artifacts = store.runs() if all_runs else (store.resolve_run(run),)
    if not artifacts:
        raise ValueError(f"No run manifests found in {workspace}")
    if all_runs and output is not None and Path(output).suffix.lower() == ".html":
        raise ValueError("--output must be a directory when --all is used")
    return tuple(
        generate_run_report(
            workspace,
            artifact,
            output=output,
            **settings,
        )
        for artifact in artifacts
    )
