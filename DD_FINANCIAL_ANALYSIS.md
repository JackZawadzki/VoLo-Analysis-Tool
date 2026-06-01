# DD Financial Analysis — Rework Reference

Handoff doc for the **DD Financial Analysis** section of the VoLo Analysis Tool
(`/Users/jackzawadzki/Downloads/VoLo-Analysis-Tool`, branch `main`). Read this
plus the auto-loaded project memory before changing anything.

---

## 1. What this section is

An **interactive underwriting tool**. An analyst picks a saved deal report, sets
**Conservative / Base / Best** values on one shared set of assumptions, clicks
**Run Simulation**, and sees how the investment outcome changes across the three
cases — plus which assumptions drive that change.

It is the **testing ground** for a unified sensitivity/variance engine. If the
analyst likes it, the corrected methodology will later be **ported into the deal
report** sections (Risk Assessment / Sensitivity / Variance Drivers). **Do not
touch the deal report yet** — that's a separate, later phase.

It is **NOT** project finance. It's a venture/investment model: revenue → margin →
exit-multiple → MOIC/IRR, all-equity, deterministic. (An XGS Series-B project-finance
Excel was used only as a *reference* for output style and the two-way grid idea.)

## 2. Design philosophy (non-negotiable)

- **Everything must mean something and be math-backed.** No fabricated effects, no
  hidden magic. The tool is a transparent calculator + sensitivity engine; its
  output is exactly as good as the analyst's inputs.
- **Deterministic** (three fixed cases), **all-equity** (no debt), **N/A-tolerant**
  (any input can be marked N/A and is dropped from the math, never assumed).
- Each input only affects the outputs it mathematically feeds; inert levers are
  labelled "n/a" for the current metric/basis rather than shown as a fake 0.

## 3. Architecture & key files

| Layer | File | What |
|---|---|---|
| Engine | `app/engine/scenario_analysis.py` | All math. `build_pnl_projection` (P&L+DCF, N/A-tolerant, collapsed opex, breakeven/burn, `ev_by_year`), `compute_deal_returns` (ownership/dilution → MOIC/IRR, POS→expected, exit-year EV), `run_dd_analysis` (orchestrator), `_dd_decompose` (sensitivity + Base→case bridges), `_dd_two_way_grid`, `_dd_metric_value`, `trl_profile`. |
| Route | `app/routes/dd_analysis.py` | `POST /api/dd/analyze` (`DDAnalyzeRequest`), `GET /api/dd/trl-profile/{trl}`. Old `/compute`,`/defaults`,`/scenarios` still exist, unused, backward-compatible. |
| Frontend HTML | `app/templates/index.html` | `#tab-ddanalysis` block (~lines 1343–1450). |
| Frontend JS | `app/static/app.js` | The `dd*`/`_dd*` block (~lines 11580–12350). State `_dd`, schema `DD_FIELDS`, `_apiFetch`, render fns. **Do not touch `_fd*` (Fund Deployment) or `_ddr*` (DDR PDF) — different features.** |
| CSS | `app/static/styles.css` | `.dd-*` classes (mostly appended at the end). |

Nothing outside the DD module imports the engine fns — **the blast radius is the DD
section only.** `app.js`/`index.html`/`styles.css` are single shared files, so a JS
syntax error or unbalanced `<div>` breaks every tab; always `node --check app/static/app.js`
and keep the `#tab-ddanalysis` div balanced.

## 4. The math (how inputs map to outputs)

```
Revenue:  pre-launch years = 0 (Time to Launch); commercial yr1 = Year-1 Revenue;
          growth tapers from Revenue CAGR toward Terminal Growth each year.
Exit EV:  EV/Revenue  -> ExitRevenue x Multiple        (margins/opex NOT used)
          EV/EBITDA   -> ExitEBITDA  x Multiple         (margins/opex ARE used)
          (Multiple N/A -> Gordon terminal off FCF)
          ev_by_year[t] lets an explicit Exit Year value the company at year t.
Ownership: entry = Check/(PreMoney+Round); exit = entry x (1-Dilution)^Rounds
Returns:  MOIC = EV x exit_ownership / Check
          Expected MOIC = POS x MOIC + (1-POS) x Recovery
          IRR = MOIC^(1/Hold) - 1   (Hold = pre-launch + commercial years)
DCF Value = Σ PV(FCF) + PV(terminal);  FCF = NI + D&A - Capex - ΔNWC
Cash to Breakeven = |min(cumulative FCF)| (peak burn)
```

