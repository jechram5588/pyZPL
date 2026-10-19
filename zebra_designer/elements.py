"""Modelo de elementos de la etiqueta y su render de vista previa (en puntos de impresora)."""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import barcodes
from .fonts import get_font

# Calibrado contra la fuente 0 (CG Triumvirate Bold Condensed) de la impresora:
FONT_EM = 0.95        # tamaño em respecto al alto de celda
FONT_BASELINE = 0.725  # posición de la línea base dentro de la celda
FONT_WIDTH = 1.13      # la fuente 0 es más ancha que Arial Narrow Bold

ROTATIONS = [("N", "0°"), ("R", "90°"), ("I", "180°"), ("B", "270°")]
ZPL_FONTS = [("0", "0 - Escalable (CG Triumvirate)")] + [(c, f"{c} - Bitmap") for c in "ABCDEFGHPQRSTUV"]

# Tamaño base de las fuentes bitmap (alto, ancho) a 203 dpi.
BITMAP_FONT_BASE = {"A": (9, 5), "B": (11, 7), "C": (18, 10), "D": (18, 10), "E": (28, 15), "F": (26, 13),
                    "G": (60, 40), "H": (21, 13), "P": (20, 18), "Q": (28, 24), "R": (35, 31), "S": (40, 35),
                    "T": (48, 42), "U": (59, 53), "V": (80, 71)}

TYPE_NAMES = {"text": "Texto", "barcode": "Código de barras", "box": "Rectángulo", "line": "Línea",
              "ellipse": "Elipse / círculo", "image": "Imagen"}


def new_element(etype: str, x=20, y=20, **kw) -> dict:
    base = {"type": etype, "x": int(x), "y": int(y), "rot": "N"}
    if etype == "text":
        base.update(text="Texto", font="0", h=40, w=0, block=False, block_w=300, lines=1,
                    spacing=0, align="L", reverse=False)
    elif etype == "barcode":
        bt = kw.pop("btype", "code128")
        base.update(btype=bt, data=barcodes.SPECS[bt].sample, params=barcodes.defaults(bt))
    elif etype == "box":
        base.update(w=200, h=100, t=3, color="B", round=0, reverse=False)
    elif etype == "line":
        base.update(w=200, h=100, t=3, dir="H")
    elif etype == "ellipse":
        base.update(w=120, h=120, t=3, color="B", reverse=False)
    elif etype == "image":
        base.update(png=b"", w=100, h=100, gray="avg", algo="threshold", threshold=128, brightness=0,
                    contrast=0, sharpen=False, thin=False, invert=False, keep_ratio=True)
    base.update(kw)
    return base


def copy_element(el: dict) -> dict:
    c = dict(el)
    if "params" in c:
        c["params"] = dict(c["params"])
    return c


# --- render -----------------------------------------------------------------
_ROT_T = {"R": Image.Transpose.ROTATE_270, "I": Image.Transpose.ROTATE_180, "B": Image.Transpose.ROTATE_90}


def rotate(img: Image.Image, rot: str) -> Image.Image:
    t = _ROT_T.get(rot)
    return img.transpose(t) if t is not None else img


def text_metrics(el: dict) -> tuple[int, int, float]:
    """(alto celda, ancho celda, factor horizontal) en dots."""
    f = el.get("font", "0")
    h = max(1, int(el.get("h", 30)))
    w = int(el.get("w", 0))
    if f == "0":
        sx = FONT_WIDTH * ((w / h) if w > 0 else 1.0)
    else:
        bh, bw = BITMAP_FONT_BASE.get(f, (h, h))
        # las bitmap escalan en múltiplos enteros del tamaño base
        mh = max(1, round(h / bh))
        mw = max(1, round(w / bw)) if w > 0 else mh
        h = bh * mh
        sx = (bw * mw) / (h * 0.55)
    return h, w, sx


def wrap_lines(el: dict) -> list[str]:
    text = str(el.get("text", ""))
    paragraphs = text.split("\n")
    if not el.get("block"):
        return paragraphs
    h, _, sx = text_metrics(el)
    font = cell_font(h)
    max_w = max(1, int(el.get("block_w", 300)))
    out: list[str] = []
    for par in paragraphs:
        words = par.split(" ")
        cur = ""
        for word in words:
            cand = word if not cur else cur + " " + word
            if font.getlength(cand) * sx <= max_w or not cur:
                cur = cand
            else:
                out.append(cur)
                cur = word
        out.append(cur)
    return out


def cell_font(h: int):
    return get_font(max(1, round(h * FONT_EM)))


