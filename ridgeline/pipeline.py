"""Stage 4: end to end. Extract, match, assemble the validated output, write the files.

Every RFQ ends up as one outputs/<stem>.json that has passed the RfqOutput model, and one
readable draft in outputs/review/<stem>.md. Nothing is sent anywhere.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from ridgeline import config
from ridgeline.adapters import IMAGE_SUFFIXES
from ridgeline.catalog import Catalog, load_catalog
from ridgeline.extract import ExtractedRfq, extract_rfq
from ridgeline.match import MatchedLine, build_index, match_rfq
from ridgeline.report import write_review
from ridgeline.schema import OutputLine, RfqOutput

PHOTO_AS_WRITTEN = "entire RFQ (handwritten photo)"


@dataclass
class RfqResult:
    path: Path
    extracted: ExtractedRfq
    matched: list[MatchedLine]
    output: RfqOutput
    json_path: Path | None = None
    review_path: Path | None = None
    lines: int = field(init=False)
    auto_accepted: int = field(init=False)
    to_review: int = field(init=False)

    def __post_init__(self) -> None:
        self.lines = len(self.output.lines)
        self.to_review = sum(1 for line in self.output.lines if line.needs_review)
        self.auto_accepted = self.lines - self.to_review


def list_rfq_files() -> list[Path]:
    """Regular files in the RFQ folder, sorted by name. Hidden files such as .DS_Store are skipped."""
    return sorted(p for p in config.RFQS_DIR.iterdir() if p.is_file() and not p.name.startswith("."))


# --- Single review lines (images, unsupported formats, failures) ---------------------------------

def _review_line_output(rfq_id: str, flag: str, as_written: str, note: str) -> RfqOutput:
    line = OutputLine(
        line=1, as_written=as_written, quantity=None, unit=None, ridgeline_part_number=None,
        match_method=None, confidence=None, needs_review=True, flags=[flag], note=note,
    )
    return RfqOutput(rfq_id=rfq_id, customer_id=None, customer_name=None, lines=[line], non_item_rows=[])


def review_line_output(extracted: ExtractedRfq) -> RfqOutput:
    """One review line for an RFQ that could not be read. The stub flag is for images only."""
    source = extracted.source_path
    if extracted.status == "unsupported" and Path(source).suffix.lower() in IMAGE_SUFFIXES:
        note = f"Photo source: needs OCR or a vision model. Original file {source} attached for the specialist."
        return _review_line_output(extracted.rfq_id, "stub", PHOTO_AS_WRITTEN, note)
    if extracted.status == "unsupported":
        note = f"{extracted.reason}. Original file {source} attached for the specialist."
        return _review_line_output(extracted.rfq_id, "unsupported_format", "entire RFQ (unsupported format)", note)
    return _review_line_output(
        extracted.rfq_id, "extraction_failed", "entire RFQ (extraction failed)", extracted.reason or "extraction failed"
    )


# --- Matched RFQs ----------------------------------------------------------------------------

def build_output(extracted: ExtractedRfq, matched: list[MatchedLine]) -> RfqOutput:
    lines = [
        OutputLine(
            line=m.line,
            as_written=m.as_written,
            quantity=m.quantity,
            unit=m.unit,
            ridgeline_part_number=m.ridgeline_part_number,
            match_method=m.match_method,
            confidence=m.confidence,
            needs_review=m.needs_review,
            flags=m.flags,
            note=m.note,
        )
        for m in matched
    ]
    return RfqOutput(
        rfq_id=extracted.rfq_id,
        customer_id=extracted.customer_id,
        customer_name=extracted.customer_name,
        lines=lines,
        non_item_rows=extracted.non_item_rows,
    )


def write_output_json(output: RfqOutput) -> Path:
    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTPUTS_DIR / f"{output.rfq_id}.json"
    path.write_text(json.dumps(output.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def process_rfq(
    path: Path, catalog: Catalog, index, refresh: bool = False, as_of: date | None = None
) -> RfqResult:
    """Extract and match one RFQ file; never raises, so one bad file cannot stop the batch."""
    as_of = as_of or config.AS_OF_DATE
    extracted = extract_rfq(path, catalog.customers, refresh=refresh)
    matched: list[MatchedLine] = []
    if extracted.status == "ok":
        matched = match_rfq(extracted, catalog, index, as_of)
        try:
            output = build_output(extracted, matched)  # validated by the RfqOutput model
        except ValidationError as exc:
            reason = " ".join(str(exc).split())[:300]
            extracted.status, extracted.reason = "failed", f"output validation failed: {reason} (source: {extracted.source_path})"
            matched = []
            output = review_line_output(extracted)
    else:
        output = review_line_output(extracted)
    return RfqResult(path, extracted, matched, output)


def run_all(refresh: bool = False) -> tuple[list[RfqResult], Catalog]:
    """Everything, end to end, for every RFQ file. Extraction is served from the cache when it can be."""
    catalog = load_catalog()
    index = build_index(catalog)
    results = []
    for path in list_rfq_files():
        result = process_rfq(path, catalog, index, refresh=refresh)
        result.json_path = write_output_json(result.output)
        result.review_path = write_review(result.output, catalog, result.extracted.source_path, config.AS_OF_DATE)
        results.append(result)
    return results, catalog
