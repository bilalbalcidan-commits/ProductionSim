from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel


class Machine(BaseModel):
    machine_id: str
    name: str
    capacity: int = 1


class Operation(BaseModel):
    op_no: int
    machine_id: str
    cycle_min_per_unit: float


class Part(BaseModel):
    part_id: str
    name: str
    weekly_target_qty: int
    release_datetime: datetime
    route: List[Operation]


class Shift(BaseModel):
    start: str          # "08:00"
    end: str            # "16:00"
    break_min: int


class CalendarConfig(BaseModel):
    workdays: List[str]
    offdays: List[str]
    shifts: List[Shift]


class LottingConfig(BaseModel):
    mode: str
    target_lot_time_min: int
    min_visual_lot_qty: int


class DispatchConfig(BaseModel):
    strategy: str
    bottleneck_machine_id: Optional[str] = None
    variant: Optional[str] = None


class SimulationConfig(BaseModel):
    start_datetime: datetime
    timezone: str
    calendar: CalendarConfig
    lotting: LottingConfig
    dispatch: DispatchConfig


class SimulationInput(BaseModel):
    simulation: SimulationConfig
    machines: List[Machine]
    parts: List[Part]
