from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from app.simulation.engine import simulate
from app.simulation.excel_loader import load_excel_input
from app.simulation.exporters import to_json
from app.simulation.exporters_csv import to_csv_events
from app.simulation.models import Calendar, Job, Route, Shift, Station, Step


_WEEKDAY_TO_INT = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}


def _parse_shift(index: int, raw_shift: dict[str, object]) -> Shift:
    start_text = str(raw_shift["start_time"])
    duration_minutes = int(raw_shift["duration_minutes"])

    start_dt = datetime.combine(datetime.min.date(), datetime.strptime(start_text, "%H:%M").time())
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    return Shift(name=f"S{index}", start=start_dt.time(), end=end_dt.time())


def _load_from_json_scenario(scenario_path: Path) -> tuple[list[Job], list[Route], list[Station], Calendar]:
    with scenario_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    cal_data = data["calendar"]
    working_days = {_WEEKDAY_TO_INT[d] for d in cal_data["working_days"]}
    shifts = [_parse_shift(i + 1, s) for i, s in enumerate(cal_data["shifts"])]
    calendar = Calendar(working_days=working_days, shifts=shifts)

    stations: dict[str, Station] = {sid: Station(id=sid) for sid in data["stations"]}

    routes: dict[str, Route] = {}
    for route_id, raw_steps in data["routes"].items():
        steps = [
            Step(
                id=f"{route_id}-S{i + 1}",
                station_id=str(raw_step["station_id"]),
                cycle_time_per_piece_min=float(raw_step["cycle_time_per_piece_min"]),
                transport_after_minutes=int(raw_step.get("transport_time_after_min", 0)),
            )
            for i, raw_step in enumerate(raw_steps)
        ]
        routes[route_id] = Route(id=route_id, steps=steps)

    jobs: list[Job] = [
        Job(
            job_id=str(raw_job["job_id"]),
            route_id=str(raw_job["route_id"]),
            quantity=int(raw_job["quantity"]),
            batch_size=int(raw_job["batch_size"]),
            release_at=datetime.fromisoformat(str(raw_job["start_dt"])),
        )
        for raw_job in data["jobs"]
    ]
    return jobs, list(routes.values()), list(stations.values()), calendar


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ProductionSim simulation")
    parser.add_argument(
        "--excel",
        type=str,
        default=None,
        help="Path to ProductionSim Excel input file",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    if args.excel:
        jobs, routes, stations, loaded_calendar = load_excel_input(args.excel)
        if loaded_calendar is None:
            loaded_calendar = Calendar(
                working_days={0, 1, 2, 3, 4, 5, 6},
                shifts=[_parse_shift(1, {"start_time": "00:00", "duration_minutes": 24 * 60})],
            )
        calendar = loaded_calendar
    else:
        scenario_path = base_dir / "sample_scenario.json"
        jobs, routes, stations, calendar = _load_from_json_scenario(scenario_path)

    print(
        f"Loaded input: jobs={len(jobs)} routes={len(routes)} stations={len(stations)} "
        f"calendar_enabled={'yes' if calendar is not None else 'no'}"
    )

    result = simulate(jobs=jobs, routes=routes, stations=stations, cal=calendar)

    output_path = base_dir / "out" / "result.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    to_json(result, str(output_path))
    events_csv_path = base_dir / "out" / "events.csv"
    to_csv_events(result, str(events_csv_path))

    print(output_path)


if __name__ == "__main__":
    main()
