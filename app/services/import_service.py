from __future__ import annotations

import io
from typing import Any, Dict, List, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import CycleTime, Part, Role, RouteStep
from app.services.audit_service import log_audit


def _normalize_columns(columns: List[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for col in columns:
        normalized = col.strip().lower().replace(" ", "_")
        mapping[col] = normalized
    return mapping


def _read_table(filename: str, content: bytes) -> pd.DataFrame:
    buffer = io.BytesIO(content)
    if filename.lower().endswith(".csv"):
        return pd.read_csv(buffer)
    return pd.read_excel(buffer)


def preview_table(content: bytes, filename: str, max_rows: int = 20) -> Dict[str, Any]:
    df = _read_table(filename, content)
    df = df.fillna("")
    head = df.head(max_rows)
    return {"columns": list(head.columns), "rows": head.values.tolist(), "row_count": len(df)}


def import_planning(
    db: Session,
    content: bytes,
    filename: str,
    factory_id: int,
    user_id: int,
    user_role: Role,
) -> Dict[str, Any]:
    df = _read_table(filename, content)
    df = df.rename(columns=_normalize_columns(list(df.columns)))

    errors: List[dict] = []
    inserted = 0
    updated = 0

    if "part_id" in df.columns and "part_code" not in df.columns:
        df = df.rename(columns={"part_id": "part_code"})

    is_long = {"op_no", "machine_id"}.issubset(set(df.columns))
    is_wide = any(
        (col.startswith("op") and col[2:].isdigit())
        or (col.startswith("op_") and col[3:].isdigit())
        for col in df.columns
    )

    if not is_long and not is_wide:
        return {"inserted": 0, "updated": 0, "errors": [{"row": None, "error": "Missing op columns"}]}

    part_col = "part_code" if "part_code" in df.columns else "part"
    if part_col not in df.columns:
        return {"inserted": 0, "updated": 0, "errors": [{"row": None, "error": "Missing part_code"}]}

    qty_col = "weekly_target_qty" if "weekly_target_qty" in df.columns else "weekly_qty"
    if qty_col not in df.columns:
        qty_col = None

    if is_long:
        for idx, row in df.iterrows():
            part_code = str(row.get(part_col, "")).strip()
            op_no = row.get("op_no")
            machine_id = str(row.get("machine_id", "")).strip()
            weekly_qty = int(row.get(qty_col)) if qty_col and pd.notna(row.get(qty_col)) else 0

            if not part_code or not machine_id or pd.isna(op_no):
                errors.append({"row": int(idx), "error": "Missing part/op/machine"})
                continue
            try:
                op_no = int(op_no)
            except ValueError:
                errors.append({"row": int(idx), "error": "Invalid op_no"})
                continue

            part = db.query(Part).filter_by(part_code=part_code).first()
            if not part:
                if user_role in (Role.PLAN, Role.ADMIN):
                    part = Part(
                        part_code=part_code,
                        name=part_code,
                        factory_id=factory_id,
                        weekly_target_qty=weekly_qty,
                    )
                    db.add(part)
                    db.flush()
                    inserted += 1
                    log_audit(db, user_id, "create", "part", part.id, f"import {part_code}")
                else:
                    errors.append({"row": int(idx), "error": "Unknown part_code"})
                    continue
            else:
                if qty_col:
                    part.weekly_target_qty = weekly_qty
                updated += 1
                log_audit(db, user_id, "update", "part", part.id, f"import {part_code}")

            route = db.query(RouteStep).filter_by(part_id=part.id, op_no=op_no).first()
            if route:
                route.machine_id = machine_id
            else:
                db.add(RouteStep(part_id=part.id, op_no=op_no, machine_id=machine_id))
            log_audit(db, user_id, "upsert", "route", part.id, f"op {op_no} -> {machine_id}")

        db.commit()
        return {"inserted": inserted, "updated": updated, "errors": errors}

    # wide format: op1..opN
    op_cols = []
    for col in df.columns:
        if col.startswith("op") and col[2:].isdigit():
            op_cols.append(col)
        elif col.startswith("op_") and col[3:].isdigit():
            op_cols.append(col)
    op_cols = sorted(op_cols, key=lambda c: int(c.replace("op_", "op")[2:]))
    for idx, row in df.iterrows():
        part_code = str(row.get(part_col, "")).strip()
        weekly_qty = int(row.get(qty_col)) if qty_col and pd.notna(row.get(qty_col)) else 0
        if not part_code:
            errors.append({"row": int(idx), "error": "Missing part_code"})
            continue

        part = db.query(Part).filter_by(part_code=part_code).first()
        if not part:
            if user_role in (Role.PLAN, Role.ADMIN):
                part = Part(
                    part_code=part_code,
                    name=part_code,
                    factory_id=factory_id,
                    weekly_target_qty=weekly_qty,
                )
                db.add(part)
                db.flush()
                inserted += 1
                log_audit(db, user_id, "create", "part", part.id, f"import {part_code}")
            else:
                errors.append({"row": int(idx), "error": "Unknown part_code"})
                continue
        else:
            if qty_col:
                part.weekly_target_qty = weekly_qty
            updated += 1
            log_audit(db, user_id, "update", "part", part.id, f"import {part_code}")

        for col in op_cols:
            op_no = int(col.replace("op_", "op")[2:])
            machine_id = str(row.get(col, "")).strip()
            if not machine_id:
                continue
            route = db.query(RouteStep).filter_by(part_id=part.id, op_no=op_no).first()
            if route:
                route.machine_id = machine_id
            else:
                db.add(RouteStep(part_id=part.id, op_no=op_no, machine_id=machine_id))
            log_audit(db, user_id, "upsert", "route", part.id, f"op {op_no} -> {machine_id}")

    db.commit()
    return {"inserted": inserted, "updated": updated, "errors": errors}


def import_cycletime(
    db: Session,
    content: bytes,
    filename: str,
    user_id: int,
    user_role: Role,
) -> Dict[str, Any]:
    df = _read_table(filename, content)
    df = df.rename(columns=_normalize_columns(list(df.columns)))

    required = {"part_code", "op_no", "machine_id", "cycle_min_per_unit"}
    if not required.issubset(set(df.columns)):
        return {"inserted": 0, "updated": 0, "errors": [{"row": None, "error": "Missing required columns"}]}

    errors: List[dict] = []
    inserted = 0
    updated = 0

    for idx, row in df.iterrows():
        part_code = str(row.get("part_code", "")).strip()
        machine_id = str(row.get("machine_id", "")).strip()
        op_no = row.get("op_no")
        cycle = row.get("cycle_min_per_unit")

        if not part_code or not machine_id or pd.isna(op_no) or pd.isna(cycle):
            errors.append({"row": int(idx), "error": "Missing part/op/machine/cycle"})
            continue

        try:
            op_no = int(op_no)
        except ValueError:
            errors.append({"row": int(idx), "error": "Invalid op_no"})
            continue

        try:
            cycle_min = int(cycle)
        except ValueError:
            errors.append({"row": int(idx), "error": "Cycle time must be integer minutes"})
            continue

        part = db.query(Part).filter_by(part_code=part_code).first()
        if not part:
            errors.append({"row": int(idx), "error": "Unknown part_code"})
            continue

        route = db.query(RouteStep).filter_by(part_id=part.id, op_no=op_no).first()
        if not route:
            errors.append({"row": int(idx), "error": "Route step not found"})
            continue
        if route.machine_id != machine_id:
            errors.append({"row": int(idx), "error": "Machine mismatch vs routing"})
            continue

        ct = db.query(CycleTime).filter_by(part_id=part.id, op_no=op_no).first()
        if ct:
            ct.machine_id = machine_id
            ct.cycle_min_per_unit = cycle_min
            updated += 1
        else:
            db.add(CycleTime(part_id=part.id, op_no=op_no, machine_id=machine_id, cycle_min_per_unit=cycle_min))
            inserted += 1
        log_audit(db, user_id, "upsert", "cycle_time", part.id, f"op {op_no} -> {cycle_min}")

    db.commit()
    return {"inserted": inserted, "updated": updated, "errors": errors}
