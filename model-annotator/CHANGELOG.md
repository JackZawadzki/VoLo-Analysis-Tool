# Changelog

All schema changes to `report.json` require an entry here (see Integration requirements).

## 0.2.0 — 2026-06-11

- `report.json` schema_version **1.1** (additive, backward compatible):
  new optional top-level `analysis_plan` — the WHY layer. Carries the business
  archetype, rationale, risks to probe, metric-family priorities, and any
  LLM-directed custom computations (with executed/skip status).
- New planner phase (1.5): with an API key, the LLM reads workbook STRUCTURE
  (sheet names, row labels, sections — never cell values), classifies the
  business, selects benchmark archetype, and may request up to 5 extra ratio
  computations that the deterministic engine executes with full provenance
  (`llm_directed::*` metric ids). Row tokens that don't exist in the workbook
  are dropped with a recorded skip reason. `--no-llm` uses a deterministic
  heuristic plan; behavior is unchanged from 0.1.0 in that mode.
- Valuation benchmark findings now key off the plan's archetype.

## 0.1.0 — 2026-06-10

- Initial release. `report.json` schema_version **1.0**.
- Pipeline phases 0–8: ingest, structure map, assumptions census, integrity tie-outs,
  derived metrics, candidate findings, acquittal pass, severity/cause-dedup, narrate & emit.
- Outputs: `report.json`, `report.md`, `workbook_map.json`, `derived_metrics.csv`.
- Deterministic core; optional Anthropic LLM assist (`--no-llm` fully supported).
