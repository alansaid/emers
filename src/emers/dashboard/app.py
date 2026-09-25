import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, html, dcc, Output, Input, State

from emers.analysis import analyze_measurements
from emers.analysis import calculate_impact as calculate_cost
from emers.analysis import impact_table as calculate_information
from emers.artifacts import ArtifactStore, RunStore
from emers.dashboard.view import load_run_dashboard_data
from emers.dashboard.view import run_options as build_run_options

header_style = {
    'background-color': '#067B04',
    'color': 'white',
    'text-align': 'center',
    'position': 'sticky',
    'width': '100%',
    'top': '0',
    'left': '0',
    'z-index': '1000',
    'box-shadow': '0 4px 6px rgba(0, 0, 0, 0.1)'
}

row_div_style = {
    'display': 'flex',
    'justifyContent': 'center',
    'alignContent': 'center',
    'alignItems': 'stretch'
}

box_div_style = {
    'flex': '1',
    'margin': '10px',
    'padding': '20px',
    'border': '1px solid #067B04',
    'border-style': 'dashed',
    'fontSize': '18px',
    'textAlign': 'center',
    'alignContent': 'center'
}

row_content_div_style = {
    'display': 'flex',
    'justifyContent': 'center',
    'marginBottom': '10px'
}

label_style = {
    'flex': 1,
    'alignContent': 'center'
}

dropdown_style = {
    'flex': 5
}

input_style = {
    'flex': 3,
    'height': '18px',
    'padding': '8px',
    'font-size': '16px',
    'border': '1px solid #ccc',
    'border-radius': '4px',
    'box-shadow': '0 1px 3px rgba(0,0,0,0.1)',
    'outline': 'none',
    'transition': 'border-color 0.3s ease',
}

checklist_input_style = {
    'width': '26px',
    'height': '16px'
}

checklist_label_style = {
    'flex': 1,
}

def _render_table(rows, columns=None, empty_message="No data available."):
    rows = rows or []
    columns = columns or _table_columns(rows)
    column_pairs = [
        (column.get("name", column.get("id")), column.get("id"))
        for column in columns
    ]
    if not rows or not column_pairs:
        return (
            html.P(empty_message, style={"color": "#52605a"})
            if empty_message
            else None
        )
    cell_style = {
        "borderBottom": "1px solid #d7ded9",
        "padding": "8px",
        "textAlign": "left",
        "verticalAlign": "top",
    }
    return html.Div(
        style={"width": "100%", "overflowX": "auto"},
        children=html.Table(
            style={"width": "100%", "borderCollapse": "collapse"},
            children=[
                html.Thead(html.Tr([
                    html.Th(
                        name,
                        style={
                            **cell_style,
                            "fontWeight": "600",
                            "backgroundColor": "#eef4ee",
                        },
                    )
                    for name, _key in column_pairs
                ])),
                html.Tbody([
                    html.Tr([
                        html.Td(row.get(key, "—"), style=cell_style)
                        for _name, key in column_pairs
                    ])
                    for row in rows
                ]),
            ],
        ),
    )


def _table_container(component_id, columns=None):
    return html.Div(
        id=component_id,
        children=_render_table([], columns),
    )


