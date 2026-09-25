"""Canonical dashboard API."""

from emers.dashboard.view import RunDashboardData, load_run_dashboard_data


def create_dashboard(workspace="."):
    from emers.dashboard.app import create_dashboard as factory

    return factory(workspace)


create_app = create_dashboard


def run(host="127.0.0.1", port=5000, debug=False, workspace="."):
    from emers.dashboard.app import run as run_dashboard

    return run_dashboard(host=host, port=port, debug=debug, workspace=workspace)

__all__ = [
    "RunDashboardData",
    "create_app",
    "create_dashboard",
    "load_run_dashboard_data",
    "run",
]
