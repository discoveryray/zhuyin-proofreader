"""Display-only widgets and PDF geometry. Never writes an evidence artifact."""
from __future__ import annotations

import base64
import math
import tkinter as tk
from tkinter import ttk

import fitz


class WrappedLabel(tk.Label):
    """Wrap only after the geometry manager allocates a stable horizontal slot."""
    def __init__(self, master, *, width_fraction=1.0, **kwargs):
        self._width_fraction = width_fraction
        kwargs.setdefault("anchor", "w")
        kwargs.setdefault("justify", "left")
        kwargs.setdefault("wraplength", 400)
        super().__init__(master, **kwargs)
        self.bind("<Configure>", self._wrap)

    def _wrap(self, event):
        manager = self.winfo_manager()
        if manager == "pack":
            layout = self.pack_info()
            if layout.get("fill") not in {"x", "both"}:
                return
        elif manager == "grid":
            layout = self.grid_info()
            sticky = layout.get("sticky", "")
            if not ("e" in sticky and "w" in sticky):
                return
        else:
            return
        # Derive the wrap boundary from the externally allocated parent, never
        # from this label's requested/actual width. Grid callers declare their
        # proportional column share; widget padding stays outside that share.
        padding = layout.get("padx", 0)
        if not isinstance(padding, (tuple, list)):
            padding = self.tk.splitlist(str(padding))
        inset = sum(self.winfo_pixels(value) for value in padding)
        if len(padding) == 1:
            inset *= 2
        border = self.master.winfo_pixels(self.master.cget("borderwidth"))
        width = max(1, int((self.master.winfo_width() - 2 * border) * self._width_fraction) - inset - 12)
        if int(float(self.cget("wraplength"))) != width:
            self.configure(wraplength=width)


def wrap_checkbutton(widget):
    widget.configure(anchor="w", justify="left", wraplength=400)
    widget.bind("<Configure>", lambda e: widget.configure(wraplength=max(1, e.width - 36)))
    return widget


class ActionRows(tk.Frame):
    """Lay full button labels out on as many rows as the container needs."""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.items = []
        self._scheduled = None
        self._disposed = False
        self.bind("<Configure>", self.refresh)
        self.bind("<Destroy>", self._dispose)

    def _cancel_layout(self):
        if self._scheduled is not None:
            self.after_cancel(self._scheduled)
            self._scheduled = None

    def _dispose(self, event):
        if event.widget is self:
            self._disposed = True
            self._cancel_layout()

    def set_items(self, items):
        for item in self.items:
            item.place_forget()
        self.items = list(items)
        self.refresh()

    def refresh(self, _event=None):
        if not self._disposed and self._scheduled is None:
            self._scheduled = self.after_idle(self._layout)

    def _layout(self):
        self._cancel_layout()
        if self._disposed:
            return
        width = max(1, self.winfo_width())
        y, row_height, used = 3, 0, 0
        for item in self.items:
            if not item.winfo_exists():
                continue
            # tk buttons support wrapping without a character-width truncation.
            if isinstance(item, (tk.Button, tk.Menubutton)):
                item.configure(width=0, height=0, wraplength=max(40, width - 28), padx=8, pady=7)
            needed = item.winfo_reqwidth() + 8
            if used and used + needed > width:
                y += row_height + 6
                used = row_height = 0
            item.place(x=used, y=y)
            row_height = max(row_height, item.winfo_reqheight())
            used += needed
        height = y + row_height + 3
        if self.winfo_reqheight() != height:
            self.configure(height=height)


def scrollable_entry(master, variable, *, readonly=False):
    frame = tk.Frame(master)
    entry = tk.Entry(frame, textvariable=variable, width=1, state="readonly" if readonly else "normal")
    bar = ttk.Scrollbar(frame, orient="horizontal", command=entry.xview)
    entry.configure(xscrollcommand=bar.set)
    entry.pack(fill="x", expand=True)
    bar.pack(fill="x")
    entry.bind("<Control-a>", lambda _e: (entry.selection_range(0, "end"), "break")[-1])
    frame.entry = entry
    return frame


