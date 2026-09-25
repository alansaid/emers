"""Dashboard-independent analysis of EMERS measurement data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Mapping

import pandas as pd


@dataclass(frozen=True)
class SegmentSeries:
    """Plot-ready values and metadata for one uninterrupted provider segment."""

    experiment: Hashable
    segment_id: str
    provider: str
    scope: str
    readings: pd.DataFrame
    energy_kwh: float

    @property
    def label(self) -> str:
        return (
            f"{self.experiment} · {self.provider}/{self.scope} · "
            f"{self.segment_id}"
        )


@dataclass(frozen=True)
class MeasurementAnalysis:
    """Normalized series and energy totals for a collection of experiments."""

    segments: tuple[SegmentSeries, ...]
    energy_by_experiment: dict[Hashable, float | None]
    total_energy_kwh: float | None


def _first_value(readings: pd.DataFrame, column: str, default: str) -> str:
    if column not in readings.columns:
        return default
    value = readings[column].iloc[0]
    return default if pd.isna(value) else str(value)


def analyze_measurements(
    experiments: Mapping[Hashable, pd.DataFrame], smoothness: int = 1
) -> MeasurementAnalysis:
    """Normalize measurement segments without mutating the input frames.

    Cumulative energy is rebased independently for each segment because a
    provider switch can change both the counter origin and measurement scope.
    Totals are unavailable when an experiment mixes scopes.
    """

    if smoothness < 1:
        raise ValueError("smoothness must be at least 1")

    segments = []
    energy_by_experiment = {}
    total_energy_kwh: float | None = 0.0

    for experiment, original in experiments.items():
        if original.empty or "timestamp" not in original.columns:
            continue

        readings = original.sort_values(by="timestamp").copy()
        if "segment_id" not in readings.columns:
            readings["segment_id"] = "legacy"
        else:
            readings["segment_id"] = readings["segment_id"].fillna("legacy")

        experiment_start = readings["timestamp"].iloc[0]
        scopes = set()
        segment_energies = []

        for segment_id, raw_segment in readings.groupby("segment_id", sort=False):
            segment = raw_segment.copy()
            segment["timestamp"] = segment["timestamp"] - experiment_start
            segment["current_draw_smooth"] = segment["current_draw"].rolling(
                window=smoothness
            ).mean()
            segment["total_draw"] = segment["total_draw"] - segment["total_draw"].min()
            segment["total_draw_smooth"] = segment["total_draw"].rolling(
                window=smoothness
            ).mean()

            scope = _first_value(segment, "measurement_scope", "legacy")
            provider = _first_value(segment, "provider", "")
            if not provider:
                provider = _first_value(segment, "source", "legacy")
            energy_kwh = float(segment["total_draw"].max())

            scopes.add(scope)
            segment_energies.append(energy_kwh)
            segments.append(
                SegmentSeries(
                    experiment=experiment,
                    segment_id=str(segment_id),
                    provider=provider,
                    scope=scope,
                    readings=segment,
                    energy_kwh=energy_kwh,
                )
            )

        energy = sum(segment_energies) if len(scopes) <= 1 else None
        energy_by_experiment[experiment] = energy
        if energy is None:
            total_energy_kwh = None
        elif total_energy_kwh is not None:
            total_energy_kwh += energy

    return MeasurementAnalysis(
        segments=tuple(segments),
        energy_by_experiment=energy_by_experiment,
        total_energy_kwh=total_energy_kwh,
    )


def calculate_impact(
    energy_kwh: float | None,
    cost_per_kwh,
    currency,
    carbon_footprint,
    carbon_footprint_km,
) -> dict[str, float | str]:
    """Calculate cost and carbon equivalents for an energy total."""

    if energy_kwh is None:
        unavailable = "N/A (mixed measurement scopes)"
        return {
            "Total Energy Consumption (kWh)": unavailable,
            f"Cost of Experiment ({currency})": unavailable,
            "Carbon Footprint of Experiment (gCO2e)": unavailable,
            "Equivalent Distance by Car (km)": unavailable,
        }

    cost = energy_kwh * float(cost_per_kwh)
    emissions = energy_kwh * float(carbon_footprint)
    distance = emissions / float(carbon_footprint_km)
    return {
        "Total Energy Consumption (kWh)": round(energy_kwh, 2),
        f"Cost of Experiment ({currency})": round(cost, 2),
        "Carbon Footprint of Experiment (gCO2e)": round(emissions, 2),
        "Equivalent Distance by Car (km)": round(distance, 2),
    }


def impact_table(
    total_energy_kwh,
    energy_by_experiment,
    cost_per_kwh,
    currency,
    carbon_footprint,
    carbon_footprint_km,
) -> pd.DataFrame:
    """Build the summary table used by dashboards and reports."""

    information = {
        experiment: calculate_impact(
            energy,
            cost_per_kwh,
            currency,
            carbon_footprint,
            carbon_footprint_km,
        )
        for experiment, energy in energy_by_experiment.items()
    }
    information["Combined"] = calculate_impact(
        total_energy_kwh,
        cost_per_kwh,
        currency,
        carbon_footprint,
        carbon_footprint_km,
    )
    result = pd.DataFrame.from_dict(information, orient="index")
    result.reset_index(inplace=True)
    result.rename(columns={"index": "Experiment"}, inplace=True)
    return result
