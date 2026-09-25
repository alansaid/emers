import time

import pytest

from emers.cli import init_workspace
from emers.reporting import generate_reports, generate_run_report
from emers.session import Run


def _completed_run(workspace, name="reported-run"):
    run = Run(
        name,
        source="MockPlug",
        polling_rate=0.005,
        workspace=workspace,
        benchmark_release="test-release",
    )
    with run:
        time.sleep(0.03)
        run.log_metrics({"quality": 0.9})
    return run


def test_generate_self_contained_report_for_run_id_prefix(tmp_path):
    init_workspace(tmp_path)
    run = _completed_run(tmp_path)

    report = generate_run_report(
        tmp_path,
        run.run_id[:8],
        output="custom-report.html",
        smoothness=2,
    )

    contents = report.read_text(encoding="utf-8")
    assert report == (tmp_path / "custom-report.html").resolve()
    assert "EMERS run report" in contents
    assert run.run_id in contents
    assert "Artifact validation:" in contents
    assert "Provider segments" in contents
    assert "plotly.js" in contents


def test_generate_reports_uses_latest_run_by_default(tmp_path):
    init_workspace(tmp_path)
    run = _completed_run(tmp_path)

    reports = generate_reports(tmp_path)

    assert reports == (
        (tmp_path / "report" / run.run_id / "report.html").resolve(),
    )


def test_generate_reports_writes_one_directory_per_run(tmp_path):
    init_workspace(tmp_path)
    first = _completed_run(tmp_path, "first-run")
    second = _completed_run(tmp_path, "second-run")

    reports = generate_reports(tmp_path, all_runs=True, output="published")

    assert set(reports) == {
        (tmp_path / "published" / first.run_id / "report.html").resolve(),
        (tmp_path / "published" / second.run_id / "report.html").resolve(),
    }
    assert all(report.is_file() for report in reports)


def test_generate_reports_rejects_one_file_for_all_runs(tmp_path):
    init_workspace(tmp_path)
    _completed_run(tmp_path)

    with pytest.raises(ValueError, match="directory"):
        generate_reports(tmp_path, all_runs=True, output="combined.html")
