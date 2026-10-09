"""Baselines with anchors found from the derivative (in the style of Origin's Peak Analyzer).

Methods (docs.originlab.com/origin-help/pa-algorithm):
- 2nd derivative (zeros): anchors where the curvature κ = y''/(1+y'^2)^1.5 ≈ 0.
- 2nd derivative (peaks): anchors at the local maxima of y'' (the peak "feet").
- 1st + 2nd derivative: anchors where y' ≈ 0 AND y'' ≈ 0 (flat baseline stretches).

All derivatives are taken with respect to X (chain rule over the index), so they work
even when X is not perfectly uniform (e.g. measured temperature).
"""
from __future__ import annotations

import numpy as np

from .i18n import tr
from .peaks import peak_regions
from .scipy_load import scipy_parts


# ----------------------------------------------------------------- derivatives
def _window(n: int, smooth_pct: float) -> int:
    w = max(5, int(round(n * smooth_pct / 100.0)))
    w = min(w, n - 1 if n % 2 == 0 else n)
    return w if w % 2 == 1 else w - 1


def _uniform_grid(x, y):
    """Average of the points with the same X, resampled at a constant step.

    Measured X (e.g. temperature) wobbles and stays almost still at the start of the run;
    differentiating those points directly creates false peaks (dX ≈ 0)."""
    xu, inv = np.unique(np.round(x, 6), return_inverse=True)
    if len(xu) < 5:
        raise ValueError(tr("err_x_unique"))
    yu = np.bincount(inv, weights=y) / np.bincount(inv)
    n = len(xu)
    xg = np.linspace(xu[0], xu[-1], n)
    return xg, np.interp(xg, xu, yu)


