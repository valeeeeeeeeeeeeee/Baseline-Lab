"""Rheological models for flow curves (shear stress × shear rate).

Each model is fitted by least squares on the shear stress. The best model is the one with the
lowest AICc, which weighs the fit quality against the number of parameters: Herschel-Bulkley
contains Newton, Bingham and the power law, so by R² alone it would always win.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .i18n import tr
from .scipy_load import scipy_parts

MIN_POINTS = 4  # distinct shear rates needed to fit anything


@dataclass(frozen=True)
class Model:
    key: str
    equation: str
    symbols: tuple[str, ...]
    func: Callable
    guesses: Callable              # (x, y) -> list of starting points
    upper: tuple[float, ...]       # upper bounds (the lower ones are all 0)

    @property
    def name(self) -> str:
        """Displayed name, in the current language."""
        return tr(f"rheo_m_{self.key}")


@dataclass(frozen=True)
class Fit:
    model: Model
    params: tuple[float, ...]
    r2: float
    rmse: float
    aicc: float

    def predict(self, x):
        return self.model.func(np.asarray(x, float), *self.params)


def _newton(x, mu):
    return mu * x


def _bingham(x, t0, mu):
    return t0 + mu * x


def _power(x, k, n):
    return k * x ** n


def _herschel_bulkley(x, t0, k, n):
    return t0 + k * x ** n


def _casson(x, t0, mu):
    return (np.sqrt(t0) + np.sqrt(mu * x)) ** 2


def _line(x, y) -> tuple[float, float]:
    """(intercept, slope) of the straight line through the points, both kept >= 0."""
    slope, inter = np.polyfit(x, y, 1)
    return max(float(inter), 0.0), max(float(slope), 0.0)


def _loglog(x, y) -> tuple[float, float]:
    """(K, n) of the straight line through the points in log-log scale."""
    ok = y > 0
    if ok.sum() < 2:
        return 1.0, 1.0
    n, lnk = np.polyfit(np.log(x[ok]), np.log(y[ok]), 1)
    return float(np.exp(lnk)), float(np.clip(n, 0.05, 5.0))


def _guess_newton(x, y):
    return [(max(float(x @ y / (x @ x)), 0.0),)]


def _guess_hb(x, y):
    # several starts (the fit has local minima): a yield stress just below the lowest stress,
    # and the two simpler models it contains
    t0 = 0.8 * max(float(y.min()), 0.0)
    return [(t0, *_loglog(x, y - t0)), (0.0, *_loglog(x, y)), (*_line(x, y), 1.0)]


def _guess_casson(x, y):
    a, b = _line(np.sqrt(x), np.sqrt(np.clip(y, 0, None)))  # √τ = √τ0 + √μ·√γ̇
    return [(a ** 2, b ** 2), (0.0, _guess_newton(x, y)[0][0])]


_INF = float("inf")
MODELS = [
    Model("newton", "τ = μ·γ̇", ("μ",), _newton, _guess_newton, (_INF,)),
    Model("bingham", "τ = τ₀ + μp·γ̇", ("τ₀", "μp"), _bingham,
          lambda x, y: [_line(x, y)], (_INF, _INF)),
    Model("power", "τ = K·γ̇ⁿ", ("K", "n"), _power,
          lambda x, y: [_loglog(x, y)], (_INF, 10.0)),
    Model("hb", "τ = τ₀ + K·γ̇ⁿ", ("τ₀", "K", "n"), _herschel_bulkley, _guess_hb,
          (_INF, _INF, 10.0)),
    Model("casson", "√τ = √τ₀ + √(μc·γ̇)", ("τ₀", "μc"), _casson, _guess_casson, (_INF, _INF)),
]


def clean(x, y) -> tuple[np.ndarray, np.ndarray]:
    """Points usable in the fit: finite, with shear rate > 0, in increasing shear rate."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y) & (x > 0)
    x, y = x[ok], y[ok]
    order = np.argsort(x, kind="stable")
    return x[order], y[order]


def fit_model(model: Model, x, y) -> Fit | None:
    """Least-squares fit of one model (parameters >= 0), or None if it cannot be fitted."""
    n, k = len(x), len(model.symbols) + 1  # + 1: the variance of the residuals
    if n - k - 1 <= 0:  # too few points for this number of parameters
        return None
    # fitted on the stress divided by its largest value: the optimizer stops on an absolute
    # gradient, so very small stresses (another unit) would end before reaching the minimum.
    # Every parameter but the exponent n is proportional to the stress
    scale = float(np.abs(y).max()) or 1.0
    back = np.array([1.0 if s == "n" else scale for s in model.symbols])
    best, best_sse = None, np.inf
    curve_fit = scipy_parts().optimize.curve_fit
    for p0 in model.guesses(x, y / scale):
        p0 = np.clip(p0, 1e-9, [min(u, 1e300) for u in model.upper])
        try:
            p, _ = curve_fit(model.func, x, y / scale, p0=p0, bounds=(0, model.upper),
                             x_scale="jac", max_nfev=5000)
        except (RuntimeError, ValueError):  # did not converge from this start
            continue
        p = p * back
        sse = float(np.sum((y - model.func(x, *p)) ** 2))
        if np.isfinite(sse) and sse < best_sse:
            best, best_sse = p, sse
    if best is None:
        return None
    sst = float(np.sum((y - y.mean()) ** 2))
    # floor: fits that are exact to rounding tie, and the simplest model wins
    sse = max(best_sse, 1e-12 * float(y @ y), 1e-300)
    aicc = n * np.log(sse / n) + 2 * k + 2 * k * (k + 1) / (n - k - 1)
    return Fit(model, tuple(float(v) for v in best),
               1 - best_sse / sst if sst > 0 else float("nan"),
               float(np.sqrt(best_sse / n)), float(aicc))


def fit_all(x, y) -> list[Fit]:
    """Fits every model to the flow curve; the result goes from the best to the worst (AICc)."""
    x, y = clean(x, y)
    if len(np.unique(x)) < MIN_POINTS:
        raise ValueError(tr("rheo_err_points", n=MIN_POINTS))
    fits = [f for f in (fit_model(m, x, y) for m in MODELS) if f is not None]
    if not fits:
        raise ValueError(tr("rheo_err_fit"))
    return sorted(fits, key=lambda f: f.aicc)
