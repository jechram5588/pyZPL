"""Generación de código ZPL II a partir del diseño."""
from __future__ import annotations

from . import barcodes
from .elements import image_bits, rotate, render_mono

DPI_CHOICES = [(152, "152 dpi (6 dpmm)"), (203, "203 dpi (8 dpmm)"), (300, "300 dpi (12 dpmm)"), (600, "600 dpi (24 dpmm)")]


def default_label() -> dict:
    return {"w": 100.0, "h": 50.0, "units": "mm", "dpi": 203, "copies": 1, "darkness": 0,
            "speed": 4, "lh_x": 0, "lh_y": 0, "invert": False, "utf8": True, "mirror": False}


def to_dots(value: float, units: str, dpi: int) -> int:
    inches = value / 25.4 if units == "mm" else value
    return max(1, round(inches * dpi))


def label_dots(label: dict) -> tuple[int, int]:
    return (to_dots(label["w"], label["units"], label["dpi"]),
            to_dots(label["h"], label["units"], label["dpi"]))


# --- datos de campo ------------------------------------------------------------
def escape_fd(text: str) -> tuple[str, bool]:
    """Escapa ^ ~ _ y saltos de línea con ^FH (indicador '_'). Devuelve (texto, usa_FH)."""
    need = any(c in text for c in "^~_\r\n")
    if not need:
        return text, False
    out = []
    for c in text:
        if c in "^~_\r\n":
            out.append("_%02X" % ord(c))
        else:
            out.append(c)
    return "".join(out), True


# --- compresión ASCII de ^GF ----------------------------------------------------
def _count(n: int) -> str:
    s = ""
    while n >= 400:
        s += "z"
        n -= 400
    if n >= 20:
        s += chr(ord("g") + n // 20 - 1)
        n %= 20
    if n > 0:
        s += chr(ord("G") + n - 1)
    return s


def _compress_row(row: str) -> str:
    if set(row) == {"0"}:
        return ","
    if set(row) == {"F"}:
        return "!"
    tail = ""
    stripped = row.rstrip("0")
    if len(stripped) < len(row) - 1:
        row, tail = stripped, ","
    else:
        stripped = row.rstrip("F")
        if len(stripped) < len(row) - 1:
            row, tail = stripped, "!"
    out = []
    i = 0
    n = len(row)
    while i < n:
        c = row[i]
        j = i
        while j < n and row[j] == c:
            j += 1
        run = j - i
        out.append((_count(run) + c) if run > 1 else c)
        i = j
    return "".join(out) + tail


def gfa(bits, compress=True) -> str:
    """Convierte una imagen PIL '1' en ^GFA (1 = negro)."""
    w, h = bits.size
    bpr = (w + 7) // 8
    total = bpr * h
    px = bits.load()
    rows = []
    for y in range(h):
        bytes_row = bytearray(bpr)
        for x in range(w):
            if px[x, y] == 0:  # negro
                bytes_row[x >> 3] |= 0x80 >> (x & 7)
        rows.append(bytes_row.hex().upper())
    if not compress:
        return f"^GFA,{total},{total},{bpr},{''.join(rows)}"
    out = []
    prev = None
    for r in rows:
        out.append(":" if r == prev else _compress_row(r))
        prev = r
    return f"^GFA,{total},{total},{bpr},{''.join(out)}"


# --- elementos ----------------------------------------------------------------
def _fd(data: str, prefix: str = "") -> str:
    esc, fh = escape_fd(data)
    return ("^FH_" if fh else "") + f"^FD{prefix}{esc}^FS"


def element_zpl(el: dict, dpi: int) -> str:
    t = el["type"]
    x, y = int(el["x"]), int(el["y"])
    fo = f"^FO{x},{y}"
    rot = el.get("rot", "N")
    rev = "^FR" if el.get("reverse") else ""
    if t == "text":
        f = el.get("font", "0")
        h = int(el["h"])
        w = int(el.get("w", 0))
        font = f"^A{f}{rot},{h},{w if w > 0 else h}" if f == "0" else (f"^A{f}{rot},{h},{w}" if w > 0 else f"^A{f}{rot},{h}")
        text = str(el.get("text", ""))
        block = ""
        if el.get("block") or "\n" in text:
            if el.get("block"):
                bw, lines = int(el["block_w"]), max(1, int(el.get("lines", 1)))
            else:
                img, _ = render_mono(dict(el, rot="N"), dpi)
                # margen generoso: la fuente real puede ser algo más ancha que la vista previa
                bw, lines = round(img.width * 1.1) + int(el["h"]), text.count("\n") + 1
            block = f"^FB{bw},{lines},{int(el.get('spacing', 0))},{el.get('align', 'L')},0"
            esc, fh = escape_fd(text.replace("\r", "").replace("\n", "\x00"))
            esc = esc.replace("\x00", "\\&")
            data = ("^FH_" if fh else "") + f"^FD{esc}^FS"
        else:
            data = _fd(text)
        return f"{fo}{font}{block}{rev}{data}"
    if t == "barcode":
        cmd, prefix = barcodes.zpl_command(el["btype"], el.get("params", {}), rot)
        return f"{fo}{cmd}{_fd(str(el.get('data', '')), prefix)}"
    if t == "box":
        w, h, th = int(el["w"]), int(el["h"]), max(1, int(el["t"]))
        w, h = max(w, th), max(h, th)
        return f"{fo}^GB{w},{h},{th},{el.get('color', 'B')},{int(el.get('round', 0))}{rev}^FS"
    if t == "line":
        th = max(1, int(el["t"]))
        dr = el.get("dir", "H")
        if dr == "H":
            return f"{fo}^GB{int(el['w'])},{th},{th}^FS"
        if dr == "V":
            return f"{fo}^GB{th},{int(el['h'])},{th}^FS"
        return f"{fo}^GD{int(el['w'])},{int(el['h'])},{th},B,{dr}^FS"
    if t == "ellipse":
        w, h, th = int(el["w"]), int(el["h"]), max(1, int(el["t"]))
        c = el.get("color", "B")
        if w == h:
            return f"{fo}^GC{w},{th},{c}{rev}^FS"
        return f"{fo}^GE{w},{h},{th},{c}{rev}^FS"
    if t == "image":
        bits = image_bits(el)
        if bits is None:
            return ""
        bits = rotate(bits, rot)
        return f"{fo}{gfa(bits)}^FS"
    return ""


def generate(label: dict, elements: list[dict]) -> str:
    w, h = label_dots(label)
    lines = ["^XA"]
    if label.get("utf8", True):
        lines.append("^CI28")
    lines.append(f"^PW{w}")
    lines.append(f"^LL{h}")
    lines.append(f"^LH{int(label.get('lh_x', 0))},{int(label.get('lh_y', 0))}")
    lines.append(f"^PO{'I' if label.get('invert') else 'N'}")
    if label.get("mirror"):
        lines.append("^PMY")
    sp = int(label.get("speed", 0))
    if sp:
        lines.append(f"^PR{sp}")
    dk = int(label.get("darkness", 0))
    if dk:
        lines.append(f"^MD{dk}")
    for el in elements:
        if el.get("hidden"):
            continue
        z = element_zpl(el, label["dpi"])
        if z:
            if el.get("comment"):
                lines.append(f"^FX {el['comment']} ^FS")
            lines.append(z)
    copies = max(1, int(label.get("copies", 1)))
    if copies > 1:
        lines.append(f"^PQ{copies}")
    lines.append("^XZ")
    return "\n".join(lines) + "\n"
