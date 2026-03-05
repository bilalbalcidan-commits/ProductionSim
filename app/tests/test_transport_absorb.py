from __future__ import annotations

from datetime import datetime, time, timedelta

from app.simulation.engine import simulate
from app.simulation.models import Calendar, Job, Route, Shift, Station, Step


def _event_by_batch_and_step(result, batch_id: str, step_id: str):
    for ev in result.events:
        if ev.batch_id == batch_id and ev.step_id == step_id:
            return ev
    raise AssertionError(f"Event not found: batch={batch_id}, step={step_id}")


def test_transport_absorb_when_station_is_busy() -> None:
    # 24/7 calendar to behave like "no calendar constraints" in current engine.
    cal = Calendar(
        working_days={0, 1, 2, 3, 4, 5, 6},
        shifts=[Shift(name="ALL", start=time(0, 0), end=time(23, 59))],
    )

    stations = [Station(id="M1"), Station(id="M2")]
    route = Route(
        id="R",
        steps=[
            Step(id="R-S1", station_id="M1", cycle_time_per_piece_min=10, transport_after_minutes=120),
            Step(id="R-S2", station_id="M2", cycle_time_per_piece_min=303, transport_after_minutes=0),
        ],
    )
    jobs = [
        Job(
            job_id="J",
            route_id="R",
            quantity=3,  # 3 single-piece batches
            batch_size=1,
            release_at=datetime(2026, 3, 2, 8, 0, 0),
        )
    ]

    result = simulate(jobs=jobs, routes=[route], stations=stations, cal=cal)

    b1s1 = _event_by_batch_and_step(result, "J-B01", "R-S1")
    b1s2 = _event_by_batch_and_step(result, "J-B01", "R-S2")
    b2s2 = _event_by_batch_and_step(result, "J-B02", "R-S2")

    # Batch1 S2: station free, so transport is paid in full.
    assert b1s2.start_at == b1s1.end_at + timedelta(minutes=120)

    # Batch2 S2: M2 is still busy long enough, so transport is absorbed.
    assert b2s2.start_at == b1s2.end_at
