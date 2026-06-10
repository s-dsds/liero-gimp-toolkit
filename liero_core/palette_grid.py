"""Shared GTK palette swatch grid for the toolkit's plug-in dialogs.

GTK-dependent: import this module only from plug-in code (it is not pulled in
by ``liero_core/__init__.py``, so the pure core stays GTK-free).

16x16 grid of swatches. Each swatch shows a material badge and a dot on
animated indices. Click: select. Ctrl+click: toggle. Shift+click: range.
Double-click: fires ``edit_cb`` if provided.
"""
from __future__ import annotations

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk  # noqa: E402

from .defaults import MATERIAL  # noqa: E402
from .material import index_info  # noqa: E402

CELL = 30  # swatch size in px

MATERIAL_BADGE = {
    MATERIAL['UNDEF']: '',
    MATERIAL['DIRT']: 'D',
    MATERIAL['DIRT_2']: 'D2',
    MATERIAL['ROCK']: 'R',
    MATERIAL['BG']: 'B',
    MATERIAL['BG_DIRT']: 'BD',
    MATERIAL['BG_DIRT_2']: 'B2',
    MATERIAL['BG_SEESHADOW']: 'S',
    MATERIAL['WORM']: 'W',
}


class PaletteGrid:
    """Swatch grid state + widget. Access the Gtk widget via ``.widget``."""

    def __init__(self, colors, table, hover_cb=None, select_cb=None, edit_cb=None):
        self.colors = list(colors[:256])
        self.table = list(table[:256])
        self.selected = set()
        self.last_click = 0
        self._hover_cb = hover_cb
        self._select_cb = select_cb
        self._edit_cb = edit_cb

        self.widget = Gtk.DrawingArea()
        self.widget.set_size_request(CELL * 16, CELL * 16)
        self.widget.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                               | Gdk.EventMask.POINTER_MOTION_MASK)
        self.widget.connect('draw', self._on_draw)
        self.widget.connect('button-press-event', self._on_press)
        self.widget.connect('motion-notify-event', self._on_motion)

    def queue_draw(self):
        self.widget.queue_draw()

    def select_material(self, value):
        self.selected = {i for i, m in enumerate(self.table) if m == value}
        self.queue_draw()

    def clear_selection(self):
        self.selected = set()
        self.queue_draw()

    # -- drawing --------------------------------------------------------------

    def _on_draw(self, widget, cr):
        for i, (r, g, b) in enumerate(self.colors):
            x, y = (i % 16) * CELL, (i // 16) * CELL
            cr.set_source_rgb(r / 255.0, g / 255.0, b / 255.0)
            cr.rectangle(x, y, CELL, CELL)
            cr.fill()
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            fg = (0, 0, 0) if lum > 128 else (1, 1, 1)
            badge = MATERIAL_BADGE.get(self.table[i], '?')
            if badge:
                cr.set_source_rgb(*fg)
                cr.set_font_size(9)
                cr.move_to(x + 2, y + CELL - 3)
                cr.show_text(badge)
            if index_info(i, self.table).animated:
                cr.set_source_rgb(*fg)
                cr.arc(x + CELL - 5, y + 5, 2.2, 0, 6.2832)
                cr.fill()
            if i in self.selected:
                cr.set_line_width(2)
                cr.set_source_rgb(1, 1, 1)
                cr.rectangle(x + 1, y + 1, CELL - 2, CELL - 2)
                cr.stroke()
                cr.set_line_width(1)
                cr.set_source_rgb(0, 0, 0)
                cr.rectangle(x + 2.5, y + 2.5, CELL - 5, CELL - 5)
                cr.stroke()
        return False

    # -- events ---------------------------------------------------------------

    @staticmethod
    def _event_index(event):
        col = min(15, max(0, int(event.x) // CELL))
        row = min(15, max(0, int(event.y) // CELL))
        return int(row * 16 + col)

    def _on_press(self, widget, event):
        idx = self._event_index(event)
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            if self._edit_cb:
                self._edit_cb(idx)
        elif event.state & Gdk.ModifierType.CONTROL_MASK:
            self.selected.symmetric_difference_update({idx})
        elif event.state & Gdk.ModifierType.SHIFT_MASK:
            lo, hi = sorted((self.last_click, idx))
            self.selected.update(range(lo, hi + 1))
        else:
            self.selected = {idx}
        self.last_click = idx
        if self._select_cb:
            self._select_cb(idx)
        self.queue_draw()
        return True

    def _on_motion(self, widget, event):
        if self._hover_cb:
            self._hover_cb(self._event_index(event))
        return False
