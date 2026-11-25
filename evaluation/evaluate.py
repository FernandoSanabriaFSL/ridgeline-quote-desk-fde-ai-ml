#!/usr/bin/env python3
"""Evaluation: score the pipeline's outputs/ against the answer key.

This is the only code that reads data/answer_key/. Everything is computed from the JSON files in
outputs/. It imports nothing from the pipeline except the shared review threshold.

Run: python evaluation/evaluate.py     (writes evaluation/results.json and prints a report)
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ridgeline.config import REVIEW_THRESHOLD  # noqa: E402  (one threshold for pipeline and evaluation)

ANSWER_KEY_DIR = ROOT / "data" / "answer_key"
OUTPUTS_DIR = ROOT / "outputs"
RESULTS_JSON = ROOT / "evaluation" / "results.json"

AMBIGUITY_FLAGS = ("variant_inferred", "close_alternative", "weak_description_match", "discontinued_replaced")
THRESHOLDS = [round(0.50 + 0.05 * i, 2) for i in range(10)]  # 0.50 to 0.95 in steps of 0.05
HUMAN_REVIEW_FLAG = "needs_human_review"  # in the key it means "this line must go to review"
STUB_FLAG = "stub"


# --- Helpers ---------------------------------------------------------------------------------

def load_dir(directory: Path) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))}


def rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def mean(values: list[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def norm_text(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def contains_either(a: str | None, b: str | None) -> bool:
    """Normalized containment in either direction (lowercase, letters and digits only)."""
    x, y = norm_text(a), norm_text(b)
    return bool(x) and bool(y) and (x in y or y in x)


def rounded(value):
    """Round every float to 4 decimals, recursively."""
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rounded(v) for v in value]
    return value


@dataclass
class Row:
    """One line number of one RFQ: the key's line and the pipeline's line (either can be missing)."""
    rfq: str
    line: int
    exp: dict | None
    got: dict | None

    def part_correct(self) -> bool:
        return bool(self.exp and self.got and self.exp["expected_part_number"] is not None
                    and self.got["ridgeline_part_number"] == self.exp["expected_part_number"])

    def part_wrong(self) -> bool:
        """A part number was output and it is not the key's (including a part where the key expects null)."""
        if not self.got or self.got["ridgeline_part_number"] is None:
            return False
        return not self.exp or self.got["ridgeline_part_number"] != self.exp["expected_part_number"]

    def quantity_ok(self) -> bool:
        return bool(self.exp and self.got and self.exp["quantity"] == self.got["quantity"])

    def unit_ok(self) -> bool:
        return bool(self.exp and self.got and self.exp["unit"] == self.got["unit"])

    def line_correct(self) -> bool:
        """Right part (or right null), quantity and unit. A line that is not in the key is never correct."""
        if not (self.exp and self.got):
            return False
        same_part = self.got["ridgeline_part_number"] == self.exp["expected_part_number"]
        return same_part and self.quantity_ok() and self.unit_ok()


def build_rows(keys: dict[str, dict], outs: dict[str, dict]) -> list[Row]:
    rows = []
    for rfq, key in keys.items():
        exp_lines = {l["line"]: l for l in key["lines"]}
        got_lines = {l["line"]: l for l in outs.get(rfq, {}).get("lines", [])}
        for number in sorted(set(exp_lines) | set(got_lines)):
            rows.append(Row(rfq, number, exp_lines.get(number), got_lines.get(number)))
    return rows


def auto_accepted(line: dict, threshold: float) -> bool:
    """The routing rule at a given threshold: a part, confidence at or above it, and no flags."""
    return (line["ridgeline_part_number"] is not None and line["confidence"] is not None
            and line["confidence"] >= threshold and not line["flags"])


# --- Metrics ---------------------------------------------------------------------------------