def _run_panel(run_options):
    selected_run = run_options[0]["value"] if run_options else None
    return html.Div(
        style={**box_div_style, "textAlign": "left", "borderStyle": "solid"},
        children=[
            html.H2("Run evidence", style={"marginTop": "0"}),
            html.P(
                "Inspect one recorded run, including its provider segments, "
                "measurement gaps, coverage, and artifact validation."
            ),
            html.Div(
                style=row_content_div_style,
                children=[
                    html.Label("Run:", htmlFor="run_dropdown", style=label_style),
                    dcc.Dropdown(
                        id="run_dropdown",
                        options=run_options,
                        value=selected_run,
                        clearable=False,
                        style=dropdown_style,
                        placeholder="No run manifests found",
                    ),
                ],
            ),
            html.Div(
                id="run_validation_status",
                children=(
                    "Select a run to inspect its evidence."
                    if run_options
                    else "No run manifests found. Legacy CSV data remains available below."
                ),
                style={"padding": "10px", "backgroundColor": "#eef1f4"},
            ),
            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "repeat(auto-fit, minmax(320px, 1fr))",
                    "gap": "16px",
                    "marginTop": "16px",
                },
                children=[
                    html.Div([html.H3("Run summary"), _table_container(
                        "run_summary_data",
                        [{"name": "Field", "id": "Field"}, {"name": "Value", "id": "Value"}],
                    )]),
                    html.Div([html.H3("Energy and impact"), _table_container("run_impact_data")]),
                ],
            ),
            html.H3("Provider segments"),
            _table_container("run_segment_data"),
            html.H3("Measurement gaps"),
            _table_container("run_gap_data"),
            html.P(id="run_gap_message", style={"color": "#4d5963"}),
            html.H3("Validation findings"),
            _table_container("run_validation_issues"),
            dcc.Graph(id="run_plot_current_draw"),
            dcc.Graph(id="run_plot_total_draw"),
            dcc.Interval(id="run_refresh_interval", interval=2000, n_intervals=0),
        ],
    )


