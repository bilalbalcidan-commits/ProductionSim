from __future__ import annotations

from dataclasses import is_dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
import inspect

import pandas as pd

from app.simulation.models import Job, Route, Step, Station
try:
    from app.simulation.calendar import Calendar
except Exception:
    Calendar = None  # type: ignore


def _norm_sheet_name(name: str) -> str:
    return str(name).strip().upper()


def _safe_float(x: Any, default: float = 0.0) -> float:
    if x is None:
        return default
    try:
        if pd.isna(x):
            return default
    except Exception:
        pass
    try:
        return float(x)
    except Exception:
        return default


def _safe_int(x: Any, default: int = 0) -> int:
    if x is None:
        return default
    try:
        if pd.isna(x):
            return default
    except Exception:
        pass
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return default


def _parse_dt(x: Any) -> Optional[datetime]:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except Exception:
        pass
    if isinstance(x, pd.Timestamp):
        return x.to_pydatetime()
    if isinstance(x, datetime):
        return x
    try:
        return pd.to_datetime(x).to_pydatetime()
    except Exception:
        return None


def _ctor_kwargs(cls: type) -> set:
    if is_dataclass(cls):
        return {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
    try:
        sig = inspect.signature(cls.__init__)
        return {k for k in sig.parameters.keys() if k != "self"}
    except Exception:
        return set()


def _make_obj(cls: type, **kwargs):
    allowed = _ctor_kwargs(cls)
    if allowed:
        kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    return cls(**kwargs)


def _require_cols(df: pd.DataFrame, cols: List[str], sheet: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Sheet '{sheet}' is missing required column(s): {', '.join(missing)}")


def load_excel_input(path: str):
    """
    Supports BOTH:
      - canonical sheets: jobs, routes, stations, calendar
      - template sheets: PRODUCTION_PLAN, ROUTING, MACHINES, WEEK_CALENDAR, SIMULATION_SETTINGS
    Returns:
      jobs, routes, stations, calendar
    """
    sheets_raw: Dict[str, pd.DataFrame] = pd.read_excel(path, sheet_name=None)
    sheets: Dict[str, pd.DataFrame] = {_norm_sheet_name(k): v for k, v in sheets_raw.items()}

    alias = {
        "JOBS": "PRODUCTION_PLAN",
        "ROUTES": "ROUTING",
        "STATIONS": "MACHINES",
        "CALENDAR": "WEEK_CALENDAR",
    }

    def _get_sheet(primary: str) -> Optional[pd.DataFrame]:
        p = _norm_sheet_name(primary)
        if p in sheets:
            return sheets[p]
        a = alias.get(p)
        if a and a in sheets:
            return sheets[a]
        return None

    jobs_df = _get_sheet("jobs")
    routes_df = _get_sheet("routes")
    stations_df = _get_sheet("stations")
    cal_df = _get_sheet("calendar")  # optional

    if jobs_df is None or routes_df is None or stations_df is None:
        available = ", ".join(sorted(sheets.keys()))
        raise ValueError(
            "Missing required sheet(s). Expected either:\n"
            "- jobs, routes, stations (calendar optional)\n"
            "OR template sheets:\n"
            "- PRODUCTION_PLAN, ROUTING, MACHINES (WEEK_CALENDAR optional)\n"
            f"Available sheets in workbook: {available}"
        )

    def _upper_cols(df: pd.DataFrame) -> set:
        return {str(c).strip().upper() for c in df.columns}

    template_mode = (
        ("JOB" in _upper_cols(jobs_df) and "QUANTITY" in _upper_cols(jobs_df))
        and ("JOB" in _upper_cols(routes_df) and "MACHINE" in _upper_cols(routes_df))
        and ("MACHINE" in _upper_cols(stations_df))
        and ("PRODUCTION_PLAN" in sheets and "ROUTING" in sheets and "MACHINES" in sheets)
    )

    stations: List[Station] = []
    routes: List[Route] = []
    jobs: List[Job] = []
    calendar_obj = None

    if template_mode:
        # -----------------------------
        # TEMPLATE PARSE
        # -----------------------------
        # MACHINES -> stations
        _require_cols(stations_df, ["Machine"], "MACHINES")
        for _, r in stations_df.iterrows():
            machine = r.get("Machine")
            if machine is None or (isinstance(machine, str) and not machine.strip()):
                continue
            machine = str(machine).strip()

            count = _safe_int(r.get("Machines_Count"), default=1)
            cap = _safe_int(r.get("Capacity_Per_Machine"), default=1)

            stations.append(
                _make_obj(
                    Station,
                    station_id=machine,
                    id=machine,
                    name=machine,
                    machines_count=count,
                    count=count,
                    capacity_per_machine=cap,
                    capacity=cap,
                )
            )

        # ROUTING -> routes (one route per Job)
        _require_cols(routes_df, ["Job", "Machine"], "ROUTING")
        routes_df = routes_df.reset_index(drop=True)

        job_order: List[str] = []
        seen = set()
        for v in routes_df["Job"].tolist():
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            k = str(v).strip()
            if k not in seen:
                seen.add(k)
                job_order.append(k)

        batch_by_job: Dict[str, int] = {}
        if "Batch_Size" in routes_df.columns:
            for j in job_order:
                sub = routes_df[routes_df["Job"].astype(str).str.strip() == j]
                bs_val: Optional[int] = None
                for x in sub["Batch_Size"].tolist():
                    if x is None:
                        continue
                    try:
                        if pd.isna(x):
                            continue
                    except Exception:
                        pass
                    bs_val = _safe_int(x, default=1)
                    if bs_val > 0:
                        break
                if bs_val is not None and bs_val > 0:
                    batch_by_job[j] = bs_val

        for j in job_order:
            route_id = f"R_{j}"
            sub = routes_df[routes_df["Job"].astype(str).str.strip() == j].reset_index(drop=True)

            steps: List[Step] = []
            previous_step = None
            for i, rr in sub.iterrows():
                machine = rr.get("Machine")
                if machine is None or (isinstance(machine, str) and not machine.strip()):
                    continue
                machine = str(machine).strip()

                machining = _safe_float(rr.get("Machining_Time_min"), 0.0)
                lu = _safe_float(rr.get("Load_Unload_Time_min"), 0.0)
                transport = _safe_float(rr.get("Transport_Time_min"), 0.0)
                if previous_step is not None:
                    previous_step.transport_after_minutes = int(transport)

                process_time_min = machining + lu
                step_id = f"{route_id}-S{i+1}"

                step_obj = _make_obj(
                    Step,
                    step_id=step_id,
                    id=step_id,
                    station_id=machine,
                    machine_id=machine,
                    process_time_min=process_time_min,
                    cycle_time_per_piece_min=process_time_min,
                    process_time=process_time_min,
                    duration_min=process_time_min,
                    transport_after_minutes=0,
                    transport_time_min=transport,
                    transfer_time_min=transport,
                )
                steps.append(step_obj)
                previous_step = step_obj

            routes.append(_make_obj(Route, route_id=route_id, id=route_id, steps=steps))

        # PRODUCTION_PLAN -> jobs
        _require_cols(jobs_df, ["Job", "Quantity"], "PRODUCTION_PLAN")
        for _, r in jobs_df.iterrows():
            jid = r.get("Job")
            if jid is None or (isinstance(jid, str) and not jid.strip()):
                continue
            jid = str(jid).strip()
            route_id = f"R_{jid}"
            qty = _safe_int(r.get("Quantity"), default=0)
            rel = _parse_dt(r.get("Release_DateTime"))
            if rel is None:
                rel = datetime(2026, 3, 2, 8, 0, 0)
            batch_size = batch_by_job.get(jid, 1)

            jobs.append(
                _make_obj(
                    Job,
                    job_id=jid,
                    id=jid,
                    route_id=route_id,
                    quantity=qty,
                    qty=qty,
                    batch_size=batch_size,
                    release_time=rel,
                    release_at=rel,
                    release_dt=rel,
                    release_datetime=rel,
                    release_date_time=rel,
                    release_start=rel,
                    start_at=rel,
                    start_dt=rel,
                )
            )

        # WEEK_CALENDAR (optional) - fallback Shift1 only (best-effort)
        if cal_df is not None and Calendar is not None:
            # Expect columns like: Day, Is_Working, Shift1_Start, Shift1_End, Shift2_*, Shift3_*
            cols_u = {str(c).strip().upper(): c for c in cal_df.columns}
            day_col = cols_u.get("DAY")
            work_col = cols_u.get("IS_WORKING")
            s1s_col = cols_u.get("SHIFT1_START")
            s1e_col = cols_u.get("SHIFT1_END")

            if day_col and work_col and s1s_col and s1e_col:
                weekday_map = {
                    "MON": 0, "MONDAY": 0,
                    "TUE": 1, "TUESDAY": 1,
                    "WED": 2, "WEDNESDAY": 2,
                    "THU": 3, "THURSDAY": 3,
                    "FRI": 4, "FRIDAY": 4,
                    "SAT": 5, "SATURDAY": 5,
                    "SUN": 6, "SUNDAY": 6,
                }
                working_days = []
                default_shift = None
                for _, rr in cal_df.iterrows():
                    d = rr.get(day_col)
                    if d is None:
                        continue
                    d_raw = str(d).strip()
                    d_norm = d_raw.upper()
                    d = weekday_map.get(d_norm, d_raw)
                    is_work = _safe_int(rr.get(work_col), 0) == 1
                    if not is_work:
                        continue
                    st = rr.get(s1s_col)
                    en = rr.get(s1e_col)
                    if st is None or en is None:
                        continue
                    try:
                        if pd.isna(st) or pd.isna(en):
                            continue
                    except Exception:
                        pass
                    working_days.append(d)
                    if default_shift is None:
                        default_shift = (str(st).strip(), str(en).strip())

                if working_days and default_shift is not None:
                    st, en = default_shift
                    try:
                        calendar_obj = _make_obj(Calendar, working_days=working_days, shifts=[(st, en)])
                    except Exception:
                        try:
                            calendar_obj = _make_obj(Calendar, working_days=working_days, start_time=st, end_time=en)
                        except Exception:
                            calendar_obj = None
                    if calendar_obj is not None and hasattr(calendar_obj, "shifts") and len(calendar_obj.shifts) == 0:
                        calendar_obj = None

        return jobs, routes, stations, calendar_obj

    # -----------------------------
    # CANONICAL PARSE (legacy)
    # -----------------------------
    _require_cols(stations_df, ["station_id"], "stations")
    for _, r in stations_df.iterrows():
        sid = r.get("station_id")
        if sid is None or (isinstance(sid, str) and not sid.strip()):
            continue
        sid = str(sid).strip()
        stations.append(_make_obj(Station, station_id=sid, id=sid, name=sid))

    _require_cols(routes_df, ["route_id", "step_id", "station_id", "process_time"], "routes")
    routes_df = routes_df.copy()
    # Preserve a stable order: group by route_id, then as-is appearance
    routes_df["_row"] = range(len(routes_df))

    for rid, grp in routes_df.groupby("route_id", sort=False):
        steps: List[Step] = []
        grp = grp.sort_values("_row")
        for _, rr in grp.iterrows():
            step_id = str(rr["step_id"]).strip()
            station_id = str(rr["station_id"]).strip()
            pt = _safe_float(rr["process_time"], 0.0)
            steps.append(_make_obj(Step, step_id=step_id, id=step_id, station_id=station_id, process_time_min=pt, cycle_time_per_piece_min=pt, process_time=pt, duration_min=pt, transport_after_minutes=0))
        routes.append(_make_obj(Route, route_id=str(rid).strip(), id=str(rid).strip(), steps=steps))

    _require_cols(jobs_df, ["job_id", "route_id", "quantity", "batch_size"], "jobs")
    for _, r in jobs_df.iterrows():
        jid = str(r["job_id"]).strip()
        rid = str(r["route_id"]).strip()
        qty = _safe_int(r["quantity"], 0)
        bs = _safe_int(r["batch_size"], 1)
        rel = _parse_dt(r.get("release_time"))
        if rel is None:
            rel = datetime(2026, 3, 2, 8, 0, 0)
        jobs.append(_make_obj(Job, job_id=jid, id=jid, route_id=rid, quantity=qty, batch_size=bs, release_time=rel, release_at=rel, release_dt=rel, release_datetime=rel, release_date_time=rel, release_start=rel, start_at=rel, start_dt=rel))

    # calendar optional in canonical mode
    if cal_df is not None and Calendar is not None:
        # Expect columns: day, start_time, end_time
        _require_cols(cal_df, ["day", "start_time", "end_time"], "calendar")
        # Best effort: store per-day window if Calendar supports shifts, else use first row as default
        try:
            shifts = {}
            working_days = []
            for _, rr in cal_df.iterrows():
                d = str(rr["day"]).strip()
                st = str(rr["start_time"]).strip()
                en = str(rr["end_time"]).strip()
                working_days.append(d)
                shifts[d] = (st, en)
            try:
                calendar_obj = _make_obj(Calendar, working_days=working_days, shifts=shifts)
            except Exception:
                st, en = next(iter(shifts.values()))
                calendar_obj = _make_obj(Calendar, working_days=working_days, start_time=st, end_time=en)
        except Exception:
            calendar_obj = None

    return jobs, routes, stations, calendar_obj
