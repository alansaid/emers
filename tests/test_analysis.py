import pandas as pd
import pytest

from emers.analysis import analyze_measurements, calculate_impact, impact_table


def test_analysis_normalizes_segments_without_mutating_input():
    readings = pd.DataFrame({
        "timestamp": [4.0, 1.0, 2.0, 3.0],
        "current_draw": [5.0, 10.0, 12.0, 5.0],
        "total_draw": [8.05, 2.0, 2.1, 8.0],
        "source": ["fallback", "plug", "plug", "fallback"],
        "provider": ["fallback", "plug", "plug", "fallback"],
        "measurement_scope": ["wall", "wall", "wall", "wall"],
        "segment_id": ["second", "first", "first", "second"],
    })
    original = readings.copy(deep=True)

    result = analyze_measurements({"experiment": readings}, smoothness=2)

    pd.testing.assert_frame_equal(readings, original)
    assert [segment.segment_id for segment in result.segments] == ["first", "second"]
    assert result.segments[0].readings["timestamp"].tolist() == [0.0, 1.0]
    assert result.segments[1].readings["timestamp"].tolist() == [2.0, 3.0]
    assert result.segments[0].energy_kwh == pytest.approx(0.1)
    assert result.energy_by_experiment["experiment"] == pytest.approx(0.15)
    assert result.total_energy_kwh == pytest.approx(0.15)
    assert pd.isna(result.segments[0].readings["current_draw_smooth"].iloc[0])
    assert result.segments[0].readings["current_draw_smooth"].iloc[1] == 11.0


def test_analysis_marks_energy_unavailable_for_mixed_scopes():
    readings = pd.DataFrame({
        "timestamp": [1.0, 2.0],
        "current_draw": [10.0, 5.0],
        "total_draw": [1.0, 0.1],
        "measurement_scope": ["whole_system_wall", "cpu_gpu_ram"],
        "segment_id": ["wall", "estimated"],
    })

    result = analyze_measurements({"experiment": readings})

    assert result.energy_by_experiment == {"experiment": None}
    assert result.total_energy_kwh is None


def test_impact_calculations_are_reusable_without_the_dashboard():
    impact = calculate_impact(2.0, 0.5, "EUR", 100.0, 50.0)
    assert impact == {
        "Total Energy Consumption (kWh)": 2.0,
        "Cost of Experiment (EUR)": 1.0,
        "Carbon Footprint of Experiment (gCO2e)": 200.0,
        "Equivalent Distance by Car (km)": 4.0,
    }

    table = impact_table(2.0, {"run": 2.0}, 0.5, "EUR", 100.0, 50.0)
    assert table["Experiment"].tolist() == ["run", "Combined"]
