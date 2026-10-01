""""Peaks only" mode: a baseline near 0 is ambient noise and becomes 0; only the peaks count.

The same rule applies to the whole signal:
1. the ambient noise (σ) is estimated on the corrected signal (signal − method baseline) by
   MAD with iterative clipping (the peaks are discarded from the estimate);
2. the method baseline that is near 0 (|baseline| ≤ k·σ) becomes exactly 0 (only over wide
   stretches); the peaks are searched against it;
3. a peak is a stretch that exceeds k·σ and whose height is ≥ `min_height_pct` % of the largest
   peak; each peak is extended until the signal returns to the noise level (1σ), so the feet
   are not cut off; peaks that touch become one; peaks cut off at the edge of the data are
   ignored, if requested;
4. the method's anchors are discarded: each peak gets ONE anchor at each foot, at the value of
   the smoothed signal (0 if near 0); noise stretches near 0 stay at 0; the final baseline
   follows the shape of the method's baseline, shifted to pass through them;
5. inside the peaks, corrected = signal − baseline (≥ 0); outside them, corrected = 0.
"""
from __future__ import annotations

import numpy as np


def _moving_average(c, w):
    w = int(w) | 1
    if w <= 1:
        return c
    pad = np.pad(c, w // 2, mode="reflect")
    return np.convolve(pad, np.ones(w) / w, mode="valid")


def noise_level(c, n_iter=10, clip=3.0):
    """(center, σ) of the noise: median and MAD with iterative clipping of points above clip·σ."""
    c = np.asarray(c, float)
    sel = np.ones(c.size, bool)
    center, sigma = 0.0, 0.0
    for _ in range(n_iter):
        v = c[sel]
        if v.size < 5:
            break
        center = float(np.median(v))
        sigma = 1.4826 * float(np.median(np.abs(v - center)))
        if sigma == 0:
            break
        new = np.abs(c - center) <= clip * sigma
        if np.array_equal(new, sel):
            break
        sel = new
    # floor of 0.1 % of the largest value: a noiseless (already smoothed) signal would have σ ≈ 0
    floor = 1e-3 * float(np.max(np.abs(c - center)))
    return center, max(sigma, floor) or 1e-12


def peak_regions(x, corrected, k: float = 3.0, min_height_pct: float = 5.0,
                 skip_edges: bool = True, min_width: float = 0.0, exclude=()):
    """Stretches (start, end_exclusive) in indices that contain positive peaks.

    Noise that passes the threshold is discarded before picking the largest peak (the
    reference for the minimum height): stretches narrower than `min_width` (X units, foot to
    foot) and stretches whose highest point falls in an `exclude` interval (peaks marked as
    noise)."""
    x = np.asarray(x, float)
    c = np.asarray(corrected, float)
    n = c.size
    cs = _moving_average(c, max(3, n // 200))  # point noise neither starts nor interrupts a peak
    center, sigma = noise_level(cs)
    above = cs > center + k * sigma
    if not above.any():
        return []
    edge = center + sigma
    d = np.diff(np.concatenate([[0], above.astype(int), [0]]))
    cands = []  # (start, end, height) of each stretch above the threshold, already extended to the feet
    for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
        if b - a < 3:
            continue  # spike of a few points
        top_i = a + int(np.argmax(cs[a:b]))
        if any(lo <= x[top_i] <= hi for lo, hi in exclude):
            continue  # marked as noise by the user
        while a > 0 and cs[a - 1] > edge:
            a -= 1
        while b < n and cs[b] > edge:
            b += 1
        if x[b - 1] - x[a] < min_width:
            continue  # too narrow to be a peak: noise
        cands.append((a, b, float(cs[top_i]) - center))
    if not cands:
        return []
    top = max(h for _a, _b, h in cands)
    regions = []
    for a, b, h in cands:
        if h < min_height_pct / 100.0 * top:
            continue  # too low
        if skip_edges and (a == 0 or b == n):
            continue  # does not return to the noise before the edge: incomplete peak or edge artifact
        if regions and a <= regions[-1][1]:
            regions[-1] = (regions[-1][0], max(b, regions[-1][1]))
        else:
            regions.append((a, b))
    return regions


def zero_near_zero(base, threshold, min_zone_frac: float = 0.03):
    """Baseline with |value| ≤ threshold becomes 0; up to 2·threshold it rises in a ramp (no step).

    Only wide stretches are zeroed (≥ min_zone_frac of the points): ambient noise is a
    stretch, not an isolated point where the baseline touches near 0."""
    base = np.asarray(base, float)
    if threshold <= 0:
        return base.copy()
    w = np.clip((np.abs(base) - threshold) / threshold, 0.0, 1.0)
    d = np.diff(np.concatenate([[0], (w < 1).astype(int), [0]]))
    for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
        if b - a < min_zone_frac * base.size:
            w[a:b] = 1.0
    return base * w


def peaks_only(x, y, baseline, k: float = 3.0, min_height_pct: float = 5.0,
               skip_edges: bool = True, min_width: float = 0.0, exclude=(), feet=None):
    """Applies 'peaks only' mode to a baseline already calculated by the method.

    `feet` (X of the feet, two per peak, in order) replaces the automatically found feet:
    this is how the user adjusts the peaks on the plot (dragging, adding or removing anchors).
    Two peaks may share a foot (a peak split in two); an empty list means no peak at all.
    Returns (final_baseline, peaks_only_corrected, peak_regions, noise_σ, anchors_x),
    where anchors_x are only the peak feet (two per peak)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    base = np.asarray(baseline, float)
    n = y.size
    w = max(3, n // 200)
    center, sigma = noise_level(_moving_average(y - base, w))
    thr = k * sigma
    base0 = zero_near_zero(base, thr)
    if feet is not None:
        regions = [(a, b) for a, b in _regions_from_feet(x, feet)
                   if not any(lo <= x[a + int(np.argmax(y[a:b] - base0[a:b]))] <= hi
                              for lo, hi in exclude)]
    else:
        regions = peak_regions(x, y - base0, k, min_height_pct, skip_edges, min_width, exclude)

    # anchors: peak feet (smoothed signal value, 0 if near 0) ...
    ys = _moving_average(y, w)
    feet = sorted({i for a, b in regions for i in (a, b - 1)})
    pts = {i: (0.0 if abs(ys[i]) <= thr else float(ys[i])) for i in feet}
    # ... and the edges of the noise stretches near 0 (outside the peaks) pinned at 0
    inside = np.zeros(n, bool)
    for a, b in regions:
        inside[a:b] = True
    zero = (base0 == 0) & ~inside
    d = np.diff(np.concatenate([[0], zero.astype(int), [0]]))
    for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
        pts.setdefault(int(a), 0.0)
        pts.setdefault(int(b - 1), 0.0)

    if pts:
        idx = np.array(sorted(pts))
        # between the anchors, keep the shape (curvature) of the method's baseline and only
        # fix the offset to pass through the feet; joining the feet with lines would erase the spline
        offset = np.array([pts[i] for i in idx]) - base0[idx]
        final = base0 + np.interp(x, x[idx], offset)
    else:  # no peak and no stretch near 0: nothing to anchor
        final = base0
    out = np.zeros(n)
    for a, b in regions:
        out[a:b] = np.maximum(y[a:b] - final[a:b], 0.0)
    return final, out, regions, sigma, x[np.array(feet, dtype=int)]


def region_events(x, peaks, regions, signal=None):
    """Events table (same keys as derivative.integrate_events), one per peak."""
    x = np.asarray(x, float)
    events = []
    for a, b in regions:
        # foot to foot: same interval as 'area_total' and the "Calculate area" panel
        xs, ps = x[a:b], peaks[a:b]
        i = int(np.argmax(ps))
        events.append({"inicio": float(x[a]), "fim": float(x[b - 1]), "pico": float(xs[i]),
                       "altura": float(ps[i]), "area": float(np.trapezoid(ps, xs)),
                       "area_total": (float(np.trapezoid(np.asarray(signal, float)[a:b], x[a:b]))
                                      if signal is not None else float("nan"))})
    return events


def find_events(x, corrected, signal=None, k: float = 3.0, min_height_pct: float = 5.0,
                skip_edges: bool = True, min_width: float = 0.0, exclude=()):
    """Events as in Origin's Peak Analyzer: with the baseline already subtracted, finds the peaks
    (above k·σ of the noise and with height ≥ min_height_pct % of the largest) and integrates
    each one foot to foot. Noise ripples do not become events."""
    x = np.asarray(x, float)
    c = np.asarray(corrected, float)
    regions = peak_regions(x, c, k, min_height_pct, skip_edges, min_width, exclude)
    return region_events(x, np.clip(c, 0.0, None), regions, signal=signal), regions


def _regions_from_feet(x, feet):
    """Feet (X, two per peak) -> regions (start, end_exclusive) at the nearest samples."""
    f = np.sort(np.asarray(feet, float))[: len(feet) // 2 * 2]
    idx = np.clip(np.searchsorted(x, f), 1, len(x) - 1)
    idx = np.where(np.abs(x[idx - 1] - f) <= np.abs(x[idx] - f), idx - 1, idx)
    return [(int(a), int(b) + 1) for a, b in idx.reshape(-1, 2) if b - a >= 2]


def integral(x, y, lo: float, hi: float) -> float:
    """∫ y dx from lo to hi (trapezoids), with the end values interpolated. X increasing."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    inner = (x > lo) & (x < hi)
    xs = np.concatenate([[lo], x[inner], [hi]])
    ys = np.concatenate([[np.interp(lo, x, y)], y[inner], [np.interp(hi, x, y)]])
    return float(np.trapezoid(ys, xs))


def peak_areas(x, signal, baseline, corrected, lo: float, hi: float) -> dict:
    """Areas between lo and hi (start and end of a peak): under the signal, under the baseline
    and of the peak (under the corrected curve). Unit: Y unit × X unit.

    As in the events table, parts of the peak below the baseline do not count (corrected < 0
    becomes 0): so 'peak' = 'signal' − 'baseline' only when the baseline does not cross the
    signal."""
    return {"signal": integral(x, signal, lo, hi), "baseline": integral(x, baseline, lo, hi),
            "peak": integral(x, np.clip(corrected, 0.0, None), lo, hi)}
