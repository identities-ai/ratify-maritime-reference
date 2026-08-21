"""Closed work-order schema and canonical representation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from .profile import WORK_ORDER_SCOPE


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_:-]{0,127}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class WorkOrder:
    request_id: str
    scope: str
    resource: str
    category: str
    amount_minor: int
    currency: str
    description: str

    def validate(self) -> None:
        if type(self) is not WorkOrder:
            raise ValueError("action must use the closed WorkOrder schema")
        for name, value in (
            ("request_id", self.request_id),
            ("scope", self.scope),
            ("resource", self.resource),
            ("category", self.category),
        ):
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} is not canonical")
        if self.scope != WORK_ORDER_SCOPE:
            raise ValueError("scope is not the work-order scope")
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise ValueError("amount_minor must be an integer")
        if self.amount_minor < 0 or self.amount_minor > 100_000_000:
            raise ValueError("amount_minor is outside the accepted range")
        if not isinstance(self.currency, str) or not _CURRENCY.fullmatch(self.currency):
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(self.description, str) or not 1 <= len(self.description) <= 500:
            raise ValueError("description must contain 1..500 characters")

    def canonical_bytes(self) -> bytes:
        self.validate()
        return json.dumps(
            {
                "amount_minor": self.amount_minor,
                "category": self.category,
                "currency": self.currency,
                "description": self.description,
                "request_id": self.request_id,
                "resource": self.resource,
                "scope": self.scope,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
