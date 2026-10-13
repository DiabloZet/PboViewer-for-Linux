"""Просмотр и поиск классов: страница «Классы» в окне архива и отдельное окно поиска."""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import classes as K  # noqa: E402

MAX_ROWS = 5000
MODES = (("all", "Имя и описание"), ("name", "Только имя класса"), ("desc", "Только описание"))

_builtin: Optional[list] = None
_builtin_lock = threading.Lock()


def builtin_pool() -> list:
    """Встроенная база DayZ (загружается один раз, ~25 тыс. классов)."""
    global _builtin
    with _builtin_lock:
        if _builtin is None:
            pool = K.builtin_classes()
            K.ClassIndex(pool)
            _builtin = pool
        return _builtin


class ClassRow(GObject.Object):
    __gtype_name__ = "PboViewerClassRow"

    def __init__(self, info: K.ClassInfo):
        super().__init__()
        self.info = info


class ClassBrowser(Gtk.Box):
    """Список классов с фильтром, описанием родителя, цепочкой наследования и кодом класса.

    source_provider(info) -> текст файла класса (или None);
    on_open(info) — открыть файл класса в редакторе на нужной строке.
    """

    def __init__(self, source_provider: Callable[[K.ClassInfo], Optional[str]],
                 on_open: Callable[[K.ClassInfo], None], builtin: bool = False,
                 placeholder: str = "Класс (часть имени) или слова из описания…"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.source_provider = source_provider
        self.on_open = on_open
        self.pool: list[K.ClassInfo] = []
        self.index = K.ClassIndex()
        self._pending = 0
        self._source_cache: dict = {}
        self._loading_builtin = False

        bar = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        self.toolbar = bar
        self.entry = Gtk.SearchEntry(placeholder_text=placeholder, hexpand=True)
        self.entry.connect("search-changed", lambda *_: self._schedule())
        self.entry.connect("activate", lambda *_: self._activate_first())
        bar.append(self.entry)
        self.mode = Gtk.DropDown.new_from_strings([m[1] for m in MODES])
        self.mode.set_tooltip_text("Где искать: в имени класса или в описании (поиск по смыслу)")
        self.mode.connect("notify::selected", lambda *_: self._schedule())
        bar.append(self.mode)
        self.builtin_check = Gtk.CheckButton(label="База DayZ", active=builtin,
                                             tooltip_text="Искать также среди ванильных классов DayZ "
                                                          "и описанных базовых классов")
        self.builtin_check.connect("toggled", lambda *_: self._on_builtin_toggled())
        bar.append(self.builtin_check)
        self.append(bar)

        # --- таблица
        self.store = Gio.ListStore(item_type=ClassRow)
        self.view = Gtk.ColumnView(show_row_separators=False, css_classes=["data-table"])
        self._columns()
        self.sort_model = Gtk.SortListModel(model=self.store, sorter=self.view.get_sorter())
        self.selection = Gtk.SingleSelection(model=self.sort_model, autoselect=False,
                                             can_unselect=True)
        self.selection.connect("notify::selected", lambda *_: self._show_details())
        self.view.set_model(self.selection)
        self.view.connect("activate", self._on_activate)
        table = Gtk.ScrolledWindow(child=self.view, vexpand=True)

        # --- подробности
        self.details = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                    wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=10, right_margin=10,
                                    top_margin=8, bottom_margin=8)
        dbuf = self.details.get_buffer()
        dbuf.create_tag("h", weight=Pango.Weight.BOLD, scale=1.15)
        dbuf.create_tag("dim", foreground="gray")
        dbuf.create_tag("b", weight=Pango.Weight.BOLD)
        dscroll = Gtk.ScrolledWindow(child=self.details, vexpand=True)
        open_btn = Gtk.Button(label="Открыть в редакторе", css_classes=["pill"],
                              halign=Gtk.Align.END, margin_end=8, margin_bottom=6, margin_top=6)
        open_btn.connect("clicked", lambda *_: self._open_selected())
        self.open_btn = open_btn
        dbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        dbox.append(dscroll)
        dbox.append(open_btn)

        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=table, end_child=dbox,
                          resize_start_child=True, resize_end_child=True, position=330,
                          vexpand=True, shrink_start_child=False, shrink_end_child=False)
        self.append(paned)

        self.status = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"], margin_start=10,
                                margin_end=10, margin_top=4, margin_bottom=4,
                                ellipsize=Pango.EllipsizeMode.END)
        self.append(self.status)
        self.empty_text = "Классов нет"
        self._show_details()
        if builtin:
            self._on_builtin_toggled()

    # --- колонки

    def _columns(self):
        def factory(fn, dim=False, expand_ell=Pango.EllipsizeMode.END):
            f = Gtk.SignalListItemFactory()

            def setup(_f, li):
                lbl = Gtk.Label(xalign=0, ellipsize=expand_ell)
                if dim:
                    lbl.add_css_class("dim-label")
                li.set_child(lbl)

            def bind(_f, li):
                info = li.get_item().info
                text = fn(info)
                li.get_child().set_text(text)
                li.get_child().set_tooltip_text(text if len(text) > 30 else None)
            f.connect("setup", setup)
            f.connect("bind", bind)
            return f

        def sorter(key):
            def cmp(a, b, _d=None):
                x, y = key(a.info), key(b.info)
                return Gtk.Ordering.SMALLER if x < y else Gtk.Ordering.LARGER if x > y else Gtk.Ordering.EQUAL
            return Gtk.CustomSorter.new(cmp, None)

        cols = (
            ("Класс", lambda i: i.name, False, 220, lambda i: i.name.casefold()),
            ("Родитель", lambda i: i.parent or "—", False, 180, lambda i: (i.parent or "").casefold()),
            ("Тип", lambda i: i.kind_label + (f" · {i.section}" if i.section else ""), True, 150,
             lambda i: (i.kind, i.section)),
            ("Описание родителя", lambda i: i.description, False, 0, lambda i: i.description.casefold()),
            ("Файл", lambda i: i.location + (f":{i.line}" if i.line else ""), True, 260,
             lambda i: i.location.casefold()),
        )
        for title, fn, dim, width, key in cols:
            col = Gtk.ColumnViewColumn(title=title, factory=factory(fn, dim), resizable=True)
            if width:
                col.set_fixed_width(width)
            else:
                col.set_expand(True)
            col.set_sorter(sorter(key))
            self.view.append_column(col)

    # --- данные

    def set_classes(self, classes: list[K.ClassInfo], empty_text: str = "Классов нет"):
        self.pool = list(classes)
        self.index = K.ClassIndex(self.pool)
        self._source_cache.clear()
        self.empty_text = empty_text
        self.refresh()

    def add_classes(self, classes: list[K.ClassInfo]):
        self.pool += classes
        self.index.add(classes)
        self.refresh()

    def focus_search(self):
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def set_query(self, text: str):
        self.entry.set_text(text)

    def _on_builtin_toggled(self):
        if self.builtin_check.get_active() and _builtin is None and not self._loading_builtin:
            self._loading_builtin = True
            self.status.set_text("Загрузка базы классов DayZ…")

            def work():
                builtin_pool()
                GLib.idle_add(done)

            def done():
                self._loading_builtin = False
                self.refresh()
                return False
            threading.Thread(target=work, daemon=True).start()
            return
        self.refresh()

    def _schedule(self):
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(150, self._do_refresh)

    def _do_refresh(self):
        self._pending = 0
        self.refresh()
        return False

    def refresh(self):
        query = self.entry.get_text().strip()
        mode = MODES[self.mode.get_selected()][0]
        pool = self.pool
        use_builtin = self.builtin_check.get_active() and _builtin is not None
        if use_builtin:
            pool = pool + _builtin
        if query:
            found = K.search(pool, query, mode)
        else:
            found = list(self.pool) if not use_builtin else list(pool)
        shown = found[:MAX_ROWS]
        self.store.remove_all()
        self.store.splice(0, 0, [ClassRow(i) for i in shown])
        own = sum(1 for i in found if i.kind in ("script", "modded", "config"))
        if not self.pool and not query and not use_builtin:
            text = self.empty_text
        else:
            text = f"Найдено: {len(found)}"
            if use_builtin:
                text += f" (из мода: {own}, база DayZ: {len(found) - own})"
            if len(found) > MAX_ROWS:
                text += f" — показаны первые {MAX_ROWS}, уточните запрос"
            text += "   ·   Enter / двойной щелчок — открыть класс в редакторе"
        if self._loading_builtin:
            text = "Загрузка базы классов DayZ…"
        self.status.set_text(text)
        if shown and query:
            self.selection.set_selected(0)
        self._show_details()

    # --- подробности

    def selected_info(self) -> Optional[K.ClassInfo]:
        row = self.selection.get_selected_item()
        return row.info if row else None

    def _show_details(self):
        buf = self.details.get_buffer()
        buf.set_text("")
        info = self.selected_info()
        self.open_btn.set_sensitive(bool(info and info.line))
        if info is None:
            buf.insert_with_tags_by_name(buf.get_end_iter(), "Выберите класс", "dim")
            return
        end = buf.get_end_iter
        buf.insert_with_tags_by_name(end(), info.name, "h")
        buf.insert(end(), f"   {info.kind_label}" + (f" · {info.section}" if info.section else "") + "\n\n")
        buf.insert_with_tags_by_name(end(), "Наследование: ", "b")
        buf.insert(end(), " → ".join(info.chain) + "\n")
        if info.description:
            who = f" (класс {info.desc_from})" if info.desc_from and info.desc_from != info.name else ""
            buf.insert_with_tags_by_name(end(), f"Описание{who}: ", "b")
            buf.insert(end(), info.description + "\n")
        buf.insert_with_tags_by_name(end(), "Файл: ", "b")
        buf.insert(end(), info.location + (f", строка {info.line}" if info.line else "") + "\n")
        if not info.line:
            return
        text = self._source(info)
        if text:
            buf.insert(end(), "\n")
            buf.insert(end(), K.class_source(text, info.line, max_lines=300))

    def _source(self, info: K.ClassInfo) -> Optional[str]:
        key = (info.pbo, info.file)
        if key not in self._source_cache:
            try:
                self._source_cache[key] = self.source_provider(info)
            except Exception:  # noqa: BLE001
                self._source_cache[key] = None
            if len(self._source_cache) > 64:
                self._source_cache.pop(next(iter(self._source_cache)))
        return self._source_cache[key]

    def _on_activate(self, _view, pos):
        row = self.selection.get_item(pos)
        if row and row.info.line:
            self.on_open(row.info)

    def _activate_first(self):
        info = self.selected_info()
        if info and info.line:
            self.on_open(info)

    def _open_selected(self):
        info = self.selected_info()
        if info and info.line:
            self.on_open(info)


