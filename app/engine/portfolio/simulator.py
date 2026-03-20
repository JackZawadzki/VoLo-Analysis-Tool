"""
Vectorized VC fund Monte Carlo simulator.

Simulates a portfolio of N companies across P parallel universes with:
- 6 company archetypes (3 software, 3 hardware) with logistic growth
- Years-to-Commercial-Launch (YCL) based failure model
- Reserve deployment across years 2-4 with concentration control
- EV/Revenue and EV/EBITDA exit valuation modes
- Deal impact analysis (marginal lift from adding a proposed deal)
"""
from __future__ import annotations
from typing import Dict, Optional, Tuple
import numpy as np

from .config import StrategyConfig, DealConfig
from .benchmarks import Benchmarks
from .irr import irr_many

import logging as _logging
_logger = _logging.getLogger(__name__)


def _load_damodaran_multiples() -> Optional[Dict[str, float]]:
    """
    Attempt to load Damodaran EV/EBITDA acquisition multiples.
    Returns {simulator_archetype: acq_ev_ebitda_mean} or None if unavailable.
    """
    try:
        from ..valuation_comps import load_vebitda, get_simulator_ev_ebitda_multiples
        comps = load_vebitda()
        if comps.get("error"):
            _logger.warning("Damodaran comps unavailable: %s", comps["error"])
            return None
        return get_simulator_ev_ebitda_multiples(comps)
    except Exception as e:
        _logger.warning("Could not load Damodaran comps: %s", e)
        return None


