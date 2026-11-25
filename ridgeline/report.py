"""Readable review drafts for the specialist, and the run summary. No prices anywhere."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ridgeline import config
from ridgeline.catalog import Catalog
from ridgeline.schema import OutputLine, RfqOutput

DRAFT_LINE = "DRAFT: needs specialist approval, not sent to the customer"
HEADERS = [
    "Line", "As written", "Qty", "Unit", "Ridgeline part", "Catalog description",
    "Method", "Confidence", "Status", "Reasons",
]


def _cell(value: object) -> str:
    text = "-" if value is None or value == "" else str(value)
    return " ".join(text.replace("|", "\\|").split())


def status(line: OutputLine) -> str:
    return "REVIEW" if line.needs_review else "auto-accept"


def reasons(line: OutputLine, threshold: float) -> list[str]:
    """Why a line needs a specialist: no part, low confidence, and every flag."""
    out = []
    if line.ridgeline_part_number is None:
        out.append("no Ridgeline part matched")
    elif line.confidence is not None and line.confidence < threshold:
        out.append(f"confidence {line.confidence:.3f} is below {threshold:.2f}")
    out.extend(line.flags)
    return out


def render_review(output: RfqOutput, catalog: Catalog, source_path: str, as_of: date) -> str:
    threshold = config.REVIEW_THRESHOLD
    if output.customer_id:
        customer = f"Customer: {output.customer_id} {output.customer_name}"
    else:
        customer = "Customer: not identified"

    out = [DRAFT_LINE, "", f"RFQ: {output.rfq_id} (source: {source_path})", customer, ""]
    out.append("| " + " | ".join(HEADERS) + " |")
    out.append("|" + "|".join("---" for _ in HEADERS) + "|")
    for line in output.lines:
        product = catalog.products.get(line.ridgeline_part_number) if line.ridgeline_part_number else None
        cells = [
            line.line,
            line.as_written,
            line.quantity,
            line.unit,
            line.ridgeline_part_number,
            product.description if product else None,
            line.match_method,
            None if line.confidence is None else f"{line.confidence:.3f}",
            status(line),
            ", ".join(reasons(line, threshold)),
        ]
        out.append("| " + " | ".join(_cell(c) for c in cells) + " |")

    noted = [line for line in output.lines if line.note]
    if noted:
        out += ["", "## Notes for the specialist", ""]
        out += [f"- Line {line.line}: {' '.join(line.note.split())}" for line in noted]

    out += ["", "## Non-item rows", ""]
    out += [f"- {' '.join(row.split())}" for row in output.non_item_rows] or ["- (none)"]
    out += [
        "",
        f"Auto-accept means a Ridgeline part was matched with confidence of at least {threshold:.2f} and no flags. "
        f"Cross-reference dates were checked as of {as_of.isoformat()}.",
    ]
    return "\n".join(out) + "\n"


def write_review(output: RfqOutput, catalog: Catalog, source_path: str, as_of: date) -> Path:
    config.REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REVIEW_DIR / f"{output.rfq_id}.md"
    path.write_text(render_review(output, catalog, source_path, as_of), encoding="utf-8")
    return path


def format_summary(rows: list[tuple[str, int, int, int]]) -> str:
    """rows: (rfq id, lines, auto-accepted, to review). Adds a total row."""
    width = max([len(r[0]) for r in rows] + [len("total")])
    out = [f"  {'rfq':<{width}}  {'lines':>5}  {'auto-accepted':>13}  {'to review':>9}"]
    for name, lines, auto, review in rows + [
        ("total", sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows))
    ]:
        out.append(f"  {name:<{width}}  {lines:>5}  {auto:>13}  {review:>9}")
    out.insert(-1, "  " + "-" * (width + 33))
    return "\n".join(out)
