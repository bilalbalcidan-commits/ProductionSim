from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class QueueItem:
    part_id: str
    lot_id: str
    ready_time: datetime  # this lot can start on this machine at/after this time


def choose_next(
    machine_id: str,
    queue: List[QueueItem],
    strategy: str,
    bottleneck_machine_id: Optional[str] = None,
    variant: Optional[str] = None,
) -> QueueItem:
    if not queue:
        raise ValueError("Queue is empty")

    # Default FIFO (stable)
    if strategy == "fifo":
        return min(queue, key=lambda x: (x.ready_time, x.lot_id))

    if strategy == "bottleneck_first":
        if bottleneck_machine_id is None:
            raise ValueError("bottleneck_machine_id is required for bottleneck_first")

        # B1: earliest ready on bottleneck machine
        if machine_id == bottleneck_machine_id and variant == "earliest_to_bottleneck":
            return min(queue, key=lambda x: (x.ready_time, x.lot_id))

        # For other machines (MVP): FIFO
        return min(queue, key=lambda x: (x.ready_time, x.lot_id))

    raise ValueError(f"Unsupported dispatch strategy: {strategy}")