def render_text(el: dict) -> Image.Image:
    h, _, sx = text_metrics(el)
    font = cell_font(h)
    spacing = int(el.get("spacing", 0))
    lines = wrap_lines(el)
    if el.get("block"):
        nlines = max(1, int(el.get("lines", 1)))
        shown = lines[:nlines]
        width = max(1, int(el.get("block_w", 300)))
        height = nlines * h + (nlines - 1) * spacing
    else:
        shown = lines
        width = max(1, math.ceil(max((font.getlength(l) for l in lines), default=1) * sx))
        height = len(lines) * h + (len(lines) - 1) * spacing
    raw_w = max(1, math.ceil(width / sx))
    img = Image.new("L", (raw_w, max(1, height)), 255)
    d = ImageDraw.Draw(img)
    align = el.get("align", "L")
    for i, line in enumerate(shown):
        lw = font.getlength(line)
        if align == "C":
            x = (raw_w - lw) / 2
        elif align == "R":
            x = raw_w - lw
        else:
            x = 0
        y = i * (h + spacing) + h * FONT_BASELINE
        if align == "J" and el.get("block") and i < len(shown) - 1 and " " in line:
            words = line.split(" ")
            gap = (raw_w - sum(font.getlength(w) for w in words)) / (len(words) - 1)
            cx = 0.0
            for w in words:
                d.text((cx, y), w, fill=0, font=font, anchor="ls")
                cx += font.getlength(w) + gap
        else:
            d.text((x, y), line, fill=0, font=font, anchor="ls")
    if raw_w != width:
        img = img.resize((width, img.height), Image.LANCZOS)
    return img.point(lambda v: 0 if v < 140 else 255)


GRAY_MODES = [("avg", "Promedio RGB (como Labelary)"), ("lum", "Luminancia (percepción)"),
              ("min", "Canal más oscuro (colores → negro)")]
IMG_ALGOS = [("threshold", "Umbral fijo"), ("otsu", "Umbral automático (Otsu)"),
             ("fs", "Tramado Floyd-Steinberg (fotos)"), ("bayer4", "Tramado ordenado 4×4"),
             ("bayer8", "Tramado ordenado 8×8")]
IMG_PRESETS = {
    "Como Labelary": dict(gray="avg", algo="threshold", threshold=128, brightness=0, contrast=0,
                          sharpen=False, thin=False),
    "Logo / texto": dict(gray="min", algo="otsu", brightness=0, contrast=0, sharpen=False, thin=True),
    "Foto": dict(gray="lum", algo="fs", brightness=0, contrast=10, sharpen=True, thin=False),
}

_BITS_CACHE: dict = {}


def _bayer(n: int) -> np.ndarray:
    m = np.array([[0]])
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return (m + 0.5) / m.size * 255


def _otsu(g: np.ndarray) -> float:
    hist = np.bincount(np.clip(g, 0, 255).astype(np.uint8).ravel(), minlength=256).astype(float)
    total = hist.sum()
    if total == 0:
        return 128
    w0 = np.cumsum(hist)
    m0 = np.cumsum(hist * np.arange(256))
    w1 = total - w0
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (m0[-1] * w0 / total - m0) ** 2 / (w0 * w1)
    between = np.nan_to_num(between)
    return float(np.argmax(between)) + 0.5


