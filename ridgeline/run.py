"""Entry point: python -m ridgeline.run [--stage extract|match|catalog] [--refresh]

No --stage: the whole pipeline, end to end (extract, match, outputs/ JSON, outputs/review/ drafts, summary).
--stage extract: runs the extraction stage on every RFQ and prints the result.
--stage match: matches the cached extractions to Ridgeline part numbers (no model calls).
--stage catalog: prints the catalog data-quality report and lists the RFQ files.
"""
import argparse
import json
import textwrap
from collections import Counter
from pathlib import Path

from ridgeline import config
from ridgeline.catalog import load_catalog
from ridgeline.extract import ExtractedRfq, extract_rfq
from ridgeline.match import MatchedLine, build_index, match_rfq
from ridgeline.pipeline import list_rfq_files, run_all
from ridgeline.report import format_summary


# --- Printing -------------------------------------------------------------------------------

def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width, break_long_words=True, break_on_hyphens=False) or [""]


def _table(headers: list[str], rows: list[list[str]], widths: list[int]) -> list[str]:
    out = []
    for cells in [headers, ["-" * w for w in widths]] + rows:
        wrapped = [_wrap(cell, width) for cell, width in zip(cells, widths)]
        for i in range(max(len(w) for w in wrapped)):
            parts = [(w[i] if i < len(w) else "").ljust(width) for w, width in zip(wrapped, widths)]
            out.append("  " + " | ".join(parts).rstrip())
    return out


def _dash(value: object) -> str:
    return "-" if value is None or value == "" else str(value)


def print_extracted(result: ExtractedRfq) -> None:
    stats = result.stats
    print(f"\n=== {result.rfq_id} ({Path(result.source_path).name}) ===")
    if result.status != "ok":
        print(f"status: {result.status}")
        print(f"reason: {result.reason}")
        return
    print(
        f"status: ok | {'from cache' if stats.get('cached') else 'live call'}"
        f" | {stats.get('latency_s')}s | tokens in {stats.get('input_tokens')} / out {stats.get('output_tokens')}"
        f" | inference_geo {stats.get('inference_geo')} | attempts {stats.get('attempts')}"
        f" | temperature 0 applied: {stats.get('temperature_applied')}"
    )
    if result.customer_id:
        print(f"customer: {result.customer_id} {result.customer_name}")
    else:
        print("customer: unresolved (customer_id null, customer_name null)")
    print(f"  as written: {_dash(result.customer_name_as_written)} | sender: {_dash(result.sender_email)}")
    print(f"non_item_rows ({len(result.non_item_rows)}):")
    for row in result.non_item_rows:
        print(f"  - {row}")
    print(f"lines ({len(result.lines)}), '-' means null:")
    table_rows = [
        [
            str(number),
            line.as_written,
            _dash(line.quantity),
            _dash(line.unit_as_written),
            _dash(line.customer_part_number),
            _dash(line.ridgeline_part_number_as_written),
            _dash(line.item_description),
            ", ".join(line.flags) or "-",
        ]
        for number, line in enumerate(result.lines, start=1)
    ]
    headers = ["#", "as_written", "qty", "unit_as_written", "customer_pn", "rl_pn_as_written", "item_description", "flags"]
    for text in _table(headers, table_rows, [2, 34, 6, 10, 15, 10, 30, 22]):
        print(text)
    for number, line in enumerate(result.lines, start=1):
        if line.note:
            print(f"  note {number}: {line.note}")


def print_matched(result: ExtractedRfq, matched: list[MatchedLine]) -> None:
    print(f"\n=== {result.rfq_id} ({Path(result.source_path).name}) ===")
    if result.customer_id:
        print(f"customer: {result.customer_id} {result.customer_name}")
    else:
        print("customer: unresolved")
    print(f"lines ({len(matched)}), '-' means null:")
    rows = [
        [
            str(m.line),
            m.as_written,
            _dash(m.ridgeline_part_number),
            _dash(m.match_method),
            "-" if m.confidence is None else f"{m.confidence:.3f}",
            "yes" if m.needs_review else "no",
            _dash(m.unit),
            _dash(m.quantity),
            ", ".join(m.flags) or "-",
        ]
        for m in matched
    ]
    headers = ["#", "as_written", "part number", "method", "conf", "review", "unit", "qty", "flags"]
    for text in _table(headers, rows, [2, 28, 9, 21, 5, 6, 4, 5, 36]):
        print(text)
    for m in matched:
        if m.note:
            print(f"  note {m.line}: {m.note}")
    for m in matched:
        if m.candidates:
            print(f"  candidates, line {m.line}:")
            for c in m.candidates:
                description = c.description if len(c.description) <= 78 else c.description[:77] + "…"
                print(f"    {c.score:.3f}  {c.part_number} [{c.unit}]  {description}")


# --- run_stats.json: each stage updates only its own keys ------------------------------------

