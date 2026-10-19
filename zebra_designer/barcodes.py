"""Catálogo de simbologías de códigos de barras soportadas por ZPL II.

Cada simbología define:
  - los parámetros editables (con su valor por defecto),
  - cómo se genera el comando ZPL (^Bx),
  - cómo se previsualiza con zint (render aproximado en puntos de impresora).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw

try:
    import zint
except ImportError:  # la app funciona igual, solo sin vista previa real
    zint = None

from .fonts import get_font


@dataclass
class Param:
    key: str
    label: str
    kind: str  # int | float | bool | choice | str
    default: object
    options: list | None = None  # para choice: [(valor, etiqueta), ...] o [valor, ...]
    minv: float | None = None
    maxv: float | None = None


@dataclass
class Spec:
    key: str
    name: str
    kind: str  # linear | stacked | matrix | postal | maxi | databar
    sample: str
    params: list[Param] = field(default_factory=list)
    zpl: Callable[[dict, str], str] | None = None
    zint_setup: Callable[[object, dict, str], str] | None = None
    data_prefix: Callable[[dict], str] | None = None
    note: str = ""


YN = lambda b: "Y" if b else "N"

# --- parámetros comunes ----------------------------------------------------
P_MW = Param("mw", "Ancho módulo (dots)", "int", 2, minv=1, maxv=10)
P_RATIO = Param("ratio", "Relación ancho/estrecho", "float", 3.0, minv=2.0, maxv=3.0)
P_H = Param("h", "Alto barras (dots)", "int", 80, minv=1, maxv=9999)
P_HR = Param("hr", "Texto legible", "bool", True)
P_HRA = Param("hr_above", "Texto arriba", "bool", False)


def _by(p, ratio=False):
    r = f"{float(p.get('ratio', 3.0)):.1f}" if ratio else ""
    return f"^BY{int(p['mw'])},{r},{int(p.get('h', 10))}" if ratio else f"^BY{int(p['mw'])}"


def _lin(p, ratio=True):
    return [P_MW] + ([P_RATIO] if ratio else []) + [P_H, P_HR, P_HRA]


Z = (lambda name: getattr(zint.Symbology, name)) if zint else (lambda name: None)


def _setz(sym_name, **opts):
    def setup(s, p, data):
        s.symbology = Z(sym_name)
        for k, v in opts.items():
            setattr(s, k, v(p) if callable(v) else v)
        return data
    return setup


def _upce_from_upca(data: str) -> str:
    """Comprime 10 dígitos (fabricante+producto) a UPC-E como hace la impresora."""
    d = "".join(c for c in data if c.isdigit())
    if len(d) == 11:
        d = d[1:]
    if len(d) != 10:
        return data
    m, p = d[:5], d[5:]
    if m[2:] in ("000", "100", "200") and p[:2] == "00":
        e = m[:2] + p[2:] + m[2]
    elif m[3:] == "00" and p[:3] == "000":
        e = m[:3] + p[3:] + "3"
    elif m[4] == "0" and p[:4] == "0000":
        e = m[:4] + p[4] + "4"
    elif p[:4] == "0000" and p[4] in "56789":
        e = m + p[4]
    else:
        return data
    return "0" + e


def _upce_setup(s, p, data):
    s.symbology = Z("UPCE")
    return _upce_from_upca(data) if len(data) >= 10 else data


def _codabar_setup(s, p, data):
    s.symbology = Z("CODABAR")
    return f"{p.get('start', 'A')}{data}{p.get('stop', 'A')}"


def _tlc39_setup(s, p, data):
    s.symbology = Z("CODE39")
    return data.split(",")[0]


DBAR_TYPES = [(1, "1 Omnidireccional"), (2, "2 Truncado"), (3, "3 Apilado"),
              (4, "4 Apilado omni"), (5, "5 Limitado"), (6, "6 Expandido")]


def _dbar_setup(s, p, data):
    t = int(p.get("type", 1))
    seg = int(p.get("seg", 22))
    if t == 6:
        s.symbology = Z("DBAR_EXPSTK" if seg < 22 else "DBAR_EXP")
        if seg < 22:
            s.option_2 = max(1, seg // 2)
        else:
            s.height = 34
        mode = zint.InputMode.GS1
        if "(" in data:
            mode = mode | zint.InputMode.GS1PARENS
        s.input_mode = mode
        return data
    s.symbology = Z({1: "DBAR_OMN", 2: "DBAR_OMN", 3: "DBAR_STK", 4: "DBAR_OMNSTK", 5: "DBAR_LTD"}[t])
    if t in (1, 2, 5):
        s.height = {1: 33, 2: 13, 5: 10}[t]
    return "".join(c for c in data if c.isdigit())[:13]


QR_Y_OFFSET = 10
QR_ECC = [("H", "H - Ultra alta"), ("Q", "Q - Alta"), ("M", "M - Estándar"), ("L", "L - Alta densidad")]


SPECS: dict[str, Spec] = {}


def _add(spec: Spec):
    SPECS[spec.key] = spec


# --- lineales ---------------------------------------------------------------
_add(Spec("code128", "Code 128 (^BC)", "linear", "12345678",
          _lin(None, ratio=False) + [Param("check", "Dígito UCC (mod 103)", "bool", False),
                                     Param("mode", "Modo", "choice", "N",
                                           [("N", "N - Normal"), ("U", "U - UCC case"),
                                            ("A", "A - Automático"), ("D", "D - UCC/EAN (GS1)")])],
          zpl=lambda p, o: f"{_by(p)}^BC{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])},{YN(p['check'])},{p['mode']}",
          zint_setup=lambda s, p, d: (setattr(s, "symbology", Z("GS1_128") if p.get("mode") == "D" else
                                                     (Z("CODE128") if p.get("mode") == "A" else Z("CODE128AB"))),
                                      p.get("mode") == "D" and setattr(s, "input_mode", zint.InputMode.GS1 | (zint.InputMode.GS1PARENS if "(" in d else zint.InputMode.GS1)),
                                      d)[2]))
_add(Spec("code39", "Code 39 (^B3)", "linear", "ABC123",
          _lin(None) + [Param("check", "Dígito control mod 43", "bool", False)],
          zpl=lambda p, o: f"{_by(p, True)}^B3{o},{YN(p['check'])},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])}",
          zint_setup=_setz("CODE39", option_2=lambda p: 1 if p.get("check") else 0)))
_add(Spec("code93", "Code 93 (^BA)", "linear", "ABC123",
          _lin(None, ratio=False) + [Param("check", "Imprimir dígito control", "bool", False)],
          zpl=lambda p, o: f"{_by(p)}^BA{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])},{YN(p['check'])}",
          zint_setup=_setz("CODE93")))
_add(Spec("code11", "Code 11 (^B1)", "linear", "123-45",
          _lin(None) + [Param("check", "Un dígito control (si no, dos)", "bool", False)],
          zpl=lambda p, o: f"{_by(p, True)}^B1{o},{YN(p['check'])},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])}",
          zint_setup=_setz("CODE11", option_2=lambda p: 1 if p.get("check") else 0)))
_add(Spec("i2of5", "Interleaved 2 of 5 (^B2)", "linear", "12345670",
          _lin(None) + [Param("check", "Dígito control mod 10", "bool", False)],
          zpl=lambda p, o: f"{_by(p, True)}^B2{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])},{YN(p['check'])}",
          zint_setup=_setz("C25INTER", option_2=lambda p: 1 if p.get("check") else 0)))
_add(Spec("ind2of5", "Industrial 2 of 5 (^BI)", "linear", "123456", _lin(None),
          zpl=lambda p, o: f"{_by(p, True)}^BI{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])}",
          zint_setup=_setz("C25IND")))
_add(Spec("std2of5", "Standard 2 of 5 (^BJ)", "linear", "123456", _lin(None),
          zpl=lambda p, o: f"{_by(p, True)}^BJ{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])}",
          zint_setup=_setz("C25IATA")))
_add(Spec("codabar", "ANSI Codabar (^BK)", "linear", "123456",
          _lin(None) + [Param("start", "Carácter inicio", "choice", "A", ["A", "B", "C", "D"]),
                        Param("stop", "Carácter fin", "choice", "A", ["A", "B", "C", "D"])],
          zpl=lambda p, o: f"{_by(p, True)}^BK{o},N,{p['h']},{YN(p['hr'])},{YN(p['hr_above'])},{p['start']},{p['stop']}",
          zint_setup=_codabar_setup))
_add(Spec("logmars", "LOGMARS (^BL)", "linear", "ABC123",
          [P_MW, P_RATIO, P_H, P_HRA],
          zpl=lambda p, o: f"{_by(p, True)}^BL{o},{p['h']},{YN(p['hr_above'])}",
          zint_setup=_setz("LOGMARS", option_2=1)))
_add(Spec("msi", "MSI (^BM)", "linear", "123456",
          _lin(None) + [Param("check", "Dígito control", "choice", "B",
                              [("A", "A - Ninguno"), ("B", "B - Mod 10"), ("C", "C - Mod 10 + 10"), ("D", "D - Mod 11 + 10")])],
          zpl=lambda p, o: f"{_by(p, True)}^BM{o},{p['check']},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])},N",
          zint_setup=_setz("MSI_PLESSEY", option_2=lambda p: {"A": 0, "B": 1, "C": 2, "D": 3}[p.get("check", "B")])))
_add(Spec("plessey", "Plessey (^BP)", "linear", "12345",
          _lin(None) + [Param("check", "Imprimir dígito control", "bool", False)],
          zpl=lambda p, o: f"{_by(p, True)}^BP{o},{YN(p['check'])},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])}",
          zint_setup=_setz("PLESSEY")))
_add(Spec("ean13", "EAN-13 (^BE)", "linear", "590123412345", _lin(None, ratio=False),
          zpl=lambda p, o: f"{_by(p)}^BE{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])}",
          zint_setup=_setz("EANX"), note="12 dígitos; la impresora calcula el dígito de control."))
_add(Spec("ean8", "EAN-8 (^B8)", "linear", "9638507", _lin(None, ratio=False),
          zpl=lambda p, o: f"{_by(p)}^B8{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])}",
          zint_setup=_setz("EANX"), note="7 dígitos; la impresora calcula el dígito de control."))
_add(Spec("upca", "UPC-A (^BU)", "linear", "01234567890",
          _lin(None, ratio=False) + [Param("check", "Imprimir dígito control", "bool", True)],
          zpl=lambda p, o: f"{_by(p)}^BU{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])},{YN(p['check'])}",
          zint_setup=_setz("UPCA"), note="11 dígitos."))
_add(Spec("upce", "UPC-E (^B9)", "linear", "1230000045",
          _lin(None, ratio=False) + [Param("check", "Imprimir dígito control", "bool", True)],
          zpl=lambda p, o: f"{_by(p)}^B9{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])},{YN(p['check'])}",
          zint_setup=_upce_setup, note="10 dígitos (fabricante + producto), se comprimen a UPC-E."))
_add(Spec("tlc39", "TLC39 (^BT)", "linear", "123456,ABCD12345678901234",
          [Param("mw", "Ancho módulo Code39 (dots)", "int", 2, minv=1, maxv=10),
           Param("ratio", "Relación ancho/estrecho", "float", 2.0, minv=2.0, maxv=3.0),
           Param("h", "Alto Code39 (dots)", "int", 40, minv=1, maxv=9999),
           Param("mw2", "Ancho módulo MicroPDF", "int", 4, minv=1, maxv=10),
           Param("h2", "Alto filas MicroPDF", "int", 4, minv=1, maxv=255)],
          zpl=lambda p, o: f"^BT{o},{p['mw']},{float(p['ratio']):.1f},{p['h']},{p['mw2']},{p['h2']}",
          zint_setup=_tlc39_setup, note="Vista previa solo de la parte Code 39 (antes de la coma)."))

# --- postales ---------------------------------------------------------------
_add(Spec("postnet", "POSTNET (^BZ)", "postal", "12345",
          [P_MW, Param("h", "Alto barras (dots)", "int", 30, minv=1, maxv=9999), P_HR, P_HRA],
          zpl=lambda p, o: f"{_by(p)}^BZ{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])}",
          zint_setup=_setz("POSTNET")))
_add(Spec("planet", "Planet Code (^B5)", "postal", "12345678901",
          [P_MW, Param("h", "Alto barras (dots)", "int", 30, minv=1, maxv=9999), P_HR, P_HRA],
          zpl=lambda p, o: f"{_by(p)}^B5{o},{p['h']},{YN(p['hr'])},{YN(p['hr_above'])}",
          zint_setup=_setz("PLANET")))

# --- apilados ---------------------------------------------------------------
MICROPDF_MODES = [(1, 11), (1, 14), (1, 17), (1, 20), (1, 24), (1, 28), (2, 8), (2, 11), (2, 14), (2, 17),
                  (2, 20), (2, 23), (2, 26), (3, 6), (3, 8), (3, 10), (3, 12), (3, 15), (3, 20), (3, 26),
                  (3, 32), (3, 38), (3, 44), (4, 4), (4, 6), (4, 8), (4, 10), (4, 12), (4, 15), (4, 20),
                  (4, 26), (4, 32), (4, 38), (4, 44)]
P_ROWH = Param("rowh", "Alto fila (dots)", "int", 8, minv=1, maxv=255)
_add(Spec("pdf417", "PDF417 (^B7)", "stacked", "PDF417 Zebra 123456",
          [P_MW, P_ROWH,
           Param("security", "Nivel seguridad (0-8)", "int", 0, minv=0, maxv=8),
           Param("cols", "Columnas (0=auto, 1-30)", "int", 0, minv=0, maxv=30),
           Param("rows", "Filas (0=auto, 3-90)", "int", 0, minv=0, maxv=90),
           Param("trunc", "Truncado", "bool", False)],
          zpl=lambda p, o: f"{_by(p)}^B7{o},{p['rowh']},{p['security']},{p['cols'] or ''},{p['rows'] or ''},{YN(p['trunc'])}",
          zint_setup=lambda s, p, d: (setattr(s, "symbology", Z("PDF417COMP") if p.get("trunc") else Z("PDF417")),
                                      setattr(s, "option_1", int(p.get("security", 0))),
                                      setattr(s, "option_2", int(p.get("cols", 0))),
                                      setattr(s, "option_3", int(p.get("rows", 0))), d)[4]))
_add(Spec("micropdf", "MicroPDF417 (^BF)", "stacked", "MICROPDF 123",
          [P_MW, P_ROWH, Param("mode", "Modo (columnas × filas)", "choice", 7,
                               [(i, f"{i} - {c} col × {r} filas") for i, (c, r) in enumerate(MICROPDF_MODES)])],
          zpl=lambda p, o: f"{_by(p)}^BF{o},{p['rowh']},{p['mode']}",
          zint_setup=_setz("MICROPDF417", option_2=lambda p: MICROPDF_MODES[int(p.get("mode", 7))][0]),
          note="El modo fija el tamaño; si los datos no caben, la impresora no imprime el código."))
_add(Spec("codablock", "CODABLOCK F (^BB)", "stacked", "CODABLOCK F 123",
          [P_MW, Param("rowh", "Alto fila (dots)", "int", 8, minv=2, maxv=255),
           Param("cols", "Caracteres por fila (0=auto)", "int", 0, minv=0, maxv=62),
           Param("rows", "Filas (0=auto)", "int", 0, minv=0, maxv=44)],
          zpl=lambda p, o: f"{_by(p)}^BB{o},{p['rowh']},Y,{p['cols'] or ''},{p['rows'] or ''},F",
          zint_setup=_setz("CODABLOCKF", option_2=lambda p: int(p.get("cols", 0)) + 5 if p.get("cols") else 0,
                           option_1=lambda p: int(p.get("rows", 0)) or -1)))
_add(Spec("code49", "Code 49 (^B4)", "stacked", "CODE49 123",
          [P_MW, Param("rowh", "Alto fila (× módulo)", "int", 8, minv=1, maxv=255), P_HR, P_HRA],
          zpl=lambda p, o: f"{_by(p)}^B4{o},{p['rowh']},{'A' if p['hr_above'] else ('B' if p['hr'] else 'N')},A",
          zint_setup=_setz("CODE49")))

# --- matriciales 2D ---------------------------------------------------------
_add(Spec("qr", "QR Code (^BQ)", "matrix", "https://www.zebra.com",
          [Param("mag", "Magnificación (1-10)", "int", 5, minv=1, maxv=10),
           Param("ecc", "Corrección de errores", "choice", "Q", QR_ECC),
           Param("mask", "Máscara (0-7, 7=auto)", "int", 7, minv=0, maxv=7)],
          zpl=lambda p, o: f"^BQN,2,{p['mag']},{p['ecc']},{p['mask']}",
          data_prefix=lambda p: f"{p['ecc']}A,",
          zint_setup=_setz("QRCODE", option_1=lambda p: {"L": 1, "M": 2, "Q": 3, "H": 4}[p.get("ecc", "Q")],
                           input_mode=lambda p: zint.InputMode.UNICODE),
          note="^BQ no admite rotación; la impresora lo imprime siempre en orientación normal."))
_add(Spec("datamatrix", "Data Matrix (^BX)", "matrix", "DATAMATRIX 123",
          [Param("mag", "Tamaño módulo (dots)", "int", 6, minv=1, maxv=100),
           Param("cols", "Columnas (0=auto)", "int", 0, minv=0, maxv=144),
           Param("rows", "Filas (0=auto)", "int", 0, minv=0, maxv=144),
           Param("aspect", "Forma", "choice", 1, [(1, "Cuadrada"), (2, "Rectangular")])],
          zpl=lambda p, o: f"^BX{o},{p['mag']},200,{p['cols'] or ''},{p['rows'] or ''},6,~,{p['aspect']}",
          zint_setup=lambda s, p, d: (setattr(s, "symbology", Z("DATAMATRIX")),
                                      setattr(s, "input_mode", zint.InputMode.UNICODE),
                                      int(p.get("aspect", 1)) == 1 and setattr(s, "option_3", int(zint.DataMatrixOptions.SQUARE)),
                                      d)[3]))
_add(Spec("aztec", "Aztec (^BO)", "matrix", "AZTEC 123",
          [Param("mag", "Magnificación (1-10)", "int", 5, minv=1, maxv=10),
           Param("ecc", "Corrección (0=def, 1-99 %)", "int", 0, minv=0, maxv=99)],
          zpl=lambda p, o: f"^BO{o},{p['mag']},N,{p['ecc']},N,1,",
          zint_setup=_setz("AZTEC", input_mode=lambda p: zint.InputMode.UNICODE)))
_add(Spec("maxicode", "MaxiCode (^BD)", "maxi", "MAXICODE ZEBRA",
          [Param("mode", "Modo", "choice", 4, [(2, "2 - Postal numérico"), (3, "3 - Postal alfanum."),
                                               (4, "4 - Estándar"), (5, "5 - ECC completo"), (6, "6 - Lector")])],
          zpl=lambda p, o: f"^BD{p['mode']},1,1",
          zint_setup=_setz("MAXICODE", option_1=lambda p: int(p.get("mode", 4))),
          note="Tamaño físico fijo (~1 pulgada). Modos 2/3 requieren formato de datos UPS."))
_add(Spec("databar", "GS1 DataBar (^BR)", "databar", "0123456789012",
          [Param("type", "Tipo", "choice", 1, DBAR_TYPES),
           Param("mag", "Magnificación (1-10)", "int", 2, minv=1, maxv=10),
           Param("sep", "Alto separador (1-2)", "int", 1, minv=1, maxv=2),
           Param("h", "Alto parte lineal (solo compuestos)", "int", 25, minv=1, maxv=32000),
           Param("seg", "Segmentos (expandido, par 2-22)", "int", 22, minv=2, maxv=22)],
          zpl=lambda p, o: f"^BR{o},{p['type']},{p['mag']},{p['sep']},{p['h']},{p['seg']}",
          zint_setup=_dbar_setup))


def defaults(key: str) -> dict:
    return {p.key: p.default for p in SPECS[key].params}


def zpl_command(key: str, params: dict, rot: str) -> tuple[str, str]:
    """Devuelve (comandos ZPL del código, prefijo de ^FD)."""
    spec = SPECS[key]
    p = defaults(key)
    p.update(params)
    cmd = spec.zpl(p, rot)
    prefix = spec.data_prefix(p) if spec.data_prefix else ""
    return cmd, prefix


# --- render -----------------------------------------------------------------
def _hr_height(mw: int) -> int:
    return max(12, int(7 * mw + 6))


def _error_image(msg: str, w=220, h=60) -> Image.Image:
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=0, width=2)
    d.line([0, 0, w, h], fill=160)
    d.line([0, h, w, 0], fill=160)
    f = get_font(13)
    d.text((6, 4), msg[:200], fill=0, font=f)
    return img


def render(key: str, data: str, params: dict) -> tuple[Image.Image, str | None]:
    """Renderiza el código en orientación normal. Devuelve (imagen L, error)."""
    spec = SPECS[key]
    p = defaults(key)
    p.update(params)
    if zint is None:
        return _error_image("Instala zint-bindings para vista previa"), "zint no instalado"
    try:
        s = zint.Symbol()
        s.show_text = False
        s.output_options = zint.OutputOptions.BARCODE_NO_QUIET_ZONES
        enc = spec.zint_setup(s, p, data)
        if spec.kind != "maxi":
            s.scale = 0.5
        if key == "pdf417" and not int(p.get("cols", 0)) and not int(p.get("rows", 0)):
            s.option_2 = _pdf417_auto_cols(spec, p, enc)
        s.encode(enc)
        s.buffer()
        if key == "micropdf" and s.rows > MICROPDF_MODES[int(p.get("mode", 7))][1]:
            raise ValueError("Los datos no caben en el modo elegido (la impresora no lo imprimirá). "
                             "Elige un modo con más filas o columnas.")
        bmp = np.asarray(s.bitmap)
        gray = (bmp.mean(axis=2) > 127).astype(np.uint8) * 255
        base = Image.fromarray(gray, "L")
        hrt = ""
        try:
            hrt = s.text.decode("utf-8", "ignore") if isinstance(s.text, (bytes, bytearray)) else str(s.text or "")
        except Exception:
            hrt = ""
        if key == "code39" and hrt and not hrt.startswith("*"):
            hrt = f"*{hrt}*"
        if key in ("msi", "code128", "codabar"):
            hrt = data if key != "codabar" else hrt
        if key in EANUPC and p.get("hr") and not p.get("hr_above"):
            return _eanupc_native(spec, p, data, enc), None
        return _scale(spec, p, s, base, hrt or data), None
    except Exception as ex:  # datos inválidos, etc.
        msg = str(ex)
        return _error_image(f"{spec.name}\n{msg}"), msg


EANUPC = ("ean13", "ean8", "upca", "upce")
TWO_WIDTH = ("code39", "code11", "i2of5", "ind2of5", "std2of5", "codabar", "logmars", "msi", "plessey", "tlc39")


def _eanupc_native(spec: Spec, p: dict, data: str, enc: str) -> Image.Image:
    """EAN/UPC con texto y barras de guarda al estilo de la impresora (render nativo de zint)."""
    mw, h = int(p["mw"]), int(p["h"])
    s = zint.Symbol()
    spec.zint_setup(s, p, data)
    s.show_text = True
    s.scale = mw / 2
    s.height = max(1.0, h / mw)
    s.encode(enc)
    s.buffer()
    bmp = np.asarray(s.bitmap)
    img = Image.fromarray(((bmp.mean(axis=2) > 127).astype(np.uint8) * 255), "L")
    # la impresora coloca la primera barra en ^FO (el dígito inicial queda a la izquierda)
    top = img.crop((0, 0, img.width, max(1, int(h * 0.5))))
    bb = top.point(lambda v: 255 - v).getbbox()
    if bb:
        img = img.crop((bb[0], 0, img.width, img.height))
    return img


def _runs(row) -> list[tuple[int, int]]:
    out = []
    for v in row:
        if out and out[-1][0] == v:
            out[-1][1] += 1
        else:
            out.append([v, 1])
    return [(a, b) for a, b in out]


def _remap_width(base: Image.Image, mw: int, wide: int, gap: int | None = None) -> Image.Image:
    """Reconstruye un 1D de dos anchos: módulo estrecho = mw, ancho = wide (como ^BY w,r)."""
    row = list(base.resize((base.width, 1), Image.Resampling.BOX).point(lambda v: 0 if v < 255 else 255).getdata())
    runs = _runs(row)
    total = sum((mw if n == 1 else wide) if gap is None or v == 0 else gap for v, n in runs)
    out = Image.new("L", (max(1, total), base.height), 255)
    d = ImageDraw.Draw(out)
    x = 0
    src_x = 0
    for v, n in runs:
        w = (mw if n == 1 else wide) if gap is None or v == 0 else gap
        if v == 0:
            col = base.crop((src_x, 0, src_x + 1, base.height)).resize((w, base.height), Image.NEAREST)
            out.paste(col, (x, 0))
        x += w
        src_x += n
    return out


def _pdf417_auto_cols(spec: Spec, p: dict, enc: str) -> int:
    """Imita la elección automática de la impresora: columnas = floor(sqrt(codewords) / 2)."""
    n = None
    for c in (1, 2, 4, 8, 16, 30):
        t = zint.Symbol()
        t.warn_level = zint.WarningLevel.FAIL_ALL if hasattr(zint.WarningLevel, "FAIL_ALL") else t.warn_level
        spec.zint_setup(t, p, enc)
        t.option_2 = c
        try:
            t.encode(enc)
        except Exception:
            continue
        if t.rows <= 90:
            n = t.rows * c
            break
    if n is None:
        return 0
    return max(1, min(30, int((n ** 0.5) / 2)))


def _scale(spec: Spec, p: dict, s, base: Image.Image, hrt: str) -> Image.Image:
    kind = spec.kind
    if kind in ("linear", "postal"):
        mw = int(p["mw"])
        h = int(p["h"])
        if spec.key in TWO_WIDTH:
            base = _remap_width(base, mw, max(mw + 1, round(float(p.get("ratio", 3.0)) * mw)))
            img = base.resize((base.width, h), Image.NEAREST)
        elif kind == "postal":
            base = _remap_width(base, mw, mw, gap=max(1, round(mw * 1.5)))
            img = base.resize((base.width, h), Image.NEAREST)
        else:
            img = base.resize((base.width * mw, h), Image.NEAREST)
        show = p.get("hr", False) if spec.key != "logmars" else True
        if spec.key == "tlc39":
            show = False
        if show:
            img = _add_hr(img, hrt, _hr_height(mw), bool(p.get("hr_above")))
        return img
    if kind == "stacked":
        mw = int(p["mw"])
        rowh = int(p["rowh"]) * (mw if spec.key == "code49" else 1)
        rows = max(1, int(s.rows))
        if spec.key == "micropdf":
            rows = MICROPDF_MODES[int(p.get("mode", 7))][1]
        img = base.resize((base.width * mw, rows * rowh), Image.NEAREST)
        if spec.key == "code49" and (p.get("hr") or p.get("hr_above")):
            img = _add_hr(img, hrt, _hr_height(mw), bool(p.get("hr_above")))
        return img
    if kind == "matrix":
        mag = int(p["mag"])
        img = base.resize((base.width * mag, base.height * mag), Image.NEAREST)
        if spec.key == "qr":
            # la impresora desplaza el QR 10 dots hacia abajo respecto a ^FO
            out = Image.new("L", (img.width, img.height + QR_Y_OFFSET), 255)
            out.paste(img, (0, QR_Y_OFFSET))
            return out
        return img
    if kind == "databar":
        mag = int(p["mag"])
        return base.resize((base.width * mag, base.height * mag), Image.NEAREST)
    if kind == "maxi":
        return base  # se reescala a tamaño físico en render_element
    return base


def _add_hr(img: Image.Image, text: str, th: int, above: bool) -> Image.Image:
    font = get_font(th, condensed="mono", bold=False)
    gap = max(2, th // 6)
    out = Image.new("L", (img.width, img.height + th + gap), 255)
    d = ImageDraw.Draw(out)
    tw = d.textlength(text, font=font)
    if above:
        d.text(((img.width - tw) / 2, 0), text, fill=0, font=font)
        out.paste(img, (0, th + gap))
    else:
        out.paste(img, (0, 0))
        d.text(((img.width - tw) / 2, img.height + gap), text, fill=0, font=font)
    return out
