# Ridgeline Quote Desk

## Repository

https://github.com/FernandoSanabriaFSL/ridgeline-quote-desk-fde-ai-ml

## How to Run

To be completed during the recorded session.

## Approach

To be completed during the recorded session.

## Evaluation

The evaluation scores the pipeline's own output files against the answer key. `python evaluation/evaluate.py` reads `outputs/*.json` and `data/answer_key/` (it is the only code that reads the key), prints a report and writes `evaluation/results.json`. It imports nothing from the pipeline except `REVIEW_THRESHOLD`, so the pipeline and the evaluation share one threshold.

### How the metrics are computed

The answer key covers rfq_01, rfq_02 and rfq_03, 19 lines in all. Lines are aligned by line number. A line missing from the output, or an extra one, counts as an error and is listed in `results.json`.

- **extraction_accuracy**: the share of answer-key lines whose aligned output line has the same quantity and the same unit (null equals null).
- **match_precision**: correct part numbers divided by the lines where we output a part number. A part number on a line the key expects to be null counts as wrong.
- **match_recall**: correct part numbers divided by the answer-key lines with a non-null expected part number.
- **Lines the key expects to be null** (not carried, needs review) are left out of the recall denominator. Returning null with `needs_review` true on them is reported as `expected_null_handled`. A part number returned for one of them is a precision error.

The additional metrics in `results.json` are computed the same way from the same alignment:

- **Fields**: quantity, unit and `as_written` accuracy (`as_written` matches if either normalized string contains the other), and the line count per RFQ.
- **Method and flags**: `match_method` accuracy on correctly matched lines, and flag recall for the key's flags. `discontinued_replaced` and `not_carried` must appear in the output flags exactly; `needs_human_review` counts as met when `needs_review` is true.
- **Review and auto-accept**: the review rate on the labeled lines and on all processed lines with the photo stub excluded, and auto-accept precision. A line is wrong if its part number (null counts), quantity or unit differs from the key.
- **Confidence and routing**: mean confidence of correct matches, of wrong matches and of matches carrying an ambiguity flag (`variant_inferred`, `close_alternative`, `weak_description_match`, `discontinued_replaced`) across all six RFQs, and routing violations, meaning lines with no part or a confidence below the threshold that have `needs_review` false. That count must be 0.
- **Threshold sweep and errors**: described below.

### Results

These are the values in `evaluation/results.json` at `REVIEW_THRESHOLD` 0.80, with cross-reference dates checked as of 2026-11-25 (`AS_OF_DATE`; set it to reproduce a run on a later day). Extraction is served from the committed cache, so the numbers reproduce without an API key.

| Metric | Value | Basis |
|---|---|---|
| extraction_accuracy | 1.0000 | 19 of 19 key lines with the right quantity and unit |
| match_precision | 1.0000 | 17 of 17 output part numbers correct |
| match_recall | 1.0000 | 17 of 17 expected part numbers found |
| expected_null_handled | 1.0000 | 2 of 2 (null part, `needs_review` true) |
| quantity, unit, `as_written` accuracy | 1.0000 each | 19 key lines; line counts match in all 3 RFQs |
| match_method accuracy on correct matches | 1.0000 | 17 of 17 |
| flag recall | 1.0000 | 3 of 3 (`discontinued_replaced`, `needs_human_review`, `not_carried`) |
| review rate, labeled lines | 0.1579 | 3 of 19 |
| review rate, all processed lines (stub excluded) | 0.3929 | 11 of 28 |
| auto-accept precision | 1.0000 | 16 lines auto-accepted, 0 wrong |
| mean confidence, correct matches | 0.9188 | 17 matches |
| mean confidence, wrong matches | none | there are no wrong matches |
| mean confidence, ambiguity-flagged matches | 0.7000 | 3 matches across all six RFQs |
| routing violations | 0 | no line without a part, or below 0.80, skips review |

### Threshold

`REVIEW_THRESHOLD` is 0.80. The sweep re-applies the pipeline's routing rule at each threshold: a line is auto-accepted if it has a part number, a confidence at or above the threshold and no flags. The last column shows how many ambiguity-flagged matches would pass if confidence alone decided.

| Threshold | Labeled lines auto-accepted | Auto-accepted errors | Review rate, all lines | Ambiguity-flagged lines passing on confidence alone |
|---|---|---|---|---|
| 0.50 | 16 | 0 | 39.3% | 3 |
| 0.55 | 16 | 0 | 39.3% | 3 |
| 0.60 | 16 | 0 | 39.3% | 3 |
| 0.65 | 16 | 0 | 39.3% | 3 |
| 0.70 | 16 | 0 | 39.3% | 2 |
| 0.75 | 16 | 0 | 39.3% | 1 |
| **0.80** | 16 | 0 | 39.3% | 0 |
| 0.85 | 16 | 0 | 39.3% | 0 |
| 0.90 | 15 | 0 | 42.9% | 0 |
| 0.95 | 5 | 0 | 82.1% | 0 |

