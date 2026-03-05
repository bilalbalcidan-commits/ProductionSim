from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time


@dataclass(slots=True)
class Shift:
    name: str
    start: time
    end: time


@dataclass(slots=True)
class Calendar:
    working_days: set[int] = field(default_factory=lambda: {0, 1, 2, 3, 4})
    shifts: list[Shift] = field(default_factory=list)
    holidays: set[date] = field(default_factory=set)


@dataclass(slots=True)
class Step:
    id: str
    station_id: str
    cycle_time_per_piece_min: float
    transport_after_minutes: int = 0


@dataclass(slots=True)
class Route:
    id: str
    steps: list[Step] = field(default_factory=list)


@dataclass(slots=True)
class Job:
    job_id: str
    route_id: str
    quantity: int = 1
    batch_size: int = 1
    release_at: datetime | None = None


@dataclass(slots=True)
class Station:
    id: str
    capacity: int = 1
    queue: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BatchStepEvent:
    batch_id: str
    job_id: str
    step_id: str
    station_id: str
    start_at: datetime
    end_at: datetime


@dataclass(slots=True)
class StationStat:
    station_id: str
    work_minutes: int
    utilization: float


@dataclass(slots=True)
class SimulationResult:
    finished_jobs: list[str] = field(default_factory=list)
    events: list[BatchStepEvent] = field(default_factory=list)
    makespan_minutes: int = 0
    batch_count: int = 0
    station_stats: list[StationStat] = field(default_factory=list)
