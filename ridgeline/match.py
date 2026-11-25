"""Stage 3: matching. Plain code against Ridgeline's data; no model calls.

For each extracted line, in order: a Ridgeline part number the customer wrote, an exact
cross-reference lookup, then an IDF-weighted description score. Every match then goes through
the lifecycle check and the unit rules. Part numbers are never fuzzy-matched: a near-miss is a
different part.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from ridgeline import config
from ridgeline.catalog import Catalog, Product, XrefRow, normalize_part_number
from ridgeline.units import apply_units, normalize_unit

if TYPE_CHECKING:
    from ridgeline.extract import CheckedLine, ExtractedRfq

# --- Tunables ---------------------------------------------------------------------------------

RL_CONFIDENCE = 0.98          # a Ridgeline part number the customer wrote that exists in the catalog
XREF_START = 0.95             # a cross-reference hit, before penalties
CLOSE_SUB = 0.15              # penalty: cross-reference note "close sub"
PKG_QTY = 0.10                # penalty: cross-reference note "pkg qty differs"
OTHER_NOTE = 0.03             # penalty: any other cross-reference note
STALE_VERIFIED = 0.03         # penalty: last_verified empty or too old
STALE_YEARS = 2               # "too old" means more than this many years before AS_OF_DATE
XREF_DESC_MISMATCH = 0.15     # cross-reference target scores below this against the line's description
NOT_CARRIED_SCORE = 0.25      # description match: best score below this means not carried
NOT_CARRIED_WORDS = 1 / 3     # ... or less than this share of descriptive words is in the best product
DESC_CAP = 0.92               # a description match never outranks a verified cross-reference
VARIANT_CAP = 0.65            # the variant (part code) was inferred, not written
CLOSE_MARGIN = 0.05           # margin over the runner-up below this is a close call
CLOSE_CAP = 0.70
WEAK_SCORE = 0.45             # a description score below this is weak
UNIT_SWAP_WITHIN = 0.12       # the runner-up wins on unit if the scores are this close
DISCONTINUED_CAP = 0.75       # a replacement was substituted for a discontinued product
DIGIT_WEIGHT = 1.5            # tokens containing digits weigh this much more
MAX_REPLACEMENT_HOPS = 5

# --- Normalizer (the same for the request and the catalog) -----------------------------------

_ABBREVIATIONS = {
    "brg": ["bearing"], "gr": ["grade"], "hyd": ["hydraulic"], "ss": ["stainless", "steel"],
    "fp": ["full", "port"], "buna": ["buna", "n"], "gal": ["gallon"], "bolt": ["hex", "cap", "screw"],
}
_SIZE_WORDS = (("large", "l"), ("medium", "m"), ("small", "s"))
_SIZE_CODES = {"s", "m", "l", "xl", "xs", "xxl"}
_NUMERIC_ATTRIBUTES = {"grade", "merv", "type"}
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/.][a-z0-9]+)*")


def _stem(word: str) -> str:
    """Strip a plural, the same way everywhere."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def normalize(text: str) -> frozenset[str]:
    """The set of normalized tokens in a description."""
    t = (text or "").lower().replace("”", '"').replace("″", '"')
    t = re.sub(r'(?<=[\d/])"', " in ", t)                      # inch marks
    t = re.sub(r"(\d+)#", r"\1 lb ", t)                        # 150# -> 150 lb
    t = re.sub(r"\b(\d{1,2})\s+(\d+/\d+)\b", r"\1-\2", t)      # 1 1/2 -> 1-1/2
    t = re.sub(r"\bextra large\b", "size xl", t)
    for word, code in _SIZE_WORDS:
        t = re.sub(rf"(?<!size )\b{word}\b", f"size {code}", t)

    parts: list[str] = []
    for raw in _TOKEN_RE.findall(t):
        # Mixed numbers (1-1/2) and fractions (1/2) stay whole; other hyphenated words split
        # into parts (3/8-16 -> 3/8, 16; o-ring -> o, ring).
        pieces = [raw] if re.fullmatch(r"\d+-\d+/\d+", raw) or "-" not in raw else raw.split("-")
        for piece in pieces:
            letters_digits = re.fullmatch(r"([a-z]+)(\d+)", piece)  # aw46 -> aw, 46 (but 2rs stays)
            parts.extend(letters_digits.groups() if letters_digits else [piece])
    parts = [_stem(p) if p.isalpha() else p for p in parts]

    mapped: list[str] = []
    for p in parts:
        mapped.extend(_ABBREVIATIONS.get(p, [p]))
    parts, mapped = mapped, []
    i = 0
    while i < len(parts):  # push-in -> push-to-connect
        if parts[i:i + 2] == ["push", "in"]:
            mapped.extend(["push", "to", "connect"])
            i += 2
        else:
            mapped.append(parts[i])
            i += 1

    tokens: list[str] = []
    i = 0
    while i < len(mapped):  # join an attribute and its value (grade 5 -> grade-5, size l -> size-l)
        word, nxt = mapped[i], mapped[i + 1] if i + 1 < len(mapped) else None
        numeric = nxt is not None and re.fullmatch(r"\d+(?:\.\d+)?", nxt)
        if nxt is not None and (
            (word in _NUMERIC_ATTRIBUTES and numeric) or (word == "size" and (numeric or nxt in _SIZE_CODES))
        ):
            tokens.append(f"{word}-{nxt}")
            i += 2
        else:
            tokens.append(word)
            i += 1
    return frozenset(tokens)


