# EMERS: Energy Meter for Experimental Research Software

EMERS measures, records, visualizes, and reports the energy consumption of
computational experiments. It supports physical whole-system meters, hardware
energy counters, and software estimates while preserving the provenance and
scope of every reading.

Originally developed for recommender-system experiments, EMERS is
domain-independent and can wrap arbitrary computational workloads. The
accompanying paper was accepted at the
[RecSoGood 2024 Workshop](https://recsogood.github.io/recsogood24/), colocated
with the 18th ACM Conference on Recommender Systems. A
[demo video](https://youtu.be/vmXOcrVpRDg) shows the original workflow.

## Installation

EMERS requires Python 3.10 or newer.

```bash
python -m pip install emers
```

Optional providers are installed through extras:

```bash
python -m pip install "emers[codecarbon]"
python -m pip install "emers[zeus]"
python -m pip install "emers[all]"
```

For development from a clone:

```bash
python -m pip install -e ".[all]"
```

## Concepts

- A **source** is a named configuration in `settings.json`, such as
  `office_plug` or `software_estimate`.
- A **provider** is the implementation that produces readings, such as
  `tapo`, `shelly`, `zeus`, or `codecarbon`.
- A **method** says whether readings are `measured`, `estimated`, or
  `synthetic`.
- A **scope** identifies what is covered, such as `whole_system_wall`,
  `hardware_components`, or `cpu_gpu_ram`.
- A **segment** is one uninterrupted interval collected by one provider with
  one method and scope.

Provider transitions always create a new segment. EMERS does not stitch a
whole-system meter total to a component-level estimate.

## Quick start

Initialize a workspace:

```bash
emers init
```

This creates `settings.json`, `monitor_settings.json`, `measurements/`, and
`report/` without overwriting existing configuration. The initial workspace
contains a synthetic mock source.

Start measurement:

```bash
emers measure
```

EMERS presents an interactive menu for selecting, adding, editing, or removing
sources. Tapo password input is hidden, source removal requires confirmation,
and historical measurements are retained. Skip the menu with:

```bash
emers measure --source MockPlug
```

Press Ctrl+C to stop. Start the dashboard separately with:

```bash
emers dashboard
```

`emers monitor` remains available as a compatibility alias.

For the usual local workflow, start measurement and the dashboard together:

```bash
emers run --source MockPlug
```

The dashboard is available at <http://127.0.0.1:5000>. Press Ctrl+C to stop
both services cleanly.

The dashboard selects recorded runs rather than raw CSV files. For each run it
shows lifecycle and coverage information, energy and impact estimates,
provider segments, measurement gaps, validation errors and warnings, and
segment-aware power and energy plots. It refreshes while a run is active.
CSV-only experiments created by older EMERS versions remain available under
the collapsible legacy section.

Generate a self-contained HTML report for the latest run:

```bash
emers report
```

Select a run by its full ID, a unique ID prefix, its run directory, or its
`run.json` path. Generate one report per run with `--all`:

```bash
emers report --run 83b4b6e2
emers report --all --output published-reports
```

Reports default to `report/<run-id>/report.html`. Cost and carbon assumptions
come from `monitor_settings.json` and can be overridden with options such as
`--cost-per-kwh`, `--currency`, and `--gco2e-per-kwh`.

Use a different workspace when required:

```bash
emers --workspace path/to/experiment init
emers --workspace path/to/experiment run --source MockPlug
```

Run `emers --help` or `emers <command> --help` for all options. EMERS can
also be invoked as `python -m emers`.

## Source configuration

Sources are top-level entries in `settings.json`. Do not commit this file when
it contains credentials. See [settings.example.json](settings.example.json) for
a safe minimal example.

### Mock

```json
{
  "MockPlug": {
    "provider": "mock"
  }
}
```

The mock provider is synthetic. Select CodeCarbon or Zeus when a software or
hardware-counter measurement is wanted without a physical plug.

### Shelly Plug Plus S

```json
{
  "shelly_meter": {
    "provider": "shelly",
    "device_ip": "192.168.2.1",
    "device_id": "0"
  }
}
```

### TP-Link Tapo P115

```json
{
  "tapo_meter": {
    "provider": "tapo",
    "device_ip": "192.168.2.2",
    "tapo_user": "yourname@example.com",
    "tapo_password": "password"
  }
}
```

The computer running EMERS must be able to reach the physical plug over the
network.

Tapo cumulative energy is treated as having a default resolution of 0.001 kWh
(1 Wh), configurable with `cumulative_energy_resolution_kwh` if a validated
device-specific value is available. For a short run whose observed cumulative
change is not greater than that resolution, `emers validate` emits
`tapo_resolution_insufficient`. Do not use that cumulative value as interval
energy; use the reference analyzer instead.

### CodeCarbon

CodeCarbon estimates CPU, GPU, and RAM energy. Its readings use the
`estimated` method and `cpu_gpu_ram` scope; they are not presented as
equivalent to whole-system wall power.

```json
{
  "software_estimate": {
    "provider": "codecarbon",
    "tracking_mode": "machine",
    "measure_power_secs": 15,
    "country_iso_code": "SWE"
  }
}
```

Supplying a three-letter country code uses CodeCarbon's offline tracker and
avoids a location lookup.

### Zeus

Zeus reads supported GPU, CPU/RAPL, DRAM, and SoC energy counters. Availability
depends on the host hardware and permissions. Install it with
`pip install "emers[zeus]"`.

On Apple Silicon, also install Zeus's platform extra with
`pip install "zeus[apple]"`.

```json
{
  "hardware_counters": {
    "provider": "zeus",
    "measure_power_secs": 1,
    "gpu_indices": [0],
    "cpu_indices": [0],
    "sync_execution": true,
    "sync_execution_with": "torch"
  }
}
```

Omit index lists to use every counter Zeus can discover. On Linux, CPU and DRAM
RAPL access may require elevated permissions or the Zeus daemon. If Zeus finds
no supported GPU, CPU/RAPL, or SoC counter, EMERS marks it unavailable and
tries the next fallback.

### Calibration and reference uncertainty

Calibration metadata belongs in the source configuration and is copied into
the manifest without credentials:

```json
{
  "lab_meter": {
    "provider": "tapo",
    "device_ip": "192.168.2.2",
    "tapo_user": "yourname@example.com",
    "tapo_password": "password",
    "calibration": {
      "calibrated_at": "2026-09-24",
      "reference_meter": "analyzer-serial-123",
      "reference_uncertainty_percent": 0.5,
      "procedure": "steady-load comparison",
      "certificate": "CAL-2026-0042"
    }
  }
}
```

EMERS records these declarations but does not infer calibration factors or
uncertainty. Any applied correction, its units, and the reference instrument
must be stated explicitly in this object.

## Ordered fallback chains

A source may define an ordered `fallbacks` list. Each item can be:

- the name of another configured source;
- a built-in provider name such as `zeus` or `codecarbon`; or
- an inline provider configuration.

```json
{
  "software_estimate": {
    "provider": "codecarbon",
    "tracking_mode": "machine",
    "country_iso_code": "SWE"
  },
  "experiment_power": {
    "provider": "tapo",
    "device_ip": "192.168.2.2",
    "tapo_user": "yourname@example.com",
    "tapo_password": "password",
    "fallbacks": [
      {
        "provider": "zeus",
        "gpu_indices": [0],
        "measure_power_secs": 1
      },
      "software_estimate"
    ]
  }
}
```

With `failure_policy="fallback"`, EMERS behaves as follows:

1. The primary provider is started before experiment code is entered.
2. If startup fails, fallback providers are tried immediately in order.
3. During a run, transient failures use bounded exponential backoff.
4. After `max_failures` consecutive failures, EMERS tries the remaining
   fallback providers in order.
5. If none are available, the experiment continues with explicit measurement
   gaps while EMERS retries the active provider.

Fallbacks are cold failovers: they start when needed rather than running in
parallel. Consequently, a provider transition can contain a short gap.

Override the configured chain from the CLI by repeating `--fallback-source`:

```bash
emers run --source experiment_power \
  --fallback-source hardware_counters \
  --fallback-source software_estimate
```

If neither configuration nor CLI supplies a chain, EMERS uses CodeCarbon as
the default fallback. Install `emers[codecarbon]` to make that fallback
available. Set `"fallbacks": []` to disable the default fallback chain.

Alternative failure policies are:

```bash
emers run --source experiment_power --failure-policy continue
emers run --source experiment_power --failure-policy fail
```

- `continue` records gaps and retries the current provider without switching.
- `fail` rejects an unavailable provider before the experiment starts. A
  persistent runtime failure is raised when the run context exits because a
  background measurement thread cannot safely inject an exception into
  experiment code.

## Python API

The recommended API wraps the experiment in an EMERS run:

```python
import emers

with emers.run(
    "model-training",
    source="experiment_power",
    parameters={"model": "BPR", "factors": 128},
    tags=["baseline"],
    benchmark_release="benchmark-v1.0.0",
) as run:
    with run.phase("training"):
        model.fit(train_data)

    with run.phase("evaluation"):
        metrics = evaluate(model, test_data)
        run.log_metrics(metrics)
```

Fallbacks can also be supplied directly:

```python
with emers.run(
    "model-training",
    source="tapo_meter",
    fallbacks=[
        {"provider": "zeus", "gpu_indices": [0]},
        "software_estimate",
    ],
    max_failures=3,
) as run:
    model.fit(train_data)
```

Runs can be applied as a decorator:

```python
from emers import track

@track("model-training", source="experiment_power")
def train():
    ...
```

The optional `workspace` argument selects the directory containing
`settings.json` and receiving measurements. `MeasurementManager` remains
available as the lower-level acquisition API.

The dashboard also has an application factory for embedding or custom server
integration. It does not change the process working directory:

```python
from emers.dashboard import create_dashboard

dashboard = create_dashboard("path/to/experiment")
server = dashboard.server  # Flask WSGI application
```

`create_app` is an alias for `create_dashboard`. Multiple dashboard instances
can be created in one process and remain bound to their own workspaces.
Each dashboard keeps a workspace-local `RunStore`: unchanged manifests and
measurement chunks are reused, while live CSV files are read from their last
complete row when new samples arrive. Replaced or truncated artifacts are
detected and loaded again in full.

The dashboard remains a Dash application: its small callback graph and
two-second local polling model fit the framework without requiring a separate
JavaScript frontend or ASGI service. The canonical implementation lives under
`emers.dashboard`; `emers.monitor` is retained only as a compatibility facade.
Read-only result tables use semantic HTML rather than the deprecated Dash
DataTable component.

## Provenance, segments, and artifacts

Every run receives a UUID and writes:

```text
measurements/<source>/<experiment>/
├── <timestamp>.csv
└── .emers/runs/<run-id>/
    ├── run.json
    └── events.jsonl
```

CSV rows contain:

- the sample wall-clock and monotonic timestamps;
- request start and end on both clocks, plus acquisition latency;
- requested, effective, and achieved polling intervals;
- power with an explicit `W` unit and provider-local cumulative energy with an
  explicit `kWh` unit;
- configured source, provider, method, and measurement scope;
- run ID, segment ID, and segment index;
- provider-specific details in `misc`.

`run.json` records runtime and Git metadata, parameters, latest metrics,
phases, the configured fallback chain, capabilities, provider segments, errors,
and final status. Each segment also records sanitized provider configuration,
provider/library versions, calibration metadata, requested/effective polling
rates, achieved-interval statistics, and sample count. `events.jsonl` is an
append-only lifecycle and error log. Provider credentials are never copied into
these artifacts.

The manifest contains derived coverage metrics: observed duration, covered
seconds, gap seconds, coverage ratio, gap count, segment count, and sample
count. Every gap has wall-clock boundaries, duration, provider, cause, and
resolution.

The runtime metadata includes the full installed Python package list and its
SHA-256 hash, a SHA-256 hash of the installed EMERS Python sources, Python
implementation/version, host and platform, and workspace and EMERS Git
commit/branch/dirty state when available. Use
`benchmark_release` or `--benchmark-release` to associate a run with an
immutable benchmark release.

The dashboard plots segments separately. Energy, cost, and carbon calculations
are summed only when all segments for an experiment have the same scope. Mixed
scopes are reported as `N/A` instead of producing a misleading combined
number.

## Third-party providers

External packages can register providers with a Python entry point:

```toml
[project.entry-points."emers.providers"]
my_provider = "my_package:make_provider"
```

The factory is called with:

```python
provider = make_provider(
    settings=settings,
    timeout=request_timeout,
    polling_rate=polling_rate,
)
```

The returned object must expose:

- `source`, `method`, `scope`, and `polling_interval`;
- asynchronous `start()`, `read()`, and `stop()` methods;
- readings as `emers.MeasurementLogResult`.

A provider may expose a `capabilities` dictionary or an object with a
`to_dict()` method. Configuration files refer to the entry-point name through
`"provider": "my_provider"`. EMERS does not import arbitrary module paths from
`settings.json`.

Built-in function-style integrations live under `src/emers/providers/`. The
legacy `emers.meters` namespace remains as a compatibility layer.

## Artifact validation

Validate every manifest, JSONL event stream, and associated CSV file with:

```bash
emers --workspace path/to/experiment validate --pretty
```

Validate one run and require benchmark-release provenance:

```bash
emers --workspace path/to/experiment validate \
  --run measurements/source/experiment/.emers/runs/<run-id> \
  --strict-benchmark
```

The command writes a machine-readable JSON report and exits nonzero on errors.
It checks schema fields, explicit units, wall/monotonic request timing,
acquisition latency, polling intervals, segment provenance, sample counts,
coverage arithmetic, environment hashes, event JSON, credential-like manifest
keys, and CSV/run consistency. It also reports warnings for legacy schemas,
missing or dirty Git provenance, missing benchmark releases, and inadequate
Tapo cumulative-energy resolution. Under `--strict-benchmark`, missing release,
commit, or clean-worktree provenance is an error.

## Benchmark data-collection protocol

Before collecting benchmark data:

1. Commit all source and configuration changes and run the full test suite.
2. Create an immutable Git tag or release for the benchmark implementation.
3. Build the wheel and install that exact artifact in the collection
   environment.
4. Record the release with `--benchmark-release <tag>` or the Python API.
5. Record calibration certificate, procedure, reference instrument, and
   uncertainty in `settings.json`.
6. Run a pilot long enough to exceed the provider's cumulative-energy
   resolution; otherwise select the reference analyzer for interval energy.
7. Run `emers validate --strict-benchmark` and retain its JSON output with the
   dataset.

EMERS records the commit and environment hash, but creating and preserving the
immutable release remains part of the experimental protocol.

## Compatibility

`provider`, `source=`, `--source`, and `--fallback-source` are the
canonical names. Existing `device_type`, `device=`, `device_name=`,
`--device`, and `--fallback-device` usage remains supported. Historical
measurement directory names are unchanged.

## Development and releases

```bash
pytest
python -m build
python -m twine check dist/*
```

Release artifacts should include both the wheel and source distribution from
`dist/`. Publishing through PyPI Trusted Publishing is recommended.

## License

EMERS is released under the [MIT License](LICENSE).
