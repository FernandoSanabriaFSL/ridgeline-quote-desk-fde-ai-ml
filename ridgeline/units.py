"""Stage 3, step 3: units. Pure functions; nothing here reads the catalog.

Only Ridgeline units are ever returned: EA, BX, CS, PK, PR, RL, FT (or None).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RIDGELINE_UNITS = ("EA", "BX", "CS", "PK", "PR", "RL", "FT")

_UNIT_WORDS = {
    "ea": "EA", "each": "EA", "pcs": "EA",
    "box": "BX", "boxes": "BX",
    "case": "CS", "cases": "CS",
    "pack": "PK", "pkg": "PK",
    "pair": "PR", "pairs": "PR",
    "roll": "RL",
    "ft": "FT", "feet": "FT",
    **{unit.lower(): unit for unit in RIDGELINE_UNITS},
}
_CONTAINER_WORDS = ("pail", "drum", "can")
_PACK_RE = re.compile(r"\b(?:box|case|pack|pkg)\s+of\s+(\d[\d,]*)", re.IGNORECASE)


@dataclass
class UnitResult:
    quantity: int | float | None
    unit: str | None
    flags: list[str] = field(default_factory=list)
    note: str | None = None


def normalize_unit(text: str | None) -> str | None:
    """A written unit as a Ridgeline unit, or None when it isn't one (e.g. a container word)."""
    word = re.sub(r"[^a-z]", "", (text or "").lower())
    if word in _UNIT_WORDS:
        return _UNIT_WORDS[word]
    if word.endswith("s") and word[:-1] in _UNIT_WORDS:  # simple plurals of the listed words
        return _UNIT_WORDS[word[:-1]]
    return None


def pack_size(description: str) -> int | None:
    """N from "box of N", "case of N", "pack of N" or "pkg of N" in a catalog description."""
    match = _PACK_RE.search(description or "")
    if not match:
        return None
    size = int(match.group(1).replace(",", ""))
    return size or None


def container_word(text: str | None) -> str | None:
    """pail, drum or can when the written unit is one of those words (plural-tolerant)."""
    word = re.sub(r"[^a-z]", "", (text or "").lower())
    for candidate in _CONTAINER_WORDS:
        if word in (candidate, candidate + "s"):
            return candidate
    return None


def has_container_word(description: str, word: str) -> bool:
    return bool(re.search(rf"\b{word}s?\b", description or "", re.IGNORECASE))


def _fmt(number: int | float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


def apply_units(
    quantity: int | float | None,
    written_unit: str | None,
    product_unit: str | None,
    product_description: str = "",
) -> UnitResult:
    """The output quantity and unit for one line.

    product_unit is None for an unmatched line: the result is the normalized written unit
    (or None) and the quantity is untouched. A null quantity is never changed.
    """
    written = normalize_unit(written_unit)
    if product_unit is None:
        return UnitResult(quantity, written)

    raw = (written_unit or "").strip()
    size = pack_size(product_description)

    if not raw:  # no unit written
        if quantity is None or size is None or quantity < size:
            return UnitResult(quantity, product_unit)  # a quantity below the pack size means packs
        if quantity % size == 0:  # pack size or more, whole packs: the customer meant pieces
            packs = int(quantity // size)
            note = (
                f"no unit written; {_fmt(quantity)} treated as pieces: "
                f"{_fmt(quantity)} / {size} per {product_unit} = {packs} {product_unit}"
            )
            return UnitResult(packs, product_unit, ["unit_converted"], note)
        note = (
            f"no unit written; {_fmt(quantity)} is not a whole number of {size}-piece {product_unit} "
            f"packs, kept as written"
        )
        return UnitResult(quantity, None, ["unit_mismatch"], note)

    if written == product_unit:
        return UnitResult(quantity, product_unit)

    word = container_word(raw)
    if word and has_container_word(product_description, word):  # a pail of oil is one pail
        qty = "" if quantity is None else f"{_fmt(quantity)} "
        note = f"{qty}{raw} = {qty}{product_unit} (1 {word} per {product_unit}; the description says {word})"
        return UnitResult(quantity, product_unit, [], note)

    if written == "EA" and size is not None and product_unit != "EA":  # pieces against a pack
        if quantity is not None and quantity >= size and quantity % size == 0:
            packs = int(quantity // size)
            note = (
                f"{_fmt(quantity)} pieces / {size} per {product_unit} = {packs} {product_unit}"
            )
            return UnitResult(packs, product_unit, ["unit_converted"], note)

    note = f"customer unit {raw!r} does not match catalog unit {product_unit}; quantity kept as written"
    return UnitResult(quantity, written, ["unit_mismatch"], note)
