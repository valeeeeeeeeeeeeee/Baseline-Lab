"""Baseline correction algorithms.

All functions take (x, y, **params) and return the baseline (an array of the
same size as y). The METHODS dictionary describes each method and its
parameters so the GUI can build the controls on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import derivative as dv
from .i18n import tr


# --------------------------------------------------------------------------
# Methods
# --------------------------------------------------------------------------
def _nearest(x, values) -> np.ndarray:
    """Index of the sample closest to each X value (x increasing)."""
    v = np.asarray(values, dtype=float)
    right = np.clip(np.searchsorted(x, v), 1, len(x) - 1)
    return np.where(np.abs(x[right - 1] - v) <= np.abs(x[right] - v), right - 1, right)


def edit_baseline(x, y, base, method_anchors, edits, interp: str = "linear",
                  smooth_pct: float = 0.5):
    """Baseline of a method with the anchors adjusted by hand (dragged, added or deleted).

    `edits`: list of (x, y); y None = new or moved anchor, which sits on the smoothed signal.
    - Method that joins anchors (derivatives): the baseline joins the edited anchors, using
      the method's interpolation.
    - Method without anchors (polynomial): the method's shape is kept and only shifted to pass
      through the anchors (one anchor: shifts everything; two or more: offset interpolated
      between them).
    Returns (baseline, anchors_x)."""
    if not len(edits):  # anchor-joining method with none left: there is no baseline (becomes 0)
        return (base, None) if method_anchors is None else (np.zeros_like(base), np.array([]))
    idx = _nearest(x, [e[0] for e in edits])
    ys = None
    pts = {}
    for i, (_ex, ey) in zip(idx, edits):  # two anchors on the same sample: the last one wins
        if ey is None:
            if ys is None:
                ys = dv.smooth_derivatives(x, y, smooth_pct)[0]
            ey = ys[i]
        pts[int(i)] = float(ey)
    ai = np.array(sorted(pts))
    ay = np.array([pts[i] for i in ai])
    if method_anchors is None:
        return base + np.interp(x, x[ai], ay - base[ai]), x[ai]
    return dv.interp_baseline(x, x[ai], ay, interp), x[ai]


def modpoly(x, y, degree: int = 3, max_iter: int = 100, tol: float = 1e-3, **_):
    """Iterative polynomial with peak rejection (Lieber & Mahadevan-Jansen, 2003)."""
    xs = (x - x.mean()) / (x.std() or 1.0)  # normalize: avoids ill-conditioning
    yw = y.astype(float).copy()
    base = yw
    # the least-squares fit, as np.polyfit does it. Only the values change from one iteration
    # to the next: the matrix that gives the coefficients from them is worked out once
    powers = np.vander(xs, int(degree) + 1)
    scale = np.sqrt((powers * powers).sum(axis=0))
    solve = np.linalg.pinv(powers / scale, rcond=len(xs) * np.finfo(float).eps).T / scale

    def norm(v):  # not np.linalg.norm: on long vectors it spends more starting threads than adding
        return float(np.sqrt(np.square(v).sum()))

    for _i in range(max_iter):
        base = powers @ (yw @ solve)
        new = np.minimum(yw, base)
        if norm(new - yw) / (norm(yw) or 1.0) < tol:
            break
        yw = new
    return base


# --------------------------------------------------------------------------
# Registry (used by the GUI)
# --------------------------------------------------------------------------
@dataclass
class Param:
    key: str
    label: str
    default: object
    minimum: float = 0.0
    maximum: float = 1.0
    integer: bool = False
    log: bool = False  # control on a logarithmic scale (e.g. lambda)
    choices: tuple = ()  # if filled in, becomes a list of options
    help: str = ""       # short explanation shown in the GUI's help balloon


@dataclass
class Method:
    name: str
    func: Callable
    params: list[Param] = field(default_factory=list)
    description: str = ""


_INTERP_HELP = ("Como as âncoras são ligadas. Linear: retas entre âncoras (previsível, usual em "
                "análise térmica). Spline (PCHIP): curva suave que não passa dos valores das "
                "âncoras, mas achata o fundo perto delas. Spline (cúbica natural): acompanha "
                "melhor fundos curvos, mas pode subir um pouco entre âncoras distantes. Fora "
                "das âncoras a baseline segue na horizontal.")


def _deriv_params(tol_default, combined=False, peaks=False, smooth=1.0, tol1=5.0, n=12):
    tol_help = (
        "Proeminência mínima de um máximo de y'' para virar âncora, em % do maior |y''|. "
        "Maior = só os pés de picos bem definidos; menor = aceita máximos pequenos (ruído)."
        if peaks else
        "Quão perto de zero a 2ª derivada (curvatura) precisa estar para o trecho contar "
        "como baseline, em % do maior |y''|. Maior = mais trechos aceitos (mais âncoras, "
        "que podem entrar nos pés dos picos); menor = só trechos bem planos.")
    ps = [Param("smooth_pct", "Suavização (% dos pontos)", smooth, 0.2, 10.0,
                help="Largura da janela de suavização (Savitzky-Golay) aplicada antes de "
                     "derivar, em % do nº de pontos. Maior = derivadas menos ruidosas, mas "
                     "picos próximos podem se fundir. Aumente se surgirem âncoras no ruído."),
          Param("tol_pct", ("Proeminência mín. y'' (%)" if peaks else "Tolerância y'' (%)"),
                tol_default, 0.1, 50.0, log=True, help=tol_help)]
    if combined:
        ps.append(Param("tol1_pct", "Tolerância y' (%)", tol1, 0.1, 50.0, log=True,
                        help="Quão perto de zero a 1ª derivada (inclinação) precisa estar, em % "
                             "do maior |y'|. Maior = aceita trechos inclinados como baseline; "
                             "menor = só trechos horizontais."))
    ps += [Param("n_anchors", "Nº de âncoras", n, 2, 60, integer=True,
                 help="Máximo de âncoras automáticas: o eixo X é dividido nesse nº de faixas e "
                      "cada faixa recebe no máximo uma âncora, no trecho plano mais longo. Mais "
                      "âncoras = a baseline segue melhor o fundo, mas pode entrar em picos largos."),
           Param("interp", "Interpolação", "linear", choices=dv.INTERP_CHOICES,
                 help=_INTERP_HELP),
           Param("include_ends", "Incluir extremos", "sim", choices=("sim", "não"),
                 help="Sim: o primeiro e o último ponto dos dados viram âncoras, e a baseline "
                      "cobre toda a faixa. Não: além das âncoras achadas, a baseline é prolongada "
                      "na horizontal; use se o início ou o fim da corrida tiver artefatos."),
           Param("below", "Baseline não cruza o sinal", "sim", choices=("sim", "não"),
                 help="Sim: onde a baseline passaria acima do sinal, novas âncoras são postas no "
                      "ponto mais baixo até ela ficar abaixo da curva (evita área negativa). "
                      "Não: usa só as âncoras achadas pela derivada.")]
    return ps


METHODS: dict[str, Method] = {
    "Derivada 1ª + 2ª": Method(
        "Derivada 1ª + 2ª", dv.deriv_combined,
        _deriv_params(dv.COMBINED_DEFAULTS["tol_pct"], combined=True,
                      smooth=dv.COMBINED_DEFAULTS["smooth_pct"],
                      tol1=dv.COMBINED_DEFAULTS["tol1_pct"], n=dv.COMBINED_DEFAULTS["n_anchors"]),
        description="Âncoras onde y' ≈ 0 e y'' ≈ 0 (trechos planos). Recomendado para DTG."),
    "Derivada 2ª (zeros)": Method(
        "Derivada 2ª (zeros)", dv.deriv_zeros, _deriv_params(5.0),
        description="Âncoras onde a curvatura y''/(1+y'²)^1.5 ≈ 0 (como no Origin)."),
    "Derivada 2ª (picos)": Method(
        "Derivada 2ª (picos)", dv.deriv_peaks, _deriv_params(5.0, peaks=True),
        description="Âncoras nos máximos de y'' (como no Origin). Atenção: em picos largos "
                    "a âncora cai no flanco e corta a base do pico."),
    "Polinomial iterativo": Method(
        "Polinomial iterativo", modpoly,
        [Param("degree", "Grau", 3, 1, 12, integer=True,
               help="Grau do polinômio que representa o fundo. Baixo (1 a 3) para fundos "
                    "simples; alto acompanha curvas complexas, mas pode engolir picos largos."),
         Param("max_iter", "Iterações", 100, 10, 500, integer=True,
               help="Nº máximo de repetições: a cada uma, os pontos acima do polinômio (picos) "
                    "são rebaixados e o ajuste é refeito. Costuma convergir antes do limite.")],
        description="Ajusta polinômio ignorando picos a cada iteração."),
}


def compute_full(method: str, x, y, edits=None, **params):
    """Returns (baseline, corrected, anchors_x or None).
    `edits`: anchors adjusted by hand on top of the method's result (see edit_baseline)."""
    m = METHODS[method]
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) < 5:
        raise ValueError(tr("err_few_points"))
    out = m.func(x, y, **params)
    anchors = None
    if isinstance(out, tuple):
        out, anchors = out
    base = np.asarray(out, dtype=float)
    if edits is not None:
        base, anchors = edit_baseline(x, y, base, anchors, edits, params.get("interp", "linear"),
                                      params.get("smooth_pct", 0.5))
    return base, y - base, anchors


def compute(method: str, x, y, **params):
    """Calculates the baseline and returns (baseline, corrected)."""
    base, corr, _ = compute_full(method, x, y, **params)
    return base, corr