Which inputs are "live" depends on the **Primary Metric**:
- **Expected MOIC / MOIC**: revenue, exit multiple, dilution, POS (+ margins/opex only on EV/EBITDA).
- **IRR**: the above **+ Time to Launch / Exit Year** (they change the hold).
- **DCF Value**: **+ discount rate, terminal growth, margins, opex, capex, tax, NWC**.
- **Cash to Breakeven**: revenue ramp, margins, opex, capex.

**Sensitivity ("What Moves the Outcome"):** for each lever, the exact change in the
chosen metric when *only* that lever moves Conservative→Best (others at Base), by
re-running the deterministic model. Impact% = share of total movement. Interactions
shown as their own bar. **Exact, no estimation.**

**Bridges ("How Each Case Is Built"):** Base→Conservative and Base→Best waterfalls;
each lever's contribution + an interaction residual sum *exactly* to the case
outcome (verified to the cent). This is the answer to "how do outcomes change per case."

**Two-way grid:** metric across Exit Multiple × Revenue CAGR (the two axes a
multiple-based return responds to; discount rate is deliberately not an axis because
MOIC doesn't depend on it).

**TRL seeder:** `trl_profile(trl)` derives POS / time-to-launch / exit-multiple
discount from the calibrated tables (`adoption.TRL_PARAMETERS`, `dilution.TRL_MODIFIERS`).
TRL **seeds defaults only — it is deliberately NOT a sensitivity driver** (its
effects are already captured by POS/launch/multiple; making it a driver double-counts).

## 5. Locked decisions

- Hybrid core input set; deterministic 3-case; all-equity.
- Primary-metric selector (don't hide inert inputs — let the analyst choose the lens).
- **DCF is "indicative" for early-stage** (terminal-value dominated = the exit-multiple
  bet discounted; hyper-sensitive to discount rate). Flagged in UI; not the default.
- Sensitivity/variance run on the **deterministic DD model**, not Monte Carlo →
  noise-free (this is the fix for the deal report's noisy MC-based sensitivity).

## 6. Known limitations / honest caveats

- DCF is a cross-check, not a primary valuation (above).
- Interactions bucket can be large — the model is multiplicative, so levers compound;
  shown separately rather than smeared in. This is correct, not a bug.
- P&L is intentionally coarse (single %-of-revenue ramps).
- "Current" column populates from `report.deal_overview` where present; the preview
  test reports have empty JSON so it shows "—".

## 7. Run / preview

- Launch config: `.claude/launch.json` → `preview_start volo-engine` (uvicorn :8000).
- Preview login: `preview@voloearth.com` / `Preview1234!` (password was set into the
  **gitignored** `data/rvm.db`; not pushed). 4 demo reports exist (Mitra Chem,
  Sublime Systems, Type One Energy, Plain).
- **Gotchas:** (a) JWT secret regenerates per server process → re-login after restart.
  (b) Stale uvicorn procs can serve old code → `pkill -f "uvicorn app.main"` then restart.
  (c) **Cache-bust:** `index.html` loads `styles.css?v=NNN` and `app.js?v=NNN` — **bump
  both when you change those files** or browsers serve stale assets (this caused a
  "charts render as text blocks" bug). Currently at `v=202`.

## 8. State & what's next

- **Pushed:** commit `410ff6e` on `origin/main` (core v2.1 + DCF caveat).
- **Uncommitted in working tree (push after review):** cache-bust v=202, IC-memo-style
  report search selector, polished empty-state card, segmented "View case:" pills,
  TRL note on its own line, `$M` sign fix.
- **Commit only the 5 DD files** (engine, route, app.js, styles.css, index.html).
  Pre-existing `volomind/*` changes are unrelated — never `git add -A`.
- **Eventual goal:** once approved, port the corrected sensitivity/variance methodology
  into the deal report. Possible polish: widen the Primary-metric `<select>` (clips
  its label); add goal-seek (entry price for target MOIC).

## 9. How the user works (important)

Rigorous, finance-fluent, wants **honesty over agreement** (explicitly: "do not take my
bias into account"), wants claims **proven empirically** (read code / run scripts, don't
hand-wave), and works **review-before-push** — explain, plan, ask, then build; get an
explicit OK before pushing major changes.