class VCSimulator:
    """Vectorized VC fund simulator with EV/Revenue and EV/EBITDA modes."""

    def __init__(self, cfg: StrategyConfig, bench: Optional[Benchmarks] = None,
                 company_overrides: Optional[dict] = None,
                 damodaran_multiples: Optional[Dict[str, float]] = None):
        self.C = cfg
        self.bench = bench
        self.company_overrides = company_overrides
        self.ages = np.arange(1, cfg.years + 1, dtype=int)
        self.stage_mult = np.array(cfg.mark_stage_mult, dtype=float)

        # Per-archetype EV/EBITDA multiples from Damodaran public comps.
        # If not provided, attempt auto-load; fall back to strategy.json defaults.
        if damodaran_multiples is not None:
            self.damodaran = damodaran_multiples
        elif cfg.mode == "ev_ebitda":
            self.damodaran = _load_damodaran_multiples()
        else:
            self.damodaran = None

        self.ARCH = {
            "software.networked": dict(m_scale=(0.35, 0.50), k_range=(0.9, 1.2), x_range=(2.5, 3.5)),
            "software.saas":      dict(m_scale=(0.25, 0.40), k_range=(0.5, 0.8), x_range=(3.0, 4.0)),
            "software.market":    dict(m_scale=(0.30, 0.50), k_range=(0.8, 1.1), x_range=(2.5, 3.5)),
            "hardware.infra":     dict(m_scale=(0.25, 0.45), k_range=(0.3, 0.6), x_range=(4.5, 6.5)),
            "hardware.consumer":  dict(m_scale=(0.35, 0.55), k_range=(0.7, 1.0), x_range=(3.0, 4.0)),
            "hardware.modular":   dict(m_scale=(0.30, 0.50), k_range=(0.5, 0.8), x_range=(4.0, 5.0)),
        }
        self.SW_KEYS = np.array(["software.networked", "software.saas", "software.market"])
        self.HW_KEYS = np.array(["hardware.infra", "hardware.modular", "hardware.consumer"])
        self.SW_PROBS = np.array([0.33, 0.34, 0.33])
        self.HW_PROBS = np.array([0.4, 0.3, 0.3])
        self.floor_val = 10_000_000.0

    @staticmethod
    def logistic(t, m, k, x):
        return np.where(t >= 0, m / (1.0 + np.exp(-k * (t - x))), 0.0)

    def _pick_types_models(self, rng: np.random.Generator, P: int, N: int) -> Tuple[np.ndarray, np.ndarray]:
        if self.company_overrides is None:
            is_hw = rng.random((P, N)) < 0.5
            sw_choice = rng.choice(len(self.SW_KEYS), size=(P, N), p=self.SW_PROBS)
            hw_choice = rng.choice(len(self.HW_KEYS), size=(P, N), p=self.HW_PROBS)
            keys = np.where(is_hw, self.HW_KEYS[hw_choice], self.SW_KEYS[sw_choice])
            return keys, is_hw

        companies = self.company_overrides
        if len(companies) != N:
            raise ValueError(f"Company overrides has {len(companies)} entries but strategy nc={N}")

        key_1 = []
        is_hw_1 = []
        for co in companies:
            ctype = co.get("type", "software")
            model = co.get("model", "saas")
            is_hw_1.append(ctype == "hardware")
            if ctype == "software":
                if model in ("networked", "network"):
                    key_1.append("software.networked")
                elif model in ("marketplace", "market"):
                    key_1.append("software.market")
                else:
                    key_1.append("software.saas")
            else:
                if model == "infra":
                    key_1.append("hardware.infra")
                elif model == "consumer":
                    key_1.append("hardware.consumer")
                else:
                    key_1.append("hardware.modular")

        keys = np.tile(np.array(key_1, dtype=object), (P, 1))
        is_hw = np.tile(np.array(is_hw_1), (P, 1))
        return keys, is_hw

    def _sample_som(self, rng, P, N, is_hw) -> np.ndarray:
        if self.company_overrides is not None:
            som1 = np.array([co.get("som", 50e6 if co.get("type") == "hardware" else 60e6)
                             for co in self.company_overrides], dtype=float)
            return np.tile(som1, (P, 1))
        som_base = np.where(is_hw, 50e6, 60e6)
        return np.exp(rng.normal(np.log(som_base), 0.5, size=(P, N)))

    def _sample_ycl(self, rng, P, N) -> np.ndarray:
        if self.company_overrides is not None:
            y1 = np.array([co.get("ycl", 0) for co in self.company_overrides], dtype=int)
            return np.tile(np.clip(y1, -3, 3), (P, 1))
        ycl = np.rint(rng.triangular(-3, 0, 3, size=(P, N))).astype(int)
        return np.clip(ycl, -3, 3)

    def _sample_growth_params(self, rng, keys, som) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        P, N = keys.shape
        ms = np.empty((P, N), dtype=float)
        ks = np.empty((P, N), dtype=float)
        xs = np.empty((P, N), dtype=float)
        flat_keys = keys.ravel()
        flat_som = som.ravel()
        for kname in np.unique(flat_keys):
            mask = (flat_keys == kname)
            cfg = self.ARCH[kname]
            mscale = self.C.growth.get("mscale_hw", 1.0) if kname.startswith("hardware") else self.C.growth.get("mscale_sw", 1.3)
            mlo, mhi = cfg["m_scale"]
            klo, khi = cfg["k_range"]
            xlo, xhi = cfg["x_range"]
            ms.flat[mask] = flat_som[mask] * rng.uniform(mlo * mscale, mhi * mscale, size=mask.sum())
            ks.flat[mask] = rng.uniform(klo, khi, size=mask.sum())
            xs.flat[mask] = rng.uniform(xlo, xhi, size=mask.sum())
        return ms, ks, xs

    def _failure_probability(self, ycl: np.ndarray, is_hw: np.ndarray, years_since_launch: np.ndarray) -> np.ndarray:
        fm = self.C.failure_model
        p0 = fm.ycl_base_fail_p0
        sw_pos = fm.ycl_slopes["software"]["pos"]
        sw_neg = fm.ycl_slopes["software"]["neg"]
        hw_pos = fm.ycl_slopes["hardware"]["pos"]
        hw_neg = fm.ycl_slopes["hardware"]["neg"]
        pos_slope = np.where(is_hw, hw_pos, sw_pos)
        neg_slope = np.where(is_hw, hw_neg, sw_neg)
        p_ycl = p0 + np.where(ycl >= 0, pos_slope * ycl, neg_slope * np.abs(ycl))

        thr_pre = fm.stage_thresholds_years_since_launch["prelaunch_max"]
        thr_early = fm.stage_thresholds_years_since_launch["early_max"]
        stage_pre = years_since_launch <= thr_pre
        stage_early = (years_since_launch > thr_pre) & (years_since_launch <= thr_early)
        p_stage = np.where(
            stage_pre, fm.stage_base_fail["prelaunch"],
            np.where(stage_early, fm.stage_base_fail["early"], fm.stage_base_fail["growth"]),
        )
        return np.clip(p_ycl + p_stage, 0.10, 0.90)

    def _sample_outcomes(self, rng, ycl, is_hw):
        years_since_launch = -ycl.astype(float)
        p_fail = self._failure_probability(ycl, is_hw, years_since_launch)
        is_fail = rng.random(ycl.shape) < p_fail
        exit_year = np.where(is_fail, np.nan, rng.triangular(3.0, 7.0, 10.0, size=ycl.shape))
        base = np.where(is_hw, 3.3, 3.7)
        sens = np.where(is_hw, 0.45, 0.35)
        writeoff_year = np.where(is_fail, np.maximum(0.5, rng.normal(base + sens * ycl, 0.7)), np.nan)
        return is_fail, exit_year, writeoff_year

    def _valuation_params(self, is_hw: np.ndarray,
                          keys: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Return (mean_multiple, log_sigma) arrays shaped (P, N).

        When Damodaran multiples are loaded and mode is ev_ebitda, each company
        gets an archetype-specific acquisition EV/EBITDA multiple from real
        public-comp data.  Otherwise falls back to the flat sw/hw means in
        strategy.json.
        """
        if self.C.mode == "ev_rev":
            mean = np.where(is_hw, self.C.ev_rev.mean_hw, self.C.ev_rev.mean_sw).astype(float)
            logsig = np.full_like(mean, self.C.ev_rev.log_sigma, dtype=float) * float(self.C.ev_rev.sigma_scale)
        else:
            # EV/EBITDA mode — use per-archetype Damodaran multiples if available
            if self.damodaran is not None and keys is not None:
                P, N = keys.shape
                mean = np.empty((P, N), dtype=float)
                flat_keys = keys.ravel()
                for kname in np.unique(flat_keys):
                    mask = (flat_keys == kname)
                    mult = self.damodaran.get(kname)
                    if mult is None:
                        # Fallback for unknown archetypes
                        mult = self.C.ev_ebitda.mean_sw if kname.startswith("software") else self.C.ev_ebitda.mean_hw
                    mean.flat[mask] = mult
            else:
                mean = np.where(is_hw, self.C.ev_ebitda.mean_hw, self.C.ev_ebitda.mean_sw).astype(float)
            logsig = np.full_like(mean, self.C.ev_ebitda.log_sigma, dtype=float) * float(self.C.ev_ebitda.sigma_scale)
        return mean, logsig

    def _ebitda_margin(self, t_since_launch: np.ndarray) -> np.ndarray:
        e = self.C.ev_ebitda
        ramp = max(1, int(e.ebitda_margin_ramp_years))
        m = e.ebitda_margin_start + (e.ebitda_margin_end - e.ebitda_margin_start) * np.clip(t_since_launch / ramp, 0.0, 1.0)
        return np.clip(m, 0.0, 0.6)

    def run(self, n_portfolios: int = 500, seed: Optional[int] = None) -> Dict[str, np.ndarray]:
        seed = int(self.C.seed if seed is None else seed)
        rng = np.random.default_rng(seed)
        P, N = n_portfolios, self.C.nc

        keys, is_hw = self._pick_types_models(rng, P, N)
        som = self._sample_som(rng, P, N, is_hw)
        ycl = self._sample_ycl(rng, P, N)
        ms, ks, xs = self._sample_growth_params(rng, keys, som)
        is_fail, exit_year, writeoff_year = self._sample_outcomes(rng, ycl, is_hw)
        mult_mean, mult_logsig = self._valuation_params(is_hw, keys=keys)

        first_check_pct = 100.0 - self.C.mgmt_fee_pct - self.C.reserve_pct
        first_pool = self.C.fund_size * (first_check_pct / 100.0)
        target_avg = first_pool / N
        checks_base = np.maximum(100_000.0, rng.normal(target_avg, 0.10 * target_avg, size=(P, N)))
        discount_factor = np.where(ycl > 0, (1.0 - self.C.prelaunch_check_discount) ** ycl, 1.0)
        checks = np.maximum(self.C.min_check, checks_base * discount_factor)

        tau1 = 1.0 - np.maximum(ycl, 0)
        rev1 = self.logistic(tau1, ms, ks, xs)
        if self.C.mode == "ev_rev":
            V0_raw = np.maximum(rev1 * (mult_mean * 0.5), self.floor_val)
        else:
            mrg1 = self._ebitda_margin(np.maximum(tau1, 0.0))
            V0_raw = np.maximum((rev1 * mrg1) * (mult_mean * 0.5), self.floor_val)
        V0 = np.maximum(V0_raw * discount_factor, self.floor_val * 0.2)

        own0 = checks / V0
        alpha = self.C.initial_ownership_target / np.maximum(own0.mean(axis=1, keepdims=True), 1e-9)
        ownership = np.minimum(self.C.ownership_cap, own0 * alpha)
        invested = checks.copy()

        reserve_pool = self.C.fund_size * (self.C.reserve_pct / 100.0)
        thirds = {2: reserve_pool / 3.0, 3: reserve_pool / 3.0, 4: reserve_pool / 3.0}
        for yr in [2, 3, 4]:
            deploy = thirds.get(yr, 0.0)
            if deploy <= 0:
                continue
            active = ((np.isnan(exit_year)) | (exit_year > yr)) & ((np.isnan(writeoff_year)) | (writeoff_year > yr))
            tau = yr - np.maximum(ycl, 0)
            revs = self.logistic(tau, ms, ks, xs)
            sig = np.where(active, np.maximum(revs, 1e-9), 0.0)
            equal_w = active.astype(float)
            equal_w = equal_w / np.maximum(equal_w.sum(axis=1, keepdims=True), 1e-9)
            prop = sig / np.maximum(sig.sum(axis=1, keepdims=True), 1e-9)
            w = (1.0 - self.C.reserve_conc) * equal_w + self.C.reserve_conc * prop
            alloc = deploy * (w / np.maximum(w.sum(axis=1, keepdims=True), 1e-9))

            stage_mult = self.stage_mult[yr - 1]
            if self.C.mode == "ev_rev":
                val = np.maximum(revs * mult_mean * stage_mult, self.floor_val)
            else:
                mrg = self._ebitda_margin(np.maximum(tau, 0.0))
                val = np.maximum((revs * mrg) * mult_mean * stage_mult, self.floor_val)
            ownership = np.minimum(self.C.ownership_cap, ownership + alloc / val)
            invested += alloc

        t_eff = exit_year - np.maximum(ycl, 0)
        rev_exit = self.logistic(t_eff, ms, ks, xs)
        Z = rng.normal(0, 1, size=(P, N))
        multiple = np.exp(mult_logsig * Z) * mult_mean
        if self.C.mode == "ev_rev":
            exit_val = rev_exit * multiple
        else:
            mrgx = self._ebitda_margin(np.maximum(t_eff, 0.0))
            exit_val = (rev_exit * mrgx) * multiple

        fee_annual = (self.C.fund_size * (self.C.mgmt_fee_pct / 100.0)) / self.C.years
        paid_in = fee_annual * self.ages + first_pool
        for yr in [2, 3, 4]:
            if reserve_pool > 0:
                paid_in[yr - 1:] += (reserve_pool / 3.0) / (self.C.years + 1 - yr)

        dists = np.zeros((P, self.C.years), dtype=float)
        nav = np.zeros((P, self.C.years), dtype=float)
        for ai, age in enumerate(self.ages):
            mask_exit = (~np.isnan(exit_year)) & (exit_year <= age)
            dists[:, ai] = np.nansum(ownership * exit_val * mask_exit, axis=1)

            active = ((np.isnan(exit_year)) | (exit_year > age)) & ((np.isnan(writeoff_year)) | (writeoff_year > age))
            tau = age - np.maximum(ycl, 0)
            rev = self.logistic(tau, ms, ks, xs)
            sm = self.stage_mult[ai]
            if self.C.mode == "ev_rev":
                nav_comp = ownership * np.maximum(rev * mult_mean * sm, self.floor_val * 0.2)
            else:
                mrg = self._ebitda_margin(np.maximum(tau, 0.0))
                nav_comp = ownership * np.maximum((rev * mrg) * mult_mean * sm, self.floor_val * 0.2)

            # Cost-basis NAV floor: active companies marked at least at invested
            # capital × nav_cost_floor.  Reflects real VC marking at last-round
            # price for P90-calibre funds.
            cost_floor = getattr(self.C, 'nav_cost_floor', 0.0)
            if cost_floor > 0:
                cost_basis_nav = invested * cost_floor
                nav_comp = np.maximum(nav_comp, cost_basis_nav)

            nav[:, ai] = np.sum(nav_comp * active, axis=1)

            mask_wo = (~np.isnan(writeoff_year)) & (writeoff_year <= age)
            dists[:, ai] += 0.10 * np.nansum(invested * mask_wo, axis=1)

        tvpi = (dists + nav) / paid_in
        ptiles = {
            "p10": np.percentile(tvpi, 10, axis=0),
            "p50": np.percentile(tvpi, 50, axis=0),
            "p75": np.percentile(tvpi, 75, axis=0),
            "p90": np.percentile(tvpi, 90, axis=0),
        }

        delta_paid = np.concatenate([[paid_in[0]], np.diff(paid_in)])
        delta_dists = np.concatenate([dists[:, [0]], np.diff(dists, axis=1)], axis=1)
        cashflows = -delta_paid[None, :] + delta_dists
        irrs = irr_many(cashflows)

        # DPI by year
        dpi = dists / paid_in

        # Failure rate
        fail_rate = float(is_fail.mean())
        n_hw = float(is_hw.mean())

        return {
            "ages": self.ages.copy(),
            "tvpi_matrix": tvpi,
            "dpi_matrix": dpi,
            "cashflows": cashflows,
            "irrs": irrs,
            "fail_rate": fail_rate,
            "hw_pct": n_hw,
            **ptiles,
        }

    def _committed_cashflows(self, committed_deals: list, P: int,
                              rng: np.random.Generator) -> tuple:
        """Generate Monte Carlo cash flows for previously committed deals.

        Handles two commitment types:
        - **first_check**: Displaces a random baseline company slot.
          Investment at t=0, proceeds at exit.
        - **follow_on**: Draws from the reserve pool (no slot displacement).
          Investment at follow_on_year, proceeds at exit.  Reduces the
          reserve capital available for random baseline follow-ons.

        Returns
        -------
        cf_committed : (P, years) ndarray
            Aggregate cash flows from all committed deals.
        n_first_check : int
            Number of first-check deals (= baseline slots displaced).
        reserve_used : float
            Total reserve capital consumed by follow-on commitments ($).
        """
        import json as _json

        if not committed_deals:
            return np.zeros((P, self.C.years), dtype=float), 0, 0.0

        cf_committed = np.zeros((P, self.C.years), dtype=float)
        n_first_check = 0
        reserve_used = 0.0

        for cd in committed_deals:
            check = float(cd["check_size_m"]) * 1_000_000
            ctype = cd.get("commitment_type", "first_check")
            moic_json = cd.get("moic_distribution_json", "[]")
            if isinstance(moic_json, str):
                moic_list = _json.loads(moic_json)
            else:
                moic_list = moic_json

            # Sample MOICs
            if moic_list and len(moic_list) > 10:
                moic_arr = np.asarray(moic_list, dtype=np.float64)
                mult = rng.choice(moic_arr, size=P, replace=True)
            else:
                surv = float(cd.get("survival_rate", 0.3))
                cap = float(cd.get("moic_cond_mean", 3.0))
                u = rng.random(P)
                mult = np.where(u < surv, cap, 0.0)

            # Exit timing
            ey_lo = float(cd.get("exit_year_low", 5))
            ey_hi = float(cd.get("exit_year_high", 10))
            ey_mode = (ey_lo + ey_hi) / 2.0
            exit_yr = rng.triangular(ey_lo, ey_mode, ey_hi, size=P)
            ey = np.clip(np.rint(exit_yr).astype(int), 1, self.C.years) - 1

            # Investment timing depends on commitment type
            if ctype == "follow_on":
                # Follow-on: invest from reserve at follow_on_year
                fo_yr = int(cd.get("follow_on_year", 2))
                invest_col = max(fo_yr - 1, 0)  # 0-indexed column
                invest_col = min(invest_col, self.C.years - 1)
                cf_committed[:, invest_col] -= check
                reserve_used += check
            else:
                # First check: invest at t=0
                cf_committed[:, 0] -= check
                n_first_check += 1

            # Proceeds at exit (same for both types)
            proceeds = check * mult
            for i in range(P):
                cf_committed[i, ey[i]] += proceeds[i]

        return cf_committed, n_first_check, reserve_used

    def deal_impact(self, deal: DealConfig, n_portfolios: int = 4000,
                    seed: Optional[int] = None,
                    committed_deals: Optional[list] = None,
                    deal_commitment_type: str = "first_check",
                    deal_follow_on_year: int = 2) -> Dict[str, float]:
        """Measure the marginal fund impact of adding a proposed deal.

        Parameters
        ----------
        committed_deals : list[dict], optional
            Previously committed deals from the fund_commitments table.
        deal_commitment_type : str
            "first_check" — proposed deal displaces baseline company slots
            (draws from first-check capital).
            "follow_on" — proposed deal draws from reserve pool and invests
            at deal_follow_on_year (no slot displacement).
        deal_follow_on_year : int
            Year the follow-on investment is deployed (1-indexed, typically 2-4).
        """
        base = self.run(n_portfolios=n_portfolios, seed=seed)
        seed = int(self.C.seed if seed is None else seed)
        rng = np.random.default_rng(seed + 999)
        P = n_portfolios

        # ── Inject committed deals into the baseline ───────────────────────
        committed_deals = committed_deals or []
        cf_committed, n_first_check, reserve_used = self._committed_cashflows(
            committed_deals, P, rng)

        n_committed = len([cd for cd in committed_deals
                           if cd.get("status", "active") == "active"])

        # Shrink the random baseline to make room for first-check deals.
        # Follow-on deals consume reserve, not baseline slots.
        total_slots = self.C.nc
        remaining_random = max(total_slots - n_first_check, 1)
        first_check_frac = n_first_check / total_slots

        cf_base = base["cashflows"].copy()

        # Scale random baseline down for first-check displacement
        if first_check_frac > 0:
            keep_random = 1.0 - first_check_frac
            cf_mean = cf_base.mean(axis=0, keepdims=True)
            cf_noise = cf_base - cf_mean
            cf_base = cf_mean * keep_random + cf_noise * np.sqrt(max(keep_random, 0.01))

        # Reduce random baseline reserve deployment for committed follow-ons.
        # Follow-on commitments consume reserve capital that would otherwise
        # go to random baseline companies in years 2-4.
        total_reserve = self.C.fund_size * (self.C.reserve_pct / 100.0)
        if reserve_used > 0 and total_reserve > 0:
            reserve_remaining_frac = max(1.0 - reserve_used / total_reserve, 0.0)
            # Scale down the reserve-driven portion of baseline CFs.
            # Reserve CFs appear in years 2-4 (columns 1-3).
            # Approximate: shrink years 2+ outflows proportionally.
            for col in range(1, min(4, self.C.years)):
                outflows = np.minimum(cf_base[:, col], 0)  # negative = investment
                cf_base[:, col] -= outflows * (1.0 - reserve_remaining_frac)

        # Add committed deal cash flows
        if n_committed > 0:
            cf_base = cf_base + cf_committed

        # ── Proposed deal exit simulation ──────────────────────────────────
        if deal.exit_year_mode == "triangular":
            tri = deal.exit_year_triangular
            exit_yr = rng.triangular(tri["low"], tri["mode"], tri["high"], size=P)
        else:
            exit_yr = np.full(P, float(deal.exit_year))

        if deal.moic_distribution is not None and len(deal.moic_distribution) > 0:
            moic_arr = np.asarray(deal.moic_distribution, dtype=np.float64)
            gross_mult = rng.choice(moic_arr, size=P, replace=True)
        else:
            u = rng.random(P)
            success = u < deal.success_prob
            gross_mult = np.where(success, deal.cap_multiple, deal.failure_multiple)

        cf_adj = cf_base.copy()

        # Displacement depends on whether the proposed deal is first_check or follow_on
        if deal_commitment_type == "follow_on":
            # Follow-on: no slot displacement — draws from reserve.
            # Reduce remaining reserve in baseline proportionally.
            remaining_reserve = total_reserve - reserve_used
            if remaining_reserve > 0 and deal.check_size > 0:
                fo_reserve_frac = min(deal.check_size / remaining_reserve, 0.95)
                for col in range(1, min(4, self.C.years)):
                    outflows = np.minimum(cf_adj[:, col], 0)
                    cf_adj[:, col] -= outflows * fo_reserve_frac
        else:
            # First check: displace baseline slots proportionally
            first_check_pct = 100.0 - self.C.mgmt_fee_pct - self.C.reserve_pct
            avg_baseline_check = self.C.fund_size * max(first_check_pct, 30.0) / 100.0 / total_slots
            displacement_n = min(deal.check_size / avg_baseline_check, remaining_random - 1)
            displacement_frac = displacement_n / remaining_random if remaining_random > 0 else 0

            if displacement_frac > 0:
                keep_frac = 1.0 - displacement_frac
                if n_committed > 0:
                    cf_random = cf_adj - cf_committed
                    cf_r_mean = cf_random.mean(axis=0, keepdims=True)
                    cf_r_noise = cf_random - cf_r_mean
                    cf_random_adj = cf_r_mean * keep_frac + cf_r_noise * np.sqrt(max(keep_frac, 0.01))
                    cf_adj = cf_random_adj + cf_committed
                else:
                    cf_mean = cf_adj.mean(axis=0, keepdims=True)
                    cf_noise = cf_adj - cf_mean
                    cf_adj = cf_mean * keep_frac + cf_noise * np.sqrt(max(keep_frac, 0.01))

        # Build deal cash flows
        cf_deal = np.zeros((P, self.C.years), dtype=float)
        if deal_commitment_type == "follow_on":
            invest_col = max(deal_follow_on_year - 1, 0)
            invest_col = min(invest_col, self.C.years - 1)
            cf_deal[:, invest_col] -= deal.check_size
        else:
            cf_deal[:, 0] -= deal.check_size

        ey = np.clip(np.rint(exit_yr).astype(int), 1, self.C.years) - 1
        proceeds = deal.check_size * gross_mult
        for i in range(P):
            cf_deal[i, ey[i]] += proceeds[i]

        cf_new = cf_adj + cf_deal
        irrs_new = irr_many(cf_new)

        cum_d_base = np.cumsum(np.maximum(cf_base, 0), axis=1)
        cum_d_new = np.cumsum(np.maximum(cf_new, 0), axis=1)
        cum_p_base = np.cumsum(np.maximum(-cf_base, 0), axis=1)
        cum_p_new = np.cumsum(np.maximum(-cf_new, 0), axis=1)

        tvpi_base = cum_d_base[:, -1] / np.maximum(cum_p_base[:, -1], 1e-9)
        tvpi_new = cum_d_new[:, -1] / np.maximum(cum_p_new[:, -1], 1e-9)

        irrs_base = irr_many(cf_base)

        return {
            "deal_name": deal.name,
            "check_size": deal.check_size,
            "success_prob": deal.success_prob,
            "cap_multiple": deal.cap_multiple,
            "n_committed_deals": n_committed,
            "n_first_check": n_first_check,
            "reserve_used": reserve_used,
            "commitment_type": deal_commitment_type,
            "tvpi_base_mean": float(np.mean(tvpi_base)),
            "tvpi_new_mean": float(np.mean(tvpi_new)),
            "tvpi_mean_lift": float(np.mean(tvpi_new - tvpi_base)),
            "tvpi_base_p10": float(np.percentile(tvpi_base, 10)),
            "tvpi_new_p10": float(np.percentile(tvpi_new, 10)),
            "tvpi_base_p50": float(np.percentile(tvpi_base, 50)),
            "tvpi_new_p50": float(np.percentile(tvpi_new, 50)),
            "tvpi_base_p75": float(np.percentile(tvpi_base, 75)),
            "tvpi_new_p75": float(np.percentile(tvpi_new, 75)),
            "tvpi_p75_lift": float(np.percentile(tvpi_new, 75) - np.percentile(tvpi_base, 75)),
            "tvpi_base_p90": float(np.percentile(tvpi_base, 90)),
            "tvpi_new_p90": float(np.percentile(tvpi_new, 90)),
            "irr_base_mean": float(np.nanmean(irrs_base)),
            "irr_new_mean": float(np.nanmean(irrs_new)),
            "irr_mean_lift": float(np.nanmean(irrs_new) - np.nanmean(irrs_base)),
            "irr_base_p50": float(np.nanpercentile(irrs_base, 50)),
            "irr_new_p50": float(np.nanpercentile(irrs_new, 50)),
        }
