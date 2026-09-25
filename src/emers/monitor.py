"""Compatibility facade for the historical ``emers.monitor`` API."""

from importlib import import_module


_implementation = import_module("emers.dashboard.app")

calculate_cost = _implementation.calculate_cost
calculate_information = _implementation.calculate_information
create_app = _implementation.create_app
create_dashboard = _implementation.create_dashboard
get_experiment_files = _implementation.get_experiment_files
make_graph = _implementation.make_graph
make_scatters = _implementation.make_scatters
refresh_run_dropdown = _implementation.refresh_run_dropdown
run = _implementation.run
update_experiment_dropdown = _implementation.update_experiment_dropdown
update_file_dropdown = _implementation.update_file_dropdown
update_graph = _implementation.update_graph
update_interval = _implementation.update_interval
update_interval_disabled = _implementation.update_interval_disabled
update_run_dashboard = _implementation.update_run_dashboard

__all__ = [
    "calculate_cost",
    "calculate_information",
    "create_app",
    "create_dashboard",
    "get_experiment_files",
    "make_graph",
    "make_scatters",
    "refresh_run_dropdown",
    "run",
    "update_experiment_dropdown",
    "update_file_dropdown",
    "update_graph",
    "update_interval",
    "update_interval_disabled",
    "update_run_dashboard",
]


def __getattr__(name):
    return getattr(_implementation, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_implementation)))
