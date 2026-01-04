from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.models import (
    CalendarConfig,
    DispatchConfig,
    LottingConfig,
    Machine,
    Operation,
    Part,
    SimulationConfig,
    SimulationInput,
)
from app.core.reports import (
    filter_first_weekly_rows,
    make_onepage_dashboard_html,
    summarize_busy_minutes,
    weekly_load_minutes,
    machine_part_op_load_breakdown,
    make_offline_gantt_html,
)
from app.core.simulator import simulate_mvp
from app.db.models import CycleTime, Factory, Part as DbPart, RouteStep, SimulationRun


def _default_calendar(factory: Factory) -> CalendarConfig:
    return CalendarConfig(
        workdays=["MON", "TUE", "WED", "THU", "FRI", "SAT"],
        offdays=["SUN"],
        shifts=[
            {"start": "08:00", "end": "16:00", "break_min": 30},
            {"start": "16:00", "end": "00:00", "break_min": 30},
        ],
    )


def _default_lotting() -> LottingConfig:
    return LottingConfig(
        mode="auto_cycle_based",
        target_lot_time_min=60,
        min_visual_lot_qty=10,
    )


def _default_dispatch() -> DispatchConfig:
    return DispatchConfig(
        strategy="bottleneck_first",
        bottleneck_machine_id=None,
        variant=None,
    )


def build_simulation_input(db: Session, factory_id: Optional[int]) -> SimulationInput:
    if factory_id is None:
        factories = db.query(Factory).all()
        if not factories:
            raise ValueError("No factories available")
        factory = factories[0]
        parts = db.query(DbPart).all()
    else:
        factory = db.get(Factory, factory_id)
        if not factory:
            raise ValueError("Factory not found")
        parts = db.query(DbPart).filter_by(factory_id=factory_id, active=True).all()

    if not parts:
        raise ValueError("No parts defined for factory")

    machines: Dict[str, Machine] = {}
    part_models: List[Part] = []

    for p in parts:
        routes = db.query(RouteStep).filter_by(part_id=p.id).order_by(RouteStep.op_no).all()
        cycles = {ct.op_no: ct for ct in db.query(CycleTime).filter_by(part_id=p.id).all()}
        ops: List[Operation] = []
        for r in routes:
            ct = cycles.get(r.op_no)
            if not ct:
                raise ValueError(f"Missing cycle time for part {p.part_code} op {r.op_no}")
            ops.append(Operation(op_no=r.op_no, machine_id=r.machine_id, cycle_min_per_unit=ct.cycle_min_per_unit))
            if r.machine_id not in machines:
                machines[r.machine_id] = Machine(machine_id=r.machine_id, name=r.machine_id, capacity=1)

        release_dt = p.release_datetime or datetime.utcnow()
        part_models.append(
            Part(
                part_id=p.part_code,
                name=p.name,
                weekly_target_qty=p.weekly_target_qty,
                release_datetime=release_dt,
                route=ops,
            )
        )

    sim_cfg = SimulationConfig(
        start_datetime=datetime.utcnow(),
        timezone=factory.timezone or "Europe/Istanbul",
        calendar=_default_calendar(factory),
        lotting=_default_lotting(),
        dispatch=_default_dispatch(),
    )

    return SimulationInput(
        simulation=sim_cfg,
        machines=list(machines.values()),
        parts=part_models,
    )


def run_simulation_and_store(
    db: Session,
    run: SimulationRun,
    factory_id: Optional[int],
    out_root: str = "app/reports",
) -> SimulationRun:
    input_model = build_simulation_input(db, factory_id)
    payload = json.loads(input_model.model_dump_json())

    out_dir = Path(out_root) / str(run.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    segments = simulate_mvp(input_model)
    busy = summarize_busy_minutes(segments)
    weekly = weekly_load_minutes(segments, input_model.simulation.calendar)
    weekly_for_dashboard = filter_first_weekly_rows(weekly)
    machine_cap = {m.machine_id: m.capacity for m in input_model.machines}
    breakdown = machine_part_op_load_breakdown(segments, input_model.simulation.calendar, machine_cap)

    gantt_path = str(out_dir / "onepage_gantt.html")
    make_offline_gantt_html(segments, gantt_path)
    dashboard_path = str(out_dir / "onepage_dashboard.html")
    make_onepage_dashboard_html(segments, busy, weekly_for_dashboard, breakdown, input_model.parts, dashboard_path)

    result = {
        "meta": {
            "machines": len(input_model.machines),
            "parts": len(input_model.parts),
            "segments": len(segments),
            "last_end": segments[-1].end if segments else None,
        },
        "busy_minutes": dict(sorted(busy.items(), key=lambda x: x[0])),
        "weekly_load": weekly,
        "machine_load_breakdown": breakdown,
        "files": {
            "gantt_html": gantt_path,
            "dashboard_html": dashboard_path,
        },
    }
    result_path = out_dir / "result.json"
    result_path.write_text(json.dumps(result, default=str, indent=2), encoding="utf-8")

    run.input_payload_json = payload
    run.result_json_path = str(result_path)
    run.dashboard_html_path = dashboard_path
    run.gantt_html_path = gantt_path
    run.status = "completed"
    db.commit()
    return run