# --- Index and scorer -------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    part_number: str
    score: float
    unit: str
    description: str


@dataclass(frozen=True)
class Index:
    products: dict[str, Product]
    tokens: dict[str, frozenset[str]]
    df: dict[str, int]
    codes: dict[str, frozenset[tuple[str, str]]]  # (model number, part code) pairs per product

    def weight(self, token: str) -> float:
        """IDF weight; a token the catalog has never seen gets the maximum IDF."""
        n = len(self.tokens)
        df = self.df.get(token, 0)
        idf = 1 + math.log(n / df) if df else 1 + math.log(n)
        return idf * (DIGIT_WEIGHT if any(ch.isdigit() for ch in token) else 1)


def build_index(catalog: Catalog) -> Index:
    tokens, codes, df = {}, {}, {}
    for part_number, product in catalog.products.items():
        text = f"{product.description} {product.category}"
        tokens[part_number] = normalize(text)
        # A part code is what follows a 3+ digit model number after a hyphen (6204-2RS, 6204-ZZ).
        codes[part_number] = frozenset(
            (model, code)
            for model, code in re.findall(r"\b(\d{3,})-([a-z0-9]+)\b", product.description.lower())
            if re.search(r"[a-z]", code)
        )
        for token in tokens[part_number]:
            df[token] = df.get(token, 0) + 1
    return Index(catalog.products, tokens, df, codes)


def _coverage(index: Index, request: frozenset[str], product_tokens: frozenset[str], only=None) -> float | None:
    chosen = [t for t in sorted(request) if only is None or only(t)]
    total = sum(index.weight(t) for t in chosen)
    if not total:
        return None
    return round(sum(index.weight(t) for t in chosen if t in product_tokens) / total, 9)


def score(index: Index, request: frozenset[str], part_number: str) -> float:
    return _coverage(index, request, index.tokens[part_number]) or 0.0


def _is_descriptive(token: str) -> bool:
    return bool(re.fullmatch(r"[a-z]{3,}", token))


def rank(index: Index, request: frozenset[str]) -> list[Candidate]:
    """Every product, best first; ties are ordered by part number so runs are repeatable."""
    ranked = [
        Candidate(pn, score(index, request, pn), p.unit, p.description) for pn, p in index.products.items()
    ]
    ranked.sort(key=lambda c: (-c.score, c.part_number))
    return ranked


def _variant(index: Index, request: frozenset[str], chosen: str, other: str) -> tuple[str, str, str] | None:
    """(model number, chosen code, other code) when the request names the model number but neither code."""
    for model, code_a in index.codes[chosen]:
        for model_b, code_b in index.codes[other]:
            if model == model_b and code_a != code_b and model in request \
                    and code_a not in request and code_b not in request:
                return model, code_a, code_b
    return None


# --- Result -----------------------------------------------------------------------------------

@dataclass
class MatchedLine:
    line: int
    as_written: str
    customer_part_number: str | None
    ridgeline_part_number: str | None
    match_method: str | None
    confidence: float | None
    needs_review: bool
    flags: list[str]
    note: str | None
    unit: str | None
    quantity: int | float | None
    candidates: list[Candidate] = field(default_factory=list)  # description lines: the top candidates


# --- Cross-reference checks -------------------------------------------------------------------