def evaluate(keys: dict[str, dict], outs: dict[str, dict]) -> dict:
    rows = build_rows(keys, outs)
    key_rows = [r for r in rows if r.exp is not None]
    aligned = [r for r in rows if r.exp is not None and r.got is not None]
    output_rows = [r for r in rows if r.got is not None]
    errors: list[dict] = []

    def err(row: Row, field: str, expected, got) -> None:
        errors.append({"rfq": row.rfq, "line": row.line, "field": field, "expected": expected, "got": got})

    # Alignment errors, then field errors
    for r in rows:
        if r.got is None:
            err(r, "line", "present", None)
        elif r.exp is None:
            err(r, "line", None, "present")
    for r in aligned:
        if not r.quantity_ok():
            err(r, "quantity", r.exp["quantity"], r.got["quantity"])
        if not r.unit_ok():
            err(r, "unit", r.exp["unit"], r.got["unit"])
        if r.exp["expected_part_number"] != r.got["ridgeline_part_number"]:
            err(r, "ridgeline_part_number", r.exp["expected_part_number"], r.got["ridgeline_part_number"])
        if not contains_either(r.exp["as_written"], r.got["as_written"]):
            err(r, "as_written", r.exp["as_written"], r.got["as_written"])
        if r.part_correct() and r.exp["match_method"] != r.got["match_method"]:
            err(r, "match_method", r.exp["match_method"], r.got["match_method"])

    # Required metrics
    extraction_ok = sum(1 for r in key_rows if r.quantity_ok() and r.unit_ok())
    with_part = [r for r in output_rows if r.got["ridgeline_part_number"] is not None]
    correct = sum(1 for r in with_part if r.part_correct())
    expected_parts = [r for r in key_rows if r.exp["expected_part_number"] is not None]
    recalled = sum(1 for r in expected_parts if r.part_correct())

    # Lines the key expects to be null
    expected_null = [r for r in key_rows if r.exp["expected_part_number"] is None]
    null_handled = [r for r in expected_null
                    if r.got and r.got["ridgeline_part_number"] is None and r.got["needs_review"] is True]
    for r in expected_null:
        if r not in null_handled and r.got and r.got["ridgeline_part_number"] is None:
            err(r, "needs_review", True, r.got["needs_review"])

    # Per-field accuracy
    line_counts = {}
    for rfq, key in keys.items():
        got = len(outs.get(rfq, {}).get("lines", []))
        line_counts[rfq] = {"expected": len(key["lines"]), "got": got, "match": got == len(key["lines"])}
    as_written_ok = sum(1 for r in aligned if contains_either(r.exp["as_written"], r.got["as_written"]))

    # match_method on correctly matched lines
    correct_rows = [r for r in with_part if r.part_correct()]
    method_ok = sum(1 for r in correct_rows if r.exp["match_method"] == r.got["match_method"])

    # Flag recall for the key's flags
    per_flag: dict[str, list[bool]] = {}
    for r in aligned:
        for flag in r.exp["flags"] or []:
            met = bool(r.got["needs_review"]) if flag == HUMAN_REVIEW_FLAG else flag in r.got["flags"]
            per_flag.setdefault(flag, []).append(met)
            if not met:
                err(r, "flags", flag, r.got["flags"] if flag != HUMAN_REVIEW_FLAG else "needs_review=false")
    all_flags = [m for v in per_flag.values() for m in v]
    flag_recall = {
        flag: {"expected": len(v), "met": sum(v), "recall": rate(sum(v), len(v))} for flag, v in sorted(per_flag.items())
    }
    flag_recall["overall"] = {"expected": len(all_flags), "met": sum(all_flags), "recall": rate(sum(all_flags), len(all_flags))}

    # Review rate and auto-accept
    reviewed_labeled = sum(1 for r in aligned if r.got["needs_review"])
    processed = [(rfq, l) for rfq, o in outs.items() for l in o["lines"] if STUB_FLAG not in l["flags"]]
    reviewed_all = sum(1 for _, l in processed if l["needs_review"])
    auto_rows = [r for r in output_rows if not r.got["needs_review"]]
    auto_correct = sum(1 for r in auto_rows if r.line_correct())

    # Mean confidence
    wrong_rows = [r for r in with_part if r.part_wrong()]
    ambiguity_lines = [(rfq, l) for rfq, o in outs.items() for l in o["lines"]
                       if l["ridgeline_part_number"] is not None and any(f in l["flags"] for f in AMBIGUITY_FLAGS)]

    def conf_stats(confidences: list[float | None]) -> dict:
        values = [c for c in confidences if c is not None]
        return {"mean": mean(values), "n": len(values)}

    # Routing violations, over every line of every RFQ
    violations = []
    for rfq, o in outs.items():
        for l in o["lines"]:
            no_part = l["ridgeline_part_number"] is None
            low = l["confidence"] is not None and l["confidence"] < REVIEW_THRESHOLD
            if (no_part or low) and not l["needs_review"]:
                violations.append({"rfq": rfq, "line": l["line"], "reason": "no part number" if no_part else "confidence below threshold"})

    # Threshold sweep
    sweep = []
    for t in THRESHOLDS:
        accepted = [r for r in output_rows if auto_accepted(r.got, t)]
        sweep.append({
            "threshold": t,
            "labeled_lines_auto_accepted": len(accepted),
            "auto_accepted_errors": sum(1 for r in accepted if not r.line_correct()),
            "review_rate_all_lines": rate(sum(1 for _, l in processed if not auto_accepted(l, t)), len(processed)),
            "ambiguity_flagged_passing_on_confidence_alone": sum(
                1 for _, l in ambiguity_lines if l["confidence"] is not None and l["confidence"] >= t
            ),
        })

    additional = {
        "expected_null_handled": {"handled": len(null_handled), "total": len(expected_null), "rate": rate(len(null_handled), len(expected_null))},
        "per_field_accuracy": {
            "quantity": rate(sum(1 for r in key_rows if r.quantity_ok()), len(key_rows)),
            "unit": rate(sum(1 for r in key_rows if r.unit_ok()), len(key_rows)),
            "as_written": rate(as_written_ok, len(key_rows)),
            "line_count_per_rfq": line_counts,
        },
        "match_method_accuracy_on_correct_matches": {"correct": method_ok, "total": len(correct_rows), "rate": rate(method_ok, len(correct_rows))},
        "flag_recall": flag_recall,
        "review_rate": {
            "labeled_lines": {"reviewed": reviewed_labeled, "total": len(aligned), "rate": rate(reviewed_labeled, len(aligned))},
            "all_processed_lines_excluding_stub": {"reviewed": reviewed_all, "total": len(processed), "rate": rate(reviewed_all, len(processed))},
        },
        "auto_accept": {
            "labeled_lines_auto_accepted": len(auto_rows),
            "correct": auto_correct,
            "precision": rate(auto_correct, len(auto_rows)),
            "wrong_lines_auto_accepted": len(auto_rows) - auto_correct,
        },
        "mean_confidence": {
            "correct_matches": conf_stats([r.got["confidence"] for r in correct_rows]),
            "wrong_matches": conf_stats([r.got["confidence"] for r in wrong_rows]),
            "ambiguity_flagged_matches_all_rfqs": conf_stats([l["confidence"] for _, l in ambiguity_lines]),
        },
        "routing_violations": {"count": len(violations), "lines": violations},
        "threshold_sweep": sweep,
        "errors": sorted(errors, key=lambda e: (e["rfq"], e["line"], e["field"])),
        "definitions": {
            "labeled_rfqs": sorted(keys),
            "alignment": "answer-key lines are matched to output lines by line number; a missing or extra line is an error",
            "wrong_line": "a line whose part number (null counts), quantity or unit differs from the key; a line not in the key is wrong",
            "routing_at_threshold": "auto-accepted = a part number, confidence at or above the threshold, and no flags (the pipeline's rule)",
            "ambiguity_flags": list(AMBIGUITY_FLAGS),
            "as_written": "match if either normalized string (lowercase, letters and digits only) contains the other",
        },
    }
    return rounded({
        "threshold": REVIEW_THRESHOLD,
        "extraction_accuracy": rate(extraction_ok, len(key_rows)) or 0.0,
        "match_precision": rate(correct, len(with_part)) or 0.0,
        "match_recall": rate(recalled, len(expected_parts)) or 0.0,
        "additional_metrics": additional,
        "_counts": {  # for the printed report only; removed before writing
            "key_lines": len(key_rows), "extraction_ok": extraction_ok, "with_part": len(with_part),
            "correct": correct, "expected_parts": len(expected_parts), "recalled": recalled,
        },
    })


