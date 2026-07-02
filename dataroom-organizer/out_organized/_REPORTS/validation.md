# Validation & Reconciliation

## Reconciliation — is every original file accounted for?

- Original files catalogued: **40**
- Files placed in the reorganized copy: **40**
- Unaccounted-for files: **0**
- Missing at destination: **0**
- Corrupted at destination (hash changed): **0**
- Distinct content hashes — source 38 / copy 38
- Content hashes preserved: **True**

### Result: ✅ PASS — nothing lost


## Classification & placement

- Companies: **7**
- Average confidence (placed): **0.843**
- Dispositions: {'PENDING': 8, 'PLACED': 28, 'SUPERSEDED': 2, 'DUPLICATE': 2}

### Files per bucket

- 01 Investment Memos & IC: 5
- 02 Financing & Legal: 2
- 03 Cap Table & Equity: 5
- 04 Financials & Models: 2
- 05 Non-Dilutive & Grants: 1
- 06 Board & Governance: 2
- 07 Monitoring & Investor Updates: 4
- 08 Meeting Notes & Calls: 1
- 09 Commercial & Customers: 1
- 10 Technical & Diligence: 2
- 11 Impact & Climate: 1
- 12 Company Materials: 2
- 14 Historical / Superseded: 4
- 15 Unclassified / Pending Review: 8

## Duplicates, versions & conflicts

- Exact duplicates: **2** (kept once, copies preserved in Historical)
- Superseded versions moved to Historical: **2**
- Version conflicts needing a human: **0**

## Needs human review

Total flagged: **9** (company-unresolved: 6)

- `F00001` UNKNOWN / Master Investment Tracker 2024-01-05.xlsx — company-unresolved (conf 0.312)
- `F00002` UNKNOWN / Master Investment Tracker 2024-02-09.xlsx — company-unresolved (conf 0.312)
- `F00003` UNKNOWN / Master Investment Tracker 2024-03-15.xlsx — company-unresolved (conf 0.312)
- `F00004` UNKNOWN / Master Investment Tracker 2024-04-20.xlsx — company-unresolved (conf 0.312)
- `F00005` UNKNOWN / Master Investment Tracker 2024-05-18.xlsx — company-unresolved (conf 0.312)
- `F00006` UNKNOWN / Master Investment Tracker 2024-06-21.xlsx — company-unresolved (conf 0.312)
- `F00021` Cambium / random_unlabeled_thing.bin — unknown-format (conf 0.15)
- `F00037` Reframe / Reframe cap table.png — image-only (conf 0.688)
- `F00040` Reframe / notes.txt — low confidence (conf 0.562)

## Expected documents missing

- **UNKNOWN**: 01 Investment Memos & IC, 02 Financing & Legal, 03 Cap Table & Equity, 04 Financials & Models
- **Nth Cycle**: 02 Financing & Legal, 04 Financials & Models
- **Veckta**: 02 Financing & Legal, 04 Financials & Models
- **Hestia**: 01 Investment Memos & IC, 02 Financing & Legal, 03 Cap Table & Equity, 04 Financials & Models
- **NxLite**: 01 Investment Memos & IC, 04 Financials & Models
- **Reframe**: 02 Financing & Legal