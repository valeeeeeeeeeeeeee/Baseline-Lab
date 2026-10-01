"""Interface languages: Português (BR) and English (EN).

The internal names (METHODS keys, option values such as "sim"/"não") do not change;
only the displayed text is translated. The Portuguese in the method and parameter
definitions (baselines.py, gui.py) is the source; the English translations live here.
"""
from __future__ import annotations

import json
from pathlib import Path

LANGS = {"pt": "Português (BR)", "en": "English (EN)"}
_CONFIG = Path.home() / ".baseline_lab.json"
_lang = "pt"


def get_lang() -> str:
    return _lang


def set_lang(code: str) -> None:
    global _lang
    if code not in LANGS:
        raise ValueError(code)
    _lang = code


def load_lang() -> str:
    """Language saved in the last session (default: Portuguese)."""
    try:
        code = json.loads(_CONFIG.read_text(encoding="utf-8")).get("lang", "pt")
    except (OSError, ValueError, AttributeError):
        return "pt"
    return code if code in LANGS else "pt"


def save_lang(code: str) -> None:
    try:
        data = json.loads(_CONFIG.read_text(encoding="utf-8")) if _CONFIG.exists() else {}
        data["lang"] = code
        _CONFIG.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError):
        pass  # no write permission: the language just isn't saved


def tr(key: str, **kw) -> str:
    """Interface text in the current language (with {name} fields filled from kw)."""
    pt, en = STRINGS[key]
    s = en if _lang == "en" else pt
    return s.format(**kw) if kw else s


def method_name(key: str) -> str:
    return METHOD_EN.get(key, (key, ""))[0] if _lang == "en" else key


def method_desc(key: str, pt_desc: str) -> str:
    return METHOD_EN.get(key, ("", pt_desc))[1] if _lang == "en" else pt_desc


def param_label(p) -> str:
    return PARAM_EN.get(p.label, (p.label, ""))[0] if _lang == "en" else p.label


def param_help(p) -> str:
    return PARAM_EN.get(p.label, ("", p.help))[1] if _lang == "en" else p.help


def choice(value: str) -> str:
    return CHOICE_EN.get(value, value) if _lang == "en" else value


