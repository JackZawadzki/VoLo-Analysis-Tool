"""Convergence testing — run simulation batches until TVPI percentiles stabilize."""
from __future__ import annotations
from typing import Tuple
import numpy as np


def max_rel_change(a: np.ndarray, b: np.ndarray, eps: float = 1e-9) -> float:
    return float(np.max(np.abs(a - b) / np.maximum(np.abs(b), eps)))


def run_until_converged(run_fn, target_keys=("p75", "p90"), tol=0.01,
                        batch=300, max_portfolios=20000) -> Tuple[dict, dict]:
    """
    Incrementally increase simulation count until percentile curves stabilize.
    Returns (final_output, convergence_meta).
    """
    hist = []
    total = 0
    prev = None
    seed_offset = 0

    while total < max_portfolios:
        total += batch
        out = run_fn(n_portfolios=total, seed_offset=seed_offset)
        snap = {"n": total, **{k: out[k].copy() for k in target_keys}}
        hist.append(snap)

        if prev is not None:
            max_change = max(max_rel_change(snap[k], prev[k]) for k in target_keys)
            if max_change < tol:
                return out, {"n_portfolios": total, "converged": True, "steps": len(hist)}

        prev = snap
        seed_offset += 1

    return out, {"n_portfolios": total, "converged": False, "steps": len(hist)}
