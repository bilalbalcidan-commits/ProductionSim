from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import List

from app.core.models import Part, LottingConfig


@dataclass(frozen=True)
class Lot:
    part_id: str
    lot_id: str
    units: int


def compute_lot_qty(part: Part, lot_cfg: LottingConfig) -> int:
    """
    Cycle-based auto lotting:
    lot_qty_auto = floor(target_lot_time / CT_max)
    lot_qty = max(lot_qty_auto, min_visual_lot_qty, 1)
    """
    if lot_cfg.mode != "auto_cycle_based":
        raise ValueError(f"Unsupported lotting mode: {lot_cfg.mode}")

    ct_max = max(op.cycle_min_per_unit for op in part.route)
    lot_qty_auto = floor(lot_cfg.target_lot_time_min / ct_max) if ct_max > 0 else 1
    lot_qty = max(lot_qty_auto, lot_cfg.min_visual_lot_qty, 1)
    return int(lot_qty)


def make_lots(part: Part, lot_cfg: LottingConfig) -> List[Lot]:
    lot_qty = compute_lot_qty(part, lot_cfg)
    total = part.weekly_target_qty

    lots: List[Lot] = []
    i = 1
    remaining = total
    while remaining > 0:
        u = lot_qty if remaining >= lot_qty else remaining
        lots.append(Lot(part_id=part.part_id, lot_id=f"{part.part_id}-L{i:03d}", units=u))
        remaining -= u
        i += 1

    return lots
