"""Tests of the 'peaks only' mode."""
import numpy as np
import pytest

from baseline_lab import peaks as pk


def _corrected(seed=1):
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 100, 4000)
    peaks = 1.0 * np.exp(-((x - 30) / 2) ** 2) + 0.4 * np.exp(-((x - 70) / 4) ** 2)
    return x, peaks + rng.normal(0, 0.01, x.size), peaks


def test_zeroes_outside_the_peaks_and_preserves_area():
    x, c, peaks = _corrected()
    _, out, regions, _, _ = pk.peaks_only(x, c, np.zeros_like(c))
    assert len(regions) == 2
    outside = np.ones(x.size, bool)
    for a, b in regions:
        outside[a:b] = False
    assert np.all(out[outside] == 0) and np.all(out >= 0)
    ev = pk.region_events(x, out, regions)
    for e, center, true in zip(ev, (30, 70), (np.sqrt(np.pi) * 2, 0.4 * np.sqrt(np.pi) * 4)):
        assert e["pico"] == pytest.approx(center, abs=0.5)
        assert e["area"] == pytest.approx(true, rel=0.03)  # the peak feet are not cut off


def test_minimum_height_discards_small_peak():
    x, c, _ = _corrected()
    assert len(pk.peak_regions(x, c, min_height_pct=50)) == 1


def test_noise_only_gives_no_peak():
    rng = np.random.default_rng(0)
    x = np.linspace(0, 1, 3000)
    y = rng.normal(0, 1, x.size)
    _, out, regions, _, _ = pk.peaks_only(x, y, np.zeros_like(y), k=5)
    assert regions == [] and not out.any()


def test_peak_cut_off_at_the_edge():
    x = np.linspace(0, 100, 2000)
    c = np.exp(-((x - 50) / 3) ** 2) + 0.5 * np.exp(-((x - 100) / 3) ** 2)
    assert len(pk.peak_regions(x, c)) == 1
    assert len(pk.peak_regions(x, c, skip_edges=False)) == 2


def test_baseline_near_zero_becomes_zero_and_does_not_copy_noise():
    # signal = peaks + background that drops to 0 + ambient noise
    rng = np.random.default_rng(2)
    x = np.linspace(0, 1000, 6000)
    background = np.clip(0.012 * (1 - x / 800), 0, None)
    peaks = 0.06 * np.exp(-((x - 200) / 15) ** 2) + 0.05 * np.exp(-((x - 600) / 20) ** 2)
    y = background + peaks + rng.normal(0, 5e-4, x.size)
    base0, out, regions, sigma, feet = pk.peaks_only(x, y, background)
    tail = x > 850                                   # ambient noise only
    assert np.all(base0[tail] == 0)                  # baseline zeroed, does not follow the noise
    assert len(regions) == 2 and not out[tail].any()
    assert len(feet) == 4                            # only one anchor at each foot of each peak
    for (a, b), (fa, fb) in zip(regions, feet.reshape(2, 2)):
        assert (fa, fb) == (x[a], x[b - 1])
        under = base0[a:b]                           # under the peak: line between the two feet
        assert np.allclose(np.diff(under, 2), 0, atol=1e-12)
        assert np.allclose(under, background[a:b], atol=2e-3)
    ev = pk.region_events(x, out, regions)
    for e, true in zip(ev, (0.06 * 15 * np.sqrt(np.pi), 0.05 * 20 * np.sqrt(np.pi))):
        assert e["area"] == pytest.approx(true, rel=0.05)


def test_isolated_point_near_zero_is_not_zeroed():
    base = np.full(1000, 0.01)
    base[500] = 0.0001                               # anchor that touches 0 at a single point
    assert pk.zero_near_zero(base, 0.002)[500] == pytest.approx(0.0001)
    base[400:700] = 0.0001                           # wide stretch: ambient noise
    assert np.all(pk.zero_near_zero(base, 0.002)[400:700] == 0)


def test_keeps_the_curvature_of_the_method_baseline():
    # curved background under a broad peak: joining the feet with a line would be off by ~0.4 mid-peak
    x = np.linspace(0, 100, 2001)
    background = 2e-3 * (x - 40) ** 2 + 1
    y = background + 5 * np.exp(-((x - 50) / 6) ** 2) + np.random.default_rng(3).normal(0, 0.01, x.size)
    final, _, _, _, feet = pk.peaks_only(x, y, background)
    under = (x >= feet[0]) & (x <= feet[-1])
    assert np.abs(final - background)[under].max() < 0.05


