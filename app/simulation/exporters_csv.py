from __future__ import annotations

import csv

from .models import SimulationResult


def to_csv_events(result: SimulationResult, path: str) -> None:
    events = sorted(result.events, key=lambda event: event.start_at)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["station_id", "batch_id", "job_id", "step_id", "start_at", "end_at", "duration_min"]
        )
        for event in events:
            duration_min = int((event.end_at - event.start_at).total_seconds() / 60)
            writer.writerow(
                [
                    event.station_id,
                    event.batch_id,
                    event.job_id,
                    event.step_id,
                    event.start_at.isoformat(),
                    event.end_at.isoformat(),
                    duration_min,
                ]
            )
