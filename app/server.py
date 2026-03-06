from __future__ import annotations

from pathlib import Path
from datetime import datetime, time

from flask import Flask, request, jsonify, send_from_directory

from app.simulation.engine import simulate
from app.simulation.exporters_csv import to_csv_events
from app.simulation.exporters import to_json
from app.simulation.models import Calendar, Job, Route, Shift, Station, Step

APP_DIR = Path(__file__).resolve().parent
OUT_DIR = APP_DIR / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=".", static_url_path="")


def _safe_makespan_min(result) -> int:
    direct = getattr(result, "makespan_minutes", None)
    if isinstance(direct, (int, float)):
        return int(direct)

    events = getattr(result, "events", None) or []
    if not events:
        return 0

    def _to_dt(v):
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            s = v.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None
        return None

    starts = []
    ends = []
    for ev in events:
        s = _to_dt(getattr(ev, "start_at", None))
        e = _to_dt(getattr(ev, "end_at", None))
        if s is not None and e is not None:
            starts.append(s)
            ends.append(e)

    if not starts or not ends:
        return 0
    return int((max(ends) - min(starts)).total_seconds() // 60)


def _to_float(x, default=0.0):
    if x is None:
        return float(default)
    try:
        if isinstance(x, str) and x.strip() == "":
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _to_int(x, default=0):
    if x is None:
        return int(default)
    try:
        if isinstance(x, str) and x.strip() == "":
            return int(default)
        return int(float(x))
    except Exception:
        return int(default)


def _coerce_step(d):
    sid = d.get("id") or d.get("step_id") or d.get("name")
    station_id = d.get("station_id") or d.get("machine_id") or d.get("station") or d.get("machine")
    cycle = (
        d.get("cycle_time_per_piece_min")
        if d.get("cycle_time_per_piece_min") is not None
        else d.get("cycle_min")
        if d.get("cycle_min") is not None
        else d.get("process_time_min")
        if d.get("process_time_min") is not None
        else d.get("process_time")
    )
    if cycle is None:
        m = d.get("Machining_Time_min") or d.get("machining_time_min")
        lu = d.get("Load_Unload_Time_min") or d.get("load_unload_time_min")
        if m is not None or lu is not None:
            cycle = _to_float(m, 0.0) + _to_float(lu, 0.0)

    t_after = (
        d.get("transport_after_minutes")
        if d.get("transport_after_minutes") is not None
        else d.get("transport_after")
        if d.get("transport_after") is not None
        else d.get("transport_time_min")
        if d.get("transport_time_min") is not None
        else d.get("Transport_Time_min")
    )

    return Step(
        id=str(sid),
        station_id=str(station_id),
        cycle_time_per_piece_min=_to_float(cycle, 0.0),
        transport_after_minutes=_to_int(t_after, 0),
    )


def _coerce_route(r):
    if isinstance(r, Route):
        return r
    rid = r.get("id") or r.get("route_id") or r.get("name")
    steps_raw = r.get("steps") or r.get("Steps") or []
    steps = [s if isinstance(s, Step) else _coerce_step(s) for s in steps_raw]
    return Route(id=str(rid), steps=steps)


def _coerce_station(s):
    if isinstance(s, Station):
        return s
    sid = s.get("id") or s.get("station_id") or s.get("machine_id") or s.get("name")
    cap = s.get("capacity") if s.get("capacity") is not None else s.get("count")
    return Station(id=str(sid), capacity=_to_int(cap, 1))


def _coerce_job(j):
    if isinstance(j, Job):
        return j
    jid = j.get("id") or j.get("job_id") or j.get("name")
    route_id = j.get("route_id") or j.get("route") or j.get("routing_id")
    qty = j.get("quantity") if j.get("quantity") is not None else j.get("qty")
    batch = j.get("batch_size") if j.get("batch_size") is not None else j.get("batch")
    rel = j.get("release_time")
    if isinstance(rel, str):
        try:
            rel = datetime.fromisoformat(rel)
        except Exception:
            rel = None
    return Job(
        job_id=str(jid),
        route_id=str(route_id),
        quantity=_to_int(qty, 1),
        batch_size=_to_int(batch, 1),
        release_at=rel,
    )


@app.get("/")
def root():
    # open builder by default
    return send_from_directory(str(APP_DIR), "ui_flow_builder.html")


@app.get("/app/<path:filename>")
def app_files(filename: str):
    return send_from_directory(str(APP_DIR), filename)


@app.get("/gantt")
def gantt():
    return send_from_directory(str(OUT_DIR), "gantt.html")


@app.post("/simulate")
def simulate_api():
    payload = request.get_json(force=True) or {}
    scenario = payload.get("scenario") or payload

    jobs = scenario.get("jobs", [])
    routes = scenario.get("routes", [])
    stations = scenario.get("stations", [])
    cal = scenario.get("calendar", None)

    if jobs and isinstance(jobs[0], dict):
        jobs = [_coerce_job(x) for x in jobs]
    if routes and isinstance(routes[0], dict):
        routes = [_coerce_route(x) for x in routes]
    if stations and isinstance(stations[0], dict):
        stations = [_coerce_station(x) for x in stations]
    if isinstance(cal, dict):
        shifts_raw = cal.get("shifts") or []
        working_days_raw = cal.get("working_days") or cal.get("workingDays") or []
        shifts: list[Shift] = []
        for i, s in enumerate(shifts_raw, start=1):
            if isinstance(s, Shift):
                shifts.append(s)
                continue
            if not isinstance(s, dict):
                continue
            start_raw = s.get("start")
            end_raw = s.get("end")
            if isinstance(start_raw, str):
                start_raw = time.fromisoformat(start_raw)
            if isinstance(end_raw, str):
                end_raw = time.fromisoformat(end_raw)
            if isinstance(start_raw, time) and isinstance(end_raw, time):
                shifts.append(Shift(name=str(s.get("name") or f"S{i}"), start=start_raw, end=end_raw))
        working_days = set(int(d) for d in working_days_raw) if working_days_raw else {0, 1, 2, 3, 4}
        cal = Calendar(working_days=working_days, shifts=shifts) if shifts else None
    elif not isinstance(cal, Calendar):
        cal = None

    result = simulate(jobs=jobs, routes=routes, stations=stations, cal=cal)

    # Export files for gantt viewer
    events_path = OUT_DIR / "events.csv"
    result_path = OUT_DIR / "result.json"

    to_csv_events(result, str(events_path))
    to_json(result, str(result_path))

    return jsonify(
        {
            "ok": True,
            "out": {
                "events_csv": str(events_path),
                "result_json": str(result_path),
                "gantt_url": "/app/out/gantt.html",
            },
            "summary": {
                "events": len(result.events),
                "makespan_min": _safe_makespan_min(result),
            },
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
