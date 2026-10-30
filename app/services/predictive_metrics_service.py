"""Métricas de error para pronósticos evaluados fuera de muestra.

Todas reciben pares (real, predicho) producidos por ``backtest_service``; nunca
predicciones de entrenamiento. MAPE se calcula solo sobre reales distintos de
cero e informa cuántos pares quedaron excluidos, para no mostrar un porcentaje
engañoso. MAPE/WAPE son ERROR: la UI no debe presentarlos como "precisión".
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

APE_BUCKETS = (
    ("APE ≤ 10% (Excelente)", 0.0, 10.0, "green"),
    ("10% < APE ≤ 20% (Bueno)", 10.0, 20.0, "yellow"),
    ("20% < APE ≤ 30% (Aceptable)", 20.0, 30.0, "orange"),
    ("APE > 30% (Alto)", 30.0, math.inf, "red"),
)


@dataclass(frozen=True)
class MetricSet:
    n: int
    mae: float | None
    rmse: float | None
    bias: float | None          # ME = mean(pred - real); >0 sobreestima
    wape: float | None          # sum|e| / sum|real| * 100
    mape: float | None          # media de |e|/|real| * 100 sobre reales != 0
    mape_n: int                 # pares usados en MAPE
    mape_excluded: int          # pares con real == 0 excluidos

    def to_dict(self) -> dict:
        return {"n": self.n, "mae": self.mae, "rmse": self.rmse, "bias": self.bias, "wape": self.wape,
                "mape": self.mape, "mape_n": self.mape_n, "mape_excluded": self.mape_excluded}


def _pairs(actual: Sequence[float], predicted: Sequence[float]) -> list[tuple[float, float]]:
    if len(actual) != len(predicted):
        raise ValueError("actual y predicted deben tener la misma longitud")
    pairs = [(float(a), float(p)) for a, p in zip(actual, predicted)]
    if any(not math.isfinite(v) for pair in pairs for v in pair):
        raise ValueError("Evaluation pairs must be finite")
    return pairs


def mae(actual, predicted) -> float | None:
    pairs = _pairs(actual, predicted)
    return sum(abs(p - a) for a, p in pairs) / len(pairs) if pairs else None


def rmse(actual, predicted) -> float | None:
    pairs = _pairs(actual, predicted)
    return math.sqrt(sum((p - a) ** 2 for a, p in pairs) / len(pairs)) if pairs else None


def bias(actual, predicted) -> float | None:
    pairs = _pairs(actual, predicted)
    return sum(p - a for a, p in pairs) / len(pairs) if pairs else None


def wape(actual, predicted) -> float | None:
    pairs = _pairs(actual, predicted)
    denominator = sum(abs(a) for a, _ in pairs)
    if not pairs or denominator == 0:
        return None
    return sum(abs(p - a) for a, p in pairs) / denominator * 100.0


def mape(actual, predicted) -> tuple[float | None, int, int]:
    """(mape, usados, excluidos). Excluye reales en cero en vez de dividir entre cero."""
    pairs = _pairs(actual, predicted)
    usable = [(a, p) for a, p in pairs if a != 0]
    excluded = len(pairs) - len(usable)
    if not usable:
        return None, 0, excluded
    return sum(abs(p - a) / abs(a) for a, p in usable) / len(usable) * 100.0, len(usable), excluded


def compute(actual: Sequence[float], predicted: Sequence[float]) -> MetricSet:
    value, used, excluded = mape(actual, predicted)
    return MetricSet(n=len(actual), mae=mae(actual, predicted), rmse=rmse(actual, predicted),
                     bias=bias(actual, predicted), wape=wape(actual, predicted),
                     mape=value, mape_n=used, mape_excluded=excluded)


def ape_distribution(actual: Sequence[float], predicted: Sequence[float]) -> list[dict]:
    """Reparto porcentual de los APE (solo reales != 0) en los tramos de ``APE_BUCKETS``."""
    apes = [abs(p - a) / abs(a) * 100.0 for a, p in _pairs(actual, predicted) if a != 0]
    total = len(apes)
    result = []
    for label, low, high, tone in APE_BUCKETS:
        count = sum(1 for x in apes if (low < x <= high) or (low == 0.0 and x == 0.0))
        result.append({"label": label, "tone": tone, "count": count,
                       "share": (count / total * 100.0) if total else None})
    return result


def approximate_accuracy(wape_value: float | None) -> float | None:
    """'Exactitud aproximada' = max(0, 100 - WAPE). Derivada y etiquetada como tal; nunca 'precisión MAPE'."""
    if wape_value is None:
        return None
    return max(0.0, 100.0 - wape_value)
