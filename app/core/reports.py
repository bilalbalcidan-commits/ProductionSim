from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

from app.core.models import CalendarConfig, Part
from app.core.simulator import GanttSegment


def summarize_busy_minutes(segments: List[GanttSegment]) -> Dict[str, int]:
    busy: Dict[str, int] = {}
    for s in segments:
        mins = int((s.end - s.start).total_seconds() // 60)
        busy[s.machine_id] = busy.get(s.machine_id, 0) + mins
    return busy


def _week_start_monday(dt: datetime) -> datetime:
    # Monday 00:00
    d0 = datetime(dt.year, dt.month, dt.day, 0, 0, 0)
    return d0 - timedelta(days=d0.weekday())


def weekly_capacity_minutes(cal: CalendarConfig) -> int:
    # Your rule: MON-SAT work, SUN off, 2 shifts, each shift net = (shift_len - break)
    # We already simplified breaks as "break at shift start", but for capacity summary we just use net.
    workdays = set(cal.workdays)
    days_per_week = len(workdays)  # MON..SAT -> 6
    net_per_day = 0
    for sh in cal.shifts:
        # compute minutes in shift
        start_h, start_m = map(int, sh.start.split(":"))
        end_h, end_m = map(int, sh.end.split(":"))
        start = start_h * 60 + start_m
        end = (24 * 60) if (end_h == 0 and end_m == 0) else (end_h * 60 + end_m)
        total = max(0, end - start)
        net = max(0, total - sh.break_min)
        net_per_day += net
    return days_per_week * net_per_day


def machine_part_op_load_breakdown(
    segments: List[GanttSegment],
    cal: CalendarConfig,
    machine_cap: Dict[str, int],
) -> Dict[str, dict]:
    """
    Aggregate load per machine and by part/op from segments.
    Load minutes are derived from segment durations.
    """
    base_avail = weekly_capacity_minutes(cal)
    machine_load: Dict[str, int] = {mid: 0 for mid in machine_cap}
    by_part_op: Dict[Tuple[str, str, int], int] = {}

    for s in segments:
        mins = int((s.end - s.start).total_seconds() // 60)
        machine_load[s.machine_id] = machine_load.get(s.machine_id, 0) + mins
        key = (s.machine_id, s.part_id, s.op_no)
        by_part_op[key] = by_part_op.get(key, 0) + mins

    breakdown: Dict[str, dict] = {}
    for mid, loaded in sorted(machine_load.items(), key=lambda x: x[0]):
        loaded_min = loaded
        avail_mid = base_avail * machine_cap.get(mid, 1)
        rows = []
        for (m_id, part_id, op_no), load in by_part_op.items():
            if m_id != mid:
                continue
            share_pct = round((load / loaded_min) * 100, 1) if loaded_min > 0 else 0.0
            cap_pct = round((load / avail_mid) * 100, 1) if avail_mid > 0 else None
            rows.append({
                "part_id": part_id,
                "op_no": op_no,
                "load_min": load,
                "share_pct": share_pct,
                "cap_pct": cap_pct,
            })

        rows.sort(key=lambda r: r["load_min"], reverse=True)
        util_pct = round((loaded_min / avail_mid) * 100, 1) if avail_mid > 0 else None
        overload_min = max(0, loaded_min - avail_mid)
        breakdown[mid] = {
            "available_min": avail_mid,
            "loaded_min": loaded_min,
            "overload_min": overload_min,
            "utilization_pct": util_pct,
            "by_part_op": rows,
        }

    return breakdown


def weekly_load_minutes(segments: List[GanttSegment], cal: CalendarConfig, start_dt: Optional[datetime] = None) -> List[dict]:
    """
    Aggregate load per machine per week (week starts Monday).
    Note: This is a rough allocation based on segment start week. (MVP)
    """
    cap = weekly_capacity_minutes(cal)
    agg: Dict[Tuple[datetime, str], int] = {}

    for s in segments:
        wk = _week_start_monday(s.start)
        key = (wk, s.machine_id)
        mins = int((s.end - s.start).total_seconds() // 60)
        agg[key] = agg.get(key, 0) + mins

    only_week = _week_start_monday(start_dt) if start_dt else None

    rows: List[dict] = []
    for (wk, mid), load in sorted(agg.items(), key=lambda x: (x[0][0], x[0][1])):
        if only_week and wk != only_week:
            continue
        rows.append({
            "week_start": wk.strftime("%Y-%m-%d"),
            "machine_id": mid,
            "capacity_min": cap,
            "load_min": load,
            "overload_min": max(0, load - cap),
            "util_pct": round((load / cap) * 100, 1) if cap > 0 else None
        })
    return rows


def filter_first_weekly_rows(weekly_rows: List[dict]) -> List[dict]:
    if not weekly_rows:
        return []
    week_keys = [r.get("week_start") for r in weekly_rows if r.get("week_start")]
    if not week_keys:
        return list(weekly_rows)
    earliest_week = min(week_keys)
    return [r for r in weekly_rows if r.get("week_start") == earliest_week]


def make_offline_gantt_html(segments: List[GanttSegment], out_path: str) -> None:
    """
    Offline HTML Gantt with simple bars (no external libs).
    - One row per segment
    - Bar position/width based on time (relative to global min/max)
    """
    segs = sorted(segments, key=lambda s: (s.machine_id, s.start, s.lot_id, s.segment_index))
    if not segs:
        from pathlib import Path
        Path(out_path).write_text("<html><body><p>No segments</p></body></html>", encoding="utf-8")
        return

    t_min = min(s.start for s in segs)
    t_max = max(s.end for s in segs)
    total_min = max(1, int((t_max - t_min).total_seconds() // 60))

    # fixed colors per machine (simple, offline)
    machine_colors = {
        "M1": "#4e79a7",
        "M2": "#f28e2b",
        "M3": "#e15759",
        "M4": "#76b7b2",
        "M5": "#59a14f",
    }

    def minutes_from_start(dt: datetime) -> int:
        return int((dt - t_min).total_seconds() // 60)

    rows_html = []
    for s in segs:
        left = (minutes_from_start(s.start) / total_min) * 100.0
        width = (int((s.end - s.start).total_seconds() // 60) / total_min) * 100.0
        width = max(width, 0.2)  # min visible width
        color = machine_colors.get(s.machine_id, "#999999")

        label = f"{s.part_id}-{s.lot_id}-op{s.op_no}"
        rows_html.append(f"""
        <div class="row">
          <div class="cell machine">{s.machine_id}</div>
          <div class="cell label">{label}</div>
          <div class="cell barcell">
            <div class="bar" style="left:{left:.4f}%; width:{width:.4f}%; background:{color};"></div>
          </div>
          <div class="cell time">{s.start}</div>
          <div class="cell time">{s.end}</div>
        </div>
        """)

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>ProductionSim Gantt (Offline Bars)</title>
  <style>
    body {{ font-family: Arial, sans-serif; padding: 16px; }}
    .meta {{ font-size: 12px; color: #333; margin-bottom: 10px; }}
    .grid {{
      border: 1px solid #ddd;
      border-radius: 8px;
      overflow: hidden;
    }}
    .header, .row {{
      display: grid;
      grid-template-columns: 70px 260px 1fr 190px 190px;
      align-items: center;
    }}
    .header {{
      background: #f2f2f2;
      font-weight: bold;
      font-size: 12px;
      position: sticky;
      top: 0;
      z-index: 2;
    }}
    .cell {{
      padding: 6px 8px;
      border-bottom: 1px solid #eee;
      font-size: 12px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .barcell {{
      position: relative;
      height: 18px;
      background: #fafafa;
      border-left: 1px solid #eee;
      border-right: 1px solid #eee;
    }}
    .bar {{
      position: absolute;
      top: 2px;
      height: 14px;
      border-radius: 6px;
    }}
    .machine {{ font-weight: bold; }}
    .time {{ font-family: Consolas, monospace; font-size: 11px; color: #222; }}
  </style>
</head>
<body>
  <h2>ProductionSim - Offline Gantt (Bars)</h2>
  <div class="meta">
    Total segments: {len(segs)}<br/>
    Range: {t_min} → {t_max} (total {total_min} min)
  </div>

  <div class="grid">
    <div class="header">
      <div class="cell">Machine</div>
      <div class="cell">Job</div>
      <div class="cell">Timeline</div>
      <div class="cell">Start</div>
      <div class="cell">End</div>
    </div>
    {''.join(rows_html)}
  </div>
</body>
</html>"""

    from pathlib import Path
    Path(out_path).write_text(html, encoding="utf-8")


# ---------------- ONEPAGE DASHBOARD (offline) ----------------

def make_onepage_dashboard_html(
    segments: List[GanttSegment],
    busy_minutes: Dict[str, int],
    weekly_rows: List[dict],
    machine_breakdown: Dict[str, dict],
    parts: List[Part],
    out_path: str,
) -> None:
    """
    Offline onepage dashboard:
    - KPI cards
    - Busy minutes table + mini bars
    - Weekly load table (overload highlighted)
    - Manual capacity check table
    No external libs.
    """
    segs = sorted(segments, key=lambda s: (s.machine_id, s.start, s.lot_id, s.segment_index))
    last_end = segs[-1].end if segs else None

    # Busy bar scaling
    max_busy = max(busy_minutes.values()) if busy_minutes else 1

    busy_rows = []
    for mid in sorted(busy_minutes.keys()):
        val = busy_minutes[mid]
        w = (val / max_busy) * 100.0
        busy_rows.append(f"""
        <tr>
          <td class="mono">{mid}</td>
          <td class="mono">{val}</td>
          <td>
            <div class="barwrap"><div class="busybar" style="width:{w:.2f}%"></div></div>
          </td>
        </tr>
        """)

    weekly_rows_html = []
    for r in weekly_rows:
        overload = r.get("overload_min", 0)
        cls = "over" if overload and overload > 0 else ""
        weekly_rows_html.append(f"""
        <tr class="{cls}">
          <td class="mono">{r['week_start']}</td>
          <td class="mono">{r['machine_id']}</td>
          <td class="mono">{r['capacity_min']}</td>
          <td class="mono">{r['load_min']}</td>
          <td class="mono">{r['overload_min']}</td>
          <td class="mono">{r['util_pct']}</td>
        </tr>
        """)

    breakdown_cards = []
    for mid, info in machine_breakdown.items():
        rows = info.get("by_part_op", [])
        part_rows = []
        for r in rows:
            part_rows.append(f"""
            <tr>
              <td class="mono">{r['part_id']}</td>
              <td class="mono">{r['op_no']}</td>
              <td class="mono">{r['load_min']}</td>
              <td class="mono">{r['share_pct']}</td>
              <td class="mono">{r['cap_pct']}</td>
            </tr>
            """)

        breakdown_cards.append(f"""
        <div class="card">
          <h3 style="margin:0 0 8px 0;">Load Breakdown - {mid}</h3>
          <div class="small">
            Avail: {info['available_min']} min | Load: {info['loaded_min']} min | Over: {info['overload_min']} min | Util: {info['utilization_pct']}%
          </div>
          <div style="max-height: 260px; overflow:auto; border:1px solid #eee; border-radius: 10px; margin-top:8px;">
            <table>
              <thead><tr><th>Part</th><th>Op</th><th>Load</th><th>Share%</th><th>Cap%</th></tr></thead>
              <tbody>
                {''.join(part_rows)}
              </tbody>
            </table>
          </div>
        </div>
        """)

    if segs:
        t_min = min(s.start for s in segs)
        t_max = max(s.end for s in segs)
    else:
        t_min, t_max = None, None

    max_ops = max((len(p.route) for p in parts), default=0)
    op_headers = "".join(f"<th>Op{i}_Machine</th>" for i in range(1, max_ops + 1))

    routing_rows = []
    for part in sorted(parts, key=lambda p: p.part_id):
        route_ops = sorted(part.route, key=lambda op: op.op_no)
        op_ids = [op.machine_id for op in route_ops]
        op_ids += [""] * (max_ops - len(op_ids))
        op_cells = "".join(f"<td class=\"mono\">{mid}</td>" for mid in op_ids)
        routing_rows.append(f"""
        <tr>
          <td class="mono">{part.part_id}</td>
          <td class="mono">{part.weekly_target_qty}</td>
          <td class="mono">{len(route_ops)}</td>
          {op_cells}
        </tr>
        """)
    if not routing_rows:
        routing_rows = ["<tr><td colspan=\"3\" class=\"mono\">No parts</td></tr>"]

    detail_rows = []
    for part in sorted(parts, key=lambda p: p.part_id):
        weekly_qty = part.weekly_target_qty
        for op in sorted(part.route, key=lambda op: op.op_no):
            derived_min = op.cycle_min_per_unit * weekly_qty
            derived_hours = derived_min / 60.0
            detail_rows.append(f"""
            <tr>
              <td class="mono">{part.part_id}</td>
              <td class="mono">{weekly_qty}</td>
              <td class="mono">{op.op_no}</td>
              <td class="mono">{op.machine_id}</td>
              <td class="mono">{op.cycle_min_per_unit:.2f}</td>
              <td class="mono">{derived_min:.2f}</td>
              <td class="mono">{derived_hours:.2f}</td>
            </tr>
            """)
    if not detail_rows:
        detail_rows = ["<tr><td colspan=\"7\" class=\"mono\">No parts</td></tr>"]

    bottleneck = max(busy_minutes, key=busy_minutes.get) if busy_minutes else "-"

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>ProductionSim - Onepage Dashboard</title>
  <style>
    body {{ font-family: Arial, sans-serif; padding: 16px; background:#fff; color:#111; }}
    h2 {{ margin: 0 0 8px 0; }}
    .mono {{ font-family: Consolas, monospace; font-size: 12px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .card {{ border:1px solid #e5e5e5; border-radius: 10px; padding: 12px; box-shadow: 0 1px 2px rgba(0,0,0,0.03); }}
    .kpis {{ display:flex; gap:10px; flex-wrap: wrap; }}
    .kpi {{ border:1px solid #eee; border-radius: 10px; padding: 10px 12px; min-width: 200px; }}
    .kpi .v {{ font-size: 18px; font-weight: 700; margin-top: 4px; }}
    table {{ border-collapse: collapse; width:100%; }}
    th, td {{ border-bottom:1px solid #eee; padding: 6px 8px; font-size: 12px; text-align:left; }}
    th {{ background:#fafafa; position: sticky; top: 0; }}
    .barwrap {{ background:#f6f6f6; height: 12px; border-radius: 8px; overflow:hidden; }}
    .busybar {{ height: 12px; background:#333; }}
    .over td {{ background: #fff2f2; }}
    .small {{ font-size: 12px; color:#444; }}

  </style>
</head>
<body>

  <h2>ProductionSim — Onepage Dashboard (Offline)</h2>
  <div class="small">
    Range: {t_min} → {t_max} | Segments: {len(segs)} | Last end: {last_end}
  </div>

  <div class="kpis" style="margin-top:10px;">
    <div class="kpi"><div class="small">Segments</div><div class="v">{len(segs)}</div></div>
    <div class="kpi"><div class="small">Last end</div><div class="v mono">{last_end}</div></div>
    <div class="kpi"><div class="small">Bottleneck (max busy)</div><div class="v mono">{bottleneck}</div></div>
  </div>

  <div class="grid" style="margin-top:12px;">
    <div class="card">
      <h3 style="margin:0 0 8px 0;">Busy Minutes (per machine)</h3>
      <table>
        <thead><tr><th>Machine</th><th>Busy (min)</th><th>Bar</th></tr></thead>
        <tbody>
          {''.join(busy_rows)}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3 style="margin:0 0 8px 0;">Weekly Load</h3>
      <div class="small">Overload rows are highlighted.</div>
      <div style="max-height: 360px; overflow:auto; border:1px solid #eee; border-radius: 10px; margin-top:8px;">
        <table>
          <thead><tr>
            <th>Week</th><th>Machine</th><th>Cap</th><th>Load</th><th>Over</th><th>Util%</th>
          </tr></thead>
          <tbody>
            {''.join(weekly_rows_html)}
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="grid" style="margin-top:12px;">
    {''.join(breakdown_cards)}
  </div>

  <div class="card" style="margin-top:12px;">
    <h3 style="margin:0 0 8px 0;">Manual Verification – Routing & Inputs</h3>
    <div class="small">Exact scenario inputs used for capacity/load math.</div>
    <div class="small" style="margin-top:8px;">Table 1 — Part Routing (Operation Order)</div>
    <div style="max-height: 320px; overflow:auto; border:1px solid #eee; border-radius: 10px; margin-top:6px;">
      <table>
        <thead><tr>
          <th>Part</th><th>Weekly_Target_Qty</th><th>Op_Count</th>{op_headers}
        </tr></thead>
        <tbody>
          {''.join(routing_rows)}
        </tbody>
      </table>
    </div>
    <div class="small" style="margin-top:12px;">Table 2 — All Part-Operation Inputs (Full Detail)</div>
    <div style="max-height: 420px; overflow:auto; border:1px solid #eee; border-radius: 10px; margin-top:6px;">
      <table>
        <thead><tr>
          <th>Part</th><th>Weekly_Target_Qty</th><th>Op_No</th><th>Machine</th>
          <th>Cycle_Min_Per_Unit</th><th>Derived_Load_Min</th><th>Derived_Load_Hours</th>
        </tr></thead>
        <tbody>
          {''.join(detail_rows)}
        </tbody>
      </table>
    </div>
  </div>

</body>
</html>
"""
    from pathlib import Path
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