# ---------------------------------------------------------------------------
# Окно поиска классов (по всем открытым PBO, папкам с модами и базе DayZ)
# ---------------------------------------------------------------------------

class ClassSearchWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=1100, default_height=760,
                         title="Поиск классов — PboViewer")
        self.app = app
        self.scanned: list[K.ClassInfo] = []
        self.folders: list[str] = []
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        tv = Adw.ToolbarView()
        self.toasts.set_child(tv)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Поиск классов",
                                                subtitle="открытые PBO, папки с модами, база DayZ"))
        btn = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Добавить папку с модами "
                         "(рекурсивно: все .pbo, .c, config.cpp / config.bin)")
        btn.connect("clicked", lambda *_: self.pick_folder())
        header.pack_start(btn)
        btn2 = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Добавить PBO-файлы")
        btn2.connect("clicked", lambda *_: self.pick_files())
        header.pack_start(btn2)
        clear = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text="Убрать добавленные папки")
        clear.connect("clicked", lambda *_: self.clear())
        header.pack_end(clear)
        tv.add_top_bar(header)
        self.progress = Gtk.ProgressBar(visible=False)
        tv.add_top_bar(self.progress)

        self.browser = ClassBrowser(app.class_source, app.open_class, builtin=True)
        tv.set_content(self.browser)
        self.reload()
        GLib.idle_add(self.browser.focus_search)

    def reload(self):
        own = list(self.app.open_archive_classes()) + self.scanned
        n_open = sum(1 for _ in self.app.open_archive_classes())
        hint = ("Добавьте папку с модами кнопкой слева вверху или откройте PBO. "
                "Поиск по базе DayZ работает сразу.")
        self.browser.set_classes(own, hint)
        src = []
        if n_open:
            src.append(f"открытые PBO: {n_open} кл.")
        if self.folders:
            src.append("папки: " + ", ".join(os.path.basename(f.rstrip("/")) or f for f in self.folders))
        if src:
            self.toast(" · ".join(src))

    def toast(self, text: str):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    def clear(self):
        self.scanned = []
        self.folders = []
        self.reload()

    def pick_folder(self):
        dlg = Gtk.FileDialog(title="Папка с модами (поиск рекурсивный)")
        if self.app.last_dir and os.path.isdir(self.app.last_dir):
            dlg.set_initial_folder(Gio.File.new_for_path(self.app.last_dir))

        def done(d, res):
            try:
                path = d.select_folder_finish(res).get_path()
            except GLib.Error:
                return
            self.scan([path])
        dlg.select_folder(self, None, done)

    def pick_files(self):
        dlg = Gtk.FileDialog(title="PBO для поиска классов")
        f = Gtk.FileFilter(name="Архивы PBO")
        f.add_pattern("*.pbo")
        f.add_pattern("*.PBO")
        store = Gio.ListStore(item_type=Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self.scan([files.get_item(i).get_path() for i in range(files.get_n_items())])
        dlg.open_multiple(self, None, done)

    def scan(self, paths: list[str]):
        self.progress.set_visible(True)
        self.progress.set_fraction(0)

        def progress(done, total):
            GLib.idle_add(self.progress.set_fraction, done / max(total, 1))

        def work():
            errors: list = []
            found = []
            for p in paths:
                found += K.scan_path(p, progress, errors)
            GLib.idle_add(finish, found, errors)

        def finish(found, errors):
            self.progress.set_visible(False)
            self.scanned += found
            self.folders += paths
            self.reload()
            msg = f"Найдено классов: {len(found)}"
            if errors:
                msg += f", ошибок чтения: {len(errors)}"
                for e in errors[:20]:
                    print("[classes]", e)
            self.toast(msg)
            return False
        threading.Thread(target=work, daemon=True).start()
