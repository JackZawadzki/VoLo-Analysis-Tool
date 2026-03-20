"""
VoLo Engine — End-to-End Test Suite
Tests all API endpoints, auth flow, simulation engines, and data loading.
Run:  python -m tests.e2e_test
"""
import sys
import os
import json
import time
import traceback

# Ensure we can import the app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0
ERRORS = []

def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✓  {name}")
    except Exception as e:
        FAIL += 1
        ERRORS.append((name, str(e), traceback.format_exc()))
        print(f"  ✗  {name}: {e}")


def run_all():
    global PASS, FAIL, ERRORS

    print("\n" + "=" * 60)
    print("  VoLo Engine — End-to-End Test Suite")
    print("=" * 60)

    # ── 1. Module imports ──────────────────────────────────────────
    print("\n[1] Module Imports")

    def test_import_fastapi():
        import fastapi
        assert fastapi.__version__
    test("Import FastAPI", test_import_fastapi)

    def test_import_numpy():
        import numpy as np
        assert np.__version__
    test("Import NumPy", test_import_numpy)

    def test_import_scipy():
        from scipy import stats
        assert stats
    test("Import SciPy", test_import_scipy)

    def test_import_pydantic():
        import pydantic
        assert pydantic.__version__
    test("Import Pydantic", test_import_pydantic)

    def test_import_uvicorn():
        import uvicorn
        assert uvicorn
    test("Import Uvicorn", test_import_uvicorn)

    def test_import_jwt():
        import jwt
        assert jwt
    test("Import PyJWT", test_import_jwt)

    def test_import_werkzeug():
        from werkzeug.security import generate_password_hash
        assert generate_password_hash
    test("Import Werkzeug", test_import_werkzeug)

    def test_import_anthropic():
        import anthropic
        assert anthropic
    test("Import Anthropic SDK", test_import_anthropic)

    def test_import_openpyxl():
        import openpyxl
        assert openpyxl
    test("Import openpyxl", test_import_openpyxl)

    def test_import_weasyprint():
        import weasyprint
        assert weasyprint
    test("Import WeasyPrint", test_import_weasyprint)

    # ── 2. Engine modules ──────────────────────────────────────────
    print("\n[2] Engine Module Imports")

    def test_engine_adoption():
        from app.engine.adoption import DEFAULT_BASS_PARAMS, bass_diffusion_cumulative
        assert len(DEFAULT_BASS_PARAMS) >= 15
    test("Adoption engine (Bass diffusion, 17 archetypes)", test_engine_adoption)

    def test_engine_monte_carlo():
        from app.engine.monte_carlo import run_simulation
        assert callable(run_simulation)
    test("Monte Carlo engine", test_engine_monte_carlo)

    def test_engine_fund_model():
        from app.engine.fund_model import simulate_fund
        assert callable(simulate_fund)
    test("Fund model engine", test_engine_fund_model)

    def test_engine_dilution():
        from app.engine.dilution import simulate_dilution
        assert callable(simulate_dilution)
    test("Dilution engine", test_engine_dilution)

    def test_engine_position_sizing():
        from app.engine.position_sizing import optimize_position_size
        assert callable(optimize_position_size)
    test("Position sizing (Kelly Criterion)", test_engine_position_sizing)

    def test_engine_market_sizing():
        from app.engine.market_sizing import get_market_sizing
        assert callable(get_market_sizing)
    test("Market sizing (TAM/SAM/SOM)", test_engine_market_sizing)

    def test_engine_valuation_comps():
        from app.engine.valuation_comps import load_vebitda
        assert callable(load_vebitda)
    test("Valuation comps (Damodaran)", test_engine_valuation_comps)

    def test_engine_carbon():
        from app.engine.rvm_carbon import RVMCarbonModel
        assert RVMCarbonModel
    test("Carbon model (RVM)", test_engine_carbon)

    def test_engine_extraction():
        from app.engine.extraction import extract_deck_fields
        assert callable(extract_deck_fields)
    test("Extraction engine (Claude AI)", test_engine_extraction)

    def test_engine_financial_pipeline():
        from app.engine.financial_pipeline import run_financial_extraction
        assert callable(run_financial_extraction)
    test("Financial pipeline (Excel extraction)", test_engine_financial_pipeline)

    def test_engine_portfolio():
        from app.engine.portfolio import VCSimulator, load_strategy, load_benchmarks
        assert VCSimulator
    test("Portfolio simulator (VCSimulator)", test_engine_portfolio)

    # ── 3. Database ────────────────────────────────────────────────
    print("\n[3] Database")

    def test_db_init():
        from app.database import startup
        startup()
    test("Database init + seed", test_db_init)

    def test_db_tables():
        from app.database import get_db
        db = get_db()
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        db.close()
        for t in ['users', 'companies', 'displaced_resources', 'success_criteria', 'deal_reports']:
            assert t in tables, f"Missing table: {t}"
    test("All tables exist", test_db_tables)

    def test_db_seed_resources():
        from app.database import get_db
        db = get_db()
        count = db.execute("SELECT COUNT(*) FROM displaced_resources WHERE is_builtin=1").fetchone()[0]
        db.close()
        assert count >= 15, f"Only {count} built-in resources"
    test(f"Seeded resources (>=15 built-in)", test_db_seed_resources)

    def test_db_seed_criteria():
        from app.database import get_db
        db = get_db()
        count = db.execute("SELECT COUNT(*) FROM success_criteria WHERE is_builtin=1").fetchone()[0]
        db.close()
        assert count >= 2, f"Only {count} built-in criteria"
    test(f"Seeded success criteria", test_db_seed_criteria)

    # ── 4. Auth flow ───────────────────────────────────────────────
    print("\n[4] Auth Flow")

    def test_auth_register():
        from app.database import get_db
        db = get_db()
        db.execute("DELETE FROM users WHERE username='test_e2e'")
        db.commit()
        db.close()
        from app.auth import register, RegisterRequest
        result = register(RegisterRequest(username="test_e2e", email="test@e2e.com", password="testpass123"))
        assert "token" in result
        assert result["user"]["username"] == "test_e2e"
    test("Register new user", test_auth_register)

    def test_auth_login():
        from app.auth import login, LoginRequest
        result = login(LoginRequest(username="test_e2e", password="testpass123"))
        assert "token" in result
    test("Login existing user", test_auth_login)

    def test_auth_token_decode():
        from app.auth import login, LoginRequest, decode_token
        result = login(LoginRequest(username="test_e2e", password="testpass123"))
        payload = decode_token(result["token"])
        assert payload is not None
        assert payload["user"] == "test_e2e"
    test("Token decode", test_auth_token_decode)

    def test_auth_bad_login():
        from app.auth import login, LoginRequest
        from fastapi import HTTPException
        try:
            login(LoginRequest(username="test_e2e", password="wrongpass"))
            assert False, "Should have raised"
        except HTTPException as e:
            assert e.status_code == 401
    test("Reject bad password", test_auth_bad_login)

    # ── 5. Data loading ────────────────────────────────────────────
    print("\n[5] Data Loading")

    def test_data_loader():
        from app.data.loader import load_all
        data = load_all()
        assert "carta_rounds" in data
        assert "archetypes" in data
    test("load_all() completes", test_data_loader)

    def test_carta_data():
        from app.data.loader import load_all
        data = load_all()
        sectors = data.get("carta_rounds", {})
        assert len(sectors) >= 3, f"Only {len(sectors)} Carta sectors"
    test(f"Carta data loaded", test_carta_data)

    def test_atb_data():
        from app.data.loader import load_all
        data = load_all()
        atb = data.get("atb_lcoe", {})
        assert len(atb) >= 1, "No ATB data"
    test(f"NREL ATB data loaded", test_atb_data)

    def test_valuation_comps_data():
        from pathlib import Path
        source = Path(__file__).resolve().parent.parent / "data" / "sources" / "VEBITDA - PubComps.xls"
        if source.exists():
            from app.engine.valuation_comps import load_vebitda
            comps = load_vebitda(str(source))
            assert len(comps.get("relevant", {})) >= 5
            print(f"       ({len(comps.get('relevant', {}))} relevant industries)")
        else:
            print("       (skipped — file not present)")
    test("Damodaran VEBITDA comps", test_valuation_comps_data)

    # ── 6. Simulation engines ──────────────────────────────────────
    print("\n[6] Simulation Engines")

    def test_bass_diffusion():
        import numpy as np
        from app.engine.adoption import bass_diffusion_cumulative
        t = np.arange(0, 26, dtype=float)
        curve = bass_diffusion_cumulative(t, 0.01, 0.3, 1.0)
        assert len(curve) == 26
        assert curve[0] < 0.02
        assert curve[-1] > 0.5
    test("Bass diffusion S-curve (25yr)", test_bass_diffusion)

    def test_monte_carlo_sim():
        from app.data.loader import load_all
        from app.engine.monte_carlo import run_simulation
        data = load_all()
        t0 = time.time()
        result = run_simulation(
            archetype="utility_solar", tam_millions=50000, trl=5,
            entry_stage="Seed", check_size_millions=2.0, pre_money_millions=15.0,
            sector_profile="Energy + Deep Tech",
            carta_data=data.get("carta_rounds", {}),
            penetration_share=(0.005, 0.03), exit_multiple_range=(5.0, 15.0),
            exit_year_range=(5, 10), n_simulations=1000, random_seed=42,
        )
        elapsed = (time.time() - t0) * 1000
        assert "summary" in result
        assert result["summary"]["n_simulations"] == 1000
        assert result["summary"]["survival_rate"] > 0
        print(f"       (1000 sims in {elapsed:.0f}ms, survival={result['summary']['survival_rate']:.1%})")
    test("Monte Carlo deal sim (1000 paths)", test_monte_carlo_sim)

    def test_fund_simulation():
        from app.data.loader import load_all
        from app.engine.monte_carlo import run_simulation
        from app.engine.fund_model import simulate_fund
        data = load_all()
        deal = run_simulation(
            archetype="battery_storage_utility", tam_millions=80000, trl=5,
            entry_stage="Seed", check_size_millions=2.0, pre_money_millions=15.0,
            sector_profile="Energy + Deep Tech",
            carta_data=data.get("carta_rounds", {}),
            penetration_share=(0.002, 0.02), exit_multiple_range=(5.0, 15.0),
            exit_year_range=(5, 10), n_simulations=500, random_seed=42,
        )
        t0 = time.time()
        result = simulate_fund(
            fund_size_m=100, n_deals=25, avg_check_m=2.0,
            management_fee_pct=0.02, carry_pct=0.20, hurdle_rate=0.08,
            fund_life_years=10, investment_period_years=4,
            deal_moic_distribution=deal.get("_raw_moic", []),
            deal_exit_year_distribution=deal.get("_raw_exit_years", []),
            recycling_rate=0.10, n_simulations=200, random_seed=42,
        )
        elapsed = (time.time() - t0) * 1000
        assert "tvpi" in result
        assert "irr" in result
        print(f"       (200 fund sims in {elapsed:.0f}ms, median TVPI={result['tvpi'].get('p50', 'N/A')})")
    test("Fund-level simulation (J-curve, DPI, TVPI)", test_fund_simulation)

    def test_portfolio_simulator():
        from app.engine.portfolio import VCSimulator, load_strategy, load_benchmarks
        from pathlib import Path
        configs = Path(__file__).resolve().parent.parent / "configs"
        cfg = load_strategy(str(configs / "strategy.json"))
        bench = load_benchmarks(str(configs / "carta_benchmarks.json"))
        sim = VCSimulator(cfg, bench=bench)
        t0 = time.time()
        out = sim.run(n_portfolios=200, seed=42)
        elapsed = (time.time() - t0) * 1000
        assert "p50" in out
        assert "irrs" in out
        print(f"       (200 portfolios in {elapsed:.0f}ms)")
    test("Portfolio simulator (VCSimulator)", test_portfolio_simulator)

    def test_position_sizing():
        from app.engine.position_sizing import optimize_position_size
        import numpy as np
        rng = np.random.default_rng(42)
        moic = rng.lognormal(0.5, 1.2, 1000)
        result = optimize_position_size(
            moic_distribution=moic.tolist(), check_size_m=2.0, pre_money_m=15.0,
            fund_size_m=100.0, n_deals=25, mgmt_fee_pct=2.0,
            reserve_pct=30.0, max_concentration_pct=15.0,
            entry_stage="Seed", survival_rate=0.3, moic_conditional_mean=3.0,
            exit_year_range=(5, 10),
        )
        assert "optimal_check_m" in result or "grid" in result or "kelly" in result
    test("Position sizing (Kelly + grid search)", test_position_sizing)

    def test_market_sizing():
        from app.engine.market_sizing import get_market_sizing
        result = get_market_sizing("battery_storage_utility")
        assert "tam" in result
        assert "sam" in result
        assert "som" in result
    test("Market sizing defaults", test_market_sizing)

    # ── 7. FastAPI app integration ─────────────────────────────────
    print("\n[7] FastAPI App Integration")

    def test_app_creation():
        from app.main import app
        assert app.title == "VoLo Underwriting Engine"
        routes = [r.path for r in app.routes]
        assert "/" in routes
        assert "/api/simulate" in routes or any("/simulate" in r for r in routes)
    test("FastAPI app instantiates", test_app_creation)

    def test_app_routes():
        from app.main import app
        routes = [r.path for r in app.routes]
        expected = ["/", "/api/simulate", "/api/archetypes", "/api/sectors",
                    "/api/fund-simulate", "/api/scurve-atlas", "/api/valuation-comps",
                    "/api/market-sizing", "/api/portfolio-simulate",
                    "/api/portfolio-deal-impact", "/api/data-status",
                    "/api/auth/register", "/api/auth/login"]
        missing = [e for e in expected if e not in routes]
        assert not missing, f"Missing routes: {missing}"
    test("All API routes registered", test_app_routes)

    def test_starlette_test_client():
        try:
            from starlette.testclient import TestClient
            from app.main import app
            client = TestClient(app)

            # Health check
            r = client.get("/api/data-status")
            assert r.status_code == 200
            data = r.json()
            assert "carta_sectors" in data
            print(f"       (Carta: {len(data['carta_sectors'])} sectors, ATB: {len(data['atb_technologies'])} techs)")

            # Homepage
            r = client.get("/")
            assert r.status_code == 200
            assert "VoLo" in r.text

            # Auth register
            r = client.post("/api/auth/register", json={
                "username": "e2e_http", "email": "e2e_http@test.com", "password": "password123"
            })
            if r.status_code == 409:
                # Already exists, login instead
                r = client.post("/api/auth/login", json={
                    "username": "e2e_http", "password": "password123"
                })
            assert r.status_code == 200
            token = r.json()["token"]
            headers = {"Authorization": f"Bearer {token}"}

            # Archetypes
            r = client.get("/api/archetypes")
            assert r.status_code == 200

            # Sectors
            r = client.get("/api/sectors")
            assert r.status_code == 200

            # S-curve atlas
            r = client.get("/api/scurve-atlas")
            assert r.status_code == 200
            assert "archetypes" in r.json()

            # Simulate deal
            r = client.post("/api/simulate", json={
                "company_name": "E2E Test Co",
                "archetype": "utility_solar",
                "tam_millions": 50000,
                "trl": 5,
                "entry_stage": "Seed",
                "check_size_millions": 2.0,
                "pre_money_millions": 15.0,
                "n_simulations": 500,
                "random_seed": 42,
            })
            assert r.status_code == 200
            sim = r.json()
            assert "summary" in sim
            print(f"       (Deal sim: p50 MOIC={sim.get('moic_conditional', {}).get('median', 'N/A')})")

            # Market sizing
            r = client.get("/api/market-sizing?archetype=battery_storage_utility")
            assert r.status_code == 200

            # Data status
            r = client.get("/api/data-status")
            assert r.status_code == 200

            print(f"       ✓ All HTTP endpoints passed")

        except ImportError:
            print("       (starlette.testclient not available — skipping HTTP tests)")
    test("HTTP endpoint integration (TestClient)", test_starlette_test_client)

    # ── 8. Frontend files ──────────────────────────────────────────
    print("\n[8] Frontend Files")

    def test_frontend_html():
        from pathlib import Path
        html = (Path(__file__).resolve().parent.parent / "app" / "templates" / "index.html").read_text()
        assert 'id="auth-overlay"' in html
        assert 'id="auth-register-form"' in html
        assert 'class="auth-hidden"' not in html, "auth-hidden class should be removed from register form"
        assert 'style="display:none"' in html, "Register form should use inline style"
        assert 'onclick="showAuthTab(\'register\')"' in html
        assert 'onclick="doRegister()"' in html
        assert 'app.js?v=51' in html, "JS cache bust not updated"
        assert 'styles.css?v=51' in html, "CSS cache bust not updated"
    test("HTML auth structure correct", test_frontend_html)

    def test_frontend_js():
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text()
        assert "function showAuthTab" in js
        assert "function doRegister" in js
        assert "function doLogin" in js
        assert "display = 'block'" in js or "display = tab" in js, "showAuthTab should set display to block"
        assert "classList.remove('auth-hidden')" in js, "Should remove auth-hidden class as safety"
    test("JS auth functions present and correct", test_frontend_js)

    def test_frontend_css():
        from pathlib import Path
        css = (Path(__file__).resolve().parent.parent / "app" / "static" / "styles.css").read_text()
        assert ".auth-overlay" in css
        assert ".auth-card" in css
        assert ".auth-tab" in css
    test("CSS auth styles present", test_frontend_css)

    def test_static_logo():
        from pathlib import Path
        logo = Path(__file__).resolve().parent.parent / "app" / "static" / "logo.png"
        assert logo.exists(), "logo.png missing"
        assert logo.stat().st_size > 100, "logo.png too small"
    test("Logo file exists", test_static_logo)

    # ── Summary ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"  Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)

    if ERRORS:
        print("\n  Failed tests:")
        for name, err, tb in ERRORS:
            print(f"\n  ✗ {name}")
            print(f"    {err}")

    print()
    return FAIL == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
