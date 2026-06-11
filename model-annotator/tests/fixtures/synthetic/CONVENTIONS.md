# Synthetic fixture conventions

Each pathology lives in one generator module `gen_<name>.py` in this directory,
exposing:

```python
NAME = "<name>"                      # fixture id, matches the file name
def build(path: str) -> None: ...    # writes <name>.xlsx at `path` with openpyxl
```

and one expectations file `../../expected/<name>.yaml`:

```yaml
fixture: <name>
exit_code: 0            # expected annotate-model exit code (0 or 3)
trust_max: null         # optional: trust score must be <= this
trust_min: null         # optional: trust score must be >= this
findings_must_include:  # each entry must match >=1 finding in report.json
  - category: grant_dependence          # exact category string
    min_severity: high                  # info|medium|high|critical
    evidence_contains_any: ["P&L!C9"]   # >=1 of these substrings appears in evidence_cells
    title_contains: null                # optional substring of the finding title
findings_must_not_include:              # no finding may match any entry
  - category: arithmetic_integrity
acquittals_must_include:                # each must match >=1 acquittal (category or substring)
  - category: expense_growth_decoupled
max_high_critical: null                 # optional cap on count of high+critical findings
```

## Workbook style (keep fixtures realistic but small)

- One statements sheet named `P&L` (or `Model`), labels in column A, annual
  period headers (e.g. 2024..2031) in row 1 starting at column B.
- **Write every number the tool must read as a LITERAL value.** openpyxl does
  not calculate, so a formula cell it writes has NO cached value and the tool
  reads it as empty. Use formula cells ONLY where the *formula text itself* is
  the signal (orphaned schedules, solver/auto-raise structure, opex pegged to
  revenue, COGS→cost-tier reference chains) — and keep the workbook's total
  formula-cell count **under 40**, or the missing-cached-values data-quality
  gate fires and poisons your fixture with an unrelated critical finding.
  Read the rule you are targeting in `model_annotator/findings.py` (and
  `integrity.py` / `metrics.py`) first and target exactly what it reads.
- Use plain labels the mapper knows: "Total Revenue", "Total COGS",
  "Gross Profit", "Total Operating Expenses", "EBITDA", "Net Change in Cash",
  "Beginning Cash", "Ending Cash", "Equity Proceeds", component labels like
  "Payroll", "Rent", "Capital Equipment".
- Keep magnitudes plausible ($K-scale startups: revenue 100 → 50,000 over the
  horizon) so small-base logic behaves as in real models.

## Verifying your fixture

```sh
cd model-annotator
../.venv/bin/python -m pytest tests/test_synthetic.py -k <name> -x -q
```

The shared harness (tests/test_synthetic.py) builds the workbook, runs
`annotate()` in-process with `no_llm=True`, and asserts the expectations YAML.
Do NOT modify anything under `model_annotator/` — if the tool genuinely
mishandles your fixture, record the bug instead (your final output), with the
exact finding list the tool produced.