def _read_run_stats() -> dict:
    try:
        data = json.loads(config.RUN_STATS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_run_stats(data: dict) -> None:
    config.EVALUATION_DIR.mkdir(parents=True, exist_ok=True)
    config.RUN_STATS_JSON.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_run_stats(results: list[ExtractedRfq]) -> None:
    per_rfq = {}
    for result in results:
        entry = {"status": result.status}
        if result.status == "ok":
            keys = ("cached", "latency_s", "input_tokens", "output_tokens", "inference_geo", "attempts", "temperature_applied")
            entry.update({key: result.stats.get(key) for key in keys})
        else:
            entry["reason"] = result.reason
        per_rfq[result.rfq_id] = entry
    data = _read_run_stats()
    data.update({
        "stage": "extract",
        "model": config.LLM_MODEL,
        "prompt_version": config.PROMPT_VERSION,
        "inference_geo_requested": config.LLM_INFERENCE_GEO,
        "metrics_source": (
            "latency, tokens, inference_geo and attempts come from the live API call that produced "
            "each result; cached says whether the most recent run reused it"
        ),
        "rfqs": per_rfq,
    })
    _write_run_stats(data)


def write_match_stats(items: list[tuple[ExtractedRfq, list[MatchedLine]]]) -> tuple[int, int, int]:
    """Record the match settings and counts; returns (lines, matched, needs_review)."""
    per_rfq: dict[str, dict] = {}
    methods: Counter = Counter()
    totals = Counter()
    for extracted, matched in items:
        if extracted.status != "ok":
            per_rfq[extracted.rfq_id] = {"status": extracted.status, "reason": extracted.reason}
            continue
        got = sum(1 for m in matched if m.ridgeline_part_number)
        review = sum(1 for m in matched if m.needs_review)
        per_rfq[extracted.rfq_id] = {"status": "ok", "lines": len(matched), "matched": got, "needs_review": review}
        totals.update(lines=len(matched), matched=got, needs_review=review)
        methods.update(m.match_method for m in matched if m.match_method)
    data = _read_run_stats()
    data["match"] = {
        "as_of_date": config.AS_OF_DATE.isoformat(),
        "review_threshold": config.REVIEW_THRESHOLD,
        "lines": totals["lines"],
        "matched": totals["matched"],
        "unmatched": totals["lines"] - totals["matched"],
        "needs_review": totals["needs_review"],
        "by_method": dict(sorted(methods.items())),
        "rfqs": per_rfq,
    }
    _write_run_stats(data)
    return totals["lines"], totals["matched"], totals["needs_review"]


# --- Stages ---------------------------------------------------------------------------------

def run_extract(refresh: bool) -> None:
    catalog = load_catalog()
    results = []
    for path in list_rfq_files():
        result = extract_rfq(path, catalog.customers, refresh=refresh)
        print_extracted(result)
        results.append(result)
    write_run_stats(results)
    counts = {status: sum(1 for r in results if r.status == status) for status in ("ok", "unsupported", "failed")}
    print(f"\n{len(results)} RFQs: {counts['ok']} ok, {counts['unsupported']} unsupported, {counts['failed']} failed")
    print(f"run stats: {config.RUN_STATS_JSON.relative_to(config.ROOT)}")


def run_match() -> None:
    """Match the cached extractions. cache_only=True: this stage can never call the model."""
    catalog = load_catalog()
    index = build_index(catalog)
    as_of = config.AS_OF_DATE
    print(f"as-of date for the cross-reference staleness check: {as_of.isoformat()} | review threshold {config.REVIEW_THRESHOLD}")

    items = []
    for path in list_rfq_files():
        extracted = extract_rfq(path, catalog.customers, cache_only=True)
        if extracted.status != "ok":
            print(f"\n=== {extracted.rfq_id} ({path.name}) ===")
            print(f"status: {extracted.status}")
            print(f"reason: {extracted.reason}")
            items.append((extracted, []))
            continue
        matched = match_rfq(extracted, catalog, index, as_of)
        print_matched(extracted, matched)
        items.append((extracted, matched))

    lines, got, review = write_match_stats(items)
    print(f"\n{lines} lines: {got} matched, {lines - got} unmatched, {review} need review")
    print(f"run stats: {config.RUN_STATS_JSON.relative_to(config.ROOT)}")


def run_everything(refresh: bool) -> None:
    """The whole pipeline: extraction (cache first), matching, outputs/, review drafts, summary."""
    print("Ridgeline quote desk: extraction is served from the cache when it can be; the model is called only on a miss.")
    print(f"as-of date {config.AS_OF_DATE.isoformat()} | review threshold {config.REVIEW_THRESHOLD}\n")
    results, _ = run_all(refresh=refresh)
    write_run_stats([r.extracted for r in results])
    write_match_stats([(r.extracted, r.matched) for r in results])

    root = config.ROOT
    for r in results:
        source = f"{r.extracted.status}" if r.extracted.status != "ok" else f"{r.lines} lines"
        print(
            f"{r.path.name:<28} {source:<12} -> {r.json_path.relative_to(root)}, {r.review_path.relative_to(root)}"
        )
    print("\nSummary (auto-accepted = no review needed; every unreadable file is one review line)")
    print(format_summary([(r.output.rfq_id, r.lines, r.auto_accepted, r.to_review) for r in results]))
    print("\nNothing was sent to anyone. Every draft needs specialist approval.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m ridgeline.run")
    parser.add_argument("--stage", choices=["extract", "match", "catalog"], help="run one stage instead of the whole pipeline")
    parser.add_argument("--refresh", action="store_true", help="skip the extraction cache")
    args = parser.parse_args()

    if args.stage == "extract":
        run_extract(refresh=args.refresh)
    elif args.stage == "match":
        run_match()
    elif args.stage == "catalog":
        load_catalog(report=True)
        files = list_rfq_files()
        print(f"\nRFQ files in {config.RFQS_DIR.relative_to(config.ROOT)}/ ({len(files)}):")
        for path in files:
            print(f"  {path.stem:<28} {path.suffix}")
    else:
        run_everything(refresh=args.refresh)


if __name__ == "__main__":
    main()
