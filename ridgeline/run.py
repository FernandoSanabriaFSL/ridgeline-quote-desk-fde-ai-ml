"""Entry point: python -m ridgeline.run [--stage extract] [--refresh]

Without --stage: prints the catalog data-quality report and lists the RFQ files.
--stage extract: runs the extraction stage on every RFQ and prints the result.
"""
import argparse
import json
import textwrap
from pathlib import Path

from ridgeline import config
from ridgeline.catalog import load_catalog
from ridgeline.extract import ExtractedRfq, extract_rfq


def list_rfq_files() -> list[Path]:
    """Regular files in the RFQ folder, sorted by name. Hidden files such as .DS_Store are skipped."""
    return sorted(p for p in config.RFQS_DIR.iterdir() if p.is_file() and not p.name.startswith("."))


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
    payload = {
        "stage": "extract",
        "model": config.LLM_MODEL,
        "prompt_version": config.PROMPT_VERSION,
        "inference_geo_requested": config.LLM_INFERENCE_GEO,
        "metrics_source": (
            "latency, tokens, inference_geo and attempts come from the live API call that produced "
            "each result; cached says whether the most recent run reused it"
        ),
        "rfqs": per_rfq,
    }
    config.EVALUATION_DIR.mkdir(parents=True, exist_ok=True)
    config.RUN_STATS_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m ridgeline.run")
    parser.add_argument("--stage", choices=["extract"], help="run a pipeline stage")
    parser.add_argument("--refresh", action="store_true", help="skip the extraction cache")
    args = parser.parse_args()

    if args.stage == "extract":
        run_extract(refresh=args.refresh)
        return

    load_catalog(report=True)
    files = list_rfq_files()
    print(f"\nRFQ files in {config.RFQS_DIR.relative_to(config.ROOT)}/ ({len(files)}):")
    for path in files:
        print(f"  {path.stem:<28} {path.suffix}")


if __name__ == "__main__":
    main()