# --------------------------------------------------------------------------- texts
STRINGS: dict[str, tuple[str, str]] = {
    # top bar / left panel
    "import_btn": ("📂  Importar .txt", "📂  Import .txt"),
    "adv_closed": ("⚙  Opções avançadas ▸", "⚙  Advanced options ▸"),
    "adv_open": ("⚙  Opções avançadas ◂", "⚙  Advanced options ◂"),
    "language": ("Idioma:", "Language:"),
    "files": ("Arquivos", "Files"),
    "remove": ("Remover", "Remove"),
    "result": ("Resultado", "Results"),
    "col_start": ("Início", "Start"),
    "col_peak": ("Pico", "Peak"),
    "col_end": ("Fim", "End"),
    "col_event": ("Evento", "Event"),
    "col_total": ("Total", "Total"),
    "empty_title": ("Importe seus arquivos .txt para calcular a baseline",
                    "Import your .txt files to calculate the baseline"),
    "empty_hint": ("Colunas, separador e vírgula decimal são detectados sozinhos.\n"
                   "Vários arquivos de uma vez: segure Ctrl ao selecionar.",
                   "Columns, delimiter and decimal comma are detected automatically.\n"
                   "Several files at once: hold Ctrl while selecting."),
    # advanced options
    "data": ("Dados", "Data"),
    "col_x": ("Coluna X", "X column"),
    "col_y": ("Coluna Y", "Y column"),
    "dtg_chk": ("Calcular DTG = −dY/dX (% do inicial)", "Calculate DTG = −dY/dX (% of initial)"),
    "invert_chk": ("Inverter sinal (picos para baixo)", "Invert signal (downward peaks)"),
    "method": ("Método", "Method"),
    "anchors_frame": ("Âncoras", "Anchors"),
    "anchors_help": ("Clique no gráfico: adiciona uma âncora.\n"
                     "Arraste uma âncora para movê-la ao longo do sinal.\n"
                     "Clique direito sobre uma âncora: remove.\n"
                     "Os ajustes valem para o arquivo, o método e os parâmetros atuais.",
                     "Click on the plot: adds an anchor.\n"
                     "Drag an anchor to move it along the signal.\n"
                     "Right click on an anchor: removes it.\n"
                     "Adjustments apply to the current file, method and parameters."),
    "n_anchors": ("{n} âncoras do método", "{n} method anchors"),
    "n_anchors_edited": ("{n} âncoras (ajustadas à mão)", "{n} anchors (adjusted by hand)"),
    "undo_edits": ("Desfazer ajustes", "Undo adjustments"),
    "clear_anchors": ("Remover todas as âncoras", "Remove all anchors"),
    "restore": ("Voltar ao padrão", "Restore defaults"),
    "peaks_title": ("Detecção de picos", "Peak detection"),
    "peaks_chk": ("Somente picos (baseline perto de 0 = ruído → 0)",
                  "Peaks only (baseline near 0 = noise → 0)"),
    "compare_chk": ("Comparar todos os métodos", "Compare all methods"),
    # (i) balloons
    "help_x": ("Eixo horizontal (ex.: temperatura, número de onda). Os dados são ordenados por X.",
               "Horizontal axis (e.g. temperature, wavenumber). Data are sorted by X."),
    "help_y": ("Sinal em que a baseline é calculada (ex.: DTG, absorbância). Arquivos sem "
               "estas colunas usam o palpite automático.",
               "Signal on which the baseline is calculated (e.g. DTG, absorbance). Files without "
               "these columns use the automatic guess."),
    "help_dtg": ("Use quando Y é a massa (TGA). Calcula −dY/dX suavizado e divide pela massa "
                 "inicial: sai em %/unidade de X, como a coluna 'Deriv. Weight' da TA.",
                 "Use when Y is the mass (TGA). Computes a smoothed −dY/dX and divides by the "
                 "initial mass: result in %/X unit, like TA's 'Deriv. Weight' column."),
    "help_invert": ("Multiplica o sinal por −1. Use quando os eventos aparecem como vales "
                    "(ex.: DSC com endotérmico para baixo): os métodos procuram picos para cima.",
                    "Multiplies the signal by −1. Use when events appear as valleys "
                    "(e.g. DSC with endotherms down): the methods look for upward peaks."),
    "help_peaks": ("Sinal perto de 0 é ruído do ambiente: a baseline ali vira 0 e só os picos "
                   "contam. Cada pico fica com uma âncora em cada pé e vai até o sinal voltar ao "
                   "ruído; fora dos picos o corrigido é 0. Arraste as âncoras dos pés no "
                   "gráfico para ajustar onde cada pico começa e termina. Clique na curva: "
                   "dentro de um pico, divide-o em dois; fora, cria um pico novo. Clique direito "
                   "numa âncora: remove o pico (ou junta dois picos, se for a âncora que os "
                   "divide).",
                   "Signal near 0 is ambient noise: the baseline there becomes 0 and only the "
                   "peaks count. Each peak gets one anchor at each foot and extends until the "
                   "signal returns to the noise; outside the peaks the corrected signal is 0. "
                   "Drag the foot anchors on the plot to adjust where each peak starts and ends. "
                   "Click on the curve: inside a peak, splits it in two; outside, creates a new "
                   "peak. Right click on an anchor: removes the peak (or joins two peaks, if it "
                   "is the anchor that splits them)."),
    "help_compare": ("Sobrepõe as baselines de todos os métodos (padrões; o selecionado usa os "
                     "seus ajustes) para ver quanto a área muda.",
                     "Overlays the baselines of all methods (defaults; the selected one uses "
                     "your settings) to see how much the area changes."),
    # files and messages
    "ft_text": ("Texto e Excel", "Text and Excel"),
    "ft_all": ("Todos", "All"),
    "open_title": ("Importar arquivos .txt ou .xlsx", "Import .txt or .xlsx files"),
    "read_fail": ("Alguns arquivos não foram lidos", "Some files could not be read"),
    "no_file": ("Nenhum arquivo aberto.", "No file open."),
    "bad_cols": ("⚠ Não foi possível usar estas colunas: {exc}", "⚠ Could not use these columns: {exc}"),
    # plot and summary
    "signal": ("Sinal", "Signal"),
    "baseline": ("Baseline", "Baseline"),
    "baseline_zero": ("Baseline (perto de 0 = 0)", "Baseline (near 0 = 0)"),
    "anchors": ("Âncoras", "Anchors"),
    "anchors_feet": ("Âncoras (pés dos picos)", "Anchors (peak feet)"),
    "corrected_axis": ("Corrigido (sinal − baseline)", "Corrected (signal − baseline)"),
    "dtg_label": ("DTG calculada (%/{unit})", "Calculated DTG (%/{unit})"),
    "describe": ("{y}  ×  {x}\nMétodo: {m}", "{y}  ×  {x}\nMethod: {m}"),
    "total_area": ("Área corrigida total: {a}", "Total corrected area: {a}"),
    "calc_area": ("Calcular área", "Calculate area"),
    "mark_noise": ("Marcar como ruído", "Mark as noise"),
    "restore_noise": ("Restaurar picos marcados como ruído", "Restore peaks marked as noise"),
    "noise_marked": ("{n} pico(s) marcado(s) como ruído", "{n} peak(s) marked as noise"),
    "open_window": ("Abrir em uma janela separada", "Open in a separate window"),
    "save_image": ("Baixar imagem", "Download image"),
    "image_saved": ("Imagem salva em:\n{path}", "Image saved to:\n{path}"),
    "area_title": ("Área do pico em {p}", "Area of the peak at {p}"),
    "area_range": ("Entre {a} e {b} (unidade Y × unidade X)",
                   "From {a} to {b} (Y unit × X unit)"),
    "area_peak": ("Do pico (curva corrigida)", "Peak (corrected curve)"),
    "area_signal": ("Sob o sinal", "Under the signal"),
    "area_base": ("Sob a baseline", "Under the baseline"),
    "calcs": ("Cálculos", "Calculations"),
    "calcs_clear": ("Limpar", "Clear"),
    "calcs_remove": ("Remover entrada", "Remove entry"),
    "calcs_empty": ("Nenhum cálculo ainda. Botão direito num pico da tabela → Calcular área.",
                    "No calculations yet. Right click a peak in the table → Calculate area."),
    "calcs_edited": ("âncoras ajustadas", "adjusted anchors"),
    "peaks_info": ("{n} picos · resto do sinal zerado", "{n} peaks · rest of signal zeroed"),
    "anchors_info": ("{a} âncoras · {e} eventos", "{a} anchors · {e} events"),
    "dtg_stats": ("Evento = perda de massa (%) acima da baseline.\n"
                  "Total = perda no intervalo (= queda do TGA).",
                  "Event = mass loss (%) above the baseline.\n"
                  "Total = loss over the interval (= TGA drop)."),
    "compare_area": ("{m}: área {a}", "{m}: area {a}"),
    "compare_summary": ("Comparação de métodos", "Method comparison"),
    "compare_need": ("Marque âncoras para incluir esses métodos.",
                     "Place anchors to include these methods."),
    # core errors
    "err_few_points": ("Poucos pontos para calcular baseline.",
                       "Too few points to calculate a baseline."),
    "err_deriv_anchors": ("A derivada não encontrou âncoras suficientes: aumente a tolerância "
                          "ou a suavização, ou inclua os extremos.",
                          "The derivative did not find enough anchors: increase the tolerance "
                          "or the smoothing, or include the ends."),
    "err_x_unique": ("A coluna X precisa de pelo menos 5 valores diferentes para derivar.",
                     "The X column needs at least 5 distinct values to differentiate."),
    "err_empty": ("Arquivo vazio.", "Empty file."),
    "err_no_numeric": ("Nenhuma linha numérica encontrada no arquivo.",
                       "No numeric rows found in the file."),
}

