"""Carta TVPI benchmark data handling."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict
import json
import numpy as np


@dataclass(frozen=True)
class Benchmarks:
    ages: np.ndarray
    tvpi: Dict[str, np.ndarray]  # p10, p50, p75, p90


def load_benchmarks(path: str) -> Benchmarks:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    ages = np.array(d["ages"], dtype=int)
    tv = {k: np.array(v, dtype=float) for k, v in d["tvpi"].items()}
    for k, arr in tv.items():
        if len(arr) != len(ages):
            raise ValueError(f"Benchmark {k} length mismatch")
    return Benchmarks(ages=ages, tvpi=tv)


def benchmarks_to_dict(b: Benchmarks) -> dict:
    return {
        "ages": b.ages.tolist(),
        "tvpi": {k: v.tolist() for k, v in b.tvpi.items()},
    }
