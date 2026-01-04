# ProductionSim Application

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app.seed
uvicorn app.api:app --reload
```

Open http://127.0.0.1:8000/login

## Seeded Users

- Admin: `admin@demo.local` / `admin123`
- Planning: `plan@demo.local` / `plan123`
- Manufacturing: `mfg@demo.local` / `mfg123`
- Production: `prod@demo.local` / `prod123`
- Executive: `exec@demo.local` / `exec123`

## Notes

- SQLite DB is stored at `app/db/app.db` (configurable via `DATABASE_URL`).
- Reports are stored under `app/reports/<run_id>/`.
- CLI pipeline still works: `python -m app.main`.
