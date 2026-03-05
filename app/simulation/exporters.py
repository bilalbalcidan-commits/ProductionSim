from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time

from .models import SimulationResult


def _serialize(value: object) -> object:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if is_dataclass(value):
        return {k: _serialize(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(v) for v in value]
    return value


def to_json(result: SimulationResult, path: str) -> None:
    payload = _serialize(result)
    if isinstance(payload, dict):
        payload["batch_count"] = result.batch_count
        payload["station_stats"] = _serialize(result.station_stats)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