def _build_layout(source_options, monitor_settings, run_options):
    return [
    html.Header(
        style=header_style,
        children=[
            html.H1(
                style={
                    'margin': '1px',
                    'fontSize': '32px',
                    'font-weight': '300'
                },
                children='EMERS: Energy Meter for Recommender Systems'),
        ]
    ),
    html.Div(
        style={
            'padding': '20px',
            'background-color': '#f4f4f4',
            'min-height': '100vh',
            'text-align': 'center',
            'fontFamily': 'system-ui, sans-serif'},
        children=[
            _run_panel(run_options),
            html.Details(
                style={**box_div_style, "textAlign": "left"},
                children=[
            html.Summary(
                "Display and impact settings, legacy CSV viewer",
                style={"fontSize": "20px", "fontWeight": "600", "cursor": "pointer"},
            ),
            html.P(
                "Adjust graph and impact settings here, or inspect measurements "
                "that predate run manifests. Generate standalone reports with "
                "the `emers report` command."
            ),
            html.Div(
                style=row_div_style,
                children=[
                    html.Div(
                        style=box_div_style,
                        children=[
                            html.Div(
                                style=row_content_div_style,
                                children=[
                                    html.Label(
                                        children='Measurement source:',
                                        title='Select a measurement source',
                                        htmlFor='plug_dropdown',
                                        style=label_style
                                    ),
                                    dcc.Dropdown(
                                        options=source_options,
                                        value=source_options[0]["value"] if source_options else None,
                                        id='plug_dropdown',
                                        style=dropdown_style,
                                        clearable=False
                                    ),
                                ]
                            ),
                            html.Div(
                                style=row_content_div_style,
                                children=[
                                    html.Label(
                                        children='Experiment:',
                                        title='Select an experiment',
                                        htmlFor='experiment_dropdown',
                                        style=label_style
                                    ),
                                    dcc.Dropdown(
                                        id='experiment_dropdown',
                                        multi=True,
                                        style=dropdown_style
                                    ),
                                ]
                            ),
                            html.Div(
                                style=row_content_div_style,
                                children=[
                                    html.Label(
                                        children='File:',
                                        title='Select a file',
                                        htmlFor='file_dropdown',
                                        style=label_style
                                    ),
                                    dcc.Dropdown(
                                        id='file_dropdown',
                                        multi=True,
                                        style=dropdown_style
                                    ),
                                ]
                            ),
                        ]
                    ),
                    html.Div(
                        style=box_div_style,
                        children=[
                            html.Div(
                                style=row_content_div_style,
                                children=[
                                    html.Label(
                                        children='Cost/kWh:',
                                        title='Cost of energy per kWh',
                                        htmlFor='cost_per_kwh',
                                        style=label_style
                                    ),
                                    dcc.Input(
                                        id='cost_per_kwh',
                                        type='text',
                                        value=monitor_settings["cost_per_kwh"],
                                        style=input_style,
                                        debounce=True
                                    ),
                                    html.Label(
                                        children='Currency:',
                                        title='Currency',
                                        htmlFor='currency',
                                        style=label_style
                                    ),
                                    dcc.Input(
                                        id='currency',
                                        type='text',
                                        value=monitor_settings["currency"],
                                        style=input_style,
                                        debounce=True
                                    ),
                                ]
                            ),
                            html.Div(
                                style=row_content_div_style,
                                children=[
                                    html.Label(
                                        children='gCO2e/kWh:',
                                        title='Carbon footprint per kWh in gCO2e',
                                        htmlFor='carbon_footprint',
                                        style=label_style
                                    ),
                                    dcc.Input(
                                        id='carbon_footprint',
                                        type='text',
                                        value=monitor_settings["gco2e_per_kwh"],
                                        style=input_style,
                                        debounce=True
                                    ),
                                    html.Label(
                                        children='gCO2e/km:',
                                        title='Carbon footprint per km in a car in gCO2e',
                                        htmlFor='carbon_footprint_km',
                                        style=label_style
                                    ),
                                    dcc.Input(
                                        id='carbon_footprint_km',
                                        type='text',
                                        value=monitor_settings["gco2e_per_kilometer_car"],
                                        style=input_style,
                                        debounce=True
                                    ),
                                ]
                            ),
                            html.Div(
                                style=row_content_div_style,
                                children=[
                                    html.Label(
                                        children='Update Interval (ms):',
                                        title='Graph Update Interval (ms)',
                                        htmlFor='graph_update_interval_input',
                                        style=label_style
                                    ),
                                    dcc.Input(
                                        id='graph_update_interval_input',
                                        type='number',
                                        value=1000,
                                        style=input_style,
                                        debounce=True
                                    ),
                                    dcc.Checklist(
                                        id='graph_update_interval_toggle',
                                        options=[{'label': 'Enable', 'value': 'ON'}],
                                        labelStyle=checklist_label_style,
                                        inputStyle=checklist_input_style
                                    )
                                ]
                            ),
                            html.Div(
                                style=row_content_div_style,
                                children=[
                                    html.Label(
                                        children='Smoothness Window:',
                                        title='Graph Smoothness Rolling Window',
                                        htmlFor='smoothness_input',
                                        style=label_style
                                    ),
                                    dcc.Input(
                                        id='smoothness_input',
                                        type='number',
                                        value=100,
                                        min=1,
                                        max=100000,
                                        step=1,
                                        style=input_style,
                                        debounce=True
                                    ),
                                    dcc.Checklist(
                                        id='graph_rolling_window_toggle',
                                        options=[{'label': 'Enable', 'value': 'ON'}],
                                        labelStyle=checklist_label_style,
                                        inputStyle=checklist_input_style
                                    )
                                ]
                            ),
                        ]
                    ),
                ]
            ),
            html.Div(
                style=box_div_style,
                children=[_table_container('experiment_data')]
            ),
            html.Div(
                style=box_div_style,
                children=[
                    dcc.Graph(id='plot_current_draw'),
                    dcc.Graph(id='plot_total_draw'),
                    dcc.Interval(id='graph_update_interval', interval=1000, n_intervals=0, disabled=True),
                ]
            ),
                ],
            ),
        ]
    ),
    ]


def update_interval_disabled(checkbox_value):
    if checkbox_value is None or len(checkbox_value) == 0:
        return True
    else:
        return False


def update_interval(value):
    if value is None or value < 1000:
        return 1000, 1000
    return value, value


