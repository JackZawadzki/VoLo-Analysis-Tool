# Archived Endpoints

These files contain API endpoints that were removed from the live application on 2026-03-17
because they are not called from the frontend (app.js). They are preserved here for reference
in case they need to be restored.

## Contents

- `main_archived.py` — Legacy standalone endpoints from main.py:
  - POST /api/simulate (legacy single-deal simulation)
  - GET /api/archetypes
  - GET /api/carbon-defaults/{archetype}
  - GET /api/sectors
  - POST /api/fund-simulate (legacy fund simulation)
  - GET /api/valuation-comps
  - GET /api/valuation-comps/all
  - GET /api/market-sizing
  - GET /api/market-sizing/defaults
  - POST /api/position-sizing
  - GET /api/portfolio-config
  - POST /api/portfolio-simulate
  - POST /api/portfolio-deal-impact
  - GET /api/data-status

- `extraction_archived.py` — Company enrichment & financial model CRUD from extraction.py:
  - POST /api/companies/{cid}/enrich
  - POST /api/companies/{cid}/extract-financial-model
  - GET /api/companies/{cid}/financial-models
  - POST /api/companies/{cid}/financial-models
  - DELETE /api/companies/{cid}/financial-models/{fid}
  - GET /api/pipeline-status/{run_id}

- `deal_pipeline_archived.py` — Unused deal pipeline endpoints:
  - DELETE /api/deal-pipeline/report/{rid}
  - GET /api/deal-pipeline/portfolio-holdings
  - DELETE /api/deal-pipeline/portfolio-holdings/{hid}
  - GET /api/deal-pipeline/fund/summary

- `resources_archived.py` — Resource update/delete endpoints:
  - PUT /api/resources/{rid}
  - DELETE /api/resources/{rid}

- `auth_archived.py` — Admin promotion endpoint:
  - POST /api/auth/promote-admin
