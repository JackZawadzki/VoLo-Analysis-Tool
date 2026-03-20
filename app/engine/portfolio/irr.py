"""IRR calculation using Newton's method — vectorized for portfolio simulation."""
from __future__ import annotations
import numpy as np


def irr_newton(cashflows: np.ndarray, guess: float = 0.15, max_iter: int = 80, tol: float = 1e-7) -> float:
    cf = np.asarray(cashflows, dtype=float)
    r = guess
    for _ in range(max_iter):
        denom = (1.0 + r) ** np.arange(len(cf))
        npv = np.sum(cf / denom)
        d_npv = np.sum(-np.arange(len(cf)) * cf / denom / (1.0 + r))
        if abs(d_npv) < 1e-12:
            return np.nan
        r_new = r - npv / d_npv
        if not np.isfinite(r_new) or r_new <= -0.9999:
            return np.nan
        if abs(r_new - r) < tol:
            return float(r_new)
        r = r_new
    return np.nan


def irr_many(cashflows_matrix: np.ndarray, guess: float = 0.15) -> np.ndarray:
    cfm = np.asarray(cashflows_matrix, dtype=float)
    out = np.empty(cfm.shape[0], dtype=float)
    for i in range(cfm.shape[0]):
        out[i] = irr_newton(cfm[i], guess=guess)
    return out
