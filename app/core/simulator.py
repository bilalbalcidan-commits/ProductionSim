from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from app.core.calendar import push_to_next_working_time, consume_working_minutes
from app.core.dispatch import QueueItem, choose_next
from app.core.lotting import Lot, make_lots
from app.core.models import Part, SimulationInput


@dataclass
class GanttSegment:
    machine_id: str
    part_id: str
    lot_id: str
    units: int
    op_no: int
    segment_index: int
    start: datetime
    end: datetime


@dataclass
class LotState:
    part_id: str
    lot_id: str
    units: int
    op_index: int           # 0-based index in route
    ready_time: datetime    # ready to start next op at/after this time


def simulate_mvp(inp: SimulationInput, end_dt: Optional[datetime] = None) -> List[GanttSegment]:
    """
    MVP flow-shop style simulation with:
    - single-capacity machines
    - lots flow through their route in order
    - calendar-aware time consumption (segments)
    - dispatch: bottleneck-first B1 (only special on bottleneck machine: earliest ready_time), FIFO elsewhere
    - optional horizon limit: do not start segments at/after end_dt, trim crossing segments
    """
    cal = inp.simulation.calendar
    disp = inp.simulation.dispatch

    # Index parts
    parts_by_id: Dict[str, Part] = {p.part_id: p for p in inp.parts}

    # Machine availability
    machine_available: Dict[str, datetime] = {}
    for m in inp.machines:
        machine_available[m.machine_id] = push_to_next_working_time(inp.simulation.start_datetime, cal)

    # Build lot states (all released at part.release_datetime)
    lots: List[LotState] = []
    for p in inp.parts:
        for lot in make_lots(p, inp.simulation.lotting):
            lots.append(LotState(
                part_id=p.part_id,
                lot_id=lot.lot_id,
                units=lot.units,
                op_index=0,
                ready_time=p.release_datetime
            ))

    # Helper to get current op for a lot
    def current_op(ls: LotState):
        route = parts_by_id[ls.part_id].route
        return route[ls.op_index]

    # Main loop: continue until all lots finished
    gantt: List[GanttSegment] = []
    remaining = len(lots)

    # To avoid O(infinite), cap iterations
    max_iters = len(lots) * 10_000
    iters = 0

    while remaining > 0:
        iters += 1
        if iters > max_iters:
            raise RuntimeError("Simulation exceeded iteration cap (likely a logic error).")

        # Collect candidates per machine: lots that are not finished and whose next op is on that machine
        candidates: Dict[str, List[LotState]] = {}
        for ls in lots:
            route = parts_by_id[ls.part_id].route
            if ls.op_index >= len(route):
                continue
            op = route[ls.op_index]
            candidates.setdefault(op.machine_id, []).append(ls)

        progressed = False
        earliest_candidate_start: Optional[datetime] = None

        # Try to schedule something on each machine if possible
        for machine_id, ls_list in candidates.items():
            # machine can only start after it is available
            # build queue items that are ready (or will be ready) for this machine
            q_items: List[QueueItem] = [
                QueueItem(part_id=ls.part_id, lot_id=ls.lot_id, ready_time=ls.ready_time)
                for ls in ls_list
            ]

            # choose next by dispatch rule
            chosen_q = choose_next(
                machine_id=machine_id,
                queue=q_items,
                strategy=disp.strategy if disp.strategy else "fifo",
                bottleneck_machine_id=disp.bottleneck_machine_id,
                variant=disp.variant,
            )

            # find the matching lotstate
            chosen_ls = next(ls for ls in ls_list if ls.lot_id == chosen_q.lot_id)

            op = current_op(chosen_ls)
            duration_min = int(chosen_ls.units * op.cycle_min_per_unit)

            start = max(machine_available[machine_id], chosen_ls.ready_time)
            start = push_to_next_working_time(start, cal)

            for ls in ls_list:
                cand_start = max(machine_available[machine_id], ls.ready_time)
                cand_start = push_to_next_working_time(cand_start, cal)
                if earliest_candidate_start is None or cand_start < earliest_candidate_start:
                    earliest_candidate_start = cand_start

            if end_dt and start >= end_dt:
                continue

            end, segs = consume_working_minutes(start, duration_min, cal)
            reached_horizon = False

            for idx, (a, b) in enumerate(segs, start=1):
                if end_dt and a >= end_dt:
                    reached_horizon = True
                    break
                if end_dt and b > end_dt:
                    gantt.append(GanttSegment(
                        machine_id=machine_id,
                        part_id=chosen_ls.part_id,
                        lot_id=chosen_ls.lot_id,
                        units=chosen_ls.units,
                        op_no=op.op_no,
                        segment_index=idx,
                        start=a,
                        end=end_dt
                    ))
                    reached_horizon = True
                    break

                gantt.append(GanttSegment(
                    machine_id=machine_id,
                    part_id=chosen_ls.part_id,
                    lot_id=chosen_ls.lot_id,
                    units=chosen_ls.units,
                    op_no=op.op_no,
                    segment_index=idx,
                    start=a,
                    end=b
                ))

            if reached_horizon:
                break

            # update machine availability and lot state
            machine_available[machine_id] = end
            chosen_ls.ready_time = end
            chosen_ls.op_index += 1

            # finished?
            if chosen_ls.op_index >= len(parts_by_id[chosen_ls.part_id].route):
                remaining -= 1

            progressed = True

        if end_dt and not progressed:
            if earliest_candidate_start is None or earliest_candidate_start >= end_dt:
                break

        if not progressed:
            raise RuntimeError("No progress made; deadlock in simulation logic.")

    # Sort Gantt for nicer output
    gantt.sort(key=lambda s: (s.machine_id, s.start, s.lot_id, s.segment_index))
    return gantt
