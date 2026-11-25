"""Stage 2: extraction. The model reads one RFQ; code validates, caches and resolves the customer.

The model only extracts what the customer wrote. It never sees the catalog, the
cross-reference or any price, and it never picks Ridgeline part numbers.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict

from ridgeline import config
from ridgeline.adapters import read_source
from ridgeline.catalog import Customer

Flag = Literal["quantity_missing", "needs_human_review", "amended_in_thread", "unparseable"]


# --- Internal extraction model (what the LLM returns; not the output schema) ---------------

class ExtractedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_written: str
    customer_part_number: str | None
    ridgeline_part_number_as_written: str | None
    item_description: str
    quantity: float | None
    unit_as_written: str | None
    flags: list[Flag]
    note: str | None


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name_as_written: str | None
    sender_email: str | None
    non_item_rows: list[str]
    lines: list[ExtractedLine]


# --- Result of extracting one RFQ -----------------------------------------------------------

@dataclass
class CheckedLine:
    as_written: str
    customer_part_number: str | None
    ridgeline_part_number_as_written: str | None
    item_description: str
    quantity: int | float | None
    unit_as_written: str | None
    flags: list[str]  # model flags plus any added by the validators
    note: str | None


@dataclass
class ExtractedRfq:
    rfq_id: str
    source_path: str
    status: str  # "ok", "unsupported" (needs OCR / unknown type) or "failed"
    reason: str | None = None
    customer_id: str | None = None
    customer_name: str | None = None
    customer_name_as_written: str | None = None
    sender_email: str | None = None
    non_item_rows: list[str] = field(default_factory=list)
    lines: list[CheckedLine] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# --- Small helpers --------------------------------------------------------------------------

def _redact(text: str) -> str:
    """Never let the API key reach a log, a cache entry or the terminal."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    return text.replace(key, "***") if key else text


def _short(exc: BaseException, limit: int = 400) -> str:
    message = " ".join(f"{type(exc).__name__}: {exc}".split())
    return _redact(message)[:limit]


def _norm(text: str) -> str:
    """Lowercase with whitespace collapsed."""
    return " ".join(text.lower().split())


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(config.ROOT))
    except ValueError:
        return str(path)


# --- Validators (flags only; nothing is dropped) --------------------------------------------

def _table_rows(source: str) -> list[list[str]]:
    """Cells of every table-style row (`a | b | c`) in the source, normalized, empty cells removed."""
    rows = []
    for line in source.splitlines():
        if "|" not in line:
            continue
        line = re.sub(r"^\s*R\d+:\s*", "", line)
        rows.append([cell for cell in (_norm(c) for c in line.split("|")) if cell])
    return rows


def _match_cells(rest: str, cells: list[str], start: int = 0) -> bool:
    """True if `rest` is some cells of a row, in order, joined by single spaces."""
    if not rest:
        return True
    for i in range(start, len(cells)):
        cell = cells[i]
        if rest.startswith(cell):
            tail = rest[len(cell):]
            if (tail == "" or tail.startswith(" ")) and _match_cells(tail.lstrip(), cells, i + 1):
                return True
    return False


def _as_written_in_source(as_written: str, norm_source: str, rows: list[list[str]]) -> bool:
    text = _norm(as_written)
    if not text:
        return False
    return text in norm_source or any(_match_cells(text, cells) for cells in rows)


_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _numbers_in(source: str) -> set[float]:
    return {float(token.replace(",", "")) for token in _NUMBER_RE.findall(source)}


