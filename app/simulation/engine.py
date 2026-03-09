from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

from .models import (
    BatchStepEvent,
    Calendar,
    Job,
    Route,
    SimulationResult,
    Station,
    StationStat,
)

DEFAULT_BASE_START = datetime(2026, 3, 2, 8, 0, 0)


def make_batch_id(job_id: str, batch_index: int) -> str:
    # For future event generation identifiers.
    return f"{job_id}-B{batch_index:02d}"


def split_into_batches(qty: int, batch_size: int) -> list[int]:
    if qty <= 0:
        return []
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")

    full_batches, remainder = divmod(qty, batch_size)
    batches = [batch_size] * full_batches
    if remainder:
        batches.append(remainder)
    return batches


def _shift_windows_for_day(day: date, shifts: list) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    for shift in sorted(shifts, key=lambda s: s.start):
        start_dt = datetime.combine(day, shift.start)
        end_dt = datetime.combine(day, shift.end)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        windows.append((start_dt, end_dt))
    return windows


def _active_shift_end(dt: datetime, shifts: list) -> datetime | None:
    candidates: list[datetime] = []
    today = dt.date()
    days_to_check = [today]
    try:
        yesterday = today - timedelta(days=1)
        days_to_check.insert(0, yesterday)
    except OverflowError:
        pass
    for day in days_to_check:
        for start_dt, end_dt in _shift_windows_for_day(day, shifts):
            if start_dt <= dt < end_dt:
                candidates.append(end_dt)
    return min(candidates) if candidates else None


def is_within_shift(dt: datetime, shifts: list) -> bool:
    return _active_shift_end(dt, shifts) is not None


def next_shift_start(dt: datetime, shifts: list) -> datetime:
    candidates: list[datetime] = []
    for offset in range(0, 15):
        day = dt.date() + timedelta(days=offset)
        for start_dt, _ in _shift_windows_for_day(day, shifts):
            if start_dt >= dt:
                candidates.append(start_dt)
    if not candidates:
        raise ValueError("No shifts configured")
    return min(candidates)


