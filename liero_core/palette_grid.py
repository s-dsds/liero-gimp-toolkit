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

from .defaults import MATERIAL, MATERIAL_NAMES, MATERIAL_GROUPS, ANIMATED_INDICES  # noqa: E402
from .colorops import similar_color_indices  # noqa: E402

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

    def __init__(self, colors, table, hover_cb=None, select_cb=None, edit_cb=None,
                 animated=None):
        self.colors = list(colors[:256])
        self.table = list(table[:256])
        self.animated = set(ANIMATED_INDICES if animated is None else animated)
        self.locked = set()  # drawn struck-through; hosts keep it in sync
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

    def select_materials(self, values):
        self.selected = {i for i, m in enumerate(self.table) if m in values}
        self.queue_draw()

    def animation_run_at(self, idx):
        """The contiguous animated run containing idx, or None."""
        if idx not in self.animated:
            return None
        a = b = idx
        while a - 1 in self.animated:
            a -= 1
        while b + 1 in self.animated:
            b += 1
        return a, b

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
            if i in self.animated:
                cr.set_source_rgb(*fg)
                cr.arc(x + CELL - 5, y + 5, 2.2, 0, 6.2832)
                cr.fill()
            if i in self.locked:
                cr.set_source_rgb(*fg)
                cr.set_line_width(1)
                cr.move_to(x + 3, y + 3)
                cr.line_to(x + CELL - 3, y + CELL - 3)
                cr.stroke()
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
        if event.button == 3:
            self._show_context_menu(idx, event)
            return True
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

    # -- context menu (right click) ---------------------------------------------

    def _apply_menu_selection(self, indices, idx, add=False):
        if add:
            self.selected.update(indices)
        else:
            self.selected = set(indices)
        self.last_click = idx
        if self._select_cb:
            self._select_cb(idx)
        self.queue_draw()

    def _show_context_menu(self, idx, event):
        material = self.table[idx]
        mat_name = MATERIAL_NAMES.get(material, str(material))
        similar = similar_color_indices(self.colors, self.colors[idx])
        items = [
            (f"Select similar colors ({len(similar)})",
             lambda *_: self._apply_menu_selection(similar, idx)),
            ("Add similar colors to selection",
             lambda *_: self._apply_menu_selection(similar, idx, add=True)),
            (f"Select material {mat_name}",
             lambda *_: self._apply_menu_selection(
                 {i for i, m in enumerate(self.table) if m == material}, idx)),
        ]
        for label, values in MATERIAL_GROUPS.values():
            if material in values:
                items.append((f"Select {label}",
                              lambda *_, v=values: self._apply_menu_selection(
                                  {i for i, m in enumerate(self.table) if m in v}, idx)))
        run = self.animation_run_at(idx)
        if run:
            items.append((f"Select this animation ({run[0]}–{run[1]})",
                          lambda *_, r=run: self._apply_menu_selection(
                              set(range(r[0], r[1] + 1)), idx)))
        r, g, b = self.colors[idx]
        hex_text = f"#{r:02x}{g:02x}{b:02x}"
        rgb_text = f"{r},{g},{b}"
        items.append(None)  # separator
        items.append((f"Copy {hex_text}", lambda *_: self._copy_text(hex_text)))
        items.append((f"Copy {rgb_text}", lambda *_: self._copy_text(rgb_text)))
        menu = Gtk.Menu()
        for entry in items:
            if entry is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            label, handler = entry
            item = Gtk.MenuItem(label=label)
            item.connect('activate', handler)
            menu.append(item)
        menu.show_all()
        self._menu = menu  # keep a reference while shown
        menu.popup_at_pointer(event)

    @staticmethod
    def _copy_text(text):
        clipboard = Gtk.Clipboard.get_default(Gdk.Display.get_default())
        clipboard.set_text(text, -1)
