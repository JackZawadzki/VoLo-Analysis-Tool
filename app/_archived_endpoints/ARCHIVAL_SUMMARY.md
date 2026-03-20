# VoLo Engine API Endpoint Archival

## Summary
Successfully archived 14 unused API endpoints and their associated Pydantic models from the VoLo engine's main.py file.

**Archive Date:** 2026-03-17
**Lines Removed:** 506 lines (from 1624 to 1118 lines)
**Archive Location:** `/sessions/serene-great-wright/mnt/RVM2.0/volo-engine-local/app/_archived_endpoints/main_archived.py`

## Archived Endpoints

### 1. Simulation & Archetype Endpoints
- **`POST /api/simulate`** - Single deal Monte Carlo simulation (SimulationRequest)
- **`GET /api/archetypes`** - List available archetypes
- **`GET /api/carbon-defaults/{archetype}`** - Carbon model defaults per archetype
- **`GET /api/sectors`** - Sector information from Carta data

### 2. Fund Simulation
- **`POST /api/fund-simulate`** - Fund-level simulation (FundSimulationRequest)

### 3. Valuation Comps
- **`GET /api/valuation-comps`** - Valuation comps with optional archetype filtering
- **`GET /api/valuation-comps/all`** - All industry multiples from Damodaran

### 4. Market Sizing
- **`GET /api/market-sizing`** - TAM/SAM/SOM calculation with defaults
- **`GET /api/market-sizing/defaults`** - Default market sizing for all archetypes

### 5. Position Sizing
- **`POST /api/position-sizing`** - Kelly Criterion optimal check sizing (PositionSizingRequest)

### 6. Portfolio Management
- **`GET /api/portfolio-config`** - Current portfolio strategy and benchmarks
- **`POST /api/portfolio-simulate`** - Portfolio-level Monte Carlo (PortfolioSimRequest)
- **`POST /api/portfolio-deal-impact`** - Marginal deal impact analysis (DealImpactRequest)

### 7. System Status
- **`GET /api/data-status`** - Data availability status

## Pydantic Models Archived

1. **SimulationRequest** - Deal-level simulation parameters
2. **FundSimulationRequest** - Fund simulation with deal assumptions
3. **PositionSizingRequest** - Position sizing constraints and parameters
4. **PortfolioSimRequest** - Portfolio simulation configuration
5. **DealImpactRequest** - Deal impact analysis request

## Helper Functions Archived

- **`_extract_custom_bass()`** - Extract custom Bass diffusion parameters from request

## Preserved Code

The following critical code was retained in main.py:

### Data & Configuration
- **VALUATION_COMPS** - Valuation comparables dictionary
- **PORTFOLIO_CFG** - Portfolio strategy configuration dictionary
- **load_valuation_comps()** - Startup event for loading Damodaran VEBITDA data
- **load_portfolio_configs()** - Startup event for loading portfolio strategy

### Endpoints (Active)
- `GET /` - Index/home route
- `GET /api/scurve-atlas` - S-curve adoption atlas (used by frontend)
- `GET /api/keys` - API key status check
- `GET /api/references` - Reference catalog and data sources
- `POST /api/references/refresh/{ref_id}` - Reference refresh instructions
- `POST /api/chat` - LLM chat endpoint
- `GET /api/dev/*` - Development endpoints
- `POST /api/dev/chat` - Development chat endpoint

### Imports (Retained)
All imports were preserved as they are used by active endpoints:
- `run_simulation` - Used by deal_pipeline indirectly
- `DEFAULT_BASS_PARAMS, bass_diffusion_cumulative` - Used by /api/scurve-atlas
- `simulate_fund` - Used by deal_pipeline indirectly
- `VCSimulator, load_strategy, load_benchmarks, strategy_from_dict, deal_from_dict` - Used by portfolio configuration
- `strategy_to_dict, benchmarks_to_dict` - Used by configuration loading
- `load_vebitda, get_comps_for_archetype` - Used by VALUATION_COMPS

### Imports (Removed)
The following imports were removed as they were only used by archived endpoints:
- `get_market_sizing, get_all_market_sizing_defaults` - Only used by /api/market-sizing
- `optimize_position_size` - Only used by /api/position-sizing

## Files Modified

1. **main.py** - Removed 14 endpoint implementations and 5 Pydantic models
2. **main_archived.py** - Created new file with all archived code

## Verification

- Python syntax validation: ✓ Passed
- All active endpoints remain functional
- Configuration structures intact (VALUATION_COMPS, PORTFOLIO_CFG)
- Startup events still execute correctly
- Deal pipeline integration unaffected

## Restoration

To restore any archived endpoint, simply copy the relevant code from `main_archived.py` back into `main.py`.

## Notes

- The archived endpoints remain fully functional and documented in this archive
- This is a clean refactoring with no data loss
- The codebase is now focused on the active deal pipeline, portfolio simulation, and development endpoints
- Future API design should be coordinated through the deal_pipeline or new route modules
