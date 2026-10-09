"""Core tests (no GUI). Run with: python -m pytest -q"""
import numpy as np
import pytest

from baseline_lab import baselines as bl
from baseline_lab import derivative as dv
from baseline_lab.io_txt import parse_text


def _signal(n=1000, seed=0):
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 100, n)
    background = 5 + 0.05 * x + 2e-3 * (x - 50) ** 2 / 10
    peaks = 40 * np.exp(-((x - 30) / 1.5) ** 2) + 25 * np.exp(-((x - 70) / 2.5) ** 2)
    y = background + peaks + rng.normal(0, 0.15, n)
    return x, y, background


def test_polynomial_recovers_background():
    x, y, background = _signal()
    base, corr = bl.compute("Polinomial iterativo", x, y, degree=3)
    error = np.sqrt(np.mean((base - background) ** 2))
    assert error < 1.5, f"RMSE {error:.2f}"
    # the main peak must survive the correction
    assert corr.max() > 30


@pytest.mark.parametrize("degree", [1, 3, 8])
def test_polynomial_is_the_fit_repeated(degree):
    """The fit is solved once for all the iterations: the baseline is the one of fitting the
    polynomial again at each of them."""
    x, y, _background = _signal()
    xs = (x - x.mean()) / x.std()
    yw = y.copy()
    for _ in range(100):
        base = np.polyval(np.polyfit(xs, yw, degree), xs)
        new = np.minimum(yw, base)
        if np.linalg.norm(new - yw) / np.linalg.norm(yw) < 1e-3:
            break
        yw = new
    np.testing.assert_allclose(bl.modpoly(x, y, degree=degree), base, rtol=1e-8, atol=1e-8)


def test_method_list():
    assert list(bl.METHODS) == ["Derivada 1ª + 2ª", "Derivada 2ª (zeros)", "Derivada 2ª (picos)",
                                "Polinomial iterativo"]


def test_adjustment_without_changes_keeps_the_baseline():
    x, y, _ = _signal()
    base, _, anc = bl.compute_full("Derivada 1ª + 2ª", x, y)
    edits = [[a, float(np.interp(a, x, base))] for a in anc]  # how the GUI starts editing
    base2, _, anc2 = bl.compute_full("Derivada 1ª + 2ª", x, y, edits=edits)
    assert np.allclose(base2, base) and np.allclose(anc2, anc)


def test_moving_a_derivative_anchor_only_changes_its_neighborhood():
    x, y, _ = _signal()
    base, _, anc = bl.compute_full("Derivada 1ª + 2ª", x, y)
    edits = [[a, float(np.interp(a, x, base))] for a in anc]
    i = len(edits) // 2
    edits[i] = [edits[i][0] + 3.0, None]  # dragged: it sits on the smoothed signal
    base2, _, anc2 = bl.compute_full("Derivada 1ª + 2ª", x, y, edits=edits)
    far = (x < anc[i - 1]) | (x > anc[i + 1])
    assert np.allclose(base2[far], base[far]) and not np.allclose(base2, base)
    assert anc2[i] == pytest.approx(anc[i] + 3.0, abs=0.2)
    del edits[i]  # deleting works too
    assert len(bl.compute_full("Derivada 1ª + 2ª", x, y, edits=edits)[2]) == len(anc) - 1


def test_anchor_on_polynomial_shifts_keeping_the_shape():
    x, y, _ = _signal()
    base, _, anc = bl.compute_full("Polinomial iterativo", x, y)
    assert anc is None
    target = base[500] + 2.0
    base2, _, anc2 = bl.compute_full("Polinomial iterativo", x, y, edits=[[x[500], target]])
    assert np.allclose(base2 - base, 2.0) and list(anc2) == [x[500]]


def test_parser_decimal_comma_and_header():
    txt = "Amostra A\nTemp (C);Massa (mg)\n25,0;10,50\n30,0;10,48\n35,0;10,40\n"
    df = parse_text(txt)
    assert df.columns == ["Temp (C)", "Massa (mg)"]
    assert df.data.shape == (3, 2)
    assert df.data[1, 0] == pytest.approx(30.0)
    assert df.data[2, 1] == pytest.approx(10.40)