def image_bits(el: dict) -> Image.Image | None:
    """Imagen monocromo ('1', 0 = negro) al tamaño final, sin rotar."""
    data = el.get("png") or b""
    if not data:
        return None
    w, h = max(1, int(el.get("w", 100))), max(1, int(el.get("h", 100)))
    gray = el.get("gray", "avg")
    algo = el.get("algo") or ("fs" if el.get("dither") else "threshold")
    key = (len(data), hash(bytes(data)), w, h, gray, algo, int(el.get("threshold", 128)),
           int(el.get("brightness", 0)), int(el.get("contrast", 0)), bool(el.get("sharpen")),
           bool(el.get("thin")), bool(el.get("invert")))
    hit = _BITS_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        src = Image.open(io.BytesIO(data))
        src.load()
    except Exception:
        return None
    # transparencia sobre fondo blanco
    src = src.convert("RGBA")
    bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
    bg.alpha_composite(src)
    rgb = np.asarray(bg.convert("RGB")).astype(np.float32)
    if gray == "lum":
        g = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    elif gray == "min":
        g = rgb.min(axis=2)
    else:
        g = rgb.mean(axis=2)
    gimg = Image.fromarray(g, "F")

    sw, sh = gimg.size
    if (w, h) != (sw, sh):
        down = w < sw or h < sh
        if down and el.get("thin"):
            # engorda los trazos oscuros antes de reducir para que no desaparezcan
            k = int(min(sw / w, sh / h))
            if k >= 2:
                k = k + 1 if k % 2 == 0 else k
                gimg = Image.fromarray(np.asarray(gimg).clip(0, 255).astype(np.uint8), "L").filter(
                    ImageFilter.MinFilter(min(k, 9))).convert("F")
        # BOX = promedio por área (sin halos al reducir); bicúbico al ampliar
        gimg = gimg.resize((w, h), Image.BOX if down else Image.BICUBIC)
    g = np.asarray(gimg, dtype=np.float32)

    c = int(el.get("contrast", 0))
    b = int(el.get("brightness", 0))
    if c or b:
        g = (g - 128) * (1 + c / 100) + 128 + b * 1.28
    if el.get("sharpen"):
        u8 = Image.fromarray(g.clip(0, 255).astype(np.uint8), "L")
        g = np.asarray(u8.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=2)), dtype=np.float32)
    if el.get("invert"):
        g = 255 - g
    g = g.clip(0, 255)

    if algo == "fs":
        out = Image.fromarray(g.astype(np.uint8), "L").convert("1")
    else:
        if algo == "otsu":
            black = g < _otsu(g)
        elif algo in ("bayer4", "bayer8"):
            m = _bayer(4 if algo == "bayer4" else 8)
            ty, tx = -(-h // m.shape[0]), -(-w // m.shape[1])
            black = g < np.tile(m, (ty, tx))[:h, :w]
        else:
            black = g < int(el.get("threshold", 128))
        out = Image.fromarray(np.where(black, 0, 255).astype(np.uint8), "L").convert("1")
    if len(_BITS_CACHE) > 24:
        _BITS_CACHE.pop(next(iter(_BITS_CACHE)))
    _BITS_CACHE[key] = out
    return out


def render_mono(el: dict, dpi: int = 203) -> tuple[Image.Image, str | None]:
    """Render 'L' (0=negro, 255=blanco) en orientación final + error opcional."""
    t = el["type"]
    err = None
    if t == "text":
        img = render_text(el)
    elif t == "barcode":
        img, err = barcodes.render(el["btype"], str(el.get("data", "")), el.get("params", {}))
        if el["btype"] == "maxicode" and not err:
            img = img.resize((round(0.98 * dpi), round(0.946 * dpi)), Image.NEAREST)
        if el["btype"] == "qr":
            return img, err  # ^BQ siempre en orientación normal
    elif t == "box":
        w, h = max(1, int(el["w"])), max(1, int(el["h"]))
        th = max(1, min(int(el["t"]), w, h))
        w, h = max(w, th), max(h, th)
        img = Image.new("L", (w, h), 255)
        d = ImageDraw.Draw(img)
        r = int(el.get("round", 0)) * min(w, h) // 16
        ink = 0
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, outline=ink, width=th) if r else \
            d.rectangle([0, 0, w - 1, h - 1], outline=ink, width=th)
        return img, None
    elif t == "line":
        th = max(1, int(el["t"]))
        dr = el.get("dir", "H")
        if dr == "H":
            img = Image.new("L", (max(1, int(el["w"])), th), 0)
        elif dr == "V":
            img = Image.new("L", (th, max(1, int(el["h"]))), 0)
        else:
            w, h = max(1, int(el["w"])), max(1, int(el["h"]))
            img = Image.new("L", (w + th, h), 255)
            d = ImageDraw.Draw(img)
            # ^GD: el grosor se mide horizontalmente
            if dr == "R":   # "/" : de abajo-izquierda a arriba-derecha
                pts = [(0, h), (th, h), (w + th, 0), (w, 0)]
            else:           # "\" : de arriba-izquierda a abajo-derecha
                pts = [(0, 0), (th, 0), (w + th, h), (w, h)]
            d.polygon(pts, fill=0)
        return img, None
    elif t == "ellipse":
        w, h = max(1, int(el["w"])), max(1, int(el["h"]))
        th = max(1, min(int(el["t"]), w // 2 + 1, h // 2 + 1))
        img = Image.new("L", (w, h), 255)
        ImageDraw.Draw(img).ellipse([0, 0, w - 1, h - 1], outline=0, width=th)
        return img, None
    elif t == "image":
        bits = image_bits(el)
        if bits is None:
            img = Image.new("L", (max(1, int(el["w"])), max(1, int(el["h"]))), 255)
            d = ImageDraw.Draw(img)
            d.rectangle([0, 0, img.width - 1, img.height - 1], outline=0)
            d.line([0, 0, img.width, img.height], fill=0)
            d.line([0, img.height, img.width, 0], fill=0)
        else:
            img = bits.convert("L")
    else:
        img = Image.new("L", (10, 10), 128)
    return rotate(img, el.get("rot", "N")), err


def render_rgba(el: dict, dpi: int = 203) -> tuple[Image.Image, str | None]:
    """Render para el lienzo: tinta opaca, fondo transparente."""
    mono, err = render_mono(el, dpi)
    white_ink = el.get("color") == "W" or el.get("reverse")
    rgba = Image.new("RGBA", mono.size, (0, 0, 0, 0))
    alpha = mono.point(lambda v: 255 if v < 128 else 0)
    color = (255, 255, 255, 255) if white_ink else (0, 0, 0, 255)
    solid = Image.new("RGBA", mono.size, color)
    rgba.paste(solid, (0, 0), alpha)
    if el["type"] == "barcode" and not err and el["btype"] in ("qr", "datamatrix", "aztec", "maxicode"):
        # fondo blanco en 2D para que se lea bien sobre otros elementos
        bg = Image.new("RGBA", mono.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        rgba = bg
    if err:
        tint = Image.new("RGBA", mono.size, (220, 40, 40, 255))
        rgba = Image.new("RGBA", mono.size, (255, 235, 235, 255))
        rgba.paste(tint, (0, 0), alpha)
    return rgba, err