def update_experiment_dropdown(source, *, store=None):
    if not source:
        return [], None
    store = store or ArtifactStore(Path.cwd())
    options = [
        {"label": item.name, "value": str(item.path)}
        for item in store.experiments(source)
    ]
    if len(options) == 0:
        return [], None
    value = options[0]['value']
    return options, value


def update_file_dropdown(experiment, *, store=None):
    if experiment is None or len(experiment) == 0:
        return [], None
    store = store or ArtifactStore(Path.cwd())
    if not type(experiment) == list:
        experiment = [experiment]

    options = []
    all_value = ""
    for ex in experiment:
        if ex[0] == '!':
            ex = ex[1:]
        options += [
            {"label": item.name, "value": str(item.path)}
            for item in store.files(ex)
        ]
        all_value += f"!{ex}"

    options.insert(0, {"label": "All", "value": all_value})
    if len(options) == 0:
        return [], None
    value = options[0]['value']
    return options, value


def get_experiment_files(files, *, store=None):
    if files is None or len(files) == 0:
        raise ValueError
    if not type(files) == list:
        files = [files]
    store = store or ArtifactStore(Path.cwd())

    files_to_read = {}
    for file in files:

        if file[0] == '!':
            folders = file.split("!")
            folders = list(filter(None, folders))

            for folder in folders:
                folder = Path(folder)
                experiment = folder.name
                if not experiment in files_to_read:
                    files_to_read[experiment] = []

                files_to_read[experiment] += [item.path for item in store.files(folder)]
        else:
            file = Path(file)
            experiment = file.parent.name
            if not experiment in files_to_read:
                files_to_read[experiment] = []

            files_to_read[experiment].append(file)

    if not files_to_read:
        raise ValueError

    full_data = {
        experiment: store.read_files(paths)
        for experiment, paths in files_to_read.items()
    }

    return full_data


