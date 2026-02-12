from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict


@dataclass(slots=True)
class ApprovedAnswer:
    ts: str
    kind: str  # review | question | chat
    ozon_entity_id: str
    input_text: str
    answer_text: str
    product_id: str | None = None
    product_name: str | None = None
    rating: int | None = None
    meta: Dict[str, Any] = field(default_factory=dict)
    hash: str | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "kind": self.kind,
            "ozon_entity_id": self.ozon_entity_id,
            "input_text": self.input_text,
            "answer_text": self.answer_text,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "rating": self.rating,
            "meta": self.meta or {},
            "hash": self.hash,
        }

    @classmethod
    def now_iso(
        cls,
        *,
        kind: str,
        ozon_entity_id: str,
        input_text: str,
        answer_text: str,
        product_id: str | None = None,
        product_name: str | None = None,
        rating: int | None = None,
        meta: Dict[str, Any] | None = None,
    ) -> "ApprovedAnswer":
        return cls(
            ts=datetime.now().astimezone().isoformat(),
            kind=kind,
            ozon_entity_id=ozon_entity_id,
            input_text=input_text,
            answer_text=answer_text,
            product_id=product_id,
            product_name=product_name,
            rating=rating,
            meta=meta or {},
        )


__all__ = ["ApprovedAnswer"]
