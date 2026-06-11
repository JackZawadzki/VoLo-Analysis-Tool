# model-annotator

Automated financial-model diligence. Point it at a startup's Excel model and it
performs the analysis a senior VC analyst performs by hand when annotating a
model: maps the workbook, verifies its internal arithmetic, derives the analyst
metrics the model's structure supports, and surfaces findings — with evidence
cells, quantified impact, and the question a partner would ask management.

Standalone tool; built to be wired into a larger underwriting engine
(stable versioned JSON schema, importable API, zero side effects outside its
output directory).

## The methodology (what the tool replicates)

Distilled from manual passes over real hardware/climate models:

- **The six questions.** Every analysis answers: how fast is it growing, at
  what unit economics, funded by whom, converting to cash how, obeying its own
  arithmetic where, and measured against what physical reality. Metric
  selection flows from the model's structure, not a fixed checklist.
- **Tie-outs gate trust.** Internal identities are checked first (cash
  roll-forward, statement sums, cross-sheet mirrors, printed check rows). If
  the arithmetic fails, that is the lead finding and everything downstream is
  reported `trust_degraded`.
- **Cause, not echo.** When five cells display the same underlying problem,
  the flag goes on the one closest to the source; downstream symptoms fold in
  as supporting evidence.
- **Investigate before flagging.** Every candidate anomaly gets a
  decomposition attempt (ex-capex, small-base, grant-timing, tax-trend) before
  it may become a finding. Acquittals are reported explicitly — they calibrate
  trust.
- **Hidden-flattery hunting.** Purpose-built rules for the archetypes that
  matter in practice: grant revenue smoothing a commercial decline, market
  share quoted on a flattering capacity basis, products priced at one cost
  tier but costed at a cheaper one, taxes booked as refunds in loss years,
  auto-raise solvers that make "never runs out of cash" unfalsifiable, support
  costs frozen while revenue grows 700×, depreciation computed but orphaned
  while capex is expensed.
- **Clean checks count.** Things the model gets right are reported too.

## Install

```sh
pip install -e .            # core (openpyxl, pydantic, pyyaml)
pip install -e ".[llm]"     # + optional Anthropic LLM assist
pip install -e ".[dev]"     # + pytest
```

Python ≥ 3.10. Synchronous, single-process, no prints in library code.

## CLI

```sh
annotate-model path/to/model.xlsx
  --out DIR          # output directory (default ./annotations/<model>-<timestamp>/)
  --json-only        # skip the markdown report
  --no-llm           # heuristics-only mode, no API calls, no network
  --benchmarks FILE  # override the default benchmarks.yaml
  --max-cells N      # safety cap, default 2,000,000
  --verbose
```

Exit codes: `0` success · `2` parse failure (corrupt/encrypted file) ·
`3` the model's cash identities fail materially (outputs are still written).

Outputs written to the out dir:

| File | Contents |
|---|---|
| `report.json` | versioned, schema-validated machine output (`schema_version: "1.0"`) |
| `report.md` | the human report (header → read-this-first → tie-outs → findings → acquittals → clean checks → appendices → limitations) |
| `workbook_map.json` | sheet roles, period axes, units, row→ontology mappings — reusable by downstream tools |
| `derived_metrics.csv` | tidy rows: `metric_id, label, sheet, period, value, formula_inputs, computation` |
| `phase*_*.json` | per-phase intermediates for inspectability |

## API

```python
from model_annotator import annotate

report = annotate("model.xlsx", no_llm=True)        # typed pydantic Report
report.workbook_map.trust_score
[f.title for f in report.findings]

# parse once, analyze many: reuse a prior run's workbook map
report2 = annotate("model.xlsx", workbook_map=report.workbook_map)
```

`annotate()` never modifies the input file and writes nothing outside the out
dir (`write_outputs=False` writes nothing at all).

## LLM assist (optional, Anthropic)

Set `ANTHROPIC_API_KEY` and install the `[llm]` extra. The LLM (default model
`claude-opus-4-8`, override with `MODEL_ANNOTATOR_LLM_MODEL`) is used for
exactly two things: classifying ambiguous row labels into the ontology, and
polishing finding narratives. It never performs arithmetic and never supplies
a number — a post-pass verifies every numeral in polished text against the
template text and falls back on any mismatch. `--no-llm` produces a complete
report with zero network access.

## Benchmarks file

`model_annotator/benchmarks.yaml` holds external reference ranges (exit
multiples, margin ranges, discount rates, receivables scenarios). Everything
sourced from it is rendered with the literal prefix **"External benchmark (not
from workbook):"** — external knowledge never mixes silently with workbook
facts. Pass `--benchmarks your.yaml` to use your house view.

## Anti-hallucination guarantees

1. Every numeric claim traces to a workbook cell or a Python computation whose
   inputs are workbook cells; provenance is stored on every metric/finding.
2. A final validator re-reads the workbook and asserts every cited cell exists
   and matches its cited value; violations are flagged in `limitations` and
   enforced in the test suite.
3. Mapping confidence is reported everywhere; findings built on low-confidence
   mappings are severity-capped with the uncertainty stated.
4. What cannot be determined is said plainly (`limitations`), not guessed.

## report.json schema (1.0)

Top level: `schema_version, tool_version, source_file, sha256, analyzed_at,
llm_used, workbook_map{sheets, period_axis, primary_statement_sheet, units,
row_mappings, named_ranges, has_macros, hidden_sheets, input_color_convention,
trust_score}, assumptions_census[], tie_outs[], derived_metrics[], findings[],
acquittals[], clean_checks[], unmapped[], limitations[]`.
Defined precisely in `model_annotator/schema.py` (pydantic). Any change
requires a `schema_version` bump and a `CHANGELOG.md` entry.

## Testing

```sh
python -m pytest                      # unit tests + synthetic-pathology fixtures
```

`tests/fixtures/synthetic/` holds generators that each embed one known
pathology (broken cash roll, grant-smoothed decline, cost-tier mismatch,
frozen support costs, runway cliff, one-way margins, $K/$1 units pair,
transposed periods, an acquittal case, and a clean model that must produce
zero high/critical findings). Real models can be dropped into
`tests/fixtures/real/` (gitignored, confidential) and run through the CLI.
`--no-llm` runs are deterministic (byte-identical modulo timestamps).
