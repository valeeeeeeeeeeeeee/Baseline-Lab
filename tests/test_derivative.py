"""Tests of the derivative baselines and of the TA Instruments file."""
from pathlib import Path

import numpy as np
import pytest

from baseline_lab import baselines as bl
from baseline_lab import derivative as dv
from baseline_lab.io_txt import read_file

TA = Path(__file__).parent.parent / "examples" / "pec_cit05_14d.txt"


def _synthetic_dtg():
    rng = np.random.default_rng(3)
    x = np.linspace(25, 900, 6000)
    background = 0.012 - 1e-5 * (x - 25)
    peaks = 0.08 * np.exp(-((x - 100) / 15) ** 2) + 0.05 * np.exp(-((x - 450) / 12) ** 2)
    return x, background + peaks + rng.normal(0, 4e-4, x.size), background, peaks


@pytest.mark.parametrize("name", ["Derivada 1ª + 2ª", "Derivada 2ª (zeros)"])
def test_derivative_methods_recover_background_and_area(name):
    x, y, background, peaks = _synthetic_dtg()
    base, corr, anchors = bl.compute_full(name, x, y)
    assert anchors is not None and len(anchors) >= 3
    assert np.sqrt(np.mean((base - background) ** 2)) < 2e-3
    ev = dv.integrate_events(x, corr, anchors)
    true_area = np.trapezoid(peaks, x)
    assert sum(e["area"] for e in ev if e["area"] > 0) == pytest.approx(true_area, rel=0.1)


def test_peaks_anchors_at_the_feet_and_baseline_below():
    # 2nd derivative (peaks): anchors at the maxima of y'' (±1.22σ on a Gaussian)
    x, y, background, peaks = _synthetic_dtg()
    base, corr, anchors = bl.compute_full("Derivada 2ª (picos)", x, y)
    for center in (100, 450):
        assert np.any((anchors > center - 35) & (anchors < center))
        assert np.any((anchors > center) & (anchors < center + 35))
    assert np.all(base <= y.max())
    assert corr.min() > -0.01


def test_calculated_dtg_is_positive_and_in_percent():
    x = np.linspace(25, 800, 4000)
    w = 10 - 2 / (1 + np.exp(-(x - 400) / 15))  # loses 2 mg out of 10 mg = 20 %
    d = dv.dtg(x, w)
    assert d.max() > 0 and abs(d.min()) < 0.05 * d.max()
    assert np.trapezoid(d, x) == pytest.approx(20.0, rel=0.02)


@pytest.mark.skipif(not TA.exists(), reason="example file missing")
def test_ta_q600_file():
    df = read_file(TA)
    assert df.columns[1] == "Temperature (°C)"
    assert df.columns[4] == "Deriv. Weight (%/°C)"
    x, y = df.xy(1, 4)
    base, corr, anchors = bl.compute_full("Derivada 1ª + 2ª", x, y)
    ev = dv.integrate_events(x, corr, anchors, signal=y)
    peaks = sorted(round(e["pico"]) for e in ev)
    assert any(70 <= p <= 90 for p in peaks) and any(410 <= p <= 430 for p in peaks) \
        and any(680 <= p <= 700 for p in peaks)
    # event + baseline = TGA loss over the interval
    T, W = df.xy(1, 2)
    for e in ev:
        tga = (np.interp(e["inicio"], T, W) - np.interp(e["fim"], T, W)) / W[0] * 100
        assert e["area_total"] == pytest.approx(tga, abs=0.02)


def test_constant_x_gives_a_clear_error():
    with pytest.raises(ValueError):
        dv.smooth_derivatives(np.ones(50), np.arange(50.0))
