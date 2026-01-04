from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import create_access_token, ensure_factory_access, get_current_user, require_roles, verify_password
from app.db import models
from app.db.session import Base, engine, get_db
from app.services.audit_service import log_audit
from app.services.import_service import import_cycletime, import_planning, preview_table
from app.services.upload_store import load_upload, save_upload
from app.services.simulation_service import run_simulation_and_store


app = FastAPI(title="ProductionSim", version="0.2")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


def _accessible_factories(db: Session, user: models.User) -> List[models.Factory]:
    if user.role in (models.Role.ADMIN, models.Role.EXEC):
        return db.query(models.Factory).order_by(models.Factory.name).all()
    return list(user.factories)


def _render(request: Request, template: str, user: models.User | None = None, **context: Any) -> HTMLResponse:
    context.update({"request": request, "user": user})
    return templates.TemplateResponse(template, context)


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: models.User = Depends(get_current_user)) -> HTMLResponse:
    if user.role == models.Role.PLAN:
        return RedirectResponse(url="/plan/parts", status_code=302)
    if user.role == models.Role.MFG:
        return RedirectResponse(url="/mfg/parts", status_code=302)
    if user.role == models.Role.PROD_MGR:
        return RedirectResponse(url="/prod/dashboard", status_code=302)
    if user.role == models.Role.EXEC:
        return RedirectResponse(url="/exec/dashboard", status_code=302)
    return RedirectResponse(url="/admin/users", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = db.query(models.User).filter_by(email=email).first()
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})

    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@app.get("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token")
    return response


# -------- Planning Engineer --------