def apply_calendar(
    start_dt: datetime,
    duration_minutes: int,
    shifts: list,
    working_days: set[int],
) -> tuple[datetime, datetime]:
    if not shifts:
        raise ValueError("No shifts configured")
    if not working_days:
        raise ValueError("No working days configured")

    first_shift_start = min(shifts, key=lambda s: s.start).start

    def align_to_working_shift(dt: datetime) -> datetime:
        cursor = dt
        while True:
            if cursor.weekday() not in working_days:
                cursor = datetime.combine(cursor.date() + timedelta(days=1), first_shift_start)
                continue
            if is_within_shift(cursor, shifts):
                return cursor
            candidate = next_shift_start(cursor, shifts)
            if candidate.weekday() in working_days:
                return candidate
            cursor = datetime.combine(candidate.date() + timedelta(days=1), first_shift_start)

    current = align_to_working_shift(start_dt)
    cal_start = current
    remaining = int(duration_minutes)

    if remaining <= 0:
        return cal_start, cal_start

    while remaining > 0:
        shift_end = _active_shift_end(current, shifts)
        if shift_end is None:
            current = align_to_working_shift(current)
            continue

        available_minutes = int((shift_end - current).total_seconds() // 60)
        if available_minutes <= 0:
            current = align_to_working_shift(shift_end)
            continue

        worked = min(remaining, available_minutes)
        current = current + timedelta(minutes=worked)
        remaining -= worked

        if remaining > 0:
            current = align_to_working_shift(current)

    return cal_start, current


def simulate(
    jobs: list[Job],
    routes: list[Route],
    stations: list[Station],
    cal: Calendar | None,
) -> SimulationResult:
    use_calendar = cal is not None
    routes_by_id = {route.id: route for route in routes}
    initial_available_at = datetime.min if use_calendar else DEFAULT_BASE_START
    available_at: dict[str, list[datetime]] = {
        station.id: [initial_available_at] * int(station.capacity) for station in stations
    }

    events: list[BatchStepEvent] = []
    batches_by_job: dict[str, list[tuple[int, int]]] = {}
    release_groups: dict[datetime, list[Job]] = {}

    for job in jobs:
        qty = int(job.quantity)
        batch_size = int(job.batch_size)
        batch_sizes = split_into_batches(qty, batch_size)
        batches_by_job[job.job_id] = [
            (batch_index, batch_qty)
            for batch_index, batch_qty in enumerate(batch_sizes, start=1)
        ]
        release_at = job.release_at if job.release_at is not None else (
            datetime.min if use_calendar else DEFAULT_BASE_START
        )
        release_groups.setdefault(release_at, []).append(job)

    dispatched_batches: list[tuple[Job, int, int]] = []
    for release_at in sorted(release_groups.keys()):
        group_jobs = sorted(release_groups[release_at], key=lambda job: job.job_id)
        group_queues: dict[str, list[tuple[int, int]]] = {
            job.job_id: list(batches_by_job.get(job.job_id, [])) for job in group_jobs
        }

        while any(group_queues[job.job_id] for job in group_jobs):
            for job in group_jobs:
                queue = group_queues[job.job_id]
                if queue:
                    batch_index, batch_qty = queue.pop(0)
                    dispatched_batches.append((job, batch_index, batch_qty))

    for job, batch_index, batch_qty in dispatched_batches:
        route = routes_by_id.get(job.route_id)
        if route is None:
            raise ValueError(f"Route not found for job {job.job_id}: route_id={job.route_id}")

        batch_id = make_batch_id(job.job_id, batch_index)
        current_time = job.release_at if job.release_at is not None else (
            datetime.min if use_calendar else DEFAULT_BASE_START
        )
        prev_end: datetime | None = None
        prev_transport_min = 0

        for step in route.steps:
            step_duration = max(1, int(math.ceil(step.cycle_time_per_piece_min * batch_qty)))
            station_id = step.station_id
            station_slots = available_at[station_id]
            slot_idx = min(range(len(station_slots)), key=lambda idx: station_slots[idx])
            slot_available_time = station_slots[slot_idx]
            if prev_end is not None:
                if slot_available_time >= prev_end + timedelta(minutes=prev_transport_min):
                    effective_ready = prev_end
                else:
                    effective_ready = prev_end + timedelta(minutes=prev_transport_min)
            else:
                effective_ready = current_time
            start_at = max(effective_ready, slot_available_time)
            if use_calendar:
                start_at, end_at = apply_calendar(
                    start_at,
                    step_duration,
                    cal.shifts,
                    cal.working_days,
                )
            else:
                end_at = start_at + timedelta(minutes=step_duration)
            events.append(
                BatchStepEvent(
                    batch_id=batch_id,
                    job_id=job.job_id,
                    step_id=step.id,
                    station_id=station_id,
                    start_at=start_at,
                    end_at=end_at,
                )
            )
            station_slots[slot_idx] = end_at
            prev_end = end_at
            prev_transport_min = int(step.transport_after_minutes)

    total_batches = len(dispatched_batches)

    finished_jobs: list[str] = []
    for job in jobs:
        route = routes_by_id.get(job.route_id)
        if route is None or not route.steps:
            continue

        job_events = [event for event in events if event.job_id == job.job_id]
        if not job_events:
            continue

        qty = int(job.quantity)
        batch_size = int(job.batch_size)
        last_batch_index = len(split_into_batches(qty, batch_size))
        last_batch_id = make_batch_id(job.job_id, last_batch_index)
        final_step_id = route.steps[-1].id

        if any(
            event.batch_id == last_batch_id and event.step_id == final_step_id
            for event in job_events
        ):
            finished_jobs.append(job.job_id)

    if events:
        min_start = min(event.start_at for event in events)
        max_end = max(event.end_at for event in events)
        makespan_minutes = int((max_end - min_start).total_seconds() / 60)
    else:
        makespan_minutes = 0

    work_minutes_by_station: dict[str, int] = {station.id: 0 for station in stations}
    for event in events:
        duration = int((event.end_at - event.start_at).total_seconds() / 60)
        work_minutes_by_station[event.station_id] = (
            work_minutes_by_station.get(event.station_id, 0) + duration
        )

    station_stats = [
        StationStat(
            station_id=station_id,
            work_minutes=work_minutes,
            utilization=(work_minutes / makespan_minutes if makespan_minutes > 0 else 0.0),
        )
        for station_id, work_minutes in work_minutes_by_station.items()
    ]
    station_stats.sort(key=lambda s: s.utilization, reverse=True)

    return SimulationResult(
        finished_jobs=finished_jobs,
        events=events,
        makespan_minutes=makespan_minutes,
        batch_count=total_batches,
        station_stats=station_stats,
    )
