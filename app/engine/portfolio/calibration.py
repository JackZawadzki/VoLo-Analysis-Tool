"""Grid-search calibration to fit simulated TVPI curves against Carta benchmarks."""
from __future__ import annotations
from typing import Dict, List
import logging
import numpy as np

from .config import StrategyConfig, EVRevConfig, EVEbitdaConfig
from .simulator import VCSimulator
from .benchmarks import Benchmarks

logger = logging.getLogger(__name__)


def fit_error(sim_p75: np.ndarray, sim_p90: np.ndarray, bench: Benchmarks, focus_ages=range(3, 9)) -> float:
    err = 0.0
    for a in focus_ages:
        idx = int(np.where(bench.ages == a)[0][0])
        b75 = bench.tvpi["p75"][idx]
        b90 = bench.tvpi["p90"][idx]
        err += ((sim_p75[idx] - b75) / b75) ** 2 + ((sim_p90[idx] - b90) / b90) ** 2
    return float(err)


def calibrate(strategy: StrategyConfig, bench: Benchmarks,
              grid: Dict[str, List[float]], n_portfolios: int = 600) -> List[dict]:
    rows = []
    for reserve_pct in grid.get("reserve_pct", [strategy.reserve_pct]):
        for reserve_conc in grid.get("reserve_conc", [strategy.reserve_conc]):
            for sigma_scale in grid.get("sigma_scale", [strategy.ev_rev.sigma_scale if strategy.mode == "ev_rev" else strategy.ev_ebitda.sigma_scale]):
                for mscale_sw in grid.get("mscale_sw", [strategy.growth.get("mscale_sw", 1.3)]):
                    for mscale_hw in grid.get("mscale_hw", [strategy.growth.get("mscale_hw", 1.0)]):
                        d = strategy.__dict__.copy()
                        d["reserve_pct"] = float(reserve_pct)
                        d["reserve_conc"] = float(reserve_conc)
                        d["growth"] = {"mscale_sw": float(mscale_sw), "mscale_hw": float(mscale_hw)}
                        if strategy.mode == "ev_rev":
                            evr = strategy.ev_rev
                            d["ev_rev"] = EVRevConfig(evr.mean_sw, evr.mean_hw, evr.log_sigma, float(sigma_scale))
                        else:
                            eve = strategy.ev_ebitda
                            d["ev_ebitda"] = EVEbitdaConfig(
                                eve.mean_sw, eve.mean_hw, eve.log_sigma, float(sigma_scale),
                                eve.ebitda_margin_start, eve.ebitda_margin_end, eve.ebitda_margin_ramp_years,
                            )
                        try:
                            test = StrategyConfig(**d)
                            sim = VCSimulator(test, bench=bench, company_overrides=None)
                            out = sim.run(n_portfolios=n_portfolios, seed=test.seed)
                            err = fit_error(out["p75"], out["p90"], bench)
                            rows.append({
                                "reserve_pct": reserve_pct,
                                "reserve_conc": reserve_conc,
                                "sigma_scale": sigma_scale,
                                "mscale_sw": mscale_sw,
                                "mscale_hw": mscale_hw,
                                "err": round(err, 6),
                            })
                        except Exception as exc:
                            logger.warning(
                                "Calibration step failed (reserve_pct=%.2f, sigma_scale=%.2f): %s",
                                reserve_pct, sigma_scale, exc,
                            )

    return sorted(rows, key=lambda r: r["err"])