def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def _xref_confidence(rows: list[XrefRow], as_of: date) -> tuple[float, list[str], list[str]]:
    """Start at XREF_START and subtract the penalties for what the sheet's own notes and dates say."""
    confidence, flags, notes = XREF_START, [], []
    for text in sorted({r.notes for r in rows if r.notes}):
        key = " ".join(text.lower().split())
        if key == "close sub":
            flags.append("close_substitute")
            confidence -= CLOSE_SUB
        elif key == "pkg qty differs":
            flags.append("pack_qty_differs")
            confidence -= PKG_QTY
        else:
            notes.append(f"cross-reference note: {text}")
            confidence -= OTHER_NOTE

    dates, unreadable = [], False
    for row in rows:
        try:
            dates.append(date.fromisoformat(row.last_verified or ""))
        except ValueError:
            unreadable = True
    if unreadable:
        confidence -= STALE_VERIFIED
        notes.append("cross-reference row was never verified (no usable last_verified date)")
    elif min(dates) < _years_before(as_of, STALE_YEARS):
        confidence -= STALE_VERIFIED
        notes.append(
            f"cross-reference last verified {min(dates).isoformat()}, more than {STALE_YEARS} years "
            f"before {as_of.isoformat()}"
        )
    return max(confidence, 0.0), flags, notes


def _short(description: str, limit: int = 60) -> str:
    return description if len(description) <= limit else description[: limit - 1] + "…"


# --- Lifecycle --------------------------------------------------------------------------------

def _lifecycle(product: Product, products: dict[str, Product]) -> tuple[Product | None, list[str], list[str], float | None]:
    """(product to output, flags, notes, confidence cap). None when no active replacement exists."""
    if product.status == "active":
        return product, [], [], None
    current, seen = product, {product.part_number}
    for _ in range(MAX_REPLACEMENT_HOPS):
        following = products.get(current.replaced_by) if current.replaced_by else None
        if following is None or following.part_number in seen:
            break
        if following.status == "active":
            note = f"{product.part_number} {product.status}, replaced by {following.part_number}"
            return following, ["discontinued_replaced"], [note], DISCONTINUED_CAP
        current = following
        seen.add(current.part_number)
    note = f"{product.part_number} is {product.status} and has no active replacement"
    return None, ["discontinued_no_replacement"], [note], None


# --- The matcher ------------------------------------------------------------------------------

def _candidate_note(c: Candidate) -> str:
    return f"{c.part_number} ({c.unit}, {c.score:.3f})"