def _alnum(text: str) -> str:
    """Lowercase, with everything except letters and digits removed."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _part_number_in_source(part_number: str, alnum_source: str) -> bool:
    """True if the part number appears in the source, compared on letters and digits only, any case."""
    key = _alnum(part_number)
    return bool(key) and key in alnum_source


def _check_lines(output: ExtractionOutput, source: str) -> list[CheckedLine]:
    norm_source = _norm(source)
    alnum_source = _alnum(source)
    rows = _table_rows(source)
    numbers = _numbers_in(source)
    checked = []
    for line in output.lines:
        flags = list(line.flags)
        if not _as_written_in_source(line.as_written, norm_source, rows):
            flags.append("as_written_not_in_source")
        quantity: int | float | None = line.quantity
        if quantity is not None:
            if float(quantity) not in numbers:
                flags.append("quantity_not_in_source")
            if float(quantity).is_integer():
                quantity = int(quantity)
        # The model must never put a part number on a quote that the customer didn't write.
        # The matcher skips a part number carrying one of these flags.
        for value, flag in (
            (line.ridgeline_part_number_as_written, "ridgeline_part_number_not_in_source"),
            (line.customer_part_number, "customer_part_number_not_in_source"),
        ):
            if value and value.strip() and not _part_number_in_source(value, alnum_source):
                flags.append(flag)
        checked.append(CheckedLine(
            as_written=line.as_written,
            customer_part_number=line.customer_part_number,
            ridgeline_part_number_as_written=line.ridgeline_part_number_as_written,
            item_description=line.item_description,
            quantity=quantity,
            unit_as_written=line.unit_as_written,
            flags=flags,
            note=line.note,
        ))
    return checked


# --- Customer resolution (code, not the model) ----------------------------------------------

_COMPANY_SUFFIXES = {"co", "company", "inc", "llc", "corp", "corporation", "ltd"}


def _norm_company(text: str | None) -> str:
    """Lowercase, punctuation and spaces dropped, trailing suffix words (co, inc, llc...) dropped."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    while words and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return "".join(words)


def _domain_label(email: str | None) -> str:
    """The label before the TLD of the sender's domain ('mail.acme.example' -> 'acme')."""
    if not email or "@" not in email:
        return ""
    labels = [part for part in email.rsplit("@", 1)[1].strip().strip(">").lower().split(".") if part]
    if not labels:
        return ""
    return labels[-2] if len(labels) >= 2 else labels[0]


def _contains_either(a: str, b: str) -> bool:
    return len(a) >= 3 and len(b) >= 3 and (a in b or b in a)


def resolve_customer(
    name_as_written: str | None, sender_email: str | None, customers: dict[str, Customer]
) -> tuple[str | None, str | None]:
    """(customer_id, catalog name) when exactly one customer matches, otherwise (None, None)."""
    candidates = [c for c in (_norm_company(name_as_written), _norm_company(_domain_label(sender_email))) if c]
    hits = {
        customer.customer_id
        for customer in customers.values()
        if any(_contains_either(candidate, _norm_company(customer.name)) for candidate in candidates)
    }
    if len(hits) != 1:
        return None, None
    (customer_id,) = hits
    return customer_id, customers[customer_id].name


# --- The LLM call ---------------------------------------------------------------------------

_client: anthropic.Anthropic | None = None
_temperature_accepted: bool | None = None  # None until the API has told us


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


def _load_prompt() -> str:
    return (config.PROMPTS_DIR / f"{config.PROMPT_VERSION}.md").read_text(encoding="utf-8")


def _user_message(source: str, error: str | None) -> list[dict]:
    content = f"<rfq_source>\n{source}\n</rfq_source>"
    if error:
        content += f"\n\nYour previous reply was rejected: {error}\nReturn a corrected response."
    return [{"role": "user", "content": content}]


def _call_once(system: str, source: str, error: str | None) -> tuple[ExtractionOutput, dict]:
    """One structured-output call. inference_geo is never dropped; temperature 0 only if accepted."""
    global _temperature_accepted
    options = [True, False] if _temperature_accepted is not False else [False]
    for use_temperature in options:
        # This SDK's messages.parse() has no keyword for either, so both go through extra_body.
        extra_body: dict = {"inference_geo": config.LLM_INFERENCE_GEO}
        if use_temperature:
            extra_body["temperature"] = 0
        try:
            response = _get_client().messages.parse(
                model=config.LLM_MODEL,
                max_tokens=4000,
                system=system,
                messages=_user_message(source, error),
                output_format=ExtractionOutput,
                extra_body=extra_body,
            )
        except anthropic.BadRequestError as exc:
            if use_temperature and "temperature" in str(exc).lower():
                _temperature_accepted = False
                continue
            raise
        if use_temperature:
            _temperature_accepted = True
        output = response.parsed_output
        if output is None:
            raise ValueError(f"no parsed output (stop_reason={response.stop_reason})")
        if not output.lines:
            raise ValueError("the reply contained no lines")
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "inference_geo": getattr(response.usage, "inference_geo", None),
            "temperature_applied": use_temperature,
        }
        return output, usage
    raise RuntimeError("unreachable")


