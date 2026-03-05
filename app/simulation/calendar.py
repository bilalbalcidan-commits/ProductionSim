from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .models import Calendar


def weekday_name(dt: date | datetime) -> str:
    d = dt.date() if isinstance(dt, datetime) else dt
    return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d.weekday()]


def is_working_day(dt: date | datetime, cal: Calendar) -> bool:
    d = dt.date() if isinstance(dt, datetime) else dt
    return d.weekday() in cal.working_days and d not in cal.holidays


def working_windows_for_day(day: date, cal: Calendar) -> list[tuple[datetime, datetime]]:
    if not is_working_day(day, cal):
        return []

    windows: list[tuple[datetime, datetime]] = []
    for shift in sorted(cal.shifts, key=lambda s: s.start):
        start_dt = datetime.combine(day, shift.start)
        end_dt = datetime.combine(day, shift.end)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        windows.append((start_dt, end_dt))
    return windows


def next_working_time(dt: datetime, cal: Calendar) -> datetime:
    if not cal.working_days or not cal.shifts:
        raise ValueError("Calendar must define at least one working day and one shift")

    cursor = dt
    while True:
        windows = working_windows_for_day(cursor.date(), cal)
        for start_dt, end_dt in windows:
            if start_dt <= cursor < end_dt:
                return cursor
            if cursor < start_dt:
                return start_dt
        cursor = datetime.combine(cursor.date() + timedelta(days=1), time.min)


def add_work_minutes(dt: datetime, minutes: int, cal: Calendar) -> datetime:
    if minutes < 0:
        raise ValueError("minutes must be >= 0")

    remaining = minutes
    cursor = next_working_time(dt, cal)

    if remaining == 0:
        return cursor

    while True:
        windows = working_windows_for_day(cursor.date(), cal)
        progressed = False

        for start_dt, end_dt in windows:
            if cursor < start_dt:
                cursor = start_dt
            if not (start_dt <= cursor < end_dt):
                continue

            available = int((end_dt - cursor).total_seconds() // 60)
            if remaining <= available:
                return cursor + timedelta(minutes=remaining)

            remaining -= available
            cursor = end_dt
            progressed = True

        if remaining <= 0:
            return cursor

        if not progressed:
            cursor = datetime.combine(cursor.date() + timedelta(days=1), time.min)
            cursor = next_working_time(cursor, cal)
        else:
            cursor = next_working_time(cursor, cal)


def working_minutes_between(dt1: datetime, dt2: datetime, cal: Calendar) -> int:
    if dt2 <= dt1:
        return 0

    total = 0
    cursor = dt1

    while cursor < dt2:
        windows = working_windows_for_day(cursor.date(), cal)
        advanced = False

        for start_dt, end_dt in windows:
            start = max(cursor, start_dt)
            end = min(dt2, end_dt)
            if start < end:
                total += int((end - start).total_seconds() // 60)

            if cursor < end_dt:
                cursor = end_dt
                advanced = True

            if cursor >= dt2:
                break

        if cursor >= dt2:
            break

        if not advanced:
            next_day = datetime.combine(cursor.date() + timedelta(days=1), time.min)
            cursor = next_day

    return total