def smooth_derivatives(x, y, smooth_pct: float = 1.0, polyorder: int = 2):
    """Smooths (Savitzky-Golay) and returns (y_smooth, dy/dx, d²y/dx²) at the x points."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    xg, yg = _uniform_grid(x, y)
    h = xg[1] - xg[0]
    w = _window(len(xg), smooth_pct)
    savgol_filter = scipy_parts().signal.savgol_filter
    ys = savgol_filter(yg, w, polyorder)
    d1 = savgol_filter(yg, w, polyorder, deriv=1, delta=h)
    d2 = savgol_filter(d1, w, polyorder, deriv=1, delta=h)  # 2nd from the already smooth 1st
    return tuple(np.interp(x, xg, v) for v in (ys, d1, d2))


def derivative_signal(x, y, smooth_pct: float = 1.0):
    """Smoothed dY/dX."""
    return smooth_derivatives(x, y, smooth_pct)[1]


def dtg(x, y, smooth_pct: float = 1.0):
    """DTG = −(dY/dX) / Y0 × 100, in %/X unit (same convention as TA's
    'Deriv. Weight (%/°C)' column). Use when the file only has the mass."""
    y = np.asarray(y, float)
    y0 = np.median(y[: max(3, len(y) // 500)]) or 1.0
    return -derivative_signal(x, y, smooth_pct) / y0 * 100.0


# -------------------------------------------------------------------- anchors
def _edge_mask(n, w):
    m = np.ones(n, bool)
    m[: w // 2] = m[n - w // 2:] = False  # derivatives at the edges are unreliable
    return m


def _runs(mask):
    """Contiguous True stretches -> list of (start, end_exclusive)."""
    d = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def _pick_from_runs(x, mask, n_anchors, min_run):
    """Splits X into n bands; in each one, the center of the longest flat stretch."""
    runs = [(a, b) for a, b in _runs(mask) if b - a >= min_run]
    edges = np.linspace(x[0], x[-1], n_anchors + 1)
    picks = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        best, best_len = None, 0
        i0, i1 = np.searchsorted(x, lo), np.searchsorted(x, hi, side="right")
        for a, b in runs:
            ia, ib = max(a, i0), min(b, i1)
            if ib - ia > best_len:
                best, best_len = (ia + ib - 1) // 2, ib - ia
        if best is not None:
            picks.append(best)
    return picks


def find_anchors(x, y, mode: str, smooth_pct=1.0, tol_pct=5.0, tol1_pct=5.0,
                 n_anchors=12, include_ends=True):
    """Returns the anchor indices. mode: 'zeros' | 'peaks' | 'combined'."""
    x = np.asarray(x, float)
    n = len(x)
    ys, d1, d2 = smooth_derivatives(x, y, smooth_pct)
    w = _window(n, smooth_pct)
    inner = _edge_mask(n, w)
    ref2 = np.max(np.abs(d2[inner])) or 1.0
    ref1 = np.max(np.abs(d1[inner])) or 1.0
    min_run = max(3, w // 2)  # isolated crossings (peak flanks) do not become anchors

    if mode == "zeros":
        kappa = d2 / (1.0 + d1 ** 2) ** 1.5
        refk = np.max(np.abs(kappa[inner])) or 1.0
        mask = (np.abs(kappa) <= tol_pct / 100.0 * refk) & inner
        idx = _pick_from_runs(x, mask, int(n_anchors), min_run)
    elif mode == "combined":
        mask = ((np.abs(d1) <= tol1_pct / 100.0 * ref1)
                & (np.abs(d2) <= tol_pct / 100.0 * ref2) & inner)
        idx = _pick_from_runs(x, mask, int(n_anchors), min_run)
    elif mode == "peaks":
        # edges get the minimum (not -inf, which would make the prominences infinite)
        find_peaks = scipy_parts().signal.find_peaks
        pk, props = find_peaks(np.where(inner, d2, d2[inner].min()),
                               height=0, prominence=tol_pct / 100.0 * ref2, distance=w)
        order = np.argsort(props["prominences"])[::-1][: int(n_anchors)]
        idx = sorted(pk[order].tolist())
    else:
        raise ValueError(mode)

    if include_ends:
        idx = [0] + list(idx) + [n - 1]
    idx = sorted(set(int(i) for i in idx))
    if len(idx) < 2:
        raise ValueError(tr("err_deriv_anchors"))
    return np.asarray(idx), ys


INTERP_CHOICES = ("linear", "spline (PCHIP)", "spline (cúbica natural)")


def interp_baseline(x, ax, ay, interp="linear"):
    """Joins the anchors (ax increasing) and evaluates at x.

    Outside the anchor range the baseline stays horizontal, at the end anchor's value,
    as in np.interp: extrapolating the last cubic piece makes the baseline dive or soar
    exactly where there is no anchor. With fewer than 3 anchors, any spline becomes a line."""
    x = np.asarray(x, float)
    if interp == "linear" or len(ax) < 3:
        return np.interp(x, ax, ay)
    if interp == "spline (PCHIP)":
        f = scipy_parts().interpolate.PchipInterpolator(ax, ay)  # does not overshoot the anchor values
    elif interp == "spline (cúbica natural)":
        # continuous curvature; may overshoot the anchors
        f = scipy_parts().interpolate.CubicSpline(ax, ay, bc_type="natural")
    else:
        raise ValueError(interp)
    return f(np.clip(x, ax[0], ax[-1]))


def _baseline_from_anchors(x, ys, idx, interp):
    return interp_baseline(x, x[idx], ys[idx], interp)


NEAR_FRAC = 0.05  # "near a peak" = within 5 % of the X range


def _peak_near(x, y, base, lo, hi) -> bool:
    """Is there a real peak (same rule as the events table) between lo and hi?"""
    return any(x[a] <= hi and x[b - 1] >= lo for a, b in peak_regions(x, y - base, 3.0, 5.0, False))


def keep_below(x, y, ys, idx, interp, ay=None, tol_frac=0.03, max_add=40):
    """Adds anchors where the baseline passes above the smoothed signal.

    As in Origin, only a valley next to a peak (the peak foot) becomes an anchor. A valley in
    the middle of the noise (negative spike) is ignored: the baseline passes over it."""
    ay = ys if ay is None else ay
    idx = list(idx)
    n = len(x)
    tol = tol_frac * (np.ptp(ys) or 1.0)
    reach = NEAR_FRAC * (x[-1] - x[0])
    ignored = np.zeros(n, bool)
    for _ in range(max_add):
        base = _baseline_from_anchors(x, ay, np.asarray(idx), interp)
        deficit = np.where(ignored, 0.0, ys - base)
        i = int(np.argmin(deficit))
        if deficit[i] >= -tol or i in idx:
            break
        if _peak_near(x, y, base, x[i] - reach, x[i] + reach):
            idx = sorted(idx + [i])
        else:  # noise valley: ignore it and its neighborhood
            ignored[max(0, i - n // 100): i + n // 100 + 1] = True
    return np.asarray(idx)


def settle_ends(x, y, idx, ay, interp):
    """An end anchor with only noise (no peak nearby) goes at the median level of the signal
    there, not at the last point, which usually drops or rises because of an end-of-run
    artifact."""
    ay = ay.copy()
    base = _baseline_from_anchors(x, ay, np.asarray(idx), interp)
    reach = NEAR_FRAC * (x[-1] - x[0])
    for end, sel in ((0, x <= x[0] + reach), (len(x) - 1, x >= x[-1] - reach)):
        if end in idx and not _peak_near(x, y, base, x[sel][0], x[sel][-1]):
            ay[end] = float(np.median(y[sel]))
    return ay


# Defaults of "1st + 2nd" calibrated against an Origin (Peak Analyzer) baseline on a reference
# DTG: peak areas within 1 % of Origin's (tests/test_origin_reference.py).
COMBINED_DEFAULTS = dict(smooth_pct=0.5, tol_pct=5.0, tol1_pct=2.0, n_anchors=16)


def _make(mode, smooth_pct=1.0, tol_pct=5.0, tol1_pct=5.0, n_anchors=12):
    d_smooth, d_tol, d_tol1, d_n = smooth_pct, tol_pct, tol1_pct, n_anchors

    def method(x, y, smooth_pct=d_smooth, tol_pct=d_tol, tol1_pct=d_tol1, n_anchors=d_n,
               include_ends="sim", interp="linear", below="sim", **_):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        idx, ys = find_anchors(x, y, mode, smooth_pct, tol_pct, tol1_pct, n_anchors,
                               include_ends == "sim")
        ay = settle_ends(x, y, idx, ys, interp) if include_ends == "sim" else ys
        if below == "sim":
            idx = keep_below(x, y, ys, idx, interp, ay)
            ay = settle_ends(x, y, idx, ys, interp) if include_ends == "sim" else ys
        return _baseline_from_anchors(x, ay, idx, interp), x[idx]
    method.__name__ = f"deriv_{mode}"
    return method


deriv_zeros = _make("zeros")
deriv_peaks = _make("peaks")
deriv_combined = _make("combined", **COMBINED_DEFAULTS)


# -------------------------------------------------------------------- events
def integrate_events(x, corrected, anchors_x, min_fraction=0.01, signal=None):
    """Integrates the corrected signal between consecutive anchors.

    On a DTG in %/°C, 'area' is the mass loss (%) of the event above the baseline and
    'area_total' (if `signal` is given) is the total loss over the interval = event + baseline,
    which matches the TGA drop between start and end.
    Segments with area < min_fraction of the total positive area are ignored.
    """
    x = np.asarray(x, float)
    c = np.asarray(corrected, float)
    s = None if signal is None else np.asarray(signal, float)
    ax = np.unique(np.asarray(anchors_x, float))
    segs = []
    for lo, hi in zip(ax[:-1], ax[1:]):
        sel = (x >= lo) & (x <= hi)
        if sel.sum() < 3:
            continue
        xs, cs = x[sel], c[sel]
        segs.append({"inicio": lo, "fim": hi, "pico": float(xs[np.argmax(np.abs(cs))]),
                     "altura": float(cs[np.argmax(np.abs(cs))]),
                     "area": float(np.trapezoid(cs, xs)),
                     "area_total": float(np.trapezoid(s[sel], xs)) if s is not None else float("nan")})
    total = sum(abs(s["area"]) for s in segs) or 1.0
    return [s for s in segs if abs(s["area"]) >= min_fraction * total]