Read as review load against auto-accept errors: the review rate stays at 39.3% from 0.50 to 0.85, then rises to 42.9% at 0.90 and 82.1% at 0.95, while auto-accepted errors are 0 at every threshold. Raising the threshold past 0.85 only adds review work. The reasons for 0.80:

- **It is the lowest value on the sweep at which confidence alone stops every ambiguous line.** The three ambiguity-flagged matches score 0.65 (`variant_inferred`), 0.70 (`close_alternative`) and 0.75 (`discontinued_replaced`). The flags already send them to review; the threshold is a second safety net behind the flags.
- **It adds no review load on this data.** The review rate at 0.80 is the same as at 0.50.
- **It leaves a margin below the weakest correct match.** The lowest correct unflagged match scores 0.89 (rfq_03 line 5, a cross-reference hit with a "check thickness" note and a 2022 verification date), 0.09 above the threshold.

Because no threshold produces an auto-accepted error on 19 labeled lines, the sweep cannot show what a different threshold would save in errors. 0.80 rests on the gap between the highest ambiguous score (0.75) and the lowest correct one (0.89), and it should be re-checked on a larger set (see "Growing the set").

### Error analysis

We missed no answer-key line. All 19 lines aligned, none was missing or extra, every expected part number was found, both expected-null lines went to review with no part, and all three flags the key asks for were present. The error list in `results.json` is empty.

The three closest calls are the labeled lines matched by description with the smallest margin over the runner-up. (Cross-reference and RL-number matches have no competing candidate.) All three were correct, and none came near the 0.05 margin that would cap confidence at 0.70 and send a line to review.

| Line | Request | Chosen (score) | Runner-up (score) | Margin |
|---|---|---|---|---|
| rfq_01 line 4 | 1/2-13 x 2 in grade 8 hex bolts | RL165435 (1.000) | RL118726, the same bolt at 3 in (0.860) | 0.140 |
| rfq_03 line 3 | 1/2 in brass ball valve, full port | RL172063 (1.000) | RL193447, the 316 stainless 1/2 in valve (0.826) | 0.174 |
| rfq_01 line 2 | A42 v-belts | RL112676 (1.000) | RL182397, the B42 belt (0.810) | 0.190 |

rfq_05 and rfq_06 have no answer key, so these judgment calls are not scored. They are where a specialist's judgment matters, and each one is flagged for review:

- **Amended lines kept in review (rfq_05 lines 1 and 2).** The forwarded reply changed the motor from 3 HP to 5 HP and the gaskets from 20 to 40, in prose. The matches themselves are clear (RL110322 at 1.000 against 0.838, RL118373 at 1.000 against 0.809, confidence 0.92), but `amended_in_thread` keeps both in review: a spec changed in a forwarded reply is worth a 30-second check, because misreading it means quoting the wrong motor.
- **The inferred 6205 variant (rfq_06 line 4).** "6205 sealed bearings" names the model number but no part code. RL104165 (6205-2RS) scores 1.000 against 0.743 for RL112280 (6205-ZZ), and "sealed" points to the 2RS version, but the code is inferred, so confidence is capped at 0.65 with `variant_inferred` and the alternative named in the note.
- **The gloves chosen by unit (rfq_06 line 2).** "nitrile gloves L - a few boxes" ties at 1.000 between cut-resistant gloves sold by the pair (RL131850) and disposable gloves sold by the box (RL153354). The written unit, boxes, matches the disposable gloves, so they are chosen. Confidence is 0.70 with `close_alternative`, and the quantity is missing.
- **The 300 bolts converted to 3 boxes (rfq_06 line 1).** The line gives 300 and no unit, against a product sold as a box of 100. Because 300 is at least the pack size and divides evenly, it becomes 3 BX, flagged `unit_converted` with the arithmetic in the note. The customer also wrote that they usually get boxes, so the reading is ambiguous and a specialist confirms it.

### Small sample

19 labeled lines (17 with an expected part number) cannot support strong conclusions. One wrong line would move precision, and recall, by about 6 points (1 in 17). The labeled set also covers one RFQ per format (email, PDF, spreadsheet) and no photo. Perfect scores here do not predict perfect scores in production.

### Growing the set

- **Source**: past RFQs paired with the specialist-approved final quotes in the quoting system. The approved lines are the labels.
- **Target**: 300 RFQs (about 2,000 lines), stratified by format, customer and competitor, with 50 RFQs frozen as a test set that is never used for tuning.
- **Cadence**: backfill in the first 2 weeks, with a specialist spot-checking 10% of the labels. Then add 20 reviewed RFQs a week from production, and re-check the threshold monthly.

## Production

To be completed during the recorded session.
