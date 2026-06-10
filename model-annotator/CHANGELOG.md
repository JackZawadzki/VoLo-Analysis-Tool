# Changelog

All schema changes to `report.json` require an entry here (see Integration requirements).

## 0.1.0 — 2026-06-10

- Initial release. `report.json` schema_version **1.0**.
- Pipeline phases 0–8: ingest, structure map, assumptions census, integrity tie-outs,
  derived metrics, candidate findings, acquittal pass, severity/cause-dedup, narrate & emit.
- Outputs: `report.json`, `report.md`, `workbook_map.json`, `derived_metrics.csv`.
- Deterministic core; optional Anthropic LLM assist (`--no-llm` fully supported).