@app.get("/plan/parts", response_class=HTMLResponse)
def plan_parts(
    request: Request,
    factory_id: Optional[int] = None,
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    factories = _accessible_factories(db, user)
    selected = factory_id or (factories[0].id if factories else None)
    if selected and user.role != models.Role.ADMIN:
        ensure_factory_access(user, selected)
    parts = db.query(models.Part).filter_by(factory_id=selected).order_by(models.Part.part_code).all() if selected else []
    return _render(
        request,
        "plan_parts.html",
        user=user,
        parts=parts,
        factories=factories,
        selected_factory=selected,
    )


@app.post("/plan/parts")
def plan_create_part(
    part_code: str = Form(...),
    name: str = Form(...),
    weekly_target_qty: int = Form(0),
    factory_id: int = Form(...),
    line: str = Form(""),
    product_family: str = Form(""),
    release_datetime: str = Form(""),
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, factory_id)
    release_dt = datetime.fromisoformat(release_datetime) if release_datetime else None
    part = models.Part(
        part_code=part_code,
        name=name,
        weekly_target_qty=weekly_target_qty,
        factory_id=factory_id,
        line=line or None,
        product_family=product_family or None,
        release_datetime=release_dt,
    )
    db.add(part)
    db.commit()
    log_audit(db, user.id, "create", "part", part.id, f"{part_code}")
    db.commit()
    return RedirectResponse(url=f"/plan/parts?factory_id={factory_id}", status_code=302)


@app.get("/plan/parts/{part_id}/edit", response_class=HTMLResponse)
def plan_edit_part(
    request: Request,
    part_id: int,
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    part = db.get(models.Part, part_id)
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, part.factory_id)
    return _render(request, "plan_part_edit.html", user=user, part=part)


@app.post("/plan/parts/{part_id}/edit")
def plan_update_part(
    part_id: int,
    name: str = Form(...),
    weekly_target_qty: int = Form(0),
    line: str = Form(""),
    product_family: str = Form(""),
    release_datetime: str = Form(""),
    active: bool = Form(False),
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    part = db.get(models.Part, part_id)
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, part.factory_id)
    part.name = name
    part.weekly_target_qty = weekly_target_qty
    part.line = line or None
    part.product_family = product_family or None
    part.release_datetime = datetime.fromisoformat(release_datetime) if release_datetime else None
    part.active = active
    db.commit()
    log_audit(db, user.id, "update", "part", part.id, f"{part.part_code}")
    db.commit()
    return RedirectResponse(url=f"/plan/parts?factory_id={part.factory_id}", status_code=302)


@app.get("/plan/parts/{part_id}/route", response_class=HTMLResponse)
def plan_route(
    request: Request,
    part_id: int,
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    part = db.get(models.Part, part_id)
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, part.factory_id)
    routes = db.query(models.RouteStep).filter_by(part_id=part_id).order_by(models.RouteStep.op_no).all()
    return _render(request, "plan_route.html", user=user, part=part, routes=routes)


@app.post("/plan/parts/{part_id}/route")
def plan_add_route(
    part_id: int,
    op_no: int = Form(...),
    machine_id: str = Form(...),
    op_name: str = Form(""),
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    part = db.get(models.Part, part_id)
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, part.factory_id)
    route = db.query(models.RouteStep).filter_by(part_id=part_id, op_no=op_no).first()
    if route:
        route.machine_id = machine_id
        route.op_name = op_name or None
    else:
        db.add(models.RouteStep(part_id=part_id, op_no=op_no, machine_id=machine_id, op_name=op_name or None))
    db.commit()
    log_audit(db, user.id, "upsert", "route", part_id, f"op {op_no} -> {machine_id}")
    db.commit()
    return RedirectResponse(url=f"/plan/parts/{part_id}/route", status_code=302)


@app.post("/plan/route/{route_id}/delete")
def plan_delete_route(
    route_id: int,
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    route = db.get(models.RouteStep, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    part = db.get(models.Part, route.part_id)
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, part.factory_id)
    db.delete(route)
    db.commit()
    log_audit(db, user.id, "delete", "route", route_id, f"part {part.part_code}")
    db.commit()
    return RedirectResponse(url=f"/plan/parts/{part.id}/route", status_code=302)


@app.get("/plan/import", response_class=HTMLResponse)
def plan_import_page(
    request: Request,
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    factories = _accessible_factories(db, user)
    return _render(request, "plan_import.html", user=user, factories=factories)


@app.post("/plan/import/preview", response_class=HTMLResponse)
def plan_import_preview(
    request: Request,
    factory_id: int = Form(...),
    file: UploadFile = File(...),
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, factory_id)
    content = file.file.read()
    token, _path = save_upload(content, file.filename or "")
    preview = preview_table(content, file.filename or "")
    return _render(
        request,
        "plan_import_preview.html",
        user=user,
        factory_id=factory_id,
        token=token,
        preview=preview,
    )


@app.post("/plan/import/confirm", response_class=HTMLResponse)
def plan_import_confirm(
    request: Request,
    factory_id: int = Form(...),
    token: str = Form(...),
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, factory_id)
    path = load_upload(token)
    if not path:
        raise HTTPException(status_code=404, detail="Upload not found")
    content = path.read_bytes()
    result = import_planning(db, content, path.name, factory_id, user.id, user.role)
    return _render(
        request,
        "plan_import_preview.html",
        user=user,
        factory_id=factory_id,
        token="",
        preview=None,
        result=result,
    )


@app.post("/api/import/planning")
def api_import_planning(
    factory_id: int = Form(...),
    file: UploadFile = File(...),
    user: models.User = Depends(require_roles([models.Role.PLAN, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, factory_id)
    content = file.file.read()
    return import_planning(db, content, file.filename or "", factory_id, user.id, user.role)


# -------- Manufacturing Engineer --------

@app.get("/mfg/parts", response_class=HTMLResponse)
def mfg_parts(
    request: Request,
    factory_id: Optional[int] = None,
    user: models.User = Depends(require_roles([models.Role.MFG, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    factories = _accessible_factories(db, user)
    selected = factory_id or (factories[0].id if factories else None)
    if selected and user.role != models.Role.ADMIN:
        ensure_factory_access(user, selected)
    parts = db.query(models.Part).filter_by(factory_id=selected).order_by(models.Part.part_code).all() if selected else []
    return _render(
        request,
        "mfg_parts.html",
        user=user,
        parts=parts,
        factories=factories,
        selected_factory=selected,
    )


@app.get("/mfg/parts/{part_id}/cycle", response_class=HTMLResponse)
def mfg_cycle_times(
    request: Request,
    part_id: int,
    user: models.User = Depends(require_roles([models.Role.MFG, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    part = db.get(models.Part, part_id)
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, part.factory_id)
    routes = db.query(models.RouteStep).filter_by(part_id=part_id).order_by(models.RouteStep.op_no).all()
    cycle_times = {ct.op_no: ct for ct in db.query(models.CycleTime).filter_by(part_id=part_id).all()}
    return _render(
        request,
        "mfg_cycle_times.html",
        user=user,
        part=part,
        routes=routes,
        cycle_times=cycle_times,
    )


@app.post("/mfg/parts/{part_id}/cycle")
def mfg_set_cycle(
    part_id: int,
    op_no: int = Form(...),
    cycle_min_per_unit: int = Form(...),
    user: models.User = Depends(require_roles([models.Role.MFG, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    part = db.get(models.Part, part_id)
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, part.factory_id)
    route = db.query(models.RouteStep).filter_by(part_id=part_id, op_no=op_no).first()
    if not route:
        raise HTTPException(status_code=400, detail="Route step missing")
    ct = db.query(models.CycleTime).filter_by(part_id=part_id, op_no=op_no).first()
    if ct:
        ct.cycle_min_per_unit = cycle_min_per_unit
        ct.machine_id = route.machine_id
    else:
        db.add(
            models.CycleTime(
                part_id=part_id,
                op_no=op_no,
                machine_id=route.machine_id,
                cycle_min_per_unit=cycle_min_per_unit,
            )
        )
    db.commit()
    log_audit(db, user.id, "upsert", "cycle_time", part_id, f"op {op_no} -> {cycle_min_per_unit}")
    db.commit()
    return RedirectResponse(url=f"/mfg/parts/{part_id}/cycle", status_code=302)


@app.get("/mfg/import", response_class=HTMLResponse)
def mfg_import_page(
    request: Request,
    user: models.User = Depends(require_roles([models.Role.MFG, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    factories = _accessible_factories(db, user)
    return _render(request, "mfg_import.html", user=user, factories=factories)


@app.post("/mfg/import/preview", response_class=HTMLResponse)
def mfg_import_preview(
    request: Request,
    file: UploadFile = File(...),
    user: models.User = Depends(require_roles([models.Role.MFG, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    content = file.file.read()
    token, _path = save_upload(content, file.filename or "")
    preview = preview_table(content, file.filename or "")
    return _render(
        request,
        "mfg_import_preview.html",
        user=user,
        token=token,
        preview=preview,
    )


@app.post("/mfg/import/confirm", response_class=HTMLResponse)
def mfg_import_confirm(
    request: Request,
    token: str = Form(...),
    user: models.User = Depends(require_roles([models.Role.MFG, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    path = load_upload(token)
    if not path:
        raise HTTPException(status_code=404, detail="Upload not found")
    content = path.read_bytes()
    result = import_cycletime(db, content, path.name, user.id, user.role)
    return _render(
        request,
        "mfg_import_preview.html",
        user=user,
        token="",
        preview=None,
        result=result,
    )


@app.post("/api/import/cycletime")
def api_import_cycletime(
    file: UploadFile = File(...),
    user: models.User = Depends(require_roles([models.Role.MFG, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    content = file.file.read()
    return import_cycletime(db, content, file.filename or "", user.id, user.role)


# -------- Production Manager --------

@app.get("/prod/dashboard", response_class=HTMLResponse)
def prod_dashboard(
    request: Request,
    factory_id: Optional[int] = None,
    user: models.User = Depends(require_roles([models.Role.PROD_MGR, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    factories = _accessible_factories(db, user)
    selected = factory_id or (factories[0].id if factories else None)
    if selected and user.role != models.Role.ADMIN:
        ensure_factory_access(user, selected)
    runs = (
        db.query(models.SimulationRun)
        .filter_by(factory_id=selected)
        .order_by(models.SimulationRun.created_at.desc())
        .limit(10)
        .all()
        if selected
        else []
    )
    return _render(
        request,
        "prod_dashboard.html",
        user=user,
        factories=factories,
        selected_factory=selected,
        runs=runs,
    )


@app.post("/api/run")
def api_run_simulation(
    factory_id: Optional[int] = Form(None),
    user: models.User = Depends(require_roles([models.Role.PROD_MGR, models.Role.EXEC, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if factory_id and user.role not in (models.Role.EXEC, models.Role.ADMIN):
        ensure_factory_access(user, factory_id)

    run = models.SimulationRun(factory_id=factory_id, created_by=user.id, status="running", input_payload_json={})
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        run_simulation_and_store(db, run, factory_id)
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"run_id": run.id, "status": run.status, "dashboard_html_path": run.dashboard_html_path}


@app.get("/reports/{run_id}/dashboard")
def report_dashboard(
    run_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    run = db.get(models.SimulationRun, run_id)
    if not run or not run.dashboard_html_path:
        raise HTTPException(status_code=404, detail="Report not found")
    if run.factory_id and user.role not in (models.Role.EXEC, models.Role.ADMIN):
        ensure_factory_access(user, run.factory_id)
    return FileResponse(run.dashboard_html_path, media_type="text/html")


@app.post("/prod/notes")
def add_note(
    run_id: int = Form(...),
    factory_id: int = Form(...),
    machine_id: str = Form(...),
    note: str = Form(...),
    user: models.User = Depends(require_roles([models.Role.PROD_MGR, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if user.role != models.Role.ADMIN:
        ensure_factory_access(user, factory_id)
    db.add(
        models.Note(
            factory_id=factory_id,
            run_id=run_id,
            machine_id=machine_id,
            note=note,
            created_by=user.id,
        )
    )
    db.commit()
    log_audit(db, user.id, "create", "note", run_id, f"{machine_id}")
    db.commit()
    return RedirectResponse(url=f"/prod/dashboard?factory_id={factory_id}", status_code=302)


# -------- Executive --------

@app.get("/exec/dashboard", response_class=HTMLResponse)
def exec_dashboard(
    request: Request,
    factory_id: str = "all",
    user: models.User = Depends(require_roles([models.Role.EXEC, models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    factories = db.query(models.Factory).order_by(models.Factory.name).all()
    runs = []
    if factory_id == "all":
        runs = (
            db.query(models.SimulationRun)
            .filter(models.SimulationRun.factory_id.is_(None))
            .order_by(models.SimulationRun.created_at.desc())
            .limit(10)
            .all()
        )
    else:
        runs = (
            db.query(models.SimulationRun)
            .filter_by(factory_id=int(factory_id))
            .order_by(models.SimulationRun.created_at.desc())
            .limit(10)
            .all()
        )
    return _render(
        request,
        "exec_dashboard.html",
        user=user,
        factories=factories,
        selected_factory=factory_id,
        runs=runs,
    )


# -------- Admin --------

@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    user: models.User = Depends(require_roles([models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    users = db.query(models.User).order_by(models.User.email).all()
    factories = db.query(models.Factory).order_by(models.Factory.name).all()
    return _render(
        request,
        "admin_users.html",
        user=user,
        users=users,
        factories=factories,
        roles=list(models.Role),
    )


@app.post("/admin/users")
def admin_create_user(
    email: str = Form(...),
    password: str = Form(...),
    role: models.Role = Form(...),
    factory_ids: List[int] = Form([]),
    user: models.User = Depends(require_roles([models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    from app.auth.security import hash_password

    new_user = models.User(email=email, hashed_password=hash_password(password), role=role, is_active=True)
    if factory_ids:
        new_user.factories = db.query(models.Factory).filter(models.Factory.id.in_(factory_ids)).all()
    db.add(new_user)
    db.commit()
    log_audit(db, user.id, "create", "user", new_user.id, email)
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=302)


@app.get("/admin/factories", response_class=HTMLResponse)
def admin_factories(
    request: Request,
    user: models.User = Depends(require_roles([models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    factories = db.query(models.Factory).order_by(models.Factory.name).all()
    return _render(request, "admin_factories.html", user=user, factories=factories)


@app.post("/admin/factories")
def admin_create_factory(
    name: str = Form(...),
    timezone: str = Form("Europe/Istanbul"),
    user: models.User = Depends(require_roles([models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    factory = models.Factory(name=name, timezone=timezone)
    db.add(factory)
    db.commit()
    log_audit(db, user.id, "create", "factory", factory.id, name)
    db.commit()
    return RedirectResponse(url="/admin/factories", status_code=302)


@app.get("/admin/audit", response_class=HTMLResponse)
def admin_audit(
    request: Request,
    user: models.User = Depends(require_roles([models.Role.ADMIN])),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    logs = db.query(models.AuditLog).order_by(models.AuditLog.created_at.desc()).limit(200).all()
    return _render(request, "admin_audit.html", user=user, logs=logs)
