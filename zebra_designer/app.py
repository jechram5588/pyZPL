"""Interfaz gráfica del diseñador de etiquetas ZPL."""
from __future__ import annotations

import base64
import io
import json
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageGrab, ImageTk

from . import barcodes, printing, zpl
from .elements import (GRAY_MODES, IMG_ALGOS, IMG_PRESETS, ROTATIONS, TYPE_NAMES, ZPL_FONTS, copy_element,
                       new_element, render_rgba)

MARGIN = 40
HANDLE = 4
ZOOMS = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
NO_ROT = {"box", "line", "ellipse"}


def _key_of(el: dict):
    parts = []
    for k in sorted(el):
        if k in ("x", "y", "_id", "comment", "hidden"):
            continue
        v = el[k]
        if isinstance(v, (bytes, bytearray)):
            parts.append((k, len(v), hash(bytes(v))))
        elif isinstance(v, dict):
            parts.append((k, tuple(sorted((a, repr(b)) for a, b in v.items()))))
        else:
            parts.append((k, repr(v)))
    return tuple(parts)


def summary(el: dict) -> str:
    t = el["type"]
    if el.get("comment"):
        return el["comment"]
    if t == "text":
        return "T  " + str(el.get("text", "")).replace("\n", " ")[:30]
    if t == "barcode":
        return f"▥  {barcodes.SPECS[el['btype']].name.split(' (')[0]}: {str(el.get('data', ''))[:20]}"
    if t == "image":
        return f"🖼  Imagen {el['w']}×{el['h']}"
    if t == "box":
        return f"▭  Rectángulo {el['w']}×{el['h']}"
    if t == "line":
        return "╱  Línea" if el.get("dir") in ("R", "L") else ("─  Línea" if el.get("dir") == "H" else "│  Línea")
    if t == "ellipse":
        return f"◯  Elipse {el['w']}×{el['h']}"
    return t