# --- Report ----------------------------------------------------------------------------------

def pct(value) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def show(value) -> str:
    return "null" if value is None else (json.dumps(value) if not isinstance(value, str) else value)


def print_report(results: dict, counts: dict) -> None:
    am = results["additional_metrics"]
    print(f"EVALUATION  (review threshold {results['threshold']}; labeled RFQs: {', '.join(am['definitions']['labeled_rfqs'])})\n")
    print("Required metrics")
    print(f"  extraction_accuracy  {pct(results['extraction_accuracy'])}   {counts['extraction_ok']}/{counts['key_lines']} key lines with the right quantity and unit")
    print(f"  match_precision      {pct(results['match_precision'])}   {counts['correct']}/{counts['with_part']} output part numbers are correct")
    print(f"  match_recall         {pct(results['match_recall'])}   {counts['recalled']}/{counts['expected_parts']} expected part numbers were found")

    print("\nAdditional metrics")
    n = am["expected_null_handled"]
    print(f"  expected-null lines handled (null part, needs_review true)  {n['handled']}/{n['total']}  rate {pct(n['rate'])}")
    f = am["per_field_accuracy"]
    print(f"  per-field accuracy  quantity {pct(f['quantity'])} | unit {pct(f['unit'])} | as_written {pct(f['as_written'])}")
    for rfq, c in f["line_count_per_rfq"].items():
        print(f"    line count {rfq}: expected {c['expected']}, got {c['got']}  {'ok' if c['match'] else 'MISMATCH'}")
    m = am["match_method_accuracy_on_correct_matches"]
    print(f"  match_method accuracy on correct matches  {m['correct']}/{m['total']}  rate {pct(m['rate'])}")
    print("  flag recall (the key's flags)")
    for flag, v in am["flag_recall"].items():
        print(f"    {flag:<24} {v['met']}/{v['expected']}  recall {pct(v['recall'])}")
    r = am["review_rate"]
    print(f"  review rate  labeled lines {r['labeled_lines']['reviewed']}/{r['labeled_lines']['total']} = {pct(r['labeled_lines']['rate'])}"
          f" | all processed lines (stub excluded) {r['all_processed_lines_excluding_stub']['reviewed']}/{r['all_processed_lines_excluding_stub']['total']} = {pct(r['all_processed_lines_excluding_stub']['rate'])}")
    a = am["auto_accept"]
    print(f"  auto-accept  lines {a['labeled_lines_auto_accepted']} | correct {a['correct']} | precision {pct(a['precision'])} | wrong lines that would be auto-accepted {a['wrong_lines_auto_accepted']}")
    c = am["mean_confidence"]
    for label, key in (("correct matches", "correct_matches"), ("wrong matches", "wrong_matches"), ("ambiguity-flagged matches, all RFQs", "ambiguity_flagged_matches_all_rfqs")):
        print(f"  mean confidence, {label:<36} {pct(c[key]['mean'])}  (n={c[key]['n']})")
    print(f"  routing violations (no part or confidence below threshold, needs_review false)  {am['routing_violations']['count']}")
    for v in am["routing_violations"]["lines"]:
        print(f"    {v['rfq']} line {v['line']}: {v['reason']}")

    print("\nThreshold sweep (auto-accept = part, confidence >= threshold, no flags)")
    head = ["threshold", "labeled auto-accepted", "auto-accepted errors", "review rate (all lines)", "ambiguity-flagged passing on confidence alone"]
    print("  " + " | ".join(head))
    for s in am["threshold_sweep"]:
        marker = "  <- pipeline threshold" if s["threshold"] == results["threshold"] else ""
        cells = [f"{s['threshold']:.2f}", str(s["labeled_lines_auto_accepted"]), str(s["auto_accepted_errors"]),
                 pct(s["review_rate_all_lines"]), str(s["ambiguity_flagged_passing_on_confidence_alone"])]
        print("  " + " | ".join(cell.center(len(h)) for cell, h in zip(cells, head)) + marker)

    errors = am["errors"]
    print(f"\nErrors ({len(errors)})")
    if not errors:
        print("  none")
    for e in errors:
        print(f"  {e['rfq']} line {e['line']} | {e['field']} | expected {show(e['expected'])} | got {show(e['got'])}")


def main() -> None:
    keys = load_dir(ANSWER_KEY_DIR)
    outs = load_dir(OUTPUTS_DIR)
    if not keys:
        sys.exit(f"no answer key files found in {ANSWER_KEY_DIR}")
    if not outs:
        sys.exit(f"no output files found in {OUTPUTS_DIR}; run python -m ridgeline.run first")
    results = evaluate(keys, outs)
    counts = results.pop("_counts")
    RESULTS_JSON.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print_report(results, counts)
    print(f"\nwrote {RESULTS_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
