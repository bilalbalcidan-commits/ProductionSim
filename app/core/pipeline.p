from __future__ import annotations

from dataclasses import asdict
from typing import Dict, Any

from app.core.io import read_json, write_json
from app.core.models import SimulationInput
from app.core.simulator import simulate_mvp
from app.core.reports import summarize_busy_minutes, weekly_load_minutes, make_offline_gantt_html


def run_pipeline(payload_path: str, out_dir: str = "app/reports") -> Dict[str, Any]:
    raw = read_json(payload_path)
    inp = SimulationInput.model_validate(raw)

    segments = simulate_mvp(inp)

    busy = summarize_busy_minutes(segments)
    weekly = weekly_load_minutes(segments, inp.simulation.calendar)

    # Onepage artifacts
    gantt_path = f"{out_dir}/onepage_gantt.html"
    make_offline_gantt_html(segments, gantt_path)

    result = {
        "meta": {
            "machines": len(inp.machines),
            "parts": len(inp.parts),
            "segments": len(segments),
            "last_end": segments[-1].end if segments else None,
        },
        "busy_minutes": dict(sorted(busy.items(), key=lambda x: x[0])),
        "weekly_load": weekly,
        "files": {
            "gantt_html": gantt_path
        }
    }

    write_json(f"{out_dir}/result.json", result)
    return result