def test_peak_areas():
    x = np.linspace(0, 100, 2001)
    base = 1 + 0.01 * x
    peak = 2 * np.exp(-((x - 50) / 5) ** 2)
    a = pk.peak_areas(x, base + peak, base, peak, 20, 80)
    assert a["peak"] == pytest.approx(2 * 5 * np.sqrt(np.pi), rel=1e-4)
    assert a["baseline"] == pytest.approx(60 + 0.005 * (80 ** 2 - 20 ** 2), rel=1e-6)
    assert a["signal"] == pytest.approx(a["baseline"] + a["peak"], rel=1e-9)
    # ends off a sample: interpolated value, not the nearest point
    assert pk.integral(x, base, 20.01, 20.04) == pytest.approx(0.03 * (1 + 0.01 * 20.025))


def test_feet_adjusted_by_the_user():
    x = np.linspace(0, 100, 2001)
    y = 0.5 + 2 * np.exp(-((x - 50) / 4) ** 2)
    _, _, regions, _, feet = pk.peaks_only(x, y, np.full_like(y, 0.5))
    assert len(feet) == 2
    # drag the feet to 35 and 60: the peak now goes exactly from one to the other
    _, out, regions, _, feet = pk.peaks_only(x, y, np.full_like(y, 0.5), feet=[60.0, 35.0])
    assert list(feet) == [35.0, 60.0] and len(regions) == 1
    assert not out[x < 35].any() and not out[x > 60].any()


def test_table_and_panel_give_the_same_area():
    x = np.linspace(0, 200, 400)
    background = 0.2 + 0.002 * x
    corr = 2 * np.exp(-((x - 60) / 5) ** 2)
    corr[100:103] = -0.05  # inside the peak, the corrected curve goes below zero
    y = background + corr
    regions = [(90, 150)]
    (e,) = pk.region_events(x, np.clip(corr, 0.0, None), regions, signal=y)  # as find_events does
    a = pk.peak_areas(x, y, background, corr, e["inicio"], e["fim"])
    assert a["peak"] == pytest.approx(e["area"], rel=1e-12)
    assert a["signal"] == pytest.approx(e["area_total"], rel=1e-12)
    assert a["peak"] > a["signal"] - a["baseline"]  # the negative stretches do not subtract


def _noise_bigger_than_the_peak():
    rng = np.random.default_rng(5)
    x = np.linspace(0, 100, 2000)
    y = 0.3 * np.exp(-((x - 30) / 4) ** 2) + rng.normal(0, 0.005, x.size)  # real, broad peak
    y += 1.0 * np.exp(-((x - 70) / 0.15) ** 2)  # noise spike: 3x taller, narrow
    return x, y


def test_noise_bigger_than_the_peak_dominates_without_filter():
    x, y = _noise_bigger_than_the_peak()
    # a high minimum height to cut the noise cuts the real peak first (the noise is the "largest")
    _, _, regions, _, _ = pk.peaks_only(x, y, np.zeros_like(y), min_height_pct=70)
    assert [round(x[int(np.mean(r))]) for r in regions] == [70]


def test_minimum_width_zeroes_the_noise_and_keeps_the_peak():
    x, y = _noise_bigger_than_the_peak()
    _, out, regions, _, _ = pk.peaks_only(x, y, np.zeros_like(y), min_width=3.0)
    assert len(regions) == 1 and x[regions[0][0]] < 30 < x[regions[0][1] - 1] < 45
    assert not out[(x > 65) & (x < 75)].any()  # the noise became 0
    # the noise also stops being the reference for the minimum height
    _, _, regions, _, _ = pk.peaks_only(x, y, np.zeros_like(y), min_width=3.0, min_height_pct=70)
    assert len(regions) == 1


def test_peak_marked_as_noise_leaves_and_is_not_a_reference():
    x, y = _noise_bigger_than_the_peak()
    ev, _ = pk.find_events(x, y)
    noise = next(e for e in ev if e["pico"] > 60)
    _, out, regions, _, _ = pk.peaks_only(x, y, np.zeros_like(y), min_height_pct=70,
                                          exclude=[(noise["inicio"], noise["fim"])])
    assert len(regions) == 1 and x[regions[0][0]] < 30 < x[regions[0][1] - 1]
    assert not out[(x > 65) & (x < 75)].any()
    ev2, _ = pk.find_events(x, y, exclude=[(noise["inicio"], noise["fim"])])
    assert [round(e["pico"]) for e in ev2] == [30]
