from __future__ import annotations

from datetime import datetime

from app.auth.security import hash_password
from app.db.models import CycleTime, Factory, Part, Role, RouteStep, User
from app.db.session import Base, SessionLocal, engine


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).first():
            print("Seed skipped: users already exist.")
            return

        factory = Factory(name="Factory A", timezone="Europe/Istanbul")
        db.add(factory)
        db.flush()

        users = [
            User(email="admin@demo.local", hashed_password=hash_password("admin123"), role=Role.ADMIN),
            User(email="plan@demo.local", hashed_password=hash_password("plan123"), role=Role.PLAN),
            User(email="mfg@demo.local", hashed_password=hash_password("mfg123"), role=Role.MFG),
            User(email="prod@demo.local", hashed_password=hash_password("prod123"), role=Role.PROD_MGR),
            User(email="exec@demo.local", hashed_password=hash_password("exec123"), role=Role.EXEC),
        ]
        for user in users:
            user.factories.append(factory)
            db.add(user)

        part = Part(
            part_code="P1",
            name="Demo Part",
            factory_id=factory.id,
            weekly_target_qty=500,
            release_datetime=datetime.utcnow(),
        )
        db.add(part)
        db.flush()
        db.add(RouteStep(part_id=part.id, op_no=1, machine_id="M1"))
        db.add(RouteStep(part_id=part.id, op_no=2, machine_id="M2"))
        db.add(CycleTime(part_id=part.id, op_no=1, machine_id="M1", cycle_min_per_unit=5))
        db.add(CycleTime(part_id=part.id, op_no=2, machine_id="M2", cycle_min_per_unit=7))

        db.commit()
        print("Seed completed.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
