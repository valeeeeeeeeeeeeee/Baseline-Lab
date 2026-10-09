"""Rheology: each model is recovered from its own flow curve and chosen as the best one."""
import numpy as np
import pytest

from baseline_lab import i18n
from baseline_lab import rheology as rh
from baseline_lab.io_txt import parse_text
from baseline_lab.rheology_gui import guess_columns

X = np.geomspace(0.1, 1000, 30)
CASES = {
    "newton": (0.85,),
    "bingham": (12.0, 0.4),
    "power": (3.5, 0.55),
    "hb": (8.0, 2.0, 0.6),
    "casson": (5.0, 0.2),
}


def _curve(key, params, noise=0.002, seed=0):
    model = next(m for m in rh.MODELS if m.key == key)
    y = model.func(X, *params)
    return y * (1 + noise * np.random.default_rng(seed).standard_normal(len(X)))


@pytest.mark.parametrize("key", CASES)
def test_best_model_and_parameters(key):
    best = rh.fit_all(X, _curve(key, CASES[key]))[0]
    assert best.model.key == key
    assert best.params == pytest.approx(CASES[key], rel=0.05)
    assert best.r2 > 0.999


@pytest.mark.parametrize("key", CASES)
def test_exact_data_picks_the_simplest_model(key):
    # without noise the models that contain the true one fit just as well: the simplest wins
    assert rh.fit_all(X, _curve(key, CASES[key], noise=0))[0].model.key == key


def test_results_sorted_and_parameters_not_negative():
    fits = rh.fit_all(X, _curve("hb", CASES["hb"]))
    assert len(fits) == len(rh.MODELS)
    assert [f.aicc for f in fits] == sorted(f.aicc for f in fits)
    assert all(v >= 0 for f in fits for v in f.params)


@pytest.mark.parametrize("scale", [1e-8, 1e-6, 1e3, 1e8])
def test_unit_of_the_stress_does_not_change_the_fit(scale):
    y = _curve("hb", CASES["hb"], noise=0.01)
    for a, b in zip(rh.fit_all(X, y), rh.fit_all(X, y * scale)):
        assert a.model.key == b.model.key
        back = [1 if s == "n" else scale for s in a.model.symbols]
        assert np.divide(b.params, back) == pytest.approx(a.params, rel=1e-4, abs=1e-6)
        assert b.r2 == pytest.approx(a.r2, abs=1e-9)


def test_every_model_has_a_name():
    assert {"rheo_m_" + m.key for m in rh.MODELS} <= set(i18n.STRINGS)


def test_ignores_invalid_points_and_order():
    y = _curve("power", CASES["power"])
    x2 = np.concatenate([[0.0, -1.0, np.nan], X[::-1]])
    y2 = np.concatenate([[0.0, 5.0, 1.0], y[::-1]])
    best = rh.fit_all(x2, y2)[0]
    assert best.model.key == "power"
    assert best.params == pytest.approx(CASES["power"], rel=0.05)


def test_too_few_points():
    with pytest.raises(ValueError, match="pelo menos 4"):
        rh.fit_all([1, 2, 3], [1, 2, 3])
    with pytest.raises(ValueError):
        rh.fit_all([1, 1, 1, 1, 1], [1, 2, 3, 4, 5])


def test_few_points_fit_only_the_models_they_allow():
    fits = rh.fit_all(X[:4], 0.5 * X[:4])
    assert [f.model.key for f in fits] == ["newton"]


def test_from_text_file_with_decimal_comma():
    y = _curve("bingham", CASES["bingham"])
    text = "Taxa de cisalhamento (1/s);Tensão de cisalhamento (Pa)\n" + "\n".join(
        f"{a:.6f};{b:.6f}".replace(".", ",") for a, b in zip(X, y))
    df = parse_text(text)
    xi, yi = guess_columns(df.columns)
    assert (xi, yi) == (0, 1)
    assert rh.fit_all(*df.xy(xi, yi))[0].model.key == "bingham"


def test_guess_columns_by_name():
    assert guess_columns(["Shear Stress (Pa)", "Shear Rate (1/s)", "Viscosity (Pa.s)"]) == (1, 0)
    assert guess_columns(["Coluna 1", "Coluna 2"]) == (0, 1)


def test_axis_label_keeps_the_unit_of_the_column():
    from baseline_lab.rheology_gui import axis_label
    assert axis_label("Taxa de cisalhamento", "Shear rate (1/s)") == "Taxa de cisalhamento (1/s)"
    assert axis_label("Tensão de cisalhamento", "Shear stress [Pa]") == "Tensão de cisalhamento (Pa)"
    assert axis_label("Taxa de cisalhamento", "Shear rate") == "Taxa de cisalhamento"
    assert axis_label("Taxa de cisalhamento", "(a) rate") == "Taxa de cisalhamento"
