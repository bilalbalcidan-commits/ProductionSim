from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import List, Tuple

from app.core.models import CalendarConfig


WEEKDAY_MAP = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6
}


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


@dataclass(frozen=True)
class ShiftWindow:
    start: time
    end: time
    break_min: int

    @property
    def net_minutes(self) -> int:
        # shift length in minutes - break
        start_dt = datetime(2000, 1, 1, self.start.hour, self.start.minute)
        # allow "00:00" end meaning midnight
        end_dt = datetime(2000, 1, 2, 0, 0) if (self.end.hour == 0 and self.end.minute == 0) else datetime(2000, 1, 1, self.end.hour, self.end.minute)
        total = int((end_dt - start_dt).total_seconds() // 60)
        return max(0, total - self.break_min)


def build_shift_windows(cal: CalendarConfig) -> List[ShiftWindow]:
    wins: List[ShiftWindow] = []
    for sh in cal.shifts:
        wins.append(ShiftWindow(start=_parse_hhmm(sh.start), end=_parse_hhmm(sh.end), break_min=sh.break_min))
    return wins


def is_workday(dt: datetime, cal: CalendarConfig) -> bool:
    wd = dt.weekday()  # 0=Mon
    workdays = {WEEKDAY_MAP[d] for d in cal.workdays}
    return wd in workdays


def day_start(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, dt.day, 0, 0, 0)


def next_workday_00(dt: datetime, cal: CalendarConfig) -> datetime:
    d = day_start(dt)
    while True:
        if is_workday(d, cal):
            return d
        d += timedelta(days=1)


def push_to_next_working_time(dt: datetime, cal: CalendarConfig) -> datetime:
    """
    If dt is outside working windows (or on offday), move it to the next valid working datetime.
    Break handling is simplified: we assume work can start at shift start + break_min.
    """
    shift_wins = build_shift_windows(cal)

    cur = dt
    while True:
        # offday -> jump to next workday
        if not is_workday(cur, cal):
            cur = next_workday_00(cur + timedelta(days=1), cal)
            continue

        # find first shift window where we can work today
        for w in shift_wins:
            work_start = datetime(cur.year, cur.month, cur.day, w.start.hour, w.start.minute) + timedelta(minutes=w.break_min)
            # end may be midnight (00:00 -> next day)
            if w.end.hour == 0 and w.end.minute == 0:
                work_end = datetime(cur.year, cur.month, cur.day, 0, 0) + timedelta(days=1)
            else:
                work_end = datetime(cur.year, cur.month, cur.day, w.end.hour, w.end.minute)

            if cur <= work_start:
                return work_start
            if work_start < cur < work_end:
                return cur

        # no shift left today -> move to next day 00:00 then push again
        cur = day_start(cur) + timedelta(days=1)


def consume_working_minutes(start: datetime, minutes: int, cal: CalendarConfig) -> Tuple[datetime, List[Tuple[datetime, datetime]]]:
    """
    Consume 'minutes' of net working time starting from 'start', respecting workdays + shifts + breaks.
    Returns: (end_datetime, list_of_segments[(seg_start, seg_end)]) for Gantt splitting.
    """
    if minutes <= 0:
        return start, []

    shift_wins = build_shift_windows(cal)
    cur = push_to_next_working_time(start, cal)
    remaining = minutes
    segments: List[Tuple[datetime, datetime]] = []

    while remaining > 0:
        # ensure we are on a workday and inside a shift
        cur = push_to_next_working_time(cur, cal)

        # find current shift window containing cur
        active = None
        for w in shift_wins:
            work_start = datetime(cur.year, cur.month, cur.day, w.start.hour, w.start.minute) + timedelta(minutes=w.break_min)
            if w.end.hour == 0 and w.end.minute == 0:
                work_end = datetime(cur.year, cur.month, cur.day, 0, 0) + timedelta(days=1)
            else:
                work_end = datetime(cur.year, cur.month, cur.day, w.end.hour, w.end.minute)

            if work_start <= cur < work_end:
                active = (work_start, work_end)
                break

        if active is None:
            # outside shifts -> jump
            cur = push_to_next_working_time(cur, cal)
            continue

        work_start, work_end = active
        available = int((work_end - cur).total_seconds() // 60)
        take = min(remaining, available)

        seg_start = cur
        seg_end = cur + timedelta(minutes=take)
        segments.append((seg_start, seg_end))

        remaining -= take
        cur = seg_end

        if remaining > 0 and cur >= work_end:
            # move to next day
            cur = day_start(cur) + timedelta(days=1)

    return cur, segments