def make_scatters(full_data, smoothness, autosize=False):
    analysis = analyze_measurements(full_data, smoothness=smoothness)
    scatters = []
    for series in analysis.segments:
        segment = series.readings
        scatters.append({
            "experiment": series.experiment,
            "cd": go.Scatter(
                x=segment["timestamp"], y=segment["current_draw"], name=series.label
            ),
            "cds": go.Scatter(
                x=segment["timestamp"],
                y=segment["current_draw_smooth"],
                name=f"Smoothed · {series.label}",
            ),
            "td": go.Scatter(
                x=segment["timestamp"], y=segment["total_draw"], name=series.label
            ),
            "tds": go.Scatter(
                x=segment["timestamp"],
                y=segment["total_draw_smooth"],
                name=f"Smoothed · {series.label}",
            ),
        })

    scatters_layout = {}

    if not autosize:
        scatters_layout["cd"] = go.Layout(title='Draw (W) Over Time (s)',
                                          xaxis={"title": 'Time (s)'},
                                          yaxis={"title": 'Draw (W)'},
                                          autosize=False,
                                          width=1800,
                                          height=600)
        scatters_layout["td"] = go.Layout(title='Consumption (kWh) Over Time (s)',
                                          xaxis={"title": 'Time (s)'},
                                          yaxis={"title": 'Consumption (kWh)', },
                                          autosize=False,
                                          width=1800,
                                          height=600)
    else:
        scatters_layout["cd"] = go.Layout(title='Draw (W) Over Time (s)',
                                          xaxis={"title": 'Time (s)'},
                                          yaxis={"title": 'Draw (W)'})

        scatters_layout["td"] = go.Layout(title='Consumption (kWh) Over Time (s)',
                                          xaxis={"title": 'Time (s)'},
                                          yaxis={"title": 'Consumption (kWh)', })

    scatters_layout["legend"] = {"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1}

    scatter_data = {
        "scatters": scatters,
        "scatters_layout": scatters_layout,
        "total_power": analysis.total_energy_kwh,
        "power_by_experiment": analysis.energy_by_experiment,
    }

    return scatter_data


def _figures_from_scatter_data(scatter_data, smoothness_toggle):
    current = [item["cd"] for item in scatter_data["scatters"]]
    current_smooth = [item["cds"] for item in scatter_data["scatters"]]
    total = [item["td"] for item in scatter_data["scatters"]]
    total_smooth = [item["tds"] for item in scatter_data["scatters"]]
    smoothed = smoothness_toggle is not None and len(smoothness_toggle) > 0
    fig_current = go.Figure(
        data=current + current_smooth if smoothed else current,
        layout=scatter_data["scatters_layout"]["cd"],
    )
    fig_total = go.Figure(
        data=total + total_smooth if smoothed else total,
        layout=scatter_data["scatters_layout"]["td"],
    )
    fig_current.update_layout(legend=scatter_data["scatters_layout"]["legend"])
    fig_total.update_layout(legend=scatter_data["scatters_layout"]["legend"])
    return fig_current, fig_total


def make_graph(files, cost_per_kwh, currency, carbon_footprint, carbon_footprint_km, smoothness, smoothness_toggle,
               autosize, *, store=None):
    full_data = get_experiment_files(files, store=store)

    scatter_data = make_scatters(full_data, smoothness, autosize)
    fig_cd, fig_td = _figures_from_scatter_data(scatter_data, smoothness_toggle)

    information_df = calculate_information(scatter_data["total_power"], scatter_data["power_by_experiment"],
                                           cost_per_kwh, currency, carbon_footprint, carbon_footprint_km)

    return fig_cd, fig_td, information_df


def update_graph(files, n_intervals, cost_per_kwh, currency, carbon_footprint, carbon_footprint_km, smoothness,
                 smoothness_toggle, *, store=None):
    invalid_experiment = {}, {}, [], []
    try:
        fig_cd, fig_td, information_df = make_graph(files, cost_per_kwh, currency, carbon_footprint,
                                                    carbon_footprint_km, smoothness, smoothness_toggle, True,
                                                    store=store)
    except ValueError:
        return invalid_experiment

    return fig_cd, fig_td, information_df.to_dict('records'), [{"name": i, "id": i} for i in information_df.columns]


def _table_columns(rows):
    return [{"name": key, "id": key} for key in rows[0]] if rows else []


def refresh_run_dropdown(current_run, *, store):
    options = build_run_options(store)
    values = {option["value"] for option in options}
    value = current_run if current_run in values else (
        options[0]["value"] if options else None
    )
    return options, value


def update_run_dashboard(
    run_manifest,
    cost_per_kwh,
    currency,
    carbon_footprint,
    carbon_footprint_km,
    smoothness,
    smoothness_toggle,
    *,
    store,
):
    empty_figure = go.Figure().update_layout(
        annotations=[{
            "text": "No run measurement data available",
            "showarrow": False,
            "xref": "paper",
            "yref": "paper",
            "x": 0.5,
            "y": 0.5,
        }]
    )
    if not run_manifest:
        status = "No run manifest selected. Legacy CSV data is available below."
        status_style = {"padding": "10px", "backgroundColor": "#eef1f4"}
        return (
            [], [], [], [], [], "No measurement gaps recorded.",
            status, status_style, [], [],
            empty_figure, empty_figure, [], [],
        )

    try:
        view = load_run_dashboard_data(store, run_manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        status = f"Unable to load run: {exc}"
        status_style = {
            "padding": "10px",
            "backgroundColor": "#fde8e8",
            "color": "#8a1c1c",
        }
        return (
            [], [], [], [], [], "Measurement gaps unavailable.",
            status, status_style, [], [],
            empty_figure, empty_figure, [], [],
        )

    manifest_status = view.manifest.get("status", "unknown")
    error_count = len(view.validation.get("errors", []))
    warning_count = len(view.validation.get("warnings", []))
    if manifest_status in {"initialized", "running", "stopping"}:
        validation_text = (
            f"Run is {manifest_status}; validation is provisional "
            f"({error_count} errors, {warning_count} warnings)."
        )
        validation_style = {
            "padding": "10px",
            "backgroundColor": "#fff4d6",
            "color": "#6b4b00",
        }
    elif view.validation.get("valid"):
        validation_text = f"Artifacts valid · {warning_count} warnings"
        validation_style = {
            "padding": "10px",
            "backgroundColor": "#e4f6e7",
            "color": "#165b25",
        }
    else:
        validation_text = (
            f"Artifact validation failed · {error_count} errors · "
            f"{warning_count} warnings"
        )
        validation_style = {
            "padding": "10px",
            "backgroundColor": "#fde8e8",
            "color": "#8a1c1c",
        }

    if view.readings.empty:
        fig_current = empty_figure
        fig_total = empty_figure
        impact_records = []
        impact_columns = []
    else:
        scatter_data = make_scatters(
            {view.artifact.experiment: view.readings},
            smoothness=smoothness,
            autosize=True,
        )
        fig_current, fig_total = _figures_from_scatter_data(
            scatter_data, smoothness_toggle
        )
        impact = calculate_information(
            scatter_data["total_power"],
            scatter_data["power_by_experiment"],
            cost_per_kwh,
            currency,
            carbon_footprint,
            carbon_footprint_km,
        )
        impact = impact[impact["Experiment"] != "Combined"]
        impact_records = impact.to_dict("records")
        impact_columns = [{"name": item, "id": item} for item in impact.columns]

    segments = list(view.segments)
    gaps = list(view.gaps)
    gap_message = (
        f"{len(gaps)} measurement gap{'s' if len(gaps) != 1 else ''} recorded."
        if gaps
        else "No measurement gaps recorded."
    )
    issues = list(view.validation_issues)
    return (
        list(view.summary),
        segments,
        _table_columns(segments),
        gaps,
        _table_columns(gaps),
        gap_message,
        validation_text,
        validation_style,
        issues,
        _table_columns(issues),
        fig_current,
        fig_total,
        impact_records,
        impact_columns,
    )


def _register_callbacks(app, workspace, store):
    @app.callback(
        Output("run_dropdown", "options"),
        Output("run_dropdown", "value"),
        Input("run_refresh_interval", "n_intervals"),
        State("run_dropdown", "value"),
    )
    def _refresh_runs(_n_intervals, current_run):
        return refresh_run_dropdown(current_run, store=store)

    @app.callback(
        Output("run_summary_data", "children"),
        Output("run_segment_data", "children"),
        Output("run_gap_data", "children"),
        Output("run_gap_message", "children"),
        Output("run_validation_status", "children"),
        Output("run_validation_status", "style"),
        Output("run_validation_issues", "children"),
        Output("run_plot_current_draw", "figure"),
        Output("run_plot_total_draw", "figure"),
        Output("run_impact_data", "children"),
        Input("run_dropdown", "value"),
        Input("run_refresh_interval", "n_intervals"),
        Input("cost_per_kwh", "value"),
        Input("currency", "value"),
        Input("carbon_footprint", "value"),
        Input("carbon_footprint_km", "value"),
        Input("smoothness_input", "value"),
        Input("graph_rolling_window_toggle", "value"),
    )
    def _update_run(
        run_manifest,
        _n_intervals,
        cost_per_kwh,
        currency,
        carbon_footprint,
        carbon_footprint_km,
        smoothness,
        smoothness_toggle,
    ):
        result = update_run_dashboard(
            run_manifest,
            cost_per_kwh,
            currency,
            carbon_footprint,
            carbon_footprint_km,
            smoothness,
            smoothness_toggle,
            store=store,
        )
        return (
            _render_table(
                result[0],
                [{"name": "Field", "id": "Field"}, {"name": "Value", "id": "Value"}],
            ),
            _render_table(result[1], result[2], "No provider segments recorded."),
            _render_table(result[3], result[4], ""),
            result[5],
            result[6],
            result[7],
            _render_table(result[8], result[9], "No validation findings."),
            result[10],
            result[11],
            _render_table(result[12], result[13], "No impact data available."),
        )

    app.callback(
        Output('graph_update_interval', 'disabled'),
        Input('graph_update_interval_toggle', 'value'),
    )(update_interval_disabled)

    app.callback(
        Output('graph_update_interval', 'interval'),
        Output('graph_update_interval_input', 'value'),
        Input('graph_update_interval_input', 'value'),
    )(update_interval)

    @app.callback(
        Output('experiment_dropdown', 'options'),
        Output('experiment_dropdown', 'value'),
        Input('plug_dropdown', 'value'),
    )
    def _update_experiments(source):
        return update_experiment_dropdown(source, store=store)

    @app.callback(
        Output('file_dropdown', 'options'),
        Output('file_dropdown', 'value'),
        Input('experiment_dropdown', 'value'),
    )
    def _update_files(experiment):
        return update_file_dropdown(experiment, store=store)

    @app.callback(
        Output('plot_current_draw', 'figure'),
        Output('plot_total_draw', 'figure'),
        Output('experiment_data', 'children'),
        Input('file_dropdown', 'value'),
        Input('graph_update_interval', 'n_intervals'),
        Input('cost_per_kwh', 'value'),
        Input('currency', 'value'),
        Input('carbon_footprint', 'value'),
        Input('carbon_footprint_km', 'value'),
        Input('smoothness_input', 'value'),
        Input('graph_rolling_window_toggle', 'value'),
    )
    def _update_graph(
        files,
        n_intervals,
        cost_per_kwh,
        currency,
        carbon_footprint,
        carbon_footprint_km,
        smoothness,
        smoothness_toggle,
    ):
        fig_current, fig_total, records, columns = update_graph(
            files,
            n_intervals,
            cost_per_kwh,
            currency,
            carbon_footprint,
            carbon_footprint_km,
            smoothness,
            smoothness_toggle,
            store=store,
        )
        return fig_current, fig_total, _render_table(records, columns)


def create_dashboard(workspace="."):
    """Create a dashboard bound to one explicit EMERS workspace."""

    workspace = Path(workspace).expanduser().resolve()
    settings_path = workspace / "monitor_settings.json"
    with settings_path.open(encoding="utf-8") as settings_file:
        monitor_settings = json.load(settings_file)

    store = RunStore(workspace)
    source_options = [
        {"label": source.name, "value": str(source.path)}
        for source in store.sources()
    ]
    dashboard = Dash(__name__)
    dashboard.title = "EMERS: Energy Meter for Recommender Systems"
    dashboard.layout = _build_layout(
        source_options, monitor_settings, build_run_options(store)
    )
    dashboard.emers_workspace = workspace
    dashboard.emers_artifact_store = store
    _register_callbacks(dashboard, workspace, store)
    return dashboard


# Conventional WSGI/application-factory spelling for embedding EMERS.
create_app = create_dashboard

_legacy_app = None


def __getattr__(name):
    """Create the historical module-level ``app`` only when explicitly used."""

    if name != "app":
        raise AttributeError(name)
    global _legacy_app
    if _legacy_app is None:
        import warnings

        warnings.warn(
            "The module-level dashboard app is deprecated; "
            "use create_dashboard(workspace)",
            DeprecationWarning,
            stacklevel=2,
        )
        _legacy_app = create_dashboard(Path.cwd())
    return _legacy_app


def run(host="127.0.0.1", port=5000, debug=False, workspace="."):
    """Create and run the EMERS dashboard for ``workspace``."""

    dashboard = create_dashboard(workspace)
    # The reloader starts a second process, which would duplicate a measurement
    # worker when the dashboard is launched through ``emers run``.
    dashboard.run(debug=debug, host=host, port=port, use_reloader=False)