def _call_with_retry(system: str, source: str) -> tuple[ExtractionOutput, dict]:
    """Call the model; on any failure retry once with the error message, then give up."""
    started = time.perf_counter()
    error: str | None = None
    for attempt in (1, 2):
        try:
            output, usage = _call_once(system, source, error)
        except Exception as exc:
            if attempt == 2:
                raise
            error = _short(exc)
            continue
        usage["attempts"] = attempt
        usage["latency_s"] = round(time.perf_counter() - started, 2)
        return output, usage
    raise RuntimeError("unreachable")


# --- Cache ----------------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_path(source: str) -> Path:
    key = _sha256("\x00".join([config.LLM_MODEL, config.PROMPT_VERSION, source]))
    return config.CACHE_DIR / f"{key}.json"


def _cache_read(path: Path, prompt_sha: str) -> tuple[ExtractionOutput, dict] | None:
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry["prompt_sha256"] != prompt_sha:  # prompt edited without a version bump: stale
            return None
        return ExtractionOutput.model_validate(entry["extraction"]), dict(entry["stats"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cache_write(path: Path, prompt_sha: str, source: str, output: ExtractionOutput, stats: dict) -> None:
    entry = {
        "model": config.LLM_MODEL,
        "prompt_version": config.PROMPT_VERSION,
        "prompt_sha256": prompt_sha,
        "source_sha256": _sha256(source),
        "stats": stats,
        "extraction": output.model_dump(mode="json"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


# --- Entry point ----------------------------------------------------------------------------

def extract_rfq(
    path: Path, customers: dict[str, Customer], refresh: bool = False, cache_only: bool = False
) -> ExtractedRfq:
    """Extract one RFQ file. Never raises: failures come back as status "failed" with a reason.

    With cache_only=True the model is never called: a cache miss is a "failed" result.
    """
    rfq_id = path.stem
    where = _display_path(path)
    try:
        doc = read_source(path)
    except Exception as exc:
        return ExtractedRfq(rfq_id, where, "failed", reason=f"{_short(exc)} (source: {where})")
    if doc.status != "ok":
        return ExtractedRfq(rfq_id, where, "unsupported", reason=doc.reason)

    try:
        prompt = _load_prompt()
        prompt_sha = _sha256(prompt)
        cache_file = _cache_path(doc.text)
        cached = None if refresh else _cache_read(cache_file, prompt_sha)
        if cached is not None:
            output, stats = cached
            stats["cached"] = True
        elif cache_only:
            reason = f"no cached extraction for this model and prompt; run --stage extract first (source: {where})"
            return ExtractedRfq(rfq_id, where, "failed", reason=reason)
        else:
            output, stats = _call_with_retry(prompt, doc.text)
            _cache_write(cache_file, prompt_sha, doc.text, output, stats)
            stats["cached"] = False
    except Exception as exc:
        return ExtractedRfq(rfq_id, where, "failed", reason=f"{_short(exc)} (source: {where})")

    customer_id, customer_name = resolve_customer(output.customer_name_as_written, output.sender_email, customers)
    return ExtractedRfq(
        rfq_id=rfq_id,
        source_path=where,
        status="ok",
        customer_id=customer_id,
        customer_name=customer_name,
        customer_name_as_written=output.customer_name_as_written,
        sender_email=output.sender_email,
        non_item_rows=list(output.non_item_rows),
        lines=_check_lines(output, doc.text),
        stats=stats,
    )