class ScrollFrame(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=8)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _wheel(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None:
            if w is self:
                if not isinstance(e.widget, (tk.Text, ttk.Combobox, ttk.Spinbox)):
                    self.canvas.yview_scroll(int(-e.delta / 120), "units")
                return
            w = w.master

    def clear(self):
        for c in self.inner.winfo_children():
            c.destroy()
        self.canvas.yview_moveto(0)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Diseñador de etiquetas Zebra ZPL")
        self.geometry("1400x860")
        self.minsize(1000, 600)
        try:
            ttk.Style(self).theme_use("vista" if os.name == "nt" else "clam")
        except tk.TclError:
            pass
        self.cfg = printing.load_config()
        self.label = zpl.default_label()
        self.elements: list[dict] = []
        self.sel: dict | None = None
        self.zoom = 2.0
        self.show_grid = tk.BooleanVar(value=True)
        self.snap = tk.BooleanVar(value=True)
        self.grid_mm = tk.StringVar(value="1 mm")
        self.path: str | None = None
        self.dirty = False
        self._next_id = 1
        self._cache: dict[int, tuple] = {}     # id -> (key, rgba, err)
        self._items: dict[int, int] = {}       # id -> canvas item
        self._photos: dict[int, ImageTk.PhotoImage] = {}
        self._undo: list = []
        self._redo: list = []
        self._last_ck = None
        self._drag = None
        self._loading = False
        self._zpl_job = None
        self._clip_el = None
        self._labelary_ok = False
        self.prop_vars: dict[str, tk.Variable] = {}

        self._build_menu()
        self._build_ui()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(50, self.zoom_fit)
        self.after(60, self.build_props)
        self.schedule_zpl()

    # ------------------------------------------------------------------ UI
    def _build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Nuevo", accelerator="Ctrl+N", command=self.file_new)
        f.add_command(label="Abrir diseño…", accelerator="Ctrl+O", command=self.file_open)
        f.add_command(label="Guardar", accelerator="Ctrl+S", command=self.file_save)
        f.add_command(label="Guardar como…", command=lambda: self.file_save(True))
        f.add_separator()
        f.add_command(label="Exportar ZPL…", accelerator="Ctrl+E", command=self.export_zpl)
        f.add_separator()
        f.add_command(label="Salir", command=self.on_close)
        m.add_cascade(label="Archivo", menu=f)

        e = tk.Menu(m, tearoff=0)
        e.add_command(label="Deshacer", accelerator="Ctrl+Z", command=self.undo)
        e.add_command(label="Rehacer", accelerator="Ctrl+Y", command=self.redo)
        e.add_separator()
        e.add_command(label="Copiar", accelerator="Ctrl+C", command=self.copy_el)
        e.add_command(label="Pegar", accelerator="Ctrl+V", command=self.paste)
        e.add_command(label="Duplicar", accelerator="Ctrl+D", command=self.duplicate)
        e.add_command(label="Eliminar", accelerator="Supr", command=self.delete_sel)
        e.add_separator()
        e.add_command(label="Traer al frente", command=lambda: self.reorder("top"))
        e.add_command(label="Enviar al fondo", command=lambda: self.reorder("bottom"))
        e.add_separator()
        e.add_command(label="Centrar horizontalmente", command=lambda: self.center("h"))
        e.add_command(label="Centrar verticalmente", command=lambda: self.center("v"))
        m.add_cascade(label="Editar", menu=e)

        ins = tk.Menu(m, tearoff=0)
        ins.add_command(label="Texto", accelerator="T", command=lambda: self.add("text"))
        bc = tk.Menu(ins, tearoff=0)
        groups = [("Lineales 1D", "linear"), ("Postales", "postal"), ("Apilados", "stacked"),
                  ("2D", "matrix"), ("Otros", None)]
        for title, kind in groups:
            bc.add_command(label=title, state="disabled")
            for k, s in barcodes.SPECS.items():
                if s.kind == kind or (kind is None and s.kind in ("maxi", "databar")):
                    bc.add_command(label="   " + s.name, command=lambda k=k: self.add("barcode", btype=k))
        ins.add_cascade(label="Código de barras", menu=bc)
        ins.add_command(label="Código QR", accelerator="Q", command=lambda: self.add("barcode", btype="qr"))
        ins.add_command(label="Imagen desde archivo…", accelerator="I", command=self.add_image)
        ins.add_command(label="Imagen desde portapapeles", accelerator="Ctrl+Shift+V", command=self.paste_image)
        ins.add_separator()
        ins.add_command(label="Rectángulo", accelerator="R", command=lambda: self.add("box"))
        ins.add_command(label="Línea horizontal", accelerator="L", command=lambda: self.add("line", dir="H"))
        ins.add_command(label="Línea vertical", command=lambda: self.add("line", dir="V", w=3, h=150))
        ins.add_command(label="Línea diagonal", command=lambda: self.add("line", dir="R", w=150, h=100))
        ins.add_command(label="Círculo / elipse", accelerator="C", command=lambda: self.add("ellipse"))
        m.add_cascade(label="Insertar", menu=ins)

        v = tk.Menu(m, tearoff=0)
        v.add_command(label="Acercar", accelerator="Ctrl++", command=lambda: self.zoom_step(1))
        v.add_command(label="Alejar", accelerator="Ctrl+-", command=lambda: self.zoom_step(-1))
        v.add_command(label="Ajustar a ventana", accelerator="Ctrl+0", command=self.zoom_fit)
        v.add_command(label="Tamaño real (1 dot = 1 px)", command=lambda: self.set_zoom(1.0))
        v.add_separator()
        v.add_checkbutton(label="Mostrar rejilla", variable=self.show_grid, command=self.redraw)
        v.add_checkbutton(label="Ajustar a rejilla", variable=self.snap)
        m.add_cascade(label="Ver", menu=v)

        p = tk.Menu(m, tearoff=0)
        p.add_command(label="Enviar a impresora…", accelerator="Ctrl+P", command=self.print_dialog)
        p.add_command(label="Vista previa real (Labelary, online)", accelerator="F5", command=self.labelary)
        p.add_command(label="Copiar ZPL al portapapeles", accelerator="Ctrl+Shift+C", command=self.copy_zpl)
        m.add_cascade(label="Imprimir", menu=p)
        self.config(menu=m)

    def _build_ui(self):
        tb1 = ttk.Frame(self, padding=(6, 4, 6, 0))
        tb1.pack(side="top", fill="x")
        tb2 = ttk.Frame(self, padding=(6, 2, 6, 4))
        tb2.pack(side="top", fill="x")
        tb = tb1

        def btn(text, cmd):
            b = ttk.Button(tb, text=text, command=cmd)
            b.pack(side="left", padx=1)
            return b

        btn("Nuevo", self.file_new)
        btn("Abrir", self.file_open)
        btn("Guardar", self.file_save)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        btn("＋ Texto", lambda: self.add("text"))
        self.bc_var = tk.StringVar(value=barcodes.SPECS["code128"].name)
        bc_names = [s.name for s in barcodes.SPECS.values()]
        btn("＋ Código", lambda: self.add("barcode", btype=self._bc_key(self.bc_var.get())))
        cb = ttk.Combobox(tb, textvariable=self.bc_var, values=bc_names, state="readonly", width=24)
        cb.pack(side="left", padx=(0, 4))
        btn("＋ QR", lambda: self.add("barcode", btype="qr"))
        btn("＋ Imagen", self.add_image)
        btn("＋ Rectángulo", lambda: self.add("box"))
        btn("＋ Línea", lambda: self.add("line", dir="H"))
        btn("＋ Círculo", lambda: self.add("ellipse"))
        tb = tb2
        btn("↶ Deshacer", self.undo)
        btn("↷ Rehacer", self.redo)
        btn("Duplicar", self.duplicate)
        btn("Eliminar", self.delete_sel)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        btn("−", lambda: self.zoom_step(-1))
        self.zoom_lbl = ttk.Label(tb, text="200%", width=6, anchor="center")
        self.zoom_lbl.pack(side="left")
        btn("+", lambda: self.zoom_step(1))
        btn("Ajustar", self.zoom_fit)
        ttk.Label(tb, text="  Rejilla:").pack(side="left")
        g = ttk.Combobox(tb, textvariable=self.grid_mm, state="readonly", width=7,
                         values=["0.5 mm", "1 mm", "2 mm", "5 mm", "10 mm", "1 dot", "4 dots", "8 dots", "10 dots"])
        g.pack(side="left")
        g.bind("<<ComboboxSelected>>", lambda e: self.redraw())
        ttk.Checkbutton(tb, text="Ajustar", variable=self.snap).pack(side="left", padx=4)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        btn("Vista Labelary", self.labelary)
        btn("🖨 Imprimir", self.print_dialog)

        self.status = ttk.Label(self, text="", anchor="w", padding=(8, 2), relief="sunken")
        self.status.pack(side="bottom", fill="x")

        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True)

        # capas
        left = ttk.Frame(pw, padding=4)
        ttk.Label(left, text="Elementos", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse", height=20)
        self.tree.pack(fill="both", expand=True, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._tree_select)
        lb = ttk.Frame(left)
        lb.pack(fill="x")
        ttk.Button(lb, text="▲", width=3, command=lambda: self.reorder("up")).pack(side="left")
        ttk.Button(lb, text="▼", width=3, command=lambda: self.reorder("down")).pack(side="left")
        ttk.Button(lb, text="Ocultar", command=self.toggle_hidden).pack(side="left", padx=2)
        pw.add(left, weight=0)

        # lienzo
        mid = ttk.Frame(pw)
        self.canvas = tk.Canvas(mid, background="#8a8f98", highlightthickness=0, cursor="arrow")
        hs = ttk.Scrollbar(mid, orient="horizontal", command=self.canvas.xview)
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hs.set, yscrollcommand=vs.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        pw.add(mid, weight=1)

        # derecha
        right = ttk.Notebook(pw, width=380)
        self.props = ScrollFrame(right)
        right.add(self.props, text="Propiedades")
        zf = ttk.Frame(right, padding=6)
        bar = ttk.Frame(zf)
        bar.pack(fill="x")
        ttk.Button(bar, text="Copiar", command=self.copy_zpl).pack(side="left")
        ttk.Button(bar, text="Guardar .zpl", command=self.export_zpl).pack(side="left", padx=2)
        ttk.Button(bar, text="Imprimir", command=self.print_dialog).pack(side="left")
        self.zpl_info = ttk.Label(bar, text="")
        self.zpl_info.pack(side="right")
        self.zpl_text = tk.Text(zf, wrap="char", font=("Consolas", 10), undo=False)
        zs = ttk.Scrollbar(zf, command=self.zpl_text.yview)
        self.zpl_text.configure(yscrollcommand=zs.set)
        zs.pack(side="right", fill="y", pady=(6, 0))
        self.zpl_text.pack(fill="both", expand=True, pady=(6, 0))
        right.add(zf, text="Código ZPL")
        self.notebook = right
        pw.add(right, weight=0)

        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_motion)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Motion>", self.on_hover)
        c.bind("<Button-3>", self.on_context)
        c.bind("<Configure>", lambda e: self._update_scrollregion())
        c.bind("<MouseWheel>", self.on_wheel)

    def _bind_keys(self):
        b = self.bind_all
        b("<Control-n>", lambda e: self.file_new())
        b("<Control-o>", lambda e: self.file_open())
        b("<Control-s>", lambda e: self.file_save())
        b("<Control-e>", lambda e: self.export_zpl())
        b("<Control-p>", lambda e: self.print_dialog())
        b("<F5>", lambda e: self.labelary())
        b("<Control-plus>", lambda e: self.zoom_step(1))
        b("<Control-equal>", lambda e: self.zoom_step(1))
        b("<Control-minus>", lambda e: self.zoom_step(-1))
        b("<Control-0>", lambda e: self.zoom_fit())
        b("<Control-Shift-C>", lambda e: self.copy_zpl())
        b("<Control-Shift-V>", lambda e: self.paste_image())
        c = self.canvas
        c.bind("<Control-z>", lambda e: self.undo())
        c.bind("<Control-y>", lambda e: self.redo())
        c.bind("<Control-c>", lambda e: self.copy_el())
        c.bind("<Control-v>", lambda e: self.paste())
        c.bind("<Control-d>", lambda e: self.duplicate())
        c.bind("<Delete>", lambda e: self.delete_sel())
        for key, (dx, dy) in {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1)}.items():
            c.bind(f"<{key}>", lambda e, dx=dx, dy=dy: self.nudge(dx, dy, 1))
            c.bind(f"<Shift-{key}>", lambda e, dx=dx, dy=dy: self.nudge(dx, dy, 10))
        c.bind("t", lambda e: self.add("text"))
        c.bind("q", lambda e: self.add("barcode", btype="qr"))
        c.bind("b", lambda e: self.add("barcode", btype=self._bc_key(self.bc_var.get())))
        c.bind("i", lambda e: self.add_image())
        c.bind("r", lambda e: self.add("box"))
        c.bind("l", lambda e: self.add("line", dir="H"))
        c.bind("c", lambda e: self.add("ellipse"))

    def _bc_key(self, name):
        for k, s in barcodes.SPECS.items():
            if s.name == name:
                return k
        return "code128"

    # ------------------------------------------------------------- geometry
    @property
    def dpi(self):
        return int(self.label["dpi"])

    def grid_dots(self) -> float:
        v = self.grid_mm.get()
        n = float(v.split()[0])
        return n if "dot" in v else n * self.dpi / 25.4

    def to_canvas(self, x, y):
        return MARGIN + x * self.zoom, MARGIN + y * self.zoom

    def to_dots(self, cx, cy):
        return (self.canvas.canvasx(cx) - MARGIN) / self.zoom, (self.canvas.canvasy(cy) - MARGIN) / self.zoom

    def snapv(self, v):
        if not self.snap.get():
            return round(v)
        g = self.grid_dots()
        return round(round(v / g) * g)

    # --------------------------------------------------------------- render
    def ensure_id(self, el):
        if "_id" not in el:
            el["_id"] = self._next_id
            self._next_id += 1
        else:
            self._next_id = max(self._next_id, el["_id"] + 1)
        return el["_id"]

    def rendered(self, el):
        eid = self.ensure_id(el)
        key = _key_of(el)
        c = self._cache.get(eid)
        if c is None or c[0] != key:
            rgba, err = render_rgba(el, self.dpi)
            c = (key, rgba, err)
            self._cache[eid] = c
        return c[1], c[2]

    def bbox(self, el):
        img, _ = self.rendered(el)
        return el["x"], el["y"], img.width, img.height

    def _photo(self, el):
        img, _ = self.rendered(el)
        w, h = max(1, round(img.width * self.zoom)), max(1, round(img.height * self.zoom))
        if (w, h) != img.size:
            img = img.resize((w, h), Image.NEAREST if self.zoom >= 1 else Image.BOX)
        return ImageTk.PhotoImage(img)

    def redraw(self):
        c = self.canvas
        c.delete("all")
        self._items.clear()
        self._photos.clear()
        W, H = zpl.label_dots(self.label)
        x0, y0 = self.to_canvas(0, 0)
        x1, y1 = self.to_canvas(W, H)
        c.create_rectangle(x0 + 5, y0 + 5, x1 + 5, y1 + 5, fill="#5b6068", outline="", tags="bg")
        c.create_rectangle(x0, y0, x1, y1, fill="white", outline="#333", tags="bg")
        if self.show_grid.get():
            g = self.grid_dots()
            step = g
            while step * self.zoom < 8:
                step *= 2
            n = 1
            while n * step < W:
                x = x0 + n * step * self.zoom
                c.create_line(x, y0, x, y1, fill="#e6e9ef", tags="grid")
                n += 1
            n = 1
            while n * step < H:
                y = y0 + n * step * self.zoom
                c.create_line(x0, y, x1, y, fill="#e6e9ef", tags="grid")
                n += 1
        for el in self.elements:
            self._draw_el(el)
        self._draw_sel()
        self._update_scrollregion()
        self.zoom_lbl.config(text=f"{round(self.zoom * 100)}%")
        self.refresh_tree()

    def _draw_el(self, el):
        if el.get("hidden"):
            return
        eid = self.ensure_id(el)
        ph = self._photo(el)
        self._photos[eid] = ph
        x, y = self.to_canvas(el["x"], el["y"])
        it = self._items.get(eid)
        if it and self.canvas.type(it):
            self.canvas.itemconfigure(it, image=ph)
            self.canvas.coords(it, x, y)
        else:
            self._items[eid] = self.canvas.create_image(x, y, image=ph, anchor="nw", tags="el")
            # mantener orden de apilado
            self.canvas.tag_raise("sel")

    def refresh_el(self, el, rerender=True):
        """Actualiza un elemento del lienzo tras un cambio."""
        if rerender:
            self._draw_el(el)
        else:
            it = self._items.get(el.get("_id"))
            if it:
                self.canvas.coords(it, *self.to_canvas(el["x"], el["y"]))
        self._draw_sel()
        self.schedule_zpl()

    def _draw_sel(self):
        c = self.canvas
        c.delete("sel")
        el = self.sel
        if el is None or el.get("hidden"):
            return
        x, y, w, h = self.bbox(el)
        x0, y0 = self.to_canvas(x, y)
        x1, y1 = self.to_canvas(x + w, y + h)
        W, H = zpl.label_dots(self.label)
        outside = x + w > W or y + h > H
        c.create_rectangle(x0 - 1, y0 - 1, x1 + 1, y1 + 1, outline="#d93025" if outside else "#1a73e8",
                           dash=(4, 2), tags="sel")
        if self._resizable(el):
            for name, (hx, hy) in self._handles(x0, y0, x1, y1).items():
                c.create_rectangle(hx - HANDLE, hy - HANDLE, hx + HANDLE, hy + HANDLE, fill="white",
                                   outline="#1a73e8", tags=("sel", "handle", f"h_{name}"))

    @staticmethod
    def _handles(x0, y0, x1, y1):
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return {"nw": (x0, y0), "n": (mx, y0), "ne": (x1, y0), "e": (x1, my),
                "se": (x1, y1), "s": (mx, y1), "sw": (x0, y1), "w": (x0, my)}

    def _resizable(self, el):
        return not (el["type"] == "barcode" and el["btype"] == "maxicode")

    def _update_scrollregion(self):
        W, H = zpl.label_dots(self.label)
        w = W * self.zoom + 2 * MARGIN
        h = H * self.zoom + 2 * MARGIN
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.configure(scrollregion=(min(0, (w - cw) / 2), min(0, (h - ch) / 2) if h < ch else 0,
                                            max(w, (w + cw) / 2), max(h, ch)))

    # ----------------------------------------------------------------- zoom
    def set_zoom(self, z):
        self.zoom = max(0.1, min(16.0, z))
        self.redraw()

    def zoom_step(self, d):
        cur = self.zoom
        if d > 0:
            nxt = next((z for z in ZOOMS if z > cur + 1e-6), ZOOMS[-1])
        else:
            nxt = next((z for z in reversed(ZOOMS) if z < cur - 1e-6), ZOOMS[0])
        self.set_zoom(nxt)

    def zoom_fit(self):
        self.update_idletasks()
        W, H = zpl.label_dots(self.label)
        cw = max(200, self.canvas.winfo_width() - 2 * MARGIN)
        ch = max(200, self.canvas.winfo_height() - 2 * MARGIN)
        self.set_zoom(min(cw / W, ch / H, 8.0))
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def on_wheel(self, e):
        if e.state & 0x4:  # Ctrl
            self.zoom_step(1 if e.delta > 0 else -1)
        elif e.state & 0x1:  # Shift
            self.canvas.xview_scroll(int(-e.delta / 120), "units")
        else:
            self.canvas.yview_scroll(int(-e.delta / 120), "units")
        return "break"

    # ---------------------------------------------------------------- undo
    def snapshot(self):
        return (dict(self.label), [copy_element(e) for e in self.elements],
                self.elements.index(self.sel) if self.sel in self.elements else None)

    def checkpoint(self, key=None):
        if key is not None and key == self._last_ck:
            return
        self._last_ck = key
        self._undo.append(self.snapshot())
        if len(self._undo) > 200:
            self._undo.pop(0)
        self._redo.clear()
        self.dirty = True
        self._update_title()

    def _restore(self, snap):
        label, els, idx = snap
        self.label = dict(label)
        self.elements = [copy_element(e) for e in els]
        self.sel = self.elements[idx] if idx is not None and idx < len(self.elements) else None
        self._last_ck = None
        self.redraw()
        self.build_props()
        self.schedule_zpl()

    def undo(self):
        if not self._undo:
            return
        self._redo.append(self.snapshot())
        self._restore(self._undo.pop())

    def redo(self):
        if not self._redo:
            return
        self._undo.append(self.snapshot())
        self._restore(self._redo.pop())

    # ------------------------------------------------------------- editing
    def _visible_origin(self):
        x, y = self.to_dots(0, 0)
        W, H = zpl.label_dots(self.label)
        n = len(self.elements) % 8
        return (max(0, min(W - 50, max(0, int(x)) + 20 + n * 10)),
                max(0, min(H - 30, max(0, int(y)) + 20 + n * 10)))

    def add(self, etype, **kw):
        x, y = self._visible_origin()
        el = new_element(etype, x, y, **kw)
        if etype == "text":
            el["h"] = max(10, round(self.dpi * 0.15))
        self.insert(el)
        return el

    def insert(self, el):
        self.checkpoint()
        self.ensure_id(el)
        self.elements.append(el)
        self.sel = el
        self.redraw()
        self.build_props()
        self.schedule_zpl()
        self.canvas.focus_set()

    def delete_sel(self):
        if self.sel is None:
            return
        self.checkpoint()
        self.elements.remove(self.sel)
        self.sel = None
        self.redraw()
        self.build_props()
        self.schedule_zpl()

    def duplicate(self):
        if self.sel is None:
            return
        el = copy_element(self.sel)
        el.pop("_id", None)
        el["x"] += 15
        el["y"] += 15
        self.insert(el)

    def copy_el(self):
        if self.sel is not None:
            self._clip_el = copy_element(self.sel)

    def paste(self):
        if self._clip_el is None:
            self.paste_image()
            return
        el = copy_element(self._clip_el)
        el.pop("_id", None)
        el["x"], el["y"] = self._visible_origin()
        self.insert(el)

    def reorder(self, how):
        if self.sel is None:
            return
        self.checkpoint()
        i = self.elements.index(self.sel)
        self.elements.pop(i)
        if how == "top":
            self.elements.append(self.sel)
        elif how == "bottom":
            self.elements.insert(0, self.sel)
        elif how == "up":
            self.elements.insert(min(len(self.elements), i + 1), self.sel)
        else:
            self.elements.insert(max(0, i - 1), self.sel)
        self.redraw()
        self.schedule_zpl()

    def toggle_hidden(self):
        if self.sel is None:
            return
        self.checkpoint()
        self.sel["hidden"] = not self.sel.get("hidden")
        self.redraw()
        self.schedule_zpl()

    def center(self, axis):
        if self.sel is None:
            return
        self.checkpoint()
        W, H = zpl.label_dots(self.label)
        _, _, w, h = self.bbox(self.sel)
        if axis == "h":
            self.sel["x"] = max(0, round((W - w) / 2))
        else:
            self.sel["y"] = max(0, round((H - h) / 2))
        self.refresh_el(self.sel, False)
        self.sync_xy()

    def nudge(self, dx, dy, step):
        if self.sel is None:
            return
        self.checkpoint(("nudge", self.sel["_id"]))
        self.sel["x"] = max(0, self.sel["x"] + dx * step)
        self.sel["y"] = max(0, self.sel["y"] + dy * step)
        self.refresh_el(self.sel, False)
        self.sync_xy()

    # -------------------------------------------------------------- images
    def add_image(self):
        path = filedialog.askopenfilename(title="Insertar imagen", filetypes=[
            ("Imágenes", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp *.ico"), ("Todos", "*.*")])
        if not path:
            return
        try:
            img = Image.open(path)
            img.load()
        except Exception as ex:
            messagebox.showerror("Imagen", f"No se pudo abrir la imagen:\n{ex}")
            return
        self._insert_pil(img)

    def paste_image(self):
        data = ImageGrab.grabclipboard()
        if isinstance(data, list) and data:
            try:
                data = Image.open(data[0])
                data.load()
            except Exception:
                data = None
        if not isinstance(data, Image.Image):
            messagebox.showinfo("Pegar imagen", "El portapapeles no contiene una imagen.")
            return
        self._insert_pil(data)

    @staticmethod
    def _png_bytes(img: Image.Image) -> bytes:
        img = img.copy()
        img.thumbnail((2400, 2400))
        if img.mode not in ("RGB", "RGBA", "L", "LA", "1", "P"):
            img = img.convert("RGBA")
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()

    def _fit_image_size(self, img, fill=None):
        """Tamaño original (1 px = 1 dot, como Labelary) si cabe; si no, reducido para caber."""
        W, H = zpl.label_dots(self.label)
        w, h = img.size
        f = min(W / w, H / h) if fill else min(1.0, W / w, H / h)
        return max(1, round(w * f)), max(1, round(h * f))

    def _insert_pil(self, img):
        x, y = self._visible_origin()
        w, h = self._fit_image_size(img)
        el = new_element("image", x, y, png=self._png_bytes(img), w=w, h=h)
        self.insert(el)

    def replace_image(self):
        el = self.sel
        path = filedialog.askopenfilename(title="Cambiar imagen", filetypes=[
            ("Imágenes", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp"), ("Todos", "*.*")])
        if not path:
            return
        try:
            img = Image.open(path)
            img.load()
        except Exception as ex:
            messagebox.showerror("Imagen", str(ex))
            return
        self.checkpoint()
        el["png"] = self._png_bytes(img)
        el["w"], el["h"] = self._fit_image_size(img)
        self.refresh_el(el)
        self.build_props()

    def image_preset(self, name):
        self.checkpoint()
        self.sel.update(IMG_PRESETS[name])
        self.sel.pop("dither", None)
        self.refresh_el(self.sel)
        self.build_props()

    def image_fit_label(self):
        el = self.sel
        try:
            img = Image.open(io.BytesIO(el["png"]))
        except Exception:
            return
        self.checkpoint()
        el["w"], el["h"] = self._fit_image_size(img, 1.0)
        el["x"], el["y"] = 0, 0
        self.refresh_el(el)
        self.build_props()

    def image_original_size(self):
        el = self.sel
        try:
            img = Image.open(io.BytesIO(el["png"]))
        except Exception:
            return
        self.checkpoint()
        el["w"], el["h"] = img.size
        self.refresh_el(el)
        self.build_props()

    # --------------------------------------------------------------- mouse
    def _hit(self, dx, dy):
        tol = max(2.0, 5 / self.zoom)
        for el in reversed(self.elements):
            if el.get("hidden"):
                continue
            x, y, w, h = self.bbox(el)
            if not (x - tol <= dx <= x + w + tol and y - tol <= dy <= y + h + tol):
                continue
            if el["type"] in ("box", "ellipse", "line"):
                # solo el trazo es "clicable", para poder seleccionar lo que hay dentro
                img, _ = self.rendered(el)
                r = int(tol)
                px, py = int(dx - x), int(dy - y)
                box = (max(0, px - r), max(0, py - r), min(img.width, px + r + 1), min(img.height, py + r + 1))
                if box[0] >= box[2] or box[1] >= box[3] or img.crop(box).getchannel("A").getextrema()[1] == 0:
                    continue
            return el
        return None

    def _handle_at(self, cx, cy):
        c = self.canvas
        x, y = c.canvasx(cx), c.canvasy(cy)
        for it in c.find_overlapping(x - 2, y - 2, x + 2, y + 2):
            for t in c.gettags(it):
                if t.startswith("h_"):
                    return t[2:]
        return None

    def on_press(self, e):
        self.canvas.focus_set()
        handle = self._handle_at(e.x, e.y) if self.sel is not None else None
        dx, dy = self.to_dots(e.x, e.y)
        if handle:
            x, y, w, h = self.bbox(self.sel)
            self._drag = {"mode": "resize", "handle": handle, "start": (dx, dy), "rect": (x, y, w, h),
                          "orig": copy_element(self.sel), "moved": False}
            return
        el = self._hit(dx, dy)
        if el is not self.sel:
            self.sel = el
            self._draw_sel()
            self.build_props()
            self.refresh_tree()
        if el is not None:
            self._drag = {"mode": "move", "start": (dx, dy), "orig": (el["x"], el["y"]), "moved": False}
        else:
            self._drag = None

    def on_motion(self, e):
        d = self._drag
        if not d or self.sel is None:
            return
        dx, dy = self.to_dots(e.x, e.y)
        ddx, ddy = dx - d["start"][0], dy - d["start"][1]
        if not d["moved"]:
            if abs(ddx) * self.zoom < 2 and abs(ddy) * self.zoom < 2:
                return
            self.checkpoint()
            d["moved"] = True
        el = self.sel
        if d["mode"] == "move":
            ox, oy = d["orig"]
            el["x"] = max(0, self.snapv(ox + ddx))
            el["y"] = max(0, self.snapv(oy + ddy))
            self.refresh_el(el, False)
            self.sync_xy()
        else:
            self._resize(d, ddx, ddy)
        self.status.config(text=self._status_text(dx, dy))

    def on_release(self, e):
        d = self._drag
        self._drag = None
        if d and d.get("moved") and d["mode"] == "resize":
            self.build_props()
            self.refresh_tree()
        elif d and d.get("moved"):
            self.refresh_tree()

    def on_double(self, e):
        if self.sel is not None:
            self.notebook.select(0)
            w = getattr(self, "_focus_widget", None)
            if w is not None and w.winfo_exists():
                w.focus_set()
                if isinstance(w, tk.Text):
                    w.tag_add("sel", "1.0", "end-1c")

    def on_hover(self, e):
        dx, dy = self.to_dots(e.x, e.y)
        self.status.config(text=self._status_text(dx, dy))
        h = self._handle_at(e.x, e.y) if self.sel is not None else None
        cur = {"nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
               "n": "sb_v_double_arrow", "s": "sb_v_double_arrow", "e": "sb_h_double_arrow",
               "w": "sb_h_double_arrow"}.get(h)
        if cur is None:
            cur = "fleur" if self._hit(dx, dy) is not None else "arrow"
        try:
            self.canvas.configure(cursor=cur)
        except tk.TclError:
            self.canvas.configure(cursor="arrow")

    def _status_text(self, dx, dy):
        mm = 25.4 / self.dpi
        s = f"  x: {dx:.0f}  y: {dy:.0f} dots   ({dx * mm:.1f}, {dy * mm:.1f} mm)"
        if self.sel is not None:
            x, y, w, h = self.bbox(self.sel)
            s += f"     |  Selección: {TYPE_NAMES[self.sel['type']]}  {x},{y}  {w}×{h} dots ({w * mm:.1f}×{h * mm:.1f} mm)"
        W, H = zpl.label_dots(self.label)
        s += f"     |  Etiqueta {W}×{H} dots @ {self.dpi} dpi"
        return s

    def on_context(self, e):
        dx, dy = self.to_dots(e.x, e.y)
        el = self._hit(dx, dy)
        if el is not None and el is not self.sel:
            self.sel = el
            self._draw_sel()
            self.build_props()
        m = tk.Menu(self, tearoff=0)
        if self.sel is not None:
            m.add_command(label="Duplicar", command=self.duplicate)
            m.add_command(label="Copiar", command=self.copy_el)
            m.add_command(label="Eliminar", command=self.delete_sel)
            m.add_separator()
            m.add_command(label="Traer al frente", command=lambda: self.reorder("top"))
            m.add_command(label="Enviar al fondo", command=lambda: self.reorder("bottom"))
            m.add_separator()
            m.add_command(label="Centrar horizontalmente", command=lambda: self.center("h"))
            m.add_command(label="Centrar verticalmente", command=lambda: self.center("v"))
            if self.sel["type"] not in NO_ROT:
                m.add_command(label="Rotar 90°", command=self.rotate_sel)
            m.add_separator()
        m.add_command(label="Pegar", command=self.paste)
        m.add_command(label="Añadir texto aquí", command=lambda: self._add_at("text", dx, dy))
        m.add_command(label="Añadir código QR aquí", command=lambda: self._add_at("barcode", dx, dy, btype="qr"))
        m.add_command(label="Añadir Code 128 aquí", command=lambda: self._add_at("barcode", dx, dy, btype="code128"))
        m.tk_popup(e.x_root, e.y_root)

    def _add_at(self, t, dx, dy, **kw):
        el = self.add(t, **kw)
        el["x"], el["y"] = max(0, self.snapv(dx)), max(0, self.snapv(dy))
        self.refresh_el(el, False)
        self.sync_xy()

    def rotate_sel(self):
        el = self.sel
        if el is None or el["type"] in NO_ROT:
            return
        self.checkpoint()
        order = [r for r, _ in ROTATIONS]
        el["rot"] = order[(order.index(el.get("rot", "N")) + 1) % 4]
        self.refresh_el(el)
        self.build_props()

    # -------------------------------------------------------------- resize
    def _resize(self, d, ddx, ddy):
        hnd = d["handle"]
        x, y, w, h = d["rect"]
        left, top, right, bottom = x, y, x + w, y + h
        if "w" in hnd:
            left = min(right - 1, left + ddx)
        if "e" in hnd:
            right = max(left + 1, right + ddx)
        if "n" in hnd:
            top = min(bottom - 1, top + ddy)
        if "s" in hnd:
            bottom = max(top + 1, bottom + ddy)
        if self.snap.get():
            if "w" in hnd:
                left = self.snapv(left)
            if "e" in hnd:
                right = self.snapv(right)
            if "n" in hnd:
                top = self.snapv(top)
            if "s" in hnd:
                bottom = self.snapv(bottom)
        nw, nh = max(1, right - left), max(1, bottom - top)
        orig = d["orig"]
        el = self.sel
        horiz = "e" in hnd or "w" in hnd
        vert = "n" in hnd or "s" in hnd
        rot = orig.get("rot", "N")
        ow, oh = w, h
        if rot in ("R", "B") and not (orig["type"] == "barcode" and orig["btype"] == "qr"):
            nw, nh, ow, oh = nh, nw, oh, ow
            horiz, vert = vert, horiz
        new = self._apply_size(orig, nw, nh, ow, oh, horiz, vert)
        keep_id = el["_id"]
        el.clear()
        el.update(new)
        el["_id"] = keep_id
        _, _, aw, ah = self.bbox(el)
        el["x"] = round(right - aw) if "w" in hnd else orig["x"]
        el["y"] = round(bottom - ah) if "n" in hnd else orig["y"]
        el["x"], el["y"] = max(0, el["x"]), max(0, el["y"])
        self.refresh_el(el)
        self.sync_xy()

    @staticmethod
    def _apply_size(orig, nw, nh, ow, oh, horiz, vert):
        el = copy_element(orig)
        t = el["type"]
        clamp = lambda v, a, b: max(a, min(b, v))
        if t in ("box", "ellipse"):
            if horiz:
                el["w"] = round(nw)
            if vert:
                el["h"] = round(nh)
            if t == "box":
                el["w"], el["h"] = max(el["w"], el["t"]), max(el["h"], el["t"])
        elif t == "line":
            dr = el.get("dir", "H")
            if dr == "H":
                if horiz:
                    el["w"] = round(nw)
                if vert:
                    el["t"] = max(1, round(nh))
            elif dr == "V":
                if vert:
                    el["h"] = round(nh)
                if horiz:
                    el["t"] = max(1, round(nw))
            else:
                if horiz:
                    el["w"] = max(1, round(nw - el["t"]))
                if vert:
                    el["h"] = round(nh)
        elif t == "image":
            ratio = orig["h"] / max(1, orig["w"])
            if el.get("keep_ratio", True):
                if horiz:
                    el["w"] = round(nw)
                    el["h"] = max(1, round(nw * ratio))
                else:
                    el["h"] = round(nh)
                    el["w"] = max(1, round(nh / ratio))
            else:
                if horiz:
                    el["w"] = round(nw)
                if vert:
                    el["h"] = round(nh)
        elif t == "text":
            h0, w0 = int(orig["h"]), int(orig.get("w", 0))
            if horiz and vert:
                f = nh / max(1, oh)
                el["h"] = max(5, round(h0 * f))
                el["w"] = max(0, round(w0 * f))
                if el.get("block"):
                    el["block_w"] = max(10, round(nw))
            elif horiz:
                if el.get("block"):
                    el["block_w"] = max(10, round(nw))
                else:
                    el["w"] = max(3, round((w0 or h0) * nw / max(1, ow)))
            elif vert:
                el["h"] = max(5, round(h0 * nh / max(1, oh)))
                if not el.get("block") and w0 == 0:
                    el["w"] = h0
        elif t == "barcode":
            spec = barcodes.SPECS[el["btype"]]
            p = dict(barcodes.defaults(el["btype"]), **el.get("params", {}))
            if spec.kind in ("linear", "postal", "stacked"):
                if horiz:
                    p["mw"] = clamp(round(int(p["mw"]) * nw / max(1, ow)), 1, 10)
                if vert:
                    if spec.kind == "stacked":
                        p["rowh"] = clamp(round(int(p["rowh"]) * nh / max(1, oh)), 1, 255)
                    else:
                        p["h"] = max(1, round(int(p["h"]) + (nh - oh)))
                        if el["btype"] == "tlc39":
                            p["h"] = max(1, p["h"])
            elif spec.kind == "matrix":
                f = max(nw / max(1, ow) if horiz else 0, nh / max(1, oh) if vert else 0)
                mx = next(q.maxv for q in spec.params if q.key == "mag")
                p["mag"] = clamp(round(int(p["mag"]) * f), 1, int(mx))
            elif spec.kind == "databar":
                if horiz:
                    p["mag"] = clamp(round(int(p["mag"]) * nw / max(1, ow)), 1, 10)
                if vert:
                    p["h"] = max(1, round(int(p["h"]) + (nh - oh)))
            el["params"] = p
        return el

    # ------------------------------------------------------------ properties
    def sync_xy(self):
        if self.sel is None:
            return
        self._loading = True
        try:
            for k in ("x", "y"):
                v = self.prop_vars.get(k)
                if v is not None and str(v.get()) != str(self.sel[k]):
                    v.set(self.sel[k])
        finally:
            self._loading = False

    def build_props(self):
        self.props.clear()
        self.prop_vars = {}
        self._focus_widget = None
        self._err_lbl = None
        f = self.props.inner
        f.columnconfigure(1, weight=1)
        self._row = 0
        if self.sel is None:
            self._label_props(f)
        else:
            self._element_props(f, self.sel)

    def _section(self, f, text):
        lbl = ttk.Label(f, text=text, font=("Segoe UI", 10, "bold"))
        lbl.grid(row=self._row, column=0, columnspan=2, sticky="w", pady=(10, 3))
        self._row += 1

    def _field(self, f, label, kind, get, set_, options=None, minv=None, maxv=None, ck=None, width=None,
               rebuild=False, inc=1):
        """Crea un campo editable ligado a get()/set_(valor)."""
        r = self._row
        self._row += 1
        if kind != "bool":
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", padx=(0, 6), pady=2)
        cur = get()
        ck = ck or ("prop", id(self.sel), label)

        def apply(value):
            if self._loading:
                return
            try:
                if value == get():
                    return
            except Exception:
                pass
            self.checkpoint(ck)
            set_(value)
            self.after_change(rebuild)

        if kind == "text":
            t = tk.Text(f, height=3, width=width or 28, wrap="word", font=("Segoe UI", 10), undo=True)
            t.insert("1.0", str(cur))
            t.grid(row=r, column=1, sticky="ew", pady=2)
            t.bind("<KeyRelease>", lambda e: apply(t.get("1.0", "end-1c")))
            t.bind("<<Paste>>", lambda e: self.after(10, lambda: apply(t.get("1.0", "end-1c"))))
            return t
        if kind == "bool":
            v = tk.BooleanVar(value=bool(cur))
            w = ttk.Checkbutton(f, text=label, variable=v, command=lambda: apply(bool(v.get())))
            w.grid(row=r, column=0, columnspan=2, sticky="w", pady=2)
            self.prop_vars[label] = v
            return w
        if kind == "choice":
            opts = [(o, str(o)) if not isinstance(o, tuple) else o for o in options]
            labels = [b for _, b in opts]
            v = tk.StringVar(value=next((b for a, b in opts if a == cur), str(cur)))
            w = ttk.Combobox(f, textvariable=v, values=labels, state="readonly", width=width or 26)
            w.grid(row=r, column=1, sticky="ew", pady=2)
            w.bind("<<ComboboxSelected>>", lambda e: apply(next(a for a, b in opts if b == v.get())))
            self.prop_vars[label] = v
            return w
        # int / float / str
        v = tk.StringVar(value=str(cur))
        if kind in ("int", "float"):
            w = ttk.Spinbox(f, textvariable=v, from_=minv if minv is not None else -99999,
                            to=maxv if maxv is not None else 99999, increment=0.1 if kind == "float" else inc,
                            width=width or 10)
        else:
            w = ttk.Entry(f, textvariable=v, width=width or 28)
        w.grid(row=r, column=1, sticky="w" if kind != "str" else "ew", pady=2)

        def on_write(*_):
            s = v.get().strip().replace(",", ".")
            try:
                val = int(float(s)) if kind == "int" else (float(s) if kind == "float" else v.get())
            except ValueError:
                return
            if kind in ("int", "float"):
                if minv is not None and val < minv:
                    return
                if maxv is not None and val > maxv:
                    return
            apply(val)

        v.trace_add("write", on_write)
        self.prop_vars[label] = v
        return w

    def after_change(self, rebuild=False):
        if self.sel is not None:
            self.refresh_el(self.sel)
            self._show_error()
            self.refresh_tree()
        else:
            self._cache.clear()
            self.redraw()
            self.schedule_zpl()
        if rebuild:
            self.after_idle(self.build_props)

    def _show_error(self):
        if self._err_lbl is None or self.sel is None:
            return
        _, err = self.rendered(self.sel)
        self._err_lbl.config(text=f"⚠ {err}" if err else "")

    def _label_props(self, f):
        L = self.label
        self._section(f, "Etiqueta")
        ck = lambda k: ("label", k)

        def setter(k, conv=lambda v: v):
            def s(v):
                L[k] = conv(v)
            return s

        def set_units(u):
            if u == L["units"]:
                return
            fct = 25.4 if u == "mm" else 1 / 25.4
            L["w"], L["h"] = round(L["w"] * fct, 2), round(L["h"] * fct, 2)
            L["units"] = u

        self._field(f, "Unidades", "choice", lambda: L["units"], set_units,
                    [("mm", "Milímetros"), ("in", "Pulgadas")], ck=ck("u"), rebuild=True)
        self._field(f, f"Ancho ({L['units']})", "float", lambda: L["w"], setter("w"), minv=1, maxv=1000, ck=ck("w"))
        self._field(f, f"Alto ({L['units']})", "float", lambda: L["h"], setter("h"), minv=1, maxv=4000, ck=ck("h"))
        self._field(f, "Resolución", "choice", lambda: L["dpi"], self._set_dpi, zpl.DPI_CHOICES,
                    ck=ck("dpi"), rebuild=True)
        presets = ttk.Frame(f)
        presets.grid(row=self._row, column=0, columnspan=2, sticky="w", pady=(4, 2))
        self._row += 1
        ttk.Label(presets, text="Tamaños: ").pack(side="left")
        for name, (w, h, u) in {"4×6\"": (4, 6, "in"), "4×2\"": (4, 2, "in"), "2×1\"": (2, 1, "in"),
                                "100×150": (100, 150, "mm"), "100×50": (100, 50, "mm"), "50×25": (50, 25, "mm")}.items():
            ttk.Button(presets, text=name, width=7,
                       command=lambda w=w, h=h, u=u: self._preset(w, h, u)).pack(side="left", padx=1)
        W, H = zpl.label_dots(L)
        ttk.Label(f, text=f"= {W} × {H} dots", foreground="#555").grid(row=self._row, column=1, sticky="w")
        self._row += 1

        self._section(f, "Impresión")
        self._field(f, "Copias (^PQ)", "int", lambda: L["copies"], setter("copies"), minv=1, maxv=99999, ck=ck("c"))
        self._field(f, "Oscuridad (^MD -30..30)", "int", lambda: L["darkness"], setter("darkness"),
                    minv=-30, maxv=30, ck=ck("d"))
        self._field(f, "Velocidad (^PR, 0=no enviar)", "int", lambda: L["speed"], setter("speed"),
                    minv=0, maxv=14, ck=ck("s"))
        self._field(f, "Origen X (^LH)", "int", lambda: L["lh_x"], setter("lh_x"), minv=0, maxv=32000, ck=ck("lx"))
        self._field(f, "Origen Y (^LH)", "int", lambda: L["lh_y"], setter("lh_y"), minv=0, maxv=32000, ck=ck("ly"))
        self._field(f, "Imprimir invertida 180° (^POI)", "bool", lambda: L["invert"], setter("invert"), ck=ck("i"))
        self._field(f, "Espejo (^PMY)", "bool", lambda: L.get("mirror", False), setter("mirror"), ck=ck("m"))
        self._field(f, "UTF-8 (^CI28)", "bool", lambda: L["utf8"], setter("utf8"), ck=ck("utf"))

        ttk.Label(f, text="\nSelecciona un elemento en el lienzo para editarlo.\n"
                          "Arrastra para mover, usa los tiradores para redimensionar.\n"
                          "Flechas: mover 1 dot (Shift: 10). Ctrl+rueda: zoom.\n"
                          "Atajos: T texto, B código, Q QR, I imagen, R rectángulo,\n"
                          "L línea, C círculo, Supr eliminar, Ctrl+D duplicar.",
                  foreground="#555", justify="left").grid(row=self._row, column=0, columnspan=2, sticky="w")
        self._row += 1

    def _preset(self, w, h, u):
        self.checkpoint()
        self.label.update(w=float(w), h=float(h), units=u)
        self.redraw()
        self.build_props()
        self.schedule_zpl()
        self.after_idle(self.zoom_fit)

    def _set_dpi(self, dpi):
        old = self.label["dpi"]
        self.label["dpi"] = dpi
        if self.elements and old != dpi and messagebox.askyesno(
                "Resolución", f"¿Escalar los elementos de {old} a {dpi} dpi para mantener su tamaño físico?"):
            f = dpi / old
            for el in self.elements:
                self._scale_el(el, f)
        self._cache.clear()
        self.after_idle(self.zoom_fit)

    @staticmethod
    def _scale_el(el, f):
        for k in ("x", "y", "w", "h", "block_w", "spacing"):
            if k in el and isinstance(el[k], (int, float)):
                el[k] = max(0 if k in ("spacing", "w") else 1, round(el[k] * f))
        if "t" in el:
            el["t"] = max(1, round(el["t"] * f))
        if el["type"] == "barcode":
            p = el["params"]
            for k in ("mw", "mag", "h", "mw2", "h2"):
                if k in p:
                    p[k] = max(1, round(p[k] * f))
            if "mw" in p:
                p["mw"] = min(10, p["mw"])
            if "mag" in p and el["btype"] != "datamatrix":
                p["mag"] = min(10, p["mag"])

    def _element_props(self, f, el):
        t = el["type"]
        self._section(f, TYPE_NAMES[t])
        set_k = lambda k, conv=lambda v: v: (lambda v: el.__setitem__(k, conv(v)))
        get_k = lambda k: (lambda: el.get(k))
        ckf = lambda k: ("el", el["_id"], k)
        self._field(f, "Nombre / comentario (^FX)", "str", lambda: el.get("comment", ""), set_k("comment"), ck=ckf("cm"))
        self._field(f, "X (dots)", "int", get_k("x"), set_k("x"), minv=0, maxv=32000, ck=ckf("x"))
        self.prop_vars["x"] = self.prop_vars.pop("X (dots)")
        self._field(f, "Y (dots)", "int", get_k("y"), set_k("y"), minv=0, maxv=32000, ck=ckf("y"))
        self.prop_vars["y"] = self.prop_vars.pop("Y (dots)")
        rot_ok = t not in NO_ROT and not (t == "barcode" and el["btype"] in ("qr", "maxicode"))
        if rot_ok:
            self._field(f, "Rotación", "choice", get_k("rot"), set_k("rot"), ROTATIONS, ck=ckf("rot"))

        if t == "text":
            self._section(f, "Contenido")
            self._focus_widget = self._field(f, "Texto", "text", get_k("text"), set_k("text"), ck=ckf("text"))
            self._section(f, "Fuente")
            self._field(f, "Fuente (^A)", "choice", get_k("font"), set_k("font"), ZPL_FONTS, ck=ckf("font"))
            self._field(f, "Alto (dots)", "int", get_k("h"), set_k("h"), minv=1, maxv=32000, ck=ckf("h"))
            self._field(f, "Ancho (dots, 0=auto)", "int", get_k("w"), set_k("w"), minv=0, maxv=32000, ck=ckf("w"))
            self._field(f, "Invertir sobre fondo (^FR)", "bool", get_k("reverse"), set_k("reverse"), ck=ckf("rev"))
            self._section(f, "Bloque de texto (^FB)")
            self._field(f, "Usar bloque (ajuste de línea)", "bool", get_k("block"), set_k("block"),
                        ck=ckf("blk"), rebuild=True)
            if el.get("block"):
                self._field(f, "Ancho bloque (dots)", "int", get_k("block_w"), set_k("block_w"), minv=1, maxv=32000,
                            ck=ckf("bw"))
                self._field(f, "Máx. líneas", "int", get_k("lines"), set_k("lines"), minv=1, maxv=9999, ck=ckf("ln"))
                self._field(f, "Espacio entre líneas", "int", get_k("spacing"), set_k("spacing"),
                            minv=-9999, maxv=9999, ck=ckf("sp"))
                self._field(f, "Alineación", "choice", get_k("align"), set_k("align"),
                            [("L", "Izquierda"), ("C", "Centro"), ("R", "Derecha"), ("J", "Justificado")],
                            ck=ckf("al"))
        elif t == "barcode":
            spec = barcodes.SPECS[el["btype"]]
            self._section(f, "Simbología")

            def set_type(k):
                old_spec = barcodes.SPECS[el["btype"]]
                el["btype"] = k
                el["params"] = barcodes.defaults(k)
                if el.get("data") == old_spec.sample or barcodes.render(k, el.get("data", ""), el["params"])[1]:
                    el["data"] = barcodes.SPECS[k].sample
                if k in ("qr", "maxicode"):
                    el["rot"] = "N"

            self._field(f, "Tipo", "choice", get_k("btype"), set_type,
                        [(k, s.name) for k, s in barcodes.SPECS.items()], ck=ckf("bt"), rebuild=True, width=30)
            if spec.note:
                ttk.Label(f, text=spec.note, foreground="#555", wraplength=330, justify="left").grid(
                    row=self._row, column=0, columnspan=2, sticky="w")
                self._row += 1
            self._section(f, "Datos")
            self._focus_widget = self._field(f, "Datos (^FD)", "text", get_k("data"), set_k("data"), ck=ckf("data"))
            self._err_lbl = ttk.Label(f, text="", foreground="#c62828", wraplength=330, justify="left")
            self._err_lbl.grid(row=self._row, column=0, columnspan=2, sticky="w")
            self._row += 1
            self._section(f, "Parámetros")
            p = el.setdefault("params", barcodes.defaults(el["btype"]))
            for prm in spec.params:
                p.setdefault(prm.key, prm.default)
                self._field(f, prm.label, prm.kind, (lambda k=prm.key: p.get(k)),
                            (lambda v, k=prm.key: p.__setitem__(k, v)), options=prm.options,
                            minv=prm.minv, maxv=prm.maxv, ck=ckf("p_" + prm.key))
            self._show_error()
        elif t == "box":
            self._section(f, "Tamaño")
            self._field(f, "Ancho (dots)", "int", get_k("w"), set_k("w"), minv=1, maxv=32000, ck=ckf("w"))
            self._field(f, "Alto (dots)", "int", get_k("h"), set_k("h"), minv=1, maxv=32000, ck=ckf("h"))
            self._field(f, "Grosor borde (dots)", "int", get_k("t"), set_k("t"), minv=1, maxv=32000, ck=ckf("t"))
            ttk.Button(f, text="Relleno sólido (grosor = lado menor)",
                       command=lambda: self._fill_box(el)).grid(row=self._row, column=0, columnspan=2, sticky="w")
            self._row += 1
            self._field(f, "Redondeo esquinas (0-8)", "int", get_k("round"), set_k("round"), minv=0, maxv=8, ck=ckf("r"))
            self._field(f, "Color", "choice", get_k("color"), set_k("color"), [("B", "Negro"), ("W", "Blanco")], ck=ckf("c"))
            self._field(f, "Invertir (^FR)", "bool", get_k("reverse"), set_k("reverse"), ck=ckf("rev"))
        elif t == "line":
            self._section(f, "Línea")
            self._field(f, "Dirección", "choice", get_k("dir"), set_k("dir"),
                        [("H", "Horizontal"), ("V", "Vertical"), ("R", "Diagonal /"), ("L", "Diagonal \\")],
                        ck=ckf("d"), rebuild=True)
            d = el.get("dir", "H")
            if d != "V":
                self._field(f, "Ancho / largo (dots)", "int", get_k("w"), set_k("w"), minv=1, maxv=32000, ck=ckf("w"))
            if d != "H":
                self._field(f, "Alto / largo (dots)", "int", get_k("h"), set_k("h"), minv=1, maxv=32000, ck=ckf("h"))
            self._field(f, "Grosor (dots)", "int", get_k("t"), set_k("t"), minv=1, maxv=32000, ck=ckf("t"))
        elif t == "ellipse":
            self._section(f, "Tamaño")
            self._field(f, "Ancho (dots)", "int", get_k("w"), set_k("w"), minv=3, maxv=4095, ck=ckf("w"))
            self._field(f, "Alto (dots)", "int", get_k("h"), set_k("h"), minv=3, maxv=4095, ck=ckf("h"))
            self._field(f, "Grosor (dots)", "int", get_k("t"), set_k("t"), minv=1, maxv=4095, ck=ckf("t"))
            self._field(f, "Color", "choice", get_k("color"), set_k("color"), [("B", "Negro"), ("W", "Blanco")], ck=ckf("c"))
            self._field(f, "Invertir (^FR)", "bool", get_k("reverse"), set_k("reverse"), ck=ckf("rev"))
        elif t == "image":
            self._section(f, "Imagen")
            bf = ttk.Frame(f)
            bf.grid(row=self._row, column=0, columnspan=2, sticky="w", pady=2)
            self._row += 1
            ttk.Button(bf, text="Cambiar imagen…", command=self.replace_image).pack(side="left")
            ttk.Button(bf, text="1 px = 1 dot", command=self.image_original_size).pack(side="left", padx=4)
            ttk.Button(bf, text="Ajustar a etiqueta", command=self.image_fit_label).pack(side="left")

            def set_w(v):
                if el.get("keep_ratio", True) and el["w"]:
                    el["h"] = max(1, round(el["h"] * v / el["w"]))
                    self._set_var("Alto (dots)", el["h"])
                el["w"] = v

            def set_h(v):
                if el.get("keep_ratio", True) and el["h"]:
                    el["w"] = max(1, round(el["w"] * v / el["h"]))
                    self._set_var("Ancho (dots)", el["w"])
                el["h"] = v

            self._field(f, "Ancho (dots)", "int", get_k("w"), set_w, minv=1, maxv=32000, ck=ckf("w"))
            self._field(f, "Alto (dots)", "int", get_k("h"), set_h, minv=1, maxv=32000, ck=ckf("h"))
            self._field(f, "Mantener proporción", "bool", get_k("keep_ratio"), set_k("keep_ratio"), ck=ckf("kr"))
            self._section(f, "Conversión a monocromo")
            pf = ttk.Frame(f)
            pf.grid(row=self._row, column=0, columnspan=2, sticky="w", pady=(0, 4))
            self._row += 1
            ttk.Label(pf, text="Preajustes: ").pack(side="left")
            for name in IMG_PRESETS:
                ttk.Button(pf, text=name, command=lambda n=name: self.image_preset(n)).pack(side="left", padx=1)
            if "algo" not in el:
                el["algo"] = "fs" if el.pop("dither", False) else "threshold"
            self._field(f, "Escala de grises", "choice", get_k("gray"), set_k("gray"), GRAY_MODES, ck=ckf("gray"))
            self._field(f, "Método", "choice", get_k("algo"), set_k("algo"), IMG_ALGOS, ck=ckf("algo"), rebuild=True)
            if el.get("algo") == "threshold":
                self._field(f, "Umbral (0-255)", "int", get_k("threshold"), set_k("threshold"), minv=0, maxv=255,
                            ck=ckf("thr"), inc=8)
            self._field(f, "Brillo (-100..100)", "int", get_k("brightness"), set_k("brightness"),
                        minv=-100, maxv=100, ck=ckf("bri"), inc=5)
            self._field(f, "Contraste (-100..100)", "int", get_k("contrast"), set_k("contrast"),
                        minv=-100, maxv=100, ck=ckf("con"), inc=5)
            self._field(f, "Enfocar (fotos)", "bool", get_k("sharpen"), set_k("sharpen"), ck=ckf("shp"))
            self._field(f, "Conservar líneas finas al reducir", "bool", get_k("thin"), set_k("thin"), ck=ckf("thin"))
            self._field(f, "Invertir colores", "bool", get_k("invert"), set_k("invert"), ck=ckf("inv"))
            try:
                ow, oh = Image.open(io.BytesIO(el["png"])).size
                ttk.Label(f, text=f"Original: {ow}×{oh} px  →  {el['w']}×{el['h']} dots "
                                  f"({el['w'] / ow * 100:.0f} %)", foreground="#555").grid(
                    row=self._row, column=0, columnspan=2, sticky="w", pady=(4, 0))
                self._row += 1
            except Exception:
                pass
            w, h = int(el["w"]), int(el["h"])
            ttk.Label(f, text=f"Datos ^GFA: {(w + 7) // 8 * h:,} bytes sin comprimir", foreground="#555").grid(
                row=self._row, column=0, columnspan=2, sticky="w", pady=4)
            self._row += 1

        self._section(f, "Organizar")
        af = ttk.Frame(f)
        af.grid(row=self._row, column=0, columnspan=2, sticky="w")
        self._row += 1
        ttk.Button(af, text="Centrar H", command=lambda: self.center("h")).pack(side="left")
        ttk.Button(af, text="Centrar V", command=lambda: self.center("v")).pack(side="left", padx=2)
        ttk.Button(af, text="Al frente", command=lambda: self.reorder("top")).pack(side="left")
        ttk.Button(af, text="Al fondo", command=lambda: self.reorder("bottom")).pack(side="left", padx=2)
        af2 = ttk.Frame(f)
        af2.grid(row=self._row, column=0, columnspan=2, sticky="w", pady=4)
        self._row += 1
        ttk.Button(af2, text="Duplicar", command=self.duplicate).pack(side="left")
        ttk.Button(af2, text="Eliminar", command=self.delete_sel).pack(side="left", padx=2)
        ttk.Button(af2, text="Deseleccionar", command=self.deselect).pack(side="left")

    def _set_var(self, label, value):
        v = self.prop_vars.get(label)
        if v is None:
            return
        self._loading = True
        try:
            v.set(value)
        finally:
            self._loading = False

    def _fill_box(self, el):
        self.checkpoint()
        el["t"] = min(int(el["w"]), int(el["h"]))
        self.refresh_el(el)
        self.build_props()

    def deselect(self):
        self.sel = None
        self._draw_sel()
        self.build_props()
        self.refresh_tree()

    # ----------------------------------------------------------------- tree
    def refresh_tree(self):
        tr = self.tree
        tr.delete(*tr.get_children())
        for el in reversed(self.elements):
            txt = summary(el) + ("  (oculto)" if el.get("hidden") else "")
            tr.insert("", "end", iid=str(el["_id"]), text=txt)
        if self.sel is not None and tr.exists(str(self.sel["_id"])):
            self._tree_prog = str(self.sel["_id"])
            tr.selection_set(self._tree_prog)
            tr.see(self._tree_prog)

    def _tree_select(self, e):
        if self._loading:
            return
        s = self.tree.selection()
        if not s or s[0] == getattr(self, "_tree_prog", None):
            self._tree_prog = None
            return
        self._tree_prog = None
        eid = int(s[0])
        el = next((x for x in self.elements if x["_id"] == eid), None)
        if el is not None and el is not self.sel:
            self.sel = el
            self._draw_sel()
            self.build_props()

    # ------------------------------------------------------------------ ZPL
    def current_zpl(self) -> str:
        return zpl.generate(self.label, self.elements)

    def schedule_zpl(self):
        if self._zpl_job:
            self.after_cancel(self._zpl_job)
        self._zpl_job = self.after(150, self._update_zpl)

    def _update_zpl(self):
        self._zpl_job = None
        code = self.current_zpl()
        y = self.zpl_text.yview()[0]
        self.zpl_text.delete("1.0", "end")
        self.zpl_text.insert("1.0", code)
        self.zpl_text.yview_moveto(y)
        self.zpl_info.config(text=f"{len(code.encode('utf-8')):,} bytes")

    def copy_zpl(self):
        self.clipboard_clear()
        self.clipboard_append(self.current_zpl())
        self.status.config(text="  ZPL copiado al portapapeles.")

    def export_zpl(self):
        base = os.path.splitext(os.path.basename(self.path))[0] if self.path else "etiqueta"
        p = filedialog.asksaveasfilename(title="Exportar ZPL", defaultextension=".zpl", initialfile=base + ".zpl",
                                         filetypes=[("ZPL", "*.zpl"), ("Texto", "*.txt"), ("Todos", "*.*")])
        if p:
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self.current_zpl())
            self.status.config(text=f"  ZPL guardado en {p}")

    # ----------------------------------------------------------- printing
    def print_dialog(self):
        PrintDialog(self)

    def labelary(self):
        if not self._labelary_ok:
            if not messagebox.askyesno(
                    "Vista previa Labelary",
                    "La vista previa real se genera enviando el código ZPL al servicio online "
                    "api.labelary.com.\n\n¿Continuar? (no lo uses con datos confidenciales)"):
                return
            self._labelary_ok = True
        code = self.current_zpl()
        L = self.label
        fct = 1 / 25.4 if L["units"] == "mm" else 1
        w_in, h_in = L["w"] * fct, L["h"] * fct
        self.status.config(text="  Solicitando vista previa a Labelary…")

        def work():
            try:
                png = printing.labelary_png(code, int(L["dpi"]), w_in, h_in)
                self.after(0, lambda: self._show_preview(png))
            except Exception as ex:
                detail = ""
                if hasattr(ex, "read"):
                    try:
                        detail = ex.read().decode("utf-8", "ignore")[:300]
                    except Exception:
                        pass
                self.after(0, lambda: messagebox.showerror("Labelary", f"Error: {ex}\n{detail}"))

        threading.Thread(target=work, daemon=True).start()

    def _show_preview(self, png):
        self.status.config(text="  Vista previa recibida.")
        img = Image.open(io.BytesIO(png))
        top = tk.Toplevel(self)
        top.title("Vista previa (Labelary)")
        maxw, maxh = self.winfo_screenwidth() * 0.8, self.winfo_screenheight() * 0.8
        f = min(1.0, maxw / img.width, maxh / img.height)
        if f < 1:
            img = img.resize((int(img.width * f), int(img.height * f)), Image.LANCZOS)
        ph = ImageTk.PhotoImage(img)
        lb = tk.Label(top, image=ph, background="#888", padx=16, pady=16)
        lb.image = ph
        lb.pack()

    # ---------------------------------------------------------------- files
    def _update_title(self):
        name = os.path.basename(self.path) if self.path else "Sin título"
        self.title(f"{'*' if self.dirty else ''}{name} - Diseñador de etiquetas Zebra ZPL")

    def _confirm_discard(self):
        if not self.dirty:
            return True
        r = messagebox.askyesnocancel("Cambios sin guardar", "¿Guardar los cambios del diseño actual?")
        if r is None:
            return False
        if r:
            return self.file_save()
        return True

    def file_new(self):
        if not self._confirm_discard():
            return
        self.label = zpl.default_label()
        self.elements = []
        self.sel = None
        self.path = None
        self.dirty = False
        self._undo.clear()
        self._redo.clear()
        self._cache.clear()
        self._update_title()
        self.zoom_fit()
        self.build_props()
        self.schedule_zpl()

    def file_open(self):
        if not self._confirm_discard():
            return
        p = filedialog.askopenfilename(title="Abrir diseño", filetypes=[("Diseño de etiqueta", "*.zlabel *.json"),
                                                                         ("Todos", "*.*")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
            label = zpl.default_label()
            label.update(doc.get("label", {}))
            els = []
            for e in doc.get("elements", []):
                if e.get("type") == "image" and isinstance(e.get("png"), str):
                    e["png"] = base64.b64decode(e["png"])
                if e.get("type") == "barcode" and e.get("btype") not in barcodes.SPECS:
                    continue
                base = new_element(e["type"], **({"btype": e["btype"]} if e["type"] == "barcode" else {}))
                base.update(e)
                els.append(base)
        except Exception as ex:
            messagebox.showerror("Abrir", f"No se pudo abrir el archivo:\n{ex}")
            return
        self.label, self.elements, self.sel = label, els, None
        self.path = p
        self.dirty = False
        self._undo.clear()
        self._redo.clear()
        self._cache.clear()
        self._update_title()
        self.zoom_fit()
        self.build_props()
        self.schedule_zpl()

    def file_save(self, save_as=False):
        p = self.path
        if save_as or not p:
            p = filedialog.asksaveasfilename(title="Guardar diseño", defaultextension=".zlabel",
                                             filetypes=[("Diseño de etiqueta", "*.zlabel"), ("JSON", "*.json")])
            if not p:
                return False
        els = []
        for e in self.elements:
            d = {k: v for k, v in e.items() if not k.startswith("_")}
            if isinstance(d.get("png"), (bytes, bytearray)):
                d["png"] = base64.b64encode(d["png"]).decode("ascii")
            els.append(d)
        try:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "label": self.label, "elements": els}, fh, ensure_ascii=False, indent=1)
        except OSError as ex:
            messagebox.showerror("Guardar", str(ex))
            return False
        self.path = p
        self.dirty = False
        self._update_title()
        self.status.config(text=f"  Diseño guardado en {p}")
        return True

    def on_close(self):
        if self._confirm_discard():
            self.destroy()


class PrintDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("Enviar a impresora")
        self.resizable(False, False)
        self.transient(app)
        cfg = app.cfg
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        self.mode = tk.StringVar(value=cfg.get("mode", "net"))
        self.host = tk.StringVar(value=cfg.get("host", "192.168.1.100"))
        self.port = tk.StringVar(value=str(cfg.get("port", 9100)))
        printers = printing.windows_printers()
        self.printer = tk.StringVar(value=cfg.get("printer") if cfg.get("printer") in printers
                                    else next((p for p in printers if "zebra" in p.lower() or "zdesigner" in p.lower()),
                                              printers[0] if printers else ""))
        self.copies = tk.StringVar(value=str(app.label.get("copies", 1)))

        ttk.Radiobutton(f, text="Impresora de red (TCP/IP, puerto RAW)", variable=self.mode, value="net").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="IP / host:").grid(row=1, column=0, sticky="w", padx=(20, 4))
        ttk.Entry(f, textvariable=self.host, width=22).grid(row=1, column=1, sticky="w")
        ttk.Label(f, text="Puerto:").grid(row=1, column=2, sticky="w", padx=4)
        ttk.Entry(f, textvariable=self.port, width=6).grid(row=1, column=3, sticky="w")
        ttk.Radiobutton(f, text="Impresora instalada en Windows (USB / compartida, modo RAW)",
                        variable=self.mode, value="win",
                        state="normal" if printers else "disabled").grid(row=2, column=0, columnspan=4, sticky="w",
                                                                         pady=(10, 0))
        ttk.Label(f, text="Impresora:").grid(row=3, column=0, sticky="w", padx=(20, 4))
        ttk.Combobox(f, textvariable=self.printer, values=printers, state="readonly", width=40).grid(
            row=3, column=1, columnspan=3, sticky="w")
        if not printers:
            ttk.Label(f, text="(instala pywin32 para listar impresoras de Windows)", foreground="#777").grid(
                row=4, column=1, columnspan=3, sticky="w")
        ttk.Label(f, text="Copias:").grid(row=5, column=0, sticky="w", pady=(12, 0))
        ttk.Spinbox(f, textvariable=self.copies, from_=1, to=99999, width=8).grid(row=5, column=1, sticky="w",
                                                                                  pady=(12, 0))
        bf = ttk.Frame(f)
        bf.grid(row=6, column=0, columnspan=4, sticky="e", pady=(16, 0))
        ttk.Button(bf, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(bf, text="Imprimir", command=self.send).pack(side="right", padx=6)
        self.bind("<Return>", lambda e: self.send())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def send(self):
        try:
            copies = max(1, int(self.copies.get()))
        except ValueError:
            copies = 1
        label = dict(self.app.label, copies=copies)
        data = zpl.generate(label, self.app.elements).encode("utf-8")
        mode = self.mode.get()
        cfg = self.app.cfg
        cfg.update(mode=mode, host=self.host.get().strip(), port=self.port.get().strip(), printer=self.printer.get())
        printing.save_config(cfg)
        try:
            if mode == "net":
                printing.send_network(self.host.get().strip(), int(self.port.get()), data)
                dest = f"{self.host.get()}:{self.port.get()}"
            else:
                printing.send_windows(self.printer.get(), data)
                dest = self.printer.get()
        except Exception as ex:
            messagebox.showerror("Imprimir", f"No se pudo enviar a la impresora:\n{ex}", parent=self)
            return
        self.app.status.config(text=f"  Enviado {len(data):,} bytes a {dest} ({copies} copia/s).")
        self.destroy()


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()
