"""Fuentes TrueType usadas para aproximar las fuentes de la impresora en la vista previa."""
from __future__ import annotations

import os
from functools import lru_cache

from PIL import ImageFont

_FONT_DIRS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
              "/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "/System/Library/Fonts"]

# La fuente 0 de Zebra (CG Triumvirate Bold Condensed) se parece a Arial Narrow Bold.
_CANDIDATES = {
    (True, True): ["ARIALNB.TTF", "arialnb.ttf", "arialbd.ttf", "DejaVuSansCondensed-Bold.ttf", "DejaVuSans-Bold.ttf"],
    (False, True): ["arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
    (False, False): ["arial.ttf", "DejaVuSans.ttf", "Arial.ttf"],
    (True, False): ["ARIALN.TTF", "arialn.ttf", "arial.ttf", "DejaVuSansCondensed.ttf"],
    ("mono", True): ["consolab.ttf", "courbd.ttf", "DejaVuSansMono-Bold.ttf"],
    ("mono", False): ["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"],
}


@lru_cache(maxsize=None)
def font_path(condensed=True, bold=True) -> str | None:
    for name in _CANDIDATES[(condensed, bold)]:
        for d in _FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return None


@lru_cache(maxsize=512)
def get_font(size: int, condensed=True, bold=True):
    size = max(1, int(size))
    path = font_path(condensed, bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size)
