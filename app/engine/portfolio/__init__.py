"""
Portfolio-level Monte Carlo simulator.
Adapted from Joe's standalone VC fund simulator for integration with VoLo platform.

Provides:
- VCSimulator: vectorized portfolio simulation with 6 company archetypes
- Calibration against Carta TVPI benchmarks
- Convergence-driven simulation sizing
- Deal impact analysis (marginal lift from adding a deal)
"""

from .simulator import VCSimulator
from .config import (
    StrategyConfig, DealConfig, EVRevConfig, EVEbitdaConfig,
    FailureModelConfig, load_strategy, load_deal, strategy_from_dict, deal_from_dict
)
from .benchmarks import Benchmarks, load_benchmarks
from .irr import irr_newton, irr_many
from .calibration import calibrate, fit_error
from .convergence import run_until_converged

__all__ = [
    "VCSimulator",
    "StrategyConfig", "DealConfig", "EVRevConfig", "EVEbitdaConfig",
    "FailureModelConfig", "load_strategy", "load_deal",
    "strategy_from_dict", "deal_from_dict",
    "Benchmarks", "load_benchmarks",
    "irr_newton", "irr_many",
    "calibrate", "fit_error",
    "run_until_converged",
]
