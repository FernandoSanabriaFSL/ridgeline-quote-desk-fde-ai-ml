DRAFT: needs specialist approval, not sent to the customer

RFQ: rfq_06_messy (source: data/rfqs/rfq_06_messy.txt)
Customer: C1008 Granite Peak Mining

| Line | As written | Qty | Unit | Ridgeline part | Catalog description | Method | Confidence | Status | Reasons |
|---|---|---|---|---|---|---|---|---|---|
| 1 | bolts 3/8 x 1 1/2 gr5 - 300 (we usually get boxes) | 3 | BX | RL142504 | Hex cap screw 3/8-16 x 1-1/2 in, grade 5, zinc plated, box of 100 | description | 0.920 | REVIEW | unit_converted |
| 2 | nitrile gloves L - a few boxes | - | BX | RL153354 | Disposable nitrile gloves, 5 mil, powder-free, size L, box of 100 | description | 0.700 | REVIEW | confidence 0.700 is below 0.80, quantity_missing, close_alternative |
| 3 | NF-778812 x 4 (whatever you have thats equivalent) | 4 | - | - | - | - | - | REVIEW | no Ridgeline part matched, unknown_part_number |
| 4 | 6205 bearings sealed qty 24 | 24 | EA | RL104165 | Deep groove ball bearing 6205-2RS, rubber sealed both sides | description | 0.650 | REVIEW | confidence 0.650 is below 0.80, variant_inferred |
| 5 | forklift tires 18x7-8 x4 | 4 | - | - | - | - | - | REVIEW | no Ridgeline part matched, not_carried |
| 6 | o-rings 214 buna | - | PK | RL165909 | O-ring AS568-214, Buna-N 70A, pack of 25 | description | 0.920 | REVIEW | quantity_missing |

## Notes for the specialist

- Line 1: Customer says they usually get boxes; unclear whether 300 is pieces or boxes; no unit written; 300 treated as pieces: 300 / 100 per BX = 3 BX
- Line 2: Amount given only as 'a few'; customer unit BX matches RL153354 (BX) but not RL131850 (PR); scores 1.000 and 1.000 are within 0.12, so RL153354 was taken; close alternative RL131850 (PR, 1.000)
- Line 3: Customer will accept an equivalent item; NF-778812 is not in the cross-reference and the line has no description
- Line 4: the request names 6205 but no part code; RL104165 (2rs) chosen, alternative RL112280 (zz)
- Line 5: best candidate RL175880 (Anti-seize compound, copper, 8 oz brush-top) scores 0.300
- Line 6: No quantity given

## Non-item rows

- sent from my phone

Auto-accept means a Ridgeline part was matched with confidence of at least 0.80 and no flags. Cross-reference dates were checked as of 2026-11-25.