def match_line(
    line: CheckedLine, number: int, catalog: Catalog, index: Index, as_of: date | None = None
) -> MatchedLine:
    as_of = as_of or config.AS_OF_DATE
    flags = list(line.flags)  # extraction flags come first
    notes = [line.note] if line.note else []
    request = normalize(line.item_description)
    review_only = "needs_human_review" in line.flags or "unparseable" in line.flags
    customer_pn = (line.customer_part_number or "").strip()
    customer_unit = normalize_unit(line.unit_as_written)

    product: Product | None = None
    method: str | None = None
    confidence = 0.0
    candidates: list[Candidate] = []
    stop = False

    # a. a Ridgeline part number the customer wrote
    written_rl = (line.ridgeline_part_number_as_written or "").strip().upper()
    if written_rl:
        product = catalog.products.get(written_rl)
        if product is not None:
            method, confidence = "ridgeline_part_number", RL_CONFIDENCE
        else:
            flags.append("unknown_ridgeline_part")
            notes.append(f"{written_rl} is not in the catalog")

    # b. exact cross-reference lookup, never fuzzy; the sheet is half-maintained, so check every hit
    in_xref = False
    if product is None and customer_pn:
        rows = catalog.xref.get(normalize_part_number(customer_pn), [])
        in_xref = bool(rows)
        targets = sorted({r.ridgeline_part_number for r in rows})
        if len(targets) > 1:
            flags.append("cross_reference_conflict")
            listed = "; ".join(
                f"{t} ({_short(catalog.products[t].description)})" if t in catalog.products else f"{t} (not in catalog)"
                for t in targets
            )
            notes.append(f"cross-reference lists {len(targets)} targets for {customer_pn}, none picked: {listed}")
            stop = True
        elif targets:
            target = catalog.products.get(targets[0])
            if target is None:
                flags.append("cross_reference_target_missing")
                notes.append(f"cross-reference target {targets[0]} for {customer_pn} is not in the catalog")
            else:
                product, method = target, "cross_reference"
                confidence, xflags, xnotes = _xref_confidence(rows, as_of)
                flags += xflags
                notes += xnotes
                if request:
                    overlap = score(index, request, target.part_number)
                    if overlap < XREF_DESC_MISMATCH:
                        flags.append("cross_reference_description_mismatch")
                        notes.append(
                            f"the description overlaps only {overlap:.2f} with cross-reference target {target.part_number}"
                        )

    if product is None and not stop:
        if customer_pn and not in_xref and not request:
            # e. a part number we don't know, and nothing else to go on
            flags.append("unknown_part_number")
            notes.append(f"{customer_pn} is not in the cross-reference and the line has no description")
        elif not request:
            if not customer_pn:
                notes.append("nothing to match: no part number and no description")
        else:
            if customer_pn and not in_xref:
                notes.append(f"{customer_pn} is not in the cross-reference")
            ranked = rank(index, request)
            top = ranked[0]
            if review_only:
                # d. never matched by description; give the reviewer the candidates
                tied = [c for c in ranked if c.score == top.score]
                if len(tied) >= 2:
                    tied.sort(key=lambda c: (c.unit != customer_unit, c.part_number))
                    candidates = tied[:3]
                    heading = f"{len(tied)} products tie at {top.score:.3f}"
                else:
                    candidates = ranked[:2]
                    heading = "top candidates"
                if top.score == 0:
                    candidates = []  # nothing shares a word: these would be arbitrary products
                    notes.append("held for review; no catalog product shares a word with the description")
                else:
                    notes.append(f"held for review, not matched by description; {heading}: "
                                 + ", ".join(_candidate_note(c) for c in candidates))
            else:
                # c. description score
                candidates = [c for c in ranked[:3] if c.score > 0]  # a zero score is not a candidate
                coverage = _coverage(index, request, index.tokens[top.part_number], only=_is_descriptive)
                if top.score < NOT_CARRIED_SCORE or (coverage is not None and coverage < NOT_CARRIED_WORDS):
                    flags.append("not_carried")
                    if top.score == 0:
                        notes.append("no catalog product shares a word with the description")
                    else:
                        notes.append(
                            f"best candidate {top.part_number} ({_short(top.description)}) scores {top.score:.3f}"
                        )
                else:
                    chosen, other = top, ranked[1]
                    if (customer_unit and other.unit == customer_unit and top.unit != customer_unit
                            and top.score - other.score <= UNIT_SWAP_WITHIN):
                        chosen, other = other, top
                        notes.append(
                            f"customer unit {customer_unit} matches {chosen.part_number} ({chosen.unit}) but not "
                            f"{top.part_number} ({top.unit}); scores {top.score:.3f} and {chosen.score:.3f} are "
                            f"within {UNIT_SWAP_WITHIN}, so {chosen.part_number} was taken"
                        )
                    margin = max(0.0, chosen.score - other.score)
                    confidence = min(DESC_CAP, 0.5 + 0.35 * chosen.score + 0.5 * min(margin, 0.3))
                    product, method = catalog.products[chosen.part_number], "description"
                    variant = _variant(index, request, chosen.part_number, other.part_number)
                    if variant:
                        model, code_a, code_b = variant
                        confidence = min(confidence, VARIANT_CAP)
                        flags.append("variant_inferred")
                        notes.append(
                            f"the request names {model} but no part code; {chosen.part_number} ({code_a}) chosen, "
                            f"alternative {other.part_number} ({code_b})"
                        )
                    if margin < CLOSE_MARGIN:
                        confidence = min(confidence, CLOSE_CAP)
                        flags.append("close_alternative")
                        notes.append(f"close alternative {_candidate_note(other)}")
                    if chosen.score < WEAK_SCORE:
                        flags.append("weak_description_match")

    # lifecycle, on every match
    if product is not None:
        replacement, lflags, lnotes, cap = _lifecycle(product, catalog.products)
        flags += lflags
        notes += lnotes
        product = replacement
        if product is None:
            method = None
        elif cap is not None:
            confidence = min(confidence, cap)

    # units, against the product that is actually output
    units = apply_units(
        line.quantity, line.unit_as_written,
        product.unit if product else None, product.description if product else "",
    )
    flags += units.flags
    if units.note:
        notes.append(units.note)

    flags = list(dict.fromkeys(flags))
    final_confidence = round(min(max(confidence, 0.0), 1.0), 3) if product else None
    needs_review = product is None or final_confidence < config.REVIEW_THRESHOLD or bool(flags)
    return MatchedLine(
        line=number,
        as_written=line.as_written,
        customer_part_number=line.customer_part_number,
        ridgeline_part_number=product.part_number if product else None,
        match_method=method if product else None,
        confidence=final_confidence,
        needs_review=needs_review,
        flags=flags,
        note="; ".join(p for p in (n.rstrip(" .") for n in notes if n) if p) or None,
        unit=units.unit,
        quantity=units.quantity,
        candidates=candidates,
    )


def match_rfq(extracted: ExtractedRfq, catalog: Catalog, index: Index, as_of: date | None = None) -> list[MatchedLine]:
    return [match_line(line, n, catalog, index, as_of) for n, line in enumerate(extracted.lines, start=1)]
