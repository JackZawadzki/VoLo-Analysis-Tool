# Required Data Files

Place these files in this directory (`data/sources/`). They are gitignored
because they contain large binary data and/or confidential financial models.

## Required

| File | Source | Used For |
|------|--------|----------|
| `Carta Insights_Fund Forecasting Profiles.xlsx` | Carta | Round sizes, graduation rates, pre-money valuations by stage/sector |
| `Annual Tech Baseline 2024_v3_Workbook.xlsx` | NREL | LCOE projections, technology cost benchmarks |
| `VEBITDA - PubComps.xls` | Damodaran (Stern NYU) | Public company EV/EBITDA multiples by industry |

## Optional (Company Validation)

| File | Source | Used For |
|------|--------|----------|
| `XGS Enterprise Model_Confidential_2026.02.17.xlsx` | XGS Energy | Geothermal company financial model validation |
| `Copy of Refiant Financial Model_ Volo.xlsx` | Refiant | AI/Privacy LLM financial model validation |

## Reference Only

| File | Source | Used For |
|------|--------|----------|
| `lazards-lcoeplus-june-2025-_vf.pdf` | Lazard | LCOE+ benchmark reference (parsed separately) |
| `data-appendix-electrification-pathways-063025.xlsx` | DOE | Electrification pathways reference data |
