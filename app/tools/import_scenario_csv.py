import csv
import json
from pathlib import Path

CSV_PATH = Path("app/data/scenario_big.csv")
OUT_PATH = Path("app/data/scenario_big_payload.json")

DEFAULT_START_DT = "2026-01-06T08:00:00"
DEFAULT_TIMEZONE = "Europe/Istanbul"

def _machine_ids_from_header(fieldnames):
    return [f[3:].upper() for f in fieldnames if f.startswith("ct_")]

def main():
    parts = []

    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        machine_ids = _machine_ids_from_header(reader.fieldnames or [])

        for row in reader:
            route = []
            for i in range(1, 6):
                m = row.get(f"op{i}")
                if not m:
                    continue
                ct = float(row[f"ct_{m.lower()}"])
                route.append({
                    "op_no": len(route) + 1,
                    "machine_id": m,
                    "cycle_min_per_unit": ct
                })

            parts.append({
                "part_id": row["part"],
                "name": row["part"],
                "weekly_target_qty": int(row["weekly_qty"]),
                "release_datetime": DEFAULT_START_DT,
                "route": route
            })

    payload = {
        "machines": [
            {"machine_id": m, "name": m, "capacity": 1}
            for m in machine_ids
        ],
        "parts": parts,
        "simulation": {
            "start_datetime": DEFAULT_START_DT,
            "timezone": DEFAULT_TIMEZONE,
            "calendar": {
                "workdays": [0, 1, 2, 3, 4],
                "offdays": [5, 6],
                "shifts": [{"start": "08:30", "end": "16:00", "break_min": 0}]
            },
            "lotting": {
                "mode": "fixed",
                "target_lot_time_min": 120,
                "min_visual_lot_qty": 10
            },
            "dispatch": {
                "strategy": "FIFO",
                "variant": "B1",
                "bottleneck_machine_id": ""
            }
        }
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("OK ->", OUT_PATH)

if __name__ == "__main__":
    main()
