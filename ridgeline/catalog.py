"""Catalog loader: products, customers and the competitor cross-reference.

Columns are allow-listed when a CSV is read. Price and pricing-tier columns are never
kept, so they cannot end up in a prompt or an output.
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from ridgeline import config

PRODUCT_COLUMNS = (
    "part_number", "description", "category", "unit", "status", "replaced_by", "primary_warehouse",
)
CUSTOMER_COLUMNS = ("customer_id", "name", "region")
XREF_COLUMNS = (
    "competitor", "competitor_part_number", "ridgeline_part_number", "notes", "last_verified",
)


@dataclass(frozen=True)
class Product:
    part_number: str
    description: str
    category: str
    unit: str
    status: str
    replaced_by: str | None
    primary_warehouse: str


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    region: str


@dataclass(frozen=True)
class XrefRow:
    competitor: str
    competitor_part_number: str
    ridgeline_part_number: str
    notes: str | None
    last_verified: str | None


@dataclass(frozen=True)
class Catalog:
    products: dict[str, Product]
    customers: dict[str, Customer]
    # Keyed by normalized competitor part number. Every row is kept: duplicates exist.
    xref: dict[str, list[XrefRow]]
    quality_report: str


def normalize_part_number(value: str) -> str:
    """Uppercase and keep only A-Z and 0-9."""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _read_rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path.name}: missing column(s) {', '.join(missing)}")
        return [{c: (row[c] or "").strip() for c in columns} for row in reader]


def _load_products() -> dict[str, Product]:
    products: dict[str, Product] = {}
    for row in _read_rows(config.PRODUCTS_CSV, PRODUCT_COLUMNS):
        part_number = row["part_number"]
        if part_number in products:
            raise ValueError(f"{config.PRODUCTS_CSV.name}: duplicate part_number {part_number}")
        products[part_number] = Product(
            part_number=part_number,
            description=row["description"],
            category=row["category"],
            unit=row["unit"],
            status=row["status"],
            replaced_by=row["replaced_by"] or None,
            primary_warehouse=row["primary_warehouse"],
        )
    return products


def _load_customers() -> dict[str, Customer]:
    return {
        row["customer_id"]: Customer(row["customer_id"], row["name"], row["region"])
        for row in _read_rows(config.CUSTOMERS_CSV, CUSTOMER_COLUMNS)
    }


def _load_xref() -> tuple[list[XrefRow], dict[str, list[XrefRow]]]:
    rows: list[XrefRow] = []
    index: dict[str, list[XrefRow]] = defaultdict(list)
    for raw in _read_rows(config.CROSS_REFERENCE_CSV, XREF_COLUMNS):
        row = XrefRow(
            competitor=raw["competitor"],
            competitor_part_number=raw["competitor_part_number"],
            ridgeline_part_number=raw["ridgeline_part_number"],
            notes=raw["notes"] or None,
            last_verified=raw["last_verified"] or None,
        )
        key = normalize_part_number(row.competitor_part_number)
        if not key:
            raise ValueError(
                f"{config.CROSS_REFERENCE_CSV.name}: empty competitor part number for "
                f"{row.ridgeline_part_number}"
            )
        rows.append(row)
        index[key].append(row)
    return rows, dict(index)


def _section(title: str, count: int, details: list[str]) -> list[str]:
    lines = [f"  [{count}] {title}"]
    lines.extend(f"        {d}" for d in details)
    return lines


def _build_report(
    products: dict[str, Product],
    customers: dict[str, Customer],
    xref_rows: list[XrefRow],
    xref: dict[str, list[XrefRow]],
) -> str:
    active = sum(1 for p in products.values() if p.status == "active")
    out = [
        "Data-quality report",
        f"  products: {len(products)} ({active} active, {len(products) - active} not active)"
        f" | customers: {len(customers)} | cross-reference rows: {len(xref_rows)}"
        f" ({len(xref)} distinct keys)",
    ]

    missing = [r for r in xref_rows if r.ridgeline_part_number not in products]
    out += _section(
        "cross-reference targets missing from products",
        len(missing),
        [f"{r.competitor_part_number} -> {r.ridgeline_part_number}" for r in missing],
    )

    inactive = []
    for r in xref_rows:
        target = products.get(r.ridgeline_part_number)
        if target is not None and target.status != "active":
            replacement = f", replaced_by {target.replaced_by}" if target.replaced_by else ""
            inactive.append(
                f"{r.competitor_part_number} -> {r.ridgeline_part_number} ({target.status}{replacement})"
            )
    out += _section("cross-reference targets that aren't active", len(inactive), inactive)

    multi = {
        key: sorted({r.ridgeline_part_number for r in rows})
        for key, rows in sorted(xref.items())
        if len({r.ridgeline_part_number for r in rows}) > 1
    }
    out += _section(
        "keys with more than one target",
        len(multi),
        [f"{key} -> {', '.join(targets)}" for key, targets in multi.items()],
    )

    notes = Counter(r.notes for r in xref_rows if r.notes)
    out += _section(
        "rows with notes",
        sum(notes.values()),
        [f"{count} x {note}" for note, count in sorted(notes.items())],
    )

    unverified = [r.competitor_part_number for r in xref_rows if not r.last_verified]
    out += _section(
        "rows never verified (blank last_verified)",
        len(unverified),
        [", ".join(unverified)] if unverified else [],
    )
    return "\n".join(out)


def load_catalog(report: bool = False) -> Catalog:
    """Load products, customers and the cross-reference. Prints the data-quality report if asked."""
    products = _load_products()
    customers = _load_customers()
    xref_rows, xref = _load_xref()
    quality_report = _build_report(products, customers, xref_rows, xref)
    if report:
        print(quality_report)
    return Catalog(products=products, customers=customers, xref=xref, quality_report=quality_report)
