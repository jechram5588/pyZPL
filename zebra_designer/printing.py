"""Envío de ZPL a la impresora y vista previa con Labelary."""
from __future__ import annotations

import json
import os
import socket
import urllib.request

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".zebra_designer.json")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def windows_printers() -> list[str]:
    try:
        import win32print
    except ImportError:
        return []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags)]


def send_windows(printer: str, data: bytes) -> None:
    import win32print
    h = win32print.OpenPrinter(printer)
    try:
        win32print.StartDocPrinter(h, 1, ("Etiqueta ZPL", None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, data)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)


def send_network(host: str, port: int, data: bytes, timeout: float = 8.0) -> None:
    with socket.create_connection((host, int(port)), timeout=timeout) as s:
        s.sendall(data)


DPMM = {152: 6, 203: 8, 300: 12, 600: 24}


def labelary_png(zpl: str, dpi: int, w_in: float, h_in: float) -> bytes:
    url = f"http://api.labelary.com/v1/printers/{DPMM.get(dpi, 8)}dpmm/labels/{w_in:.3f}x{h_in:.3f}/0/"
    req = urllib.request.Request(url, data=zpl.encode("utf-8"), method="POST",
                                 headers={"Accept": "image/png",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()
