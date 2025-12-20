from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict


@dataclass(slots=True)
class MemoryRecord:
    ts: str
    kind: str  # review | question | chat
    entity_id: str
    input_text: str
    output_text: str
    sku: str | None = None
    product_title: str | None = None
    meta: Dict[str, Any] = field(default_factory=dict)
    source: str = "sent_to_ozon"
    hash: str | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "kind": self.kind,
            "entity_id": self.entity_id,
            "input_text": self.input_text,
            "output_text": self.output_text,
            "sku": self.sku,
            "product_title": self.product_title,
            "meta": self.meta or {},
            "source": self.source,
            "hash": self.hash,
        }

    @classmethod
    def now_iso(
        cls,
        *,
        kind: str,
        entity_id: str,
        input_text: str,
        output_text: str,
        sku: str | None = None,
        product_title: str | None = None,
        meta: Dict[str, Any] | None = None,
        source: str = "sent_to_ozon",
    ) -> "MemoryRecord":
        return cls(
            ts=datetime.now().astimezone().isoformat(),
            kind=kind,
            entity_id=entity_id,
            input_text=input_text,
            output_text=output_text,
            sku=sku,
            product_title=product_title,
            meta=meta or {},
            source=source,
        )


__all__ = ["MemoryRecord"]