# internal method name -> (English name, English description)
METHOD_EN = {
    "Derivada 1ª + 2ª": ("Derivative 1st + 2nd",
                         "Anchors where y' ≈ 0 and y'' ≈ 0 (flat stretches). Recommended for DTG."),
    "Derivada 2ª (zeros)": ("Derivative 2nd (zeros)",
                            "Anchors where the curvature y''/(1+y'²)^1.5 ≈ 0 (as in Origin)."),
    "Derivada 2ª (picos)": ("Derivative 2nd (peaks)",
                            "Anchors at the maxima of y'' (as in Origin). Caution: on broad "
                            "peaks the anchor falls on the flank and cuts the peak base."),
    "Polinomial iterativo": ("Iterative polynomial",
                             "Fits a polynomial, ignoring the peaks at each iteration."),
}

# Portuguese parameter label -> (English label, English help)
PARAM_EN = {
    "Suavização (% dos pontos)": (
        "Smoothing (% of points)",
        "Width of the smoothing window (Savitzky-Golay) applied before differentiating, in % of "
        "the number of points. Larger = less noisy derivatives, but close peaks may merge. "
        "Increase it if anchors appear in the noise."),
    "Tolerância y'' (%)": (
        "y'' tolerance (%)",
        "How close to zero the 2nd derivative (curvature) must be for a stretch to count as "
        "baseline, in % of the largest |y''|. Larger = more stretches accepted (more anchors, "
        "which may enter the peak feet); smaller = only very flat stretches."),
    "Proeminência mín. y'' (%)": (
        "Min. y'' prominence (%)",
        "Minimum prominence of a y'' maximum to become an anchor, in % of the largest |y''|. "
        "Larger = only the feet of well-defined peaks; smaller = accepts small maxima (noise)."),
    "Tolerância y' (%)": (
        "y' tolerance (%)",
        "How close to zero the 1st derivative (slope) must be, in % of the largest |y'|. "
        "Larger = accepts sloped stretches as baseline; smaller = only horizontal stretches."),
    "Nº de âncoras": (
        "Number of anchors",
        "Maximum number of automatic anchors: the X axis is split into this many bands and each "
        "band gets at most one anchor, on its longest flat stretch. More anchors = the baseline "
        "follows the background better, but may enter broad peaks."),
    "Interpolação": (
        "Interpolation",
        "How the anchors are joined. Linear: straight lines between anchors (predictable, usual "
        "in thermal analysis). Spline (PCHIP): smooth curve that does not overshoot the anchor "
        "values, but flattens the background near them. Spline (natural cubic): follows curved "
        "backgrounds better, but may rise a little between distant anchors. Outside the "
        "anchors the baseline continues horizontally."),
    "Incluir extremos": (
        "Include ends",
        "Yes: the first and last data points become anchors, and the baseline covers the whole "
        "range. No: beyond the anchors found, the baseline is extended horizontally; use it if "
        "the start or end of the run has artifacts."),
    "Baseline não cruza o sinal": (
        "Baseline does not cross the signal",
        "Yes: where the baseline would pass above the signal, new anchors are placed at the "
        "lowest point until it stays below the curve (avoids negative area). No: uses only the "
        "anchors found by the derivative."),
    "Grau": (
        "Degree",
        "Degree of the polynomial representing the background. Low (1 to 3) for simple "
        "backgrounds; high follows complex curves, but may swallow broad peaks."),
    "Iterações": (
        "Iterations",
        "Maximum number of repetitions: at each one, points above the polynomial (peaks) are "
        "lowered and the fit is redone. Usually converges before the limit."),
    "Limiar k (× ruído σ)": (
        "Threshold k (× noise σ)",
        "Multiple of the ambient noise (σ, estimated automatically). The baseline within k·σ of "
        "0 becomes 0, and only what exceeds k·σ is a peak. Larger = stricter (fewer small "
        "peaks); smaller = accepts weak peaks, but also noise."),
    "Altura mínima (% do maior pico)": (
        "Minimum height (% of largest peak)",
        "Discards peaks lower than this % of the height of the largest peak. Helps ignore "
        "ripples that pass the noise threshold."),
    "Largura mínima (unidades de X)": (
        "Minimum width (X units)",
        "Stretches narrower than this, foot to foot, are noise, not peaks (in 'peaks only' mode "
        "they become 0). They also stop being the 'largest peak' of the minimum height. Use it "
        "when a noise spike is taller than a real peak. 0 = off."),
    "Ignorar picos cortados nas bordas": (
        "Ignore peaks cut at the edges",
        "Yes: a peak that does not return to the noise level before the start or end of the "
        "data is ignored (usually an edge artifact). No: it is kept, even if incomplete."),
}

CHOICE_EN = {"sim": "yes", "não": "no",
             "spline (cúbica natural)": "spline (natural cubic)"}
