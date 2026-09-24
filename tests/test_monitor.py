import pandas as pd

from emers.monitor import calculate_cost, make_scatters


def test_dashboard_does_not_combine_mixed_measurement_scopes():
    readings = pd.DataFrame({
        "timestamp": [1.0, 2.0, 3.0, 4.0],
        "current_draw": [10.0, 10.0, 5.0, 5.0],
        "total_draw": [1.0, 1.1, 0.0, 0.05],
        "source": ["tapo", "tapo", "codecarbon", "codecarbon"],
        "provider": ["tapo", "tapo", "codecarbon", "codecarbon"],
        "measurement_scope": [
            "whole_system_wall",
            "whole_system_wall",
            "cpu_gpu_ram",
            "cpu_gpu_ram",
        ],
        "segment_id": ["segment-001", "segment-001", "segment-002", "segment-002"],
    })

    result = make_scatters({"experiment": readings}, smoothness=1, autosize=True)

    assert len(result["scatters"]) == 2
    assert result["power_by_experiment"]["experiment"] is None
    assert result["total_power"] is None
    assert "mixed measurement scopes" in calculate_cost(None, 1, "USD", 1, 1)[
        "Total Energy Consumption (kWh)"
    ]