def test_parser_tab_decimal_point_multicolumn():
    txt = "# meta\nx\ty1\ty2\n1.0\t2.0\t3.0\n2.0\t2.5\t3.5\n3.0\t3.0\t4.0\n"
    df = parse_text(txt)
    assert df.data.shape == (3, 3)
    x, y = df.xy(0, 2)
    assert list(y) == [3.0, 3.5, 4.0]


def test_parser_spaces_and_no_header():
    df = parse_text("1 2\n2 4\n3 6\n")
    assert df.data.shape == (3, 2)


def test_parser_file_without_numbers():
    with pytest.raises(ValueError):
        parse_text("só texto\noutro texto\n")


def test_latin1_reading_with_degree_celsius(tmp_path):
    from baseline_lab.io_txt import read_file
    p = tmp_path / "a.txt"
    p.write_bytes("Temp (°C);Massa (%)\n25,0;10,5\n30,0;10,4\n35,0;10,3\n".encode("latin-1"))
    df = read_file(p)
    assert df.columns == ["Temp (°C)", "Massa (%)"] and df.data.shape == (3, 2)


@pytest.mark.parametrize("interp", ["linear", "spline (PCHIP)", "spline (cúbica natural)"])
def test_anchors_out_of_range_or_repeated_do_not_break(interp):
    x, y, _ = _signal()
    # clicks before the start / after the end / on the same sample
    edits = [[-10, None], [-5, None], [50, None], [50.01, None], [120, None]]
    base, _, anc = bl.compute_full("Derivada 1ª + 2ª", x, y, edits=edits, interp=interp)
    assert np.all(np.isfinite(base))
    # out of range: pinned to the ends (-10 and -5 become one); X strictly increasing
    assert anc[0] == x[0] and anc[-1] == x[-1] and len(anc) == 4 and np.all(np.diff(anc) > 0)


@pytest.mark.parametrize("interp", ["spline (PCHIP)", "spline (cúbica natural)"])
def test_spline_stays_horizontal_outside_the_anchors(interp):
    x = np.linspace(0, 100, 1001)
    ax = np.array([20.0, 30, 70, 80])
    base = dv.interp_baseline(x, ax, 2e-3 * (ax - 40) ** 2, interp)
    before, after = base[x < 20], base[x > 80]
    assert np.allclose(before, before[-1]) and np.allclose(after, after[0])


def test_natural_cubic_follows_curved_background():
    x = np.linspace(0, 100, 1001)
    background = 2e-3 * (x - 40) ** 2
    ax = np.array([0.0, 20, 30, 70, 80, 100])
    error = {k: np.abs(dv.interp_baseline(x, ax, 2e-3 * (ax - 40) ** 2, k) - background).max()
             for k in ("spline (PCHIP)", "spline (cúbica natural)")}
    assert error["spline (cúbica natural)"] < 0.1 < error["spline (PCHIP)"]


def test_anchor_uses_nearest_point():
    x = np.arange(10.0)
    assert list(bl._nearest(x, [2.1, 7.9, -3, 20])) == [2, 8, 0, 9]


def test_parser_spaces_with_decimal_comma():
    df = parse_text("25,0 10,50\n30,0 10,48\n35,0 10,40\n")
    assert df.data.shape == (3, 2) and df.data[2, 1] == pytest.approx(10.40)
    df = parse_text("1,5 2,5 3,5\n2,5 3,5 4,5\n")
    assert df.data.shape == (2, 3)


def test_parser_numeric_line_in_header():
    df = parse_text("Info\n9.5\t3\nT\tA\tB\n1\t2\t3\n2\t3\t4\n3\t4\t5\n4\t5\t6\n")
    assert df.columns == ["T", "A", "B"] and df.data.shape == (4, 3)


def test_column_guess():
    from baseline_lab.gui import guess_columns
    ta = ["Time (min)", "Temperature (°C)", "Weight (mg)", "Heat Flow (mW)", "Deriv. Weight (%/°C)"]
    assert guess_columns(ta) == (1, 4, False)
    assert guess_columns(["Temp (°C)", "Massa (%)"]) == (0, 1, True)   # mass only: calculates DTG
    assert guess_columns(["Numero de onda", "Absorbancia"]) == (0, 1, False)
