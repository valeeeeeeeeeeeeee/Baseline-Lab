"""Languages: every text used has a translation, and the core answers in the chosen language."""
import re
from pathlib import Path

import numpy as np
import pytest

from baseline_lab import baselines as bl
from baseline_lab import i18n
from baseline_lab.gui import PEAK_PARAMS

PKG = Path(__file__).parent.parent / "src" / "baseline_lab"


@pytest.fixture(autouse=True)
def _back_to_portuguese():
    yield
    i18n.set_lang("pt")


def test_every_used_key_exists():
    used = set()
    for f in PKG.glob("*.py"):
        used |= set(re.findall(r"""\btr\(\s*["']([a-z0-9_]+)["']""", f.read_text(encoding="utf-8")))
    assert used, "no tr() call found"
    assert used - set(i18n.STRINGS) == set()


def test_methods_parameters_and_options_have_english():
    assert set(bl.METHODS) <= set(i18n.METHOD_EN)
    params = [p for m in bl.METHODS.values() for p in m.params] + PEAK_PARAMS
    missing = {p.label for p in params} - set(i18n.PARAM_EN)
    assert not missing, missing
    for p in params:
        for c in p.choices:
            i18n.set_lang("en")
            assert i18n.choice(c) not in ("sim", "não")
    i18n.set_lang("en")
    for p in params:
        assert i18n.param_label(p) != p.label
        assert i18n.param_help(p) and i18n.param_help(p) != p.help


def test_english_has_no_portuguese_text():
    i18n.set_lang("en")
    markers = re.compile(r"[ãõçáéíóúâêô]|\b(não|âncora|pico|sinal|arquivo)\b", re.I)
    for key, (_pt, en) in i18n.STRINGS.items():
        assert not markers.search(en), (key, en)


def test_core_errors_in_the_chosen_language():
    x = np.linspace(0, 1, 3)
    i18n.set_lang("en")
    with pytest.raises(ValueError, match="Too few points"):
        bl.compute("Polinomial iterativo", x, x)
    i18n.set_lang("pt")
    with pytest.raises(ValueError, match="Poucos pontos"):
        bl.compute("Polinomial iterativo", x, x)
