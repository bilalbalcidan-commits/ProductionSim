from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Role(str, Enum):
    PLAN = "PLAN"
    MFG = "MFG"
    PROD_MGR = "PROD_MGR"
    EXEC = "EXEC"
    ADMIN = "ADMIN"


class UserFactory(Base):
    __tablename__ = "user_factories"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    factory_id: Mapped[int] = mapped_column(ForeignKey("factories.id"), primary_key=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(SqlEnum(Role), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    factories = relationship(
        "Factory",
        secondary="user_factories",
        back_populates="users",
        lazy="selectin",
    )


class Factory(Base):
    __tablename__ = "factories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Istanbul")

    users = relationship(
        "User",
        secondary="user_factories",
        back_populates="factories",
        lazy="selectin",
    )
    parts = relationship("Part", back_populates="factory")


class Part(Base):
    __tablename__ = "parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    part_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    factory_id: Mapped[int] = mapped_column(ForeignKey("factories.id"), nullable=False)
    weekly_target_qty: Mapped[int] = mapped_column(Integer, default=0)
    release_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    line: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    product_family: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    factory = relationship("Factory", back_populates="parts")
    routes = relationship("RouteStep", back_populates="part", cascade="all, delete-orphan")
    cycle_times = relationship("CycleTime", back_populates="part", cascade="all, delete-orphan")


class RouteStep(Base):
    __tablename__ = "routes"
    __table_args__ = (UniqueConstraint("part_id", "op_no", name="uq_route_part_op"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), nullable=False)
    op_no: Mapped[int] = mapped_column(Integer, nullable=False)
    machine_id: Mapped[str] = mapped_column(String(64), nullable=False)
    op_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    part = relationship("Part", back_populates="routes")


class CycleTime(Base):
    __tablename__ = "cycle_times"
    __table_args__ = (UniqueConstraint("part_id", "op_no", name="uq_ct_part_op"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), nullable=False)
    op_no: Mapped[int] = mapped_column(Integer, nullable=False)
    machine_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cycle_min_per_unit: Mapped[int] = mapped_column(Integer, nullable=False)

    part = relationship("Part", back_populates="cycle_times")


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factory_id: Mapped[Optional[int]] = mapped_column(ForeignKey("factories.id"), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    input_payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_json_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dashboard_html_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    gantt_html_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factory_id: Mapped[int] = mapped_column(ForeignKey("factories.id"), nullable=False)
    run_id: Mapped[int] = mapped_column(ForeignKey("simulation_runs.id"), nullable=False)
    machine_id: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
