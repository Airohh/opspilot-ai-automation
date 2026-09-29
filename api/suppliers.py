"""Supplier records. The JSON file is fictional demonstration data."""

from __future__ import annotations

import json
import re
from pathlib import Path

SUPPLIER_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
PUBLIC_FIELDS = (
    "id",
    "name",
    "category",
    "payment_terms",
    "risk_level",
    "contract_status",
)


class SupplierStore:
    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._suppliers = {
            item["id"].upper(): {field: item[field] for field in PUBLIC_FIELDS}
            for item in raw["suppliers"]
        }

    def get(self, supplier_id: str) -> dict | None:
        if not SUPPLIER_ID.fullmatch(supplier_id):
            raise ValueError("Malformed supplier id")
        return self._suppliers.get(supplier_id.upper())

    def search(self, query: str) -> list[dict]:
        needle = query.strip().lower()
        if not needle:
            return list(self._suppliers.values())
        return [
            item
            for item in self._suppliers.values()
            if needle in item["id"].lower() or needle in item["name"].lower()
        ]