def occurrence_preview(entry, *, padding=(150, 115), scale=2.2, output_dir=None):
    """Return unmarked pixels and an optional target rectangle in pixel coordinates.

    Occurrence coordinates are PyMuPDF's unrotated page coordinates. Rendering
    uses rotated page coordinates; pixmap x/y include integer crop rounding.
    Invalid geometry displays the page with an explicit message, never a guess.
    """
    pdf = entry.get("pdf")
    if output_dir is not None:
        # Preserve the actual dialog's existing unambiguous relocation lookup.
        from actual_review import _resolve_pdf_path
        pdf = _resolve_pdf_path(entry, output_dir)
    with fitz.open(pdf) as doc:
        number = entry.get("physical_page")
        if isinstance(number, bool) or float(number) != int(number) or int(number) < 1:
            raise ValueError("缺少有效實體頁碼")
        page = doc[int(number) - 1]
        target = None
        notice = "外框僅供定位，不屬於原始證據圖片。"
        try:
            values = [entry[key] for key in ("x0", "y0", "x1", "y1")]
            if any(isinstance(v, bool) or v is None or not math.isfinite(float(v)) for v in values):
                raise ValueError("invalid coordinates")
            x0, y0, x1, y1 = map(float, values)
            if x1 <= x0 or y1 <= y0:
                raise ValueError("empty coordinates")
            original = fitz.Rect(x0, y0, x1, y1)
            bounds = page.rect * page.derotation_matrix
            bounded = original & bounds
            if bounded.is_empty:
                raise ValueError("outside page")
            target = bounded * page.rotation_matrix
            px, py = padding
            clip = (fitz.Rect(bounded.x0 - px, bounded.y0 - py, bounded.x1 + px, bounded.y1 + py)
                    & bounds) * page.rotation_matrix
            if bounded != original:
                notice = "目標區域超出頁邊，外框只標示頁內部分；僅供介面定位。"
        except (KeyError, TypeError, ValueError, OverflowError):
            clip = page.rect
            notice = "無法標示目標：位置資料缺失、無效或在頁面之外。請依原頁核對。"
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        rect = None if target is None else tuple(
            coordinate * scale - origin
            for coordinate, origin in zip(target, (pix.x, pix.y, pix.x, pix.y))
        )
        return pix, rect, notice


class OccurrencePreview(tk.Frame):
    def __init__(self, master, *, height=240):
        super().__init__(master)
        self.canvas = tk.Canvas(self, bg="white", height=height, highlightthickness=0, width=1)
        self.canvas.pack(fill="both", expand=True)
        self.notice = WrappedLabel(self, fg="#555555")
        self.notice.pack(fill="x")
        self.pixmap = self.target = self.photo = None
        self._draw_pending = None
        self._disposed = False
        self.canvas.bind("<Configure>", self._schedule_draw)
        self.bind("<Destroy>", self._dispose)
        self.canvas.bind("<Destroy>", self._dispose)

    def _cancel_draw(self):
        if self._draw_pending is not None:
            self.after_cancel(self._draw_pending)
            self._draw_pending = None

    def _dispose(self, event):
        if event.widget is self or event.widget is self.canvas:
            self._disposed = True
            self._cancel_draw()

    def clear(self, message=""):
        self._cancel_draw()
        self.canvas.delete("all")
        self.pixmap = self.target = self.photo = None
        self.notice.configure(text=message)

    def load(self, entry, *, padding=(150, 115), output_dir=None):
        self.clear()
        try:
            self.pixmap, self.target, notice = occurrence_preview(entry, padding=padding, output_dir=output_dir)
            self.notice.configure(text=notice)
            self._draw()
            # A full-page fallback is useful context, but is not a successfully
            # located sample and cannot authorize a visual confirmation.
            return self.target is not None
        except Exception as exc:
            self.clear(f"無法顯示頁面：{exc}")
            return False

    def _schedule_draw(self, _event=None):
        if not self._disposed and self._draw_pending is None:
            self._draw_pending = self.after_idle(self._draw)

    def _draw(self):
        self._cancel_draw()
        if self._disposed:
            return
        self.canvas.delete("all")
        if self.pixmap is None:
            return
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        factor = min(1.0, max(1, width - 16) / self.pixmap.width, max(1, height - 16) / self.pixmap.height)
        scaled = fitz.Pixmap(self.pixmap, max(1, int(self.pixmap.width * factor)), max(1, int(self.pixmap.height * factor)))
        self.photo = tk.PhotoImage(master=self, data=base64.b64encode(scaled.tobytes("png")))
        x, y = (width - scaled.width) / 2, (height - scaled.height) / 2
        self.canvas.create_image(x, y, image=self.photo, anchor="nw", tags="page")
        if self.target is not None:
            sx, sy = scaled.width / self.pixmap.width, scaled.height / self.pixmap.height
            x0, y0, x1, y1 = self.target
            # Stroke lies outside the target glyph and tone marks.
            box = (x + x0 * sx - 3, y + y0 * sy - 3, x + x1 * sx + 3, y + y1 * sy + 3)
            self.canvas.create_rectangle(*box, outline="#ffffff", width=5, tags="target")
            self.canvas.create_rectangle(*box, outline="#bc230d", width=2, tags="target")
