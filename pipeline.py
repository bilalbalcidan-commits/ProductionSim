from __future__ import annotations

from typing import Dict, Any

from app.core.io import read_json, write_json
from app.core.models import SimulationInput
from app.core.simulator import simulate_mvp
from app.core.reports import (
    summarize_busy_minutes,
    weekly_load_minutes,
    make_offline_gantt_html,
    make_onepage_dashboard_html,
    filter_first_weekly_rows,
    machine_part_op_load_breakdown,
)


def run_pipeline(payload_path: str, out_dir: str = "app/reports") -> Dict[str, Any]:
    raw = read_json(payload_path)
    inp = SimulationInput.model_validate(raw)

    segments = simulate_mvp(inp)

    busy = summarize_busy_minutes(segments)
    weekly = weekly_load_minutes(segments, inp.simulation.calendar)
    weekly_for_dashboard = filter_first_weekly_rows(weekly)
    machine_cap = {m.machine_id: m.capacity for m in inp.machines}
    breakdown = machine_part_op_load_breakdown(segments, inp.simulation.calendar, machine_cap)

    # onepage gantt (bars)
    gantt_path = f"{out_dir}/onepage_gantt.html"
    make_offline_gantt_html(segments, gantt_path)

    # onepage dashboard (kpi + busy + weekly + gantt)
    dashboard_path = f"{out_dir}/onepage_dashboard.html"
    make_onepage_dashboard_html(segments, busy, weekly_for_dashboard, breakdown, inp.parts, dashboard_path)

    result: Dict[str, Any] = {
        "meta": {
            "machines": len(inp.machines),
            "parts": len(inp.parts),
            "segments": len(segments),
            "last_end": segments[-1].end if segments else None,
        },
        "busy_minutes": dict(sorted(busy.items(), key=lambda x: x[0])),
        "weekly_load": weekly,
        "files": {
            "gantt_html": gantt_path,
            "dashboard_html": dashboard_path,
        },
    }

    write_json(f"{out_dir}/result.json", result)
    return result
