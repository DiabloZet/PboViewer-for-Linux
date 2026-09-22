"""Графический интерфейс PboViewer (GTK 4 + libadwaita, нативно под Wayland)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from . import pbo as P  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


# ---------------------------------------------------------------------------
# Настройки и кэш
# ---------------------------------------------------------------------------

def config_path() -> str:
    return os.path.join(GLib.get_user_config_dir(), "pboviewer", "settings.json")


def cache_root() -> str:
    return os.path.join(GLib.get_user_cache_dir(), "pboviewer")


class Settings:
    DEFAULTS = {"backup": False, "confirm_replace": True}

    def __init__(self):
        self.data = dict(self.DEFAULTS)
        try:
            with open(config_path(), encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def get(self, key):
        return self.data.get(key, self.DEFAULTS.get(key))

    def set(self, key, value):
        self.data[key] = value
        try:
            os.makedirs(os.path.dirname(config_path()), exist_ok=True)
            with open(config_path(), "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except OSError:
            pass


def cleanup_cache(max_age_hours: float = 12) -> None:
    """Удаляет старые временные файлы, оставшиеся после перетаскивания/копирования."""
    now = time.time()
    for kind in ("drag", "clip"):
        base = os.path.join(cache_root(), kind)
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for n in names:
            p = os.path.join(base, n)
            try:
                if now - os.path.getmtime(p) > max_age_hours * 3600:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    if os.path.isdir(path):
        base, ext = path, ""
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def is_pbo(path: str) -> bool:
    return path.lower().endswith(".pbo") and os.path.isfile(path)


# ---------------------------------------------------------------------------
# Элемент списка
# ---------------------------------------------------------------------------

class Item(GObject.Object):
    __gtype_name__ = "PboViewerItem"

    def __init__(self, name: str, path: str, is_dir: bool, entry: Optional[P.PboEntry] = None,
                 size: int = 0, count: int = 0, virtual: bool = False, label: Optional[str] = None):
        super().__init__()
        self.name = name
        self.path = path            # полный путь внутри PBO
        self.is_dir = is_dir
        self.entry = entry
        self.size = entry.size if entry else size
        self.count = count
        self.virtual = virtual
        self.label = label or name
        self.mtime = entry.timestamp if entry else 0
        self.compressed = bool(entry and entry.compressed)

    def gicon(self) -> Gio.Icon:
        if self.is_dir:
            return Gio.ThemedIcon.new("folder")
        ctype, _ = Gio.content_type_guess(self.name, None)
        return Gio.content_type_get_icon(ctype)


class Cell(Gtk.Box):
    __gtype_name__ = "PboViewerCell"

    def __init__(self):
        super().__init__(spacing=8)
        self.list_item = None

    def item(self) -> Optional[Item]:
        return self.list_item.get_item() if self.list_item else None


def _find_cell(widget) -> Optional[Cell]:
    while widget is not None:
        if isinstance(widget, Cell):
            return widget
        widget = widget.get_parent()
    return None


# ---------------------------------------------------------------------------
# Главное окно
# ---------------------------------------------------------------------------

MENU_XML = """
<interface>
  <menu id="primary">
    <section>
      <item><attribute name="label">Открыть PBO…</attribute><attribute name="action">win.open</attribute></item>
      <item><attribute name="label">Новый пустой PBO…</attribute><attribute name="action">win.new-pbo</attribute></item>
      <item><attribute name="label">Создать PBO из папки…</attribute><attribute name="action">win.pack-folder</attribute></item>
    </section>
    <section>
      <item><attribute name="label">Распаковать всё…</attribute><attribute name="action">win.extract-all</attribute></item>
      <item><attribute name="label">Свойства PBO (префикс)…</attribute><attribute name="action">win.properties</attribute></item>
      <item><attribute name="label">Проверить контрольную сумму</attribute><attribute name="action">win.verify</attribute></item>
    </section>
    <section>
      <item><attribute name="label">Создавать резервную копию (.bak)</attribute><attribute name="action">app.backup</attribute></item>
      <item><attribute name="label">Спрашивать перед заменой файлов</attribute><attribute name="action">app.confirm-replace</attribute></item>
    </section>
    <section>
      <item><attribute name="label">Горячие клавиши</attribute><attribute name="action">win.shortcuts</attribute></item>
      <item><attribute name="label">О программе</attribute><attribute name="action">app.about</attribute></item>
    </section>
  </menu>
  <menu id="add">
    <section>
      <item><attribute name="label">Добавить файлы…</attribute><attribute name="action">win.add-files</attribute></item>
      <item><attribute name="label">Добавить папку…</attribute><attribute name="action">win.add-folder</attribute></item>
      <item><attribute name="label">Новая папка…</attribute><attribute name="action">win.new-folder</attribute></item>
    </section>
  </menu>
  <menu id="context">
    <section>
      <item><attribute name="label">Открыть</attribute><attribute name="action">win.open-selected</attribute></item>
      <item><attribute name="label">Извлечь в…</attribute><attribute name="action">win.extract</attribute></item>
    </section>
    <section>
      <item><attribute name="label">Копировать</attribute><attribute name="action">win.copy</attribute></item>
      <item><attribute name="label">Вставить</attribute><attribute name="action">win.paste</attribute></item>
      <item><attribute name="label">Копировать путь в PBO</attribute><attribute name="action">win.copy-path</attribute></item>
    </section>
    <section>
      <item><attribute name="label">Заменить файлом…</attribute><attribute name="action">win.replace</attribute></item>
      <item><attribute name="label">Переименовать…</attribute><attribute name="action">win.rename</attribute></item>
      <item><attribute name="label">Удалить</attribute><attribute name="action">win.delete</attribute></item>
    </section>
    <section>
      <item><attribute name="label">Добавить файлы…</attribute><attribute name="action">win.add-files</attribute></item>
      <item><attribute name="label">Добавить папку…</attribute><attribute name="action">win.add-folder</attribute></item>
      <item><attribute name="label">Новая папка…</attribute><attribute name="action">win.new-folder</attribute></item>
    </section>
  </menu>
</interface>
"""

SELECTION_ACTIONS = ("open-selected", "extract", "copy", "copy-path", "rename", "delete", "replace")
ARCHIVE_ACTIONS = ("add-files", "add-folder", "new-folder", "paste", "extract-all",
                   "properties", "verify", "go-up")


class PboWindow(Adw.ApplicationWindow):
    def __init__(self, app: "PboApp"):
        super().__init__(application=app, default_width=980, default_height=640)
        self.app = app
        self.archive: Optional[P.PboArchive] = None
        self.cwd = ""
        self.virtual_dirs: set[str] = set()
        self.busy = False
        self.search_text = ""
        self._drag_items: Optional[list[Item]] = None
        self._watches: dict[str, dict] = {}
        self.set_title("PboViewer")
        self._build_ui()
        self._build_actions()
        self._update_state()
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        builder = Gtk.Builder.new_from_string(MENU_XML, -1)
        self.context_menu_model = builder.get_object("context")

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        tv = Adw.ToolbarView()
        self.toasts.set_child(tv)

        # --- заголовок
        self.header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title="PboViewer", subtitle="")
        self.header.set_title_widget(self.title_widget)

        btn_open = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Открыть PBO (Ctrl+O)")
        btn_open.set_action_name("win.open")
        self.header.pack_start(btn_open)
        self.btn_up = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Вверх (Backspace)")
        self.btn_up.set_action_name("win.go-up")
        self.header.pack_start(self.btn_up)

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Меню",
                                  menu_model=builder.get_object("primary"), primary=True)
        self.header.pack_end(menu_btn)
        self.btn_search = Gtk.ToggleButton(icon_name="system-search-symbolic",
                                           tooltip_text="Поиск по архиву (Ctrl+F)")
        self.header.pack_end(self.btn_search)
        btn_extract = Gtk.Button(icon_name="folder-download-symbolic",
                                 tooltip_text="Извлечь выбранное (или всё) в папку…")
        btn_extract.set_action_name("win.extract-or-all")
        self.header.pack_end(btn_extract)
        add_btn = Gtk.MenuButton(icon_name="list-add-symbolic", tooltip_text="Добавить в архив",
                                 menu_model=builder.get_object("add"))
        self.add_btn = add_btn
        self.header.pack_end(add_btn)
        tv.add_top_bar(self.header)

        # --- поиск
        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text="Поиск файлов во всём архиве",
                                            hexpand=True)
        clamp = Adw.Clamp(maximum_size=600, child=self.search_entry)
        self.search_bar.set_child(clamp)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.btn_search.bind_property("active", self.search_bar, "search-mode-enabled",
                                      GObject.BindingFlags.BIDIRECTIONAL)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_bar.connect("notify::search-mode-enabled", self._on_search_mode)
        tv.add_top_bar(self.search_bar)

        # --- содержимое
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        tv.set_content(self.stack)

        empty = Adw.StatusPage(
            icon_name=APP_ID,
            title="Перетащите сюда .pbo",
            description="или откройте архив кнопкой ниже. Внутри архива файлы можно "
                        "перетаскивать из файлового менеджера и обратно.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        b1 = Gtk.Button(label="Открыть PBO…", css_classes=["pill", "suggested-action"])
        b1.set_action_name("win.open")
        b2 = Gtk.Button(label="Создать PBO из папки…", css_classes=["pill"])
        b2.set_action_name("win.pack-folder")
        b3 = Gtk.Button(label="Новый пустой PBO…", css_classes=["pill"])
        b3.set_action_name("win.new-pbo")
        for b in (b1, b2, b3):
            box.append(b)
        empty.set_child(box)
        self.stack.add_named(empty, "empty")

        self.store = Gio.ListStore(item_type=Item)
        self.view = Gtk.ColumnView(show_row_separators=False, show_column_separators=False,
                                   reorderable=False, css_classes=["data-table"])
        self._add_columns()
        folders_first = Gtk.CustomSorter.new(self._sort_folders_first, None)
        sorter = Gtk.MultiSorter()
        sorter.append(folders_first)
        sorter.append(self.view.get_sorter())
        self.sort_model = Gtk.SortListModel(model=self.store, sorter=sorter)
        self.selection = Gtk.MultiSelection(model=self.sort_model)
        self.selection.connect("selection-changed", lambda *a: self._update_state())
        self.view.set_model(self.selection)
        self.view.sort_by_column(self.col_name, Gtk.SortType.ASCENDING)
        self.view.connect("activate", self._on_activate)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_right_click)
        self.view.add_controller(click)
        lp = Gtk.GestureLongPress(touch_only=True)
        lp.connect("pressed", lambda g, x, y: self._show_context_menu(x, y))
        self.view.add_controller(lp)

        keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        for trig, action in (("Delete", "win.delete"), ("F2", "win.rename"),
                             ("BackSpace", "win.go-up"), ("<Control>c", "win.copy"),
                             ("<Control>v", "win.paste"), ("Menu", "win.context-menu"),
                             ("<Shift>F10", "win.context-menu")):
            keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trig),
                                           action=Gtk.NamedAction.new(action)))
        self.view.add_controller(keys)

        self.popover = Gtk.PopoverMenu.new_from_model(self.context_menu_model)
        self.popover.set_has_arrow(False)
        self.popover.set_halign(Gtk.Align.START)
        self.popover.set_parent(self.view)

        scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True)
        self.stack.add_named(scroller, "browser")

        # только COPY: при MOVE файловый менеджер удалил бы исходные файлы после drop
        drop = Gtk.DropTarget.new(Gdk.FileList.__gtype__, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.stack.add_controller(drop)

        # --- нижняя панель
        bottom = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.status = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                css_classes=["dim-label", "caption"])
        bottom.append(self.status)
        self.progress_label = Gtk.Label(css_classes=["caption"])
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=220)
        pbox = Gtk.Box(spacing=8)
        pbox.append(self.progress_label)
        pbox.append(self.progress)
        self.progress_revealer = Gtk.Revealer(child=pbox, reveal_child=False,
                                              transition_type=Gtk.RevealerTransitionType.CROSSFADE)
        bottom.append(self.progress_revealer)
        tv.add_bottom_bar(bottom)

    def _add_columns(self):
        def factory(kind):
            f = Gtk.SignalListItemFactory()
            f.connect("setup", self._cell_setup, kind)
            f.connect("bind", self._cell_bind, kind)
            return f

        self.col_name = Gtk.ColumnViewColumn(title="Имя", factory=factory("name"), expand=True,
                                             resizable=True)
        self.col_name.set_sorter(Gtk.CustomSorter.new(self._sort_name, None))
        col_size = Gtk.ColumnViewColumn(title="Размер", factory=factory("size"), resizable=True,
                                        fixed_width=110)
        col_size.set_sorter(Gtk.CustomSorter.new(self._sort_size, None))
        col_pack = Gtk.ColumnViewColumn(title="Сжатие", factory=factory("pack"), fixed_width=80)
        col_date = Gtk.ColumnViewColumn(title="Изменён", factory=factory("date"), resizable=True,
                                        fixed_width=150)
        col_date.set_sorter(Gtk.CustomSorter.new(self._sort_date, None))
        for c in (self.col_name, col_size, col_pack, col_date):
            self.view.append_column(c)

    def _cell_setup(self, factory, list_item, kind):
        cell = Cell()
        cell.list_item = list_item
        if kind == "name":
            cell.icon = Gtk.Image(icon_size=Gtk.IconSize.NORMAL)
            cell.append(cell.icon)
        cell.label = Gtk.Label(xalign=1 if kind == "size" else 0, hexpand=True,
                               ellipsize=Pango.EllipsizeMode.MIDDLE)
        if kind != "name":
            cell.label.add_css_class("dim-label")
            cell.label.add_css_class("numeric")
        cell.append(cell.label)

        src = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        src.connect("prepare", self._on_drag_prepare, cell)
        src.connect("drag-begin", self._on_drag_begin, cell)
        src.connect("drag-end", self._on_drag_end)
        cell.add_controller(src)
        list_item.set_child(cell)

    def _cell_bind(self, factory, list_item, kind):
        cell = list_item.get_child()
        item: Item = list_item.get_item()
        if kind == "name":
            cell.icon.set_from_gicon(item.gicon())
            cell.label.set_text(item.label)
            cell.set_tooltip_text(item.path)
        elif kind == "size":
            if item.is_dir:
                cell.label.set_text("пусто" if item.virtual else f"{item.count} эл.")
            else:
                cell.label.set_text(P.human_size(item.size))
        elif kind == "pack":
            cell.label.set_text("LZSS" if item.compressed else "")
        elif kind == "date":
            cell.label.set_text(datetime.fromtimestamp(item.mtime).strftime("%d.%m.%Y %H:%M")
                                if item.mtime > 0 else "")

    # --- сортировка
    @staticmethod
    def _cmp(a, b):
        return Gtk.Ordering.SMALLER if a < b else Gtk.Ordering.LARGER if a > b else Gtk.Ordering.EQUAL

    def _sort_folders_first(self, a, b, _data=None):
        return self._cmp(0 if a.is_dir else 1, 0 if b.is_dir else 1)

    def _sort_name(self, a, b, _data=None):
        return self._cmp(a.label.casefold(), b.label.casefold())

    def _sort_size(self, a, b, _data=None):
        return self._cmp((a.count if a.is_dir else a.size), (b.count if b.is_dir else b.size))

    def _sort_date(self, a, b, _data=None):
        return self._cmp(a.mtime, b.mtime)

    # ------------------------------------------------------------ действия

    def _build_actions(self):
        def add(name, cb, param=None):
            a = Gio.SimpleAction.new(name, param)
            a.connect("activate", cb)
            self.add_action(a)

        add("open", lambda *a: self.choose_and_open())
        add("new-pbo", lambda *a: self.new_pbo())
        add("pack-folder", lambda *a: self.pack_folder_dialog())
        add("extract-all", lambda *a: self.extract_all_dialog())
        add("extract", lambda *a: self.extract_selected_dialog())
        add("extract-or-all", lambda *a: self.extract_selected_dialog()
            if self.selected_items() else self.extract_all_dialog())
        add("properties", lambda *a: self.properties_dialog())
        add("verify", lambda *a: self.verify())
        add("go-up", lambda *a: self.go_up())
        add("open-selected", lambda *a: self.open_selected())
        add("copy", lambda *a: self.copy_selected())
        add("paste", lambda *a: self.paste())
        add("copy-path", lambda *a: self.copy_path())
        add("rename", lambda *a: self.rename_dialog())
        add("delete", lambda *a: self.delete_dialog())
        add("replace", lambda *a: self.replace_dialog())
        add("add-files", lambda *a: self.add_files_dialog())
        add("add-folder", lambda *a: self.add_folder_dialog())
        add("new-folder", lambda *a: self.new_folder_dialog())
        add("context-menu", lambda *a: self._show_context_menu(None, None))
        add("shortcuts", lambda *a: self.show_shortcuts())
        add("search", lambda *a: self.btn_search.set_active(not self.btn_search.get_active()))
        add("update-edited", lambda a, v: self.update_edited(v.get_string()),
            GLib.VariantType.new("s"))

    def _enable(self, name, on):
        a = self.lookup_action(name)
        if a:
            a.set_enabled(on)

    def _update_state(self):
        has = self.archive is not None and not self.busy
        sel = self.selected_items() if self.archive else []
        for n in ARCHIVE_ACTIONS:
            self._enable(n, has)
        for n in SELECTION_ACTIONS:
            self._enable(n, has and bool(sel))
        self._enable("go-up", has and bool(self.cwd) and not self.search_text)
        self._enable("rename", has and len(sel) == 1)
        self._enable("replace", has and len(sel) == 1 and not sel[0].is_dir)
        self._enable("extract-or-all", has)
        for n in ("open", "new-pbo", "pack-folder"):
            self._enable(n, not self.busy)
        self.add_btn.set_sensitive(has)
        self.btn_search.set_sensitive(self.archive is not None)

    # ------------------------------------------------------ архив / навигация

    def load(self, path: str) -> bool:
        try:
            archive = P.PboArchive.open(path)
        except Exception as e:  # noqa: BLE001
            self.error("Не удалось открыть PBO", f"{os.path.basename(path)}\n\n{e}")
            return False
        self.archive = archive
        self.cwd = ""
        self.virtual_dirs.clear()
        self.search_entry.set_text("")
        self.btn_search.set_active(False)
        self.app.add_recent(path)
        self.refresh()
        return True

    def refresh(self, keep_scroll: bool = False):
        a = self.archive
        self.store.remove_all()
        if a is None:
            self.stack.set_visible_child_name("empty")
            self.title_widget.set_title("PboViewer")
            self.title_widget.set_subtitle("")
            self.status.set_text("")
            self._update_state()
            return
        # папка могла исчезнуть после удаления/переименования
        while self.cwd and not self._folder_exists(self.cwd):
            self.cwd = self.cwd.rpartition("\\")[0]
        self.stack.set_visible_child_name("browser")
        self.set_title(f"{os.path.basename(a.path)} — PboViewer")
        self.title_widget.set_title(os.path.basename(a.path))

        items = []
        if self.search_text:
            q = self.search_text.casefold()
            for e in a.entries:
                if q in e.name.casefold():
                    items.append(Item(e.basename, e.name, False, e, label=e.name))
            self.title_widget.set_subtitle(f"Поиск: «{self.search_text}» — найдено {len(items)}")
        else:
            dirs, files = a.list_dir(self.cwd)
            seen = set()
            for d in dirs:
                full = P.join_pbo(self.cwd, d)
                seen.add(full.lower())
                items.append(Item(d, full, True, count=len(a.under(full))))
            for v in sorted(self.virtual_dirs, key=str.lower):
                parent, _, name = v.rpartition("\\")
                if parent.lower() == self.cwd.lower() and v.lower() not in seen:
                    items.append(Item(name, v, True, virtual=True))
            for e in files:
                items.append(Item(e.basename, e.name, False, e))
            self.title_widget.set_subtitle("\\" + self.cwd if self.cwd else (a.prefix or "\\"))
        self.store.splice(0, 0, items)

        prefix = a.prefix
        parts = [f"Файлов в архиве: {len(a.entries)} · {P.human_size(a.total_size)}"]
        if prefix:
            parts.append(f"Префикс: {prefix}")
        elif a.get_prop("pboprefix"):
            parts.append("⚠ префикс записан как «pboprefix» — DayZ его не увидит, "
                         "исправьте в «Свойствах PBO»")
        else:
            parts.append("Префикс не задан")
        if a.stored_checksum is None:
            parts.append("без SHA1")
        self.status.set_text("   ·   ".join(parts))
        self._update_state()

    def _folder_exists(self, folder: str) -> bool:
        k = P.path_key(folder)
        if any(v.lower() == k for v in self.virtual_dirs):
            return True
        return bool(self.archive.under(folder))

    def navigate(self, folder: str):
        self.cwd = P.to_pbo_path(folder)
        if self.search_text:
            self.search_entry.set_text("")
            self.btn_search.set_active(False)
        self.refresh()
        if self.store.get_n_items():
            self.view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)

    def go_up(self):
        if self.cwd:
            self.navigate(self.cwd.rpartition("\\")[0])

    def _on_search_changed(self, entry):
        self.search_text = entry.get_text().strip()
        if self.archive:
            self.refresh()

    def _on_search_mode(self, bar, _p):
        if not bar.get_search_mode() and self.search_text:
            self.search_entry.set_text("")

    def _on_activate(self, view, position):
        item = self.selection.get_item(position)
        if item is None or self.busy:
            return
        if item.is_dir:
            self.navigate(item.path)
        else:
            self.open_item(item)

    def selected_items(self) -> list[Item]:
        bs = self.selection.get_selection()
        return [self.selection.get_item(bs.get_nth(i)) for i in range(bs.get_size())]

    # ------------------------------------------------------ контекстное меню

    def _on_right_click(self, gesture, n_press, x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._show_context_menu(x, y)

    def _show_context_menu(self, x, y):
        if self.archive is None or self.busy:
            return
        if x is not None:
            cell = _find_cell(self.view.pick(x, y, Gtk.PickFlags.DEFAULT))
            pos = cell.list_item.get_position() if cell and cell.list_item else Gtk.INVALID_LIST_POSITION
            if pos != Gtk.INVALID_LIST_POSITION and cell.item() is not None:
                if not self.selection.is_selected(pos):
                    self.selection.select_item(pos, True)
            else:
                self.selection.unselect_all()
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            self.popover.set_pointing_to(rect)
        else:
            self.popover.set_pointing_to(None)
        self._update_state()
        self.popover.popup()

    # ------------------------------------------------------ фоновые задачи

    def run_task(self, label: str, func: Callable, on_done: Callable = None,
                 error_title: str = "Ошибка"):
        """func(progress) выполняется в отдельном потоке, on_done(result) — в главном."""
        self.busy = True
        self.header.set_sensitive(False)
        self.stack.set_sensitive(False)
        self.progress.set_fraction(0)
        self.progress_label.set_text(label)
        self.progress_revealer.set_reveal_child(True)
        self._update_state()
        last = [0.0]

        def progress(done, total):
            now = time.monotonic()
            if now - last[0] > 0.05 or done >= total:
                last[0] = now
                GLib.idle_add(self.progress.set_fraction, min(1.0, done / max(total, 1)))

        def finish(result, err, tb):
            self.busy = False
            self.header.set_sensitive(True)
            self.stack.set_sensitive(True)
            self.progress_revealer.set_reveal_child(False)
            if err is not None:
                print(tb, file=sys.stderr)
                self.refresh_safe()
                self.error(error_title, str(err) or err.__class__.__name__)
            elif on_done:
                on_done(result)
            self._update_state()
            return False

        def worker():
            try:
                res = func(progress)
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(finish, None, e, traceback.format_exc())
            else:
                GLib.idle_add(finish, res, None, None)

        threading.Thread(target=worker, daemon=True).start()

    def refresh_safe(self):
        if self.archive:
            try:
                self.archive.reload()
            except Exception as e:  # noqa: BLE001
                self.error("Архив недоступен", str(e))
                self.archive = None
        self.refresh()

    def commit(self, plan: P.EditPlan, message: str):
        backup = self.app.settings.get("backup")
        self.run_task("Запись архива…",
                      lambda progress: plan.commit(backup=backup, progress=progress),
                      lambda _res: (self.refresh(), self.toast(message)),
                      "Не удалось изменить архив")

    # ------------------------------------------------------ сообщения

    def toast(self, text: str, button: str = None, action: str = None, target: str = None,
              timeout: int = 4):
        t = Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout)
        if button:
            t.set_button_label(button)
            t.set_action_name(action)
            if target is not None:
                t.set_action_target_value(GLib.Variant("s", target))
        self.toasts.add_toast(t)

    def error(self, title: str, body: str):
        d = Adw.AlertDialog(heading=title, body=body)
        d.add_response("ok", "Закрыть")
        d.present(self)

    def ask(self, heading: str, body: str, responses: list[tuple[str, str, str]],
            callback: Callable[[str], None], extra: Gtk.Widget = None, default: str = None):
        d = Adw.AlertDialog(heading=heading, body=body)
        for rid, label, appearance in responses:
            d.add_response(rid, label)
            if appearance == "suggested":
                d.set_response_appearance(rid, Adw.ResponseAppearance.SUGGESTED)
            elif appearance == "destructive":
                d.set_response_appearance(rid, Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_close_response(responses[0][0])
        if default:
            d.set_default_response(default)
        if extra is not None:
            d.set_extra_child(extra)
        d.connect("response", lambda _d, r: callback(r))
        d.present(self)
        return d

    def prompt(self, heading: str, body: str, initial: str, ok_label: str,
               callback: Callable[[str], None], select_stem: bool = False):
        entry = Gtk.Entry(text=initial, activates_default=True)
        d = self.ask(heading, body, [("cancel", "Отмена", ""), ("ok", ok_label, "suggested")],
                     lambda r: callback(entry.get_text().strip()) if r == "ok" else None,
                     extra=entry, default="ok")

        def focus():
            entry.grab_focus()
            stem = initial.rfind(".") if select_stem else -1
            entry.select_region(0, stem if stem > 0 else -1)
            return False
        GLib.idle_add(focus)
        return d

    # ------------------------------------------------------ файловые диалоги

    @staticmethod
    def _pbo_filters():
        f = Gtk.FileFilter(name="Архивы PBO")
        f.add_pattern("*.pbo")
        f.add_pattern("*.PBO")
        a = Gtk.FileFilter(name="Все файлы")
        a.add_pattern("*")
        store = Gio.ListStore(item_type=Gtk.FileFilter)
        store.append(f)
        store.append(a)
        return store, f

    def _initial_folder(self, dlg: Gtk.FileDialog):
        base = os.path.dirname(self.archive.path) if self.archive else self.app.last_dir
        if base and os.path.isdir(base):
            dlg.set_initial_folder(Gio.File.new_for_path(base))

    def choose_and_open(self):
        dlg = Gtk.FileDialog(title="Открыть PBO")
        filters, default = self._pbo_filters()
        dlg.set_filters(filters)
        dlg.set_default_filter(default)
        self._initial_folder(dlg)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            self.app.open_paths([p for p in paths if p], self)
        dlg.open_multiple(self, None, done)

    def open_in_this_or_new(self, path: str):
        if self.archive is None:
            self.load(path)
        else:
            self.app.open_paths([path], None)

    def new_pbo(self):
        dlg = Gtk.FileDialog(title="Новый PBO", initial_name="new_mod.pbo")
        self._initial_folder(dlg)

        def done(d, res):
            try:
                path = d.save_finish(res).get_path()
            except GLib.Error:
                return
            if not path.lower().endswith(".pbo"):
                path += ".pbo"
            self.prompt("Новый PBO", "Префикс (например, Artemida\\MyMod). Можно оставить пустым "
                        "и задать позже в свойствах.", os.path.splitext(os.path.basename(path))[0],
                        "Создать", lambda prefix: self._create_empty(path, prefix))
        dlg.save(self, None, done)

    def _create_empty(self, path: str, prefix: str):
        try:
            plan = P.EditPlan(None)
            plan.set_prop("prefix", prefix)
            plan.commit(path)
        except Exception as e:  # noqa: BLE001
            self.error("Не удалось создать PBO", str(e))
            return
        self.open_in_this_or_new(path)

    def pack_folder_dialog(self):
        dlg = Gtk.FileDialog(title="Папка для упаковки в PBO")
        self._initial_folder(dlg)

        def picked(d, res):
            try:
                folder = d.select_folder_finish(res).get_path()
            except GLib.Error:
                return
            sd = Gtk.FileDialog(title="Сохранить PBO как",
                                initial_name=os.path.basename(folder.rstrip("/")) + ".pbo")
            parent = os.path.dirname(folder.rstrip("/"))
            if os.path.isdir(parent):
                sd.set_initial_folder(Gio.File.new_for_path(parent))

            def saved(d2, res2):
                try:
                    dest = d2.save_finish(res2).get_path()
                except GLib.Error:
                    return
                if not dest.lower().endswith(".pbo"):
                    dest += ".pbo"
                backup = self.app.settings.get("backup") or os.path.exists(dest)
                self.run_task("Упаковка…",
                              lambda progress: P.pack_folder(folder, dest, backup=backup,
                                                             progress=progress),
                              lambda _a: (self.toast(f"Создан {os.path.basename(dest)}"),
                                          self.open_in_this_or_new(dest)),
                              "Не удалось упаковать папку")
            sd.save(self, None, saved)
        dlg.select_folder(self, None, picked)

    def _pick_folder(self, title: str, callback: Callable[[str], None]):
        dlg = Gtk.FileDialog(title=title)
        self._initial_folder(dlg)

        def done(d, res):
            try:
                callback(d.select_folder_finish(res).get_path())
            except GLib.Error:
                return
        dlg.select_folder(self, None, done)

    # ------------------------------------------------------ извлечение

    def _collect(self, items: list[Item], dest_dir: str) -> tuple[list, list[str]]:
        """Пары (запись, путь на диске) и список верхнеуровневых путей результата."""
        pairs, tops, used = [], [], set()
        for it in items:
            first = os.path.join(dest_dir, P.safe_relpath(it.name) or "file")
            top, k = first, 2
            while top in used:
                stem, ext = os.path.splitext(first)
                top, k = f"{stem} ({k}){ext}", k + 1
            used.add(top)
            tops.append(top)
            if it.is_dir:
                base = P.to_pbo_path(it.path)
                if it.virtual:
                    os.makedirs(top, exist_ok=True)
                for e in self.archive.under(it.path):
                    rel = P.to_pbo_path(e.name)[len(base) + 1:]
                    pairs.append((e, os.path.join(top, P.safe_relpath(rel))))
            else:
                pairs.append((it.entry, top))
        return pairs, tops

    def extract_to_temp(self, items: list[Item], kind: str) -> list[str]:
        base = os.path.join(cache_root(), kind, uuid.uuid4().hex[:12])
        os.makedirs(base, exist_ok=True)
        pairs, tops = self._collect(items, base)
        self.archive.extract(pairs)
        return tops

    def extract_selected_dialog(self):
        items = self.selected_items()
        if not items:
            return
        self._pick_folder("Извлечь в папку", lambda dest: self._extract_items(items, dest))

    def _extract_items(self, items, dest):
        pairs, tops = self._collect(items, dest)
        self.run_task("Извлечение…", lambda progress: self.archive.extract(pairs, progress),
                      lambda _r: self.toast(f"Извлечено: {len(pairs)} файл(ов) в {dest}"),
                      "Не удалось извлечь файлы")

    def extract_all_dialog(self):
        if not self.archive:
            return

        def go(dest_parent):
            stem = os.path.splitext(os.path.basename(self.archive.path))[0]
            dest = unique_path(os.path.join(dest_parent, stem))
            self.run_task("Распаковка…",
                          lambda progress: self.archive.extract_all(dest, progress),
                          lambda _r: self.toast(f"Распаковано в {dest}"),
                          "Не удалось распаковать архив")
        self._pick_folder("Распаковать архив в папку", go)

    # ------------------------------------------------------ открытие файлов

    def open_selected(self):
        items = self.selected_items()
        if len(items) == 1 and items[0].is_dir:
            self.navigate(items[0].path)
            return
        for it in items:
            if not it.is_dir:
                self.open_item(it)

    def open_item(self, item: Item):
        key = hashlib.sha1(self.archive.path.encode()).hexdigest()[:10]
        dest = os.path.join(cache_root(), "open", key, P.safe_relpath(item.path))
        try:
            self.archive.extract([(item.entry, dest)], set_mtime=False)
        except Exception as e:  # noqa: BLE001
            self.error("Не удалось извлечь файл", str(e))
            return
        self._watch(dest, item.path)
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(dest))

        def done(l, res):
            try:
                l.launch_finish(res)
            except GLib.Error as e:
                if e.domain != "gtk-dialog-error-quark":
                    self.error("Не удалось открыть файл", e.message)
        launcher.launch(self, None, done)

    def _watch(self, path: str, internal: str):
        old = self._watches.pop(path, None)
        if old:
            old["monitor"].cancel()
        st = os.stat(path)
        mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        info = {"monitor": mon, "internal": internal, "sig": (st.st_mtime_ns, st.st_size),
                "pending": 0, "archive": self.archive.path}
        mon.connect("changed", self._on_watched_changed, path)
        self._watches[path] = info

    def _on_watched_changed(self, mon, f, other, event, path):
        info = self._watches.get(path)
        if not info:
            return
        if info["pending"]:
            GLib.source_remove(info["pending"])
        info["pending"] = GLib.timeout_add(600, self._check_watched, path)

    def _check_watched(self, path):
        info = self._watches.get(path)
        if not info:
            return False
        info["pending"] = 0
        try:
            st = os.stat(path)
        except OSError:
            return False
        sig = (st.st_mtime_ns, st.st_size)
        if sig != info["sig"]:
            info["sig"] = sig
            name = info["internal"].rsplit("\\", 1)[-1]
            self.toast(f"«{name}» изменён во внешней программе", "Обновить в архиве",
                       "win.update-edited", path, timeout=0)
        return False

    def update_edited(self, path: str):
        info = self._watches.get(path)
        if not info or not self.archive or self.archive.path != info["archive"]:
            self.toast("Архив, из которого открыт файл, уже закрыт")
            return
        if self.busy:
            self.toast("Дождитесь окончания текущей операции")
            return
        plan = self.archive.edit()
        plan.add_file(info["internal"], path)
        self.commit(plan, f"Обновлено в архиве: {info['internal']}")

    # ------------------------------------------------------ drag & drop

    def _on_drag_prepare(self, src, x, y, cell: Cell):
        if self.busy or self.archive is None:
            return None
        item = cell.item()
        if item is None:
            return None
        pos = cell.list_item.get_position()
        if pos != Gtk.INVALID_LIST_POSITION and not self.selection.is_selected(pos):
            self.selection.select_item(pos, True)
        items = self.selected_items() or [item]
        try:
            paths = self.extract_to_temp(items, "drag")
        except Exception as e:  # noqa: BLE001
            self.toast(f"Ошибка извлечения: {e}")
            return None
        self._drag_items = items
        return self._file_list_provider(paths)

    @staticmethod
    def _file_list_provider(paths: list[str]) -> Gdk.ContentProvider:
        files = [Gio.File.new_for_path(p) for p in paths]
        value = GObject.Value(Gdk.FileList.__gtype__, Gdk.FileList.new_from_list(files))
        uris = "".join(f.get_uri() + "\r\n" for f in files)
        return Gdk.ContentProvider.new_union([
            Gdk.ContentProvider.new_for_value(value),
            Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new(uris.encode())),
        ])

    def _on_drag_begin(self, src, drag, cell: Cell):
        items = self._drag_items or []
        icon = items[0].gicon() if items else Gio.ThemedIcon.new("text-x-generic")
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        paintable = theme.lookup_by_gicon(icon, 32, self.get_scale_factor(),
                                          Gtk.TextDirection.NONE, 0)
        src.set_icon(paintable, 16, 16)

    def _on_drag_end(self, src, drag, delete_data):
        GLib.timeout_add(500, self._clear_drag)

    def _clear_drag(self):
        self._drag_items = None
        return False

    def _folder_at(self, widget, x, y) -> Optional[str]:
        picked = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
        cell = _find_cell(picked)
        if cell:
            it = cell.item()
            if it and it.is_dir:
                return it.path
        return None

    def _on_drop(self, target, value, x, y):
        if self.busy:
            self.toast("Дождитесь окончания текущей операции")
            return False
        drop = target.get_current_drop()
        local = drop is not None and drop.get_drag() is not None
        folder = self._folder_at(self.stack, x, y) if self.archive else None

        if local:
            # перетаскивание внутри окна: перемещение в папку
            if self._drag_items and folder is not None:
                items = [it for it in self._drag_items if P.path_key(it.path) != P.path_key(folder)]
                GLib.idle_add(self.move_items, items, folder)
                return True
            return False

        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if not paths:
            self.toast("Можно перетаскивать только локальные файлы")
            return False
        GLib.idle_add(self.handle_external_paths, paths,
                      folder if folder is not None else (self.cwd if not self.search_text else ""))
        return True

    def handle_external_paths(self, paths: list[str], folder: str):
        pbos = [p for p in paths if is_pbo(p)]
        if self.archive is None:
            if pbos:
                self.app.open_paths(pbos, self)
            else:
                self.toast("Сначала откройте или создайте PBO")
            return False
        if pbos and len(pbos) == len(paths):
            names = ", ".join(os.path.basename(p) for p in pbos[:3])

            def resp(r):
                if r == "open":
                    self.app.open_paths(pbos, None)
                elif r == "add":
                    self.add_paths(paths, folder)
            self.ask("Что сделать с PBO?", names,
                     [("cancel", "Отмена", ""), ("add", "Добавить в архив", ""),
                      ("open", "Открыть", "suggested")], resp, default="open")
            return False
        self.add_paths(paths, folder)
        return False

    def move_items(self, items: list[Item], folder: str):
        if not items or self.archive is None:
            return False
        plan = self.archive.edit()
        try:
            n = 0
            for it in items:
                if it.virtual:
                    self.virtual_dirs.discard(it.path)
                    self.virtual_dirs.add(P.join_pbo(folder, it.name))
                    continue
                n += plan.rename(it.path, P.join_pbo(folder, it.name))
        except P.PboError as e:
            self.error("Не удалось переместить", str(e))
            return False
        if n:
            self.commit(plan, f"Перемещено в \\{folder}: {n} файл(ов)")
        else:
            self.refresh()
        return False

    # ------------------------------------------------------ добавление / замена

    def add_paths(self, paths: list[str], folder: str):
        if self.archive is None:
            return
        plan = self.archive.edit()
        results = []
        try:
            for p in paths:
                p = os.path.abspath(p)
                if os.path.abspath(self.archive.path) == p:
                    continue
                name = os.path.basename(p.rstrip("/"))
                if os.path.isdir(p):
                    results += plan.add_tree(p, P.join_pbo(folder, name))
                elif os.path.isfile(p):
                    results.append((P.join_pbo(folder, name), plan.add_file(P.join_pbo(folder, name), p)))
        except (OSError, P.PboError) as e:
            self.error("Не удалось добавить", str(e))
            return
        if not results:
            self.toast("Нечего добавлять")
            return
        # добавленное в виртуальную папку делает её настоящей
        self.virtual_dirs = {v for v in self.virtual_dirs if not plan.folder_exists(v)}
        replaced = [n for n, r in results if r == "replaced"]
        added = len(results) - len(replaced)
        msg = []
        if added:
            msg.append(f"добавлено {added}")
        if replaced:
            msg.append(f"заменено {len(replaced)}")
        message = "Готово: " + ", ".join(msg)

        if replaced and self.app.settings.get("confirm_replace"):
            shown = "\n".join(replaced[:12]) + (f"\n… и ещё {len(replaced) - 12}" if len(replaced) > 12 else "")
            self.ask(f"Заменить {len(replaced)} файл(ов)?",
                     f"В архиве уже есть:\n{shown}",
                     [("cancel", "Отмена", ""), ("replace", "Заменить", "destructive")],
                     lambda r: self.commit(plan, message) if r == "replace" else None,
                     default="replace")
        else:
            self.commit(plan, message)

    def add_files_dialog(self):
        dlg = Gtk.FileDialog(title="Добавить файлы в архив")
        self._initial_folder(dlg)
        folder = self.cwd if not self.search_text else ""

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            self.add_paths([p for p in paths if p], folder)
        dlg.open_multiple(self, None, done)

    def add_folder_dialog(self):
        folder = self.cwd if not self.search_text else ""
        self._pick_folder("Добавить папку в архив", lambda p: self.add_paths([p], folder))

    def replace_dialog(self):
        items = self.selected_items()
        if len(items) != 1 or items[0].is_dir:
            return
        item = items[0]
        dlg = Gtk.FileDialog(title=f"Заменить {item.name}", initial_name=item.name)
        self._initial_folder(dlg)

        def done(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            plan = self.archive.edit()
            plan.add_file(item.path, path)
            self.commit(plan, f"Заменён: {item.path}")
        dlg.open(self, None, done)

    def new_folder_dialog(self):
        def create(name):
            try:
                name = P.to_pbo_path(name)
            except P.PboError as e:
                self.error("Недопустимое имя", str(e))
                return
            if not name:
                return
            base = self.cwd if not self.search_text else ""
            self.virtual_dirs.add(P.join_pbo(base, name))
            self.refresh()
            self.toast("Папка появится в архиве, когда в неё будут добавлены файлы")
        self.prompt("Новая папка", "PBO хранит только файлы, поэтому пустая папка существует, "
                    "пока в неё ничего не добавлено.", "new_folder", "Создать", create)

    # ------------------------------------------------------ переименование / удаление

    def rename_dialog(self):
        items = self.selected_items()
        if len(items) != 1:
            return
        it = items[0]

        def do(new_name):
            if not new_name or new_name == it.name:
                return
            if "\\" in new_name or "/" in new_name:
                target = P.to_pbo_path(new_name)  # путь от корня архива
            else:
                target = P.join_pbo(it.path.rpartition("\\")[0], new_name)
            if it.virtual:
                self.virtual_dirs.discard(it.path)
                self.virtual_dirs.add(target)
                self.refresh()
                return
            plan = self.archive.edit()
            try:
                plan.rename(it.path, target)
            except P.PboError as e:
                self.error("Не удалось переименовать", str(e))
                return
            self.commit(plan, f"Переименовано: {it.name} → {target.rsplit(chr(92), 1)[-1]}")
        self.prompt("Переименовать", "Можно указать путь с «\\», чтобы переместить от корня архива.",
                    it.name, "Переименовать", do, select_stem=not it.is_dir)

    def delete_dialog(self):
        items = self.selected_items()
        if not items:
            return
        n_files = sum((len(self.archive.under(i.path)) if i.is_dir else 1) for i in items)
        names = "\n".join(i.path for i in items[:10]) + (f"\n… и ещё {len(items) - 10}" if len(items) > 10 else "")

        def do(r):
            if r != "delete":
                return
            plan = self.archive.edit()
            for it in items:
                if it.virtual:
                    self.virtual_dirs.discard(it.path)
                else:
                    plan.delete(it.path)
            if n_files:
                self.commit(plan, f"Удалено файлов: {n_files}")
            else:
                self.refresh()
        self.ask(f"Удалить из архива ({n_files} файл(ов))?", names,
                 [("cancel", "Отмена", ""), ("delete", "Удалить", "destructive")], do)

    # ------------------------------------------------------ буфер обмена

    def copy_selected(self):
        items = self.selected_items()
        if not items:
            return
        try:
            paths = self.extract_to_temp(items, "clip")
        except Exception as e:  # noqa: BLE001
            self.error("Не удалось скопировать", str(e))
            return
        self.get_clipboard().set_content(self._file_list_provider(paths))
        self.toast(f"Скопировано: {len(items)} — вставьте в файловом менеджере")

    def copy_path(self):
        items = self.selected_items()
        if not items:
            return
        prefix = (self.archive.prefix or "").strip("\\")
        lines = [f"{prefix}\\{i.path}" if prefix else i.path for i in items]
        self.get_clipboard().set("\n".join(lines))
        self.toast("Путь скопирован" if len(lines) == 1 else f"Скопировано путей: {len(lines)}")

    def paste(self):
        if self.archive is None:
            return
        folder = self.cwd if not self.search_text else ""
        sel = self.selected_items()
        if len(sel) == 1 and sel[0].is_dir:
            folder = sel[0].path

        def done(cb, res):
            try:
                value = cb.read_value_finish(res)
            except GLib.Error:
                self.toast("В буфере обмена нет файлов")
                return
            paths = [f.get_path() for f in value.get_files() if f.get_path()]
            if paths:
                self.add_paths(paths, folder)
        self.get_clipboard().read_value_async(Gdk.FileList.__gtype__, GLib.PRIORITY_DEFAULT, None, done)

    # ------------------------------------------------------ свойства / проверка

    def properties_dialog(self):
        a = self.archive
        if not a:
            return
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        rows = {}
        keys = ["prefix"] + [k for k, _ in a.props if k.lower() != "prefix"]
        bad_key = a.get_prop("prefix") is None and a.get_prop("pboprefix") is not None
        if bad_key:
            keys.remove("pboprefix")
        for k in keys:
            text = a.get_prop(k) or ("" if not (k == "prefix" and bad_key) else a.get_prop("pboprefix"))
            row = Adw.EntryRow(title=k, text=text)
            box.append(row)
            rows[k] = row
        extra = Adw.EntryRow(title="новое свойство: ключ=значение")
        box.append(extra)
        sha = a.stored_checksum.hex() if a.stored_checksum else "нет"
        if bad_key:
            sha += "\n\n⚠ Префикс был записан под ключом «pboprefix». Нажмите «Сохранить», чтобы исправить."
        body = (f"{os.path.basename(a.path)}\nФайлов: {len(a.entries)}, данные: "
                f"{P.human_size(a.total_size)}\nSHA1: {sha}")

        def do(r):
            if r != "save":
                return
            plan = a.edit()
            changed = False
            if bad_key:  # переносим ошибочный «pboprefix» в «prefix»
                plan.set_prop("pboprefix", None)
                plan.set_prop("prefix", rows["prefix"].get_text().strip() or None)
                changed = True
            for k, row in rows.items():
                v = row.get_text().strip()
                if v != (a.get_prop(k) or ""):
                    plan.set_prop(k, v or None)
                    changed = True
            t = extra.get_text().strip()
            if "=" in t:
                k, v = t.split("=", 1)
                if k.strip():
                    plan.set_prop(k.strip(), v.strip() or None)
                    changed = True
            if changed:
                self.commit(plan, "Свойства сохранены")
        return self.ask("Свойства PBO", body, [("cancel", "Отмена", ""), ("save", "Сохранить", "suggested")],
                 do, extra=box)

    def verify(self):
        a = self.archive
        if not a:
            return

        def done(ok):
            if ok is None:
                self.toast("В архиве нет контрольной суммы SHA1")
            elif ok:
                self.toast("Контрольная сумма SHA1 верна ✔")
            else:
                self.error("Контрольная сумма не совпадает",
                           "Архив мог быть повреждён или изменён сторонней программой.")
        self.run_task("Проверка SHA1…", lambda progress: a.verify(progress), done)

    def show_shortcuts(self):
        text = ("Ctrl+O — открыть PBO\nCtrl+F — поиск по архиву\nEnter / двойной щелчок — открыть\n"
                "Backspace, Alt+↑ — вверх\nF2 — переименовать\nDelete — удалить\n"
                "Ctrl+C / Ctrl+V — копировать из архива / вставить в архив\n"
                "Shift+F10, Menu — контекстное меню\nCtrl+W — закрыть окно\n\n"
                "Перетаскивание файлов в окно — добавить (или заменить) в текущую папку; "
                "на строку папки — в эту папку. Из окна наружу — извлечь.")
        self.error("Горячие клавиши", text)

    # ------------------------------------------------------ закрытие

    def _on_close_request(self, *_a):
        if self.busy:
            self.toast("Идёт запись архива — дождитесь окончания")
            return True
        for info in self._watches.values():
            info["monitor"].cancel()
        self.popover.unparent()
        return False


# ---------------------------------------------------------------------------
# Окно фоновой операции (из контекстного меню файлового менеджера)
# ---------------------------------------------------------------------------

class TaskWindow(Adw.ApplicationWindow):
    def __init__(self, app, mode: str, paths: list[str]):
        super().__init__(application=app, default_width=460, resizable=False,
                         title="PboViewer — " + ("распаковка" if mode == "unpack" else "упаковка"))
        self.mode, self.paths = mode, [os.path.abspath(p) for p in paths]
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar(show_title=False))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=24,
                      margin_end=24, margin_bottom=24)
        self.label = Gtk.Label(wrap=True, xalign=0)
        self.bar = Gtk.ProgressBar()
        self.close_btn = Gtk.Button(label="Закрыть", halign=Gtk.Align.END, visible=False)
        self.close_btn.connect("clicked", lambda *_: self.close())
        box.append(self.label)
        box.append(self.bar)
        box.append(self.close_btn)
        tv.set_content(box)
        self.set_content(tv)
        threading.Thread(target=self._work, daemon=True).start()

    def _set(self, text=None, frac=None):
        if text is not None:
            self.label.set_text(text)
        if frac is not None:
            self.bar.set_fraction(frac)
        return False

    def _work(self):
        results, errors = [], []
        n = len(self.paths)
        for i, p in enumerate(self.paths):
            base = i / n

            def progress(done, total, base=base):
                GLib.idle_add(self._set, None, base + (done / max(total, 1)) / n)
            try:
                if self.mode == "unpack":
                    GLib.idle_add(self._set, f"Распаковка {os.path.basename(p)}…")
                    a = P.PboArchive.open(p)
                    dest = unique_path(os.path.splitext(p)[0])
                    a.extract_all(dest, progress)
                    results.append(dest)
                else:
                    src = p.rstrip("/")
                    GLib.idle_add(self._set, f"Упаковка {os.path.basename(src)}…")
                    dest = src + ".pbo"
                    P.pack_folder(src, dest, backup=os.path.exists(dest), progress=progress)
                    results.append(dest)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{os.path.basename(p)}: {e}")
        GLib.idle_add(self._finish, results, errors)

    def _finish(self, results, errors):
        self.bar.set_fraction(1)
        if errors:
            self.label.set_text("Ошибки:\n" + "\n".join(errors))
            self.close_btn.set_visible(True)
        else:
            self.label.set_text("Готово:\n" + "\n".join(results))
            GLib.timeout_add(1200, lambda: self.close() or False)
        return False


# ---------------------------------------------------------------------------
# Приложение
# ---------------------------------------------------------------------------

class PboApp(Adw.Application):
    def __init__(self, task: Optional[tuple[str, list[str]]] = None):
        flags = Gio.ApplicationFlags.HANDLES_OPEN
        if task:
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
        self.task = task
        self.settings = Settings()
        self.last_dir = self.settings.get("last_dir") or os.path.expanduser("~")

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name("PboViewer")
        icons = os.path.join(DATA_DIR, "icons")
        if os.path.isdir(icons):
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(icons)
        Gtk.Window.set_default_icon_name(APP_ID)

        for name, key in (("backup", "backup"), ("confirm-replace", "confirm_replace")):
            act = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", bool(self.settings.get(key))))

            def toggled(a, _p, key=key):
                v = not a.get_state().get_boolean()
                a.set_state(GLib.Variant("b", v))
                self.settings.set(key, v)
            act.connect("activate", toggled)
            self.add_action(act)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._about)
        self.add_action(about)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *a: [w.close() for w in self.get_windows()])
        self.add_action(quit_)

        self.set_accels_for_action("win.open", ["<Control>o"])
        self.set_accels_for_action("win.search", ["<Control>f"])
        self.set_accels_for_action("win.go-up", ["<Alt>Up"])
        self.set_accels_for_action("window.close", ["<Control>w"])
        self.set_accels_for_action("app.quit", ["<Control>q"])
        threading.Thread(target=cleanup_cache, daemon=True).start()

    def do_activate(self):
        if self.task:
            TaskWindow(self, *self.task).present()
            return
        win = self.get_active_window()
        if win is None:
            win = PboWindow(self)
        win.present()

    def do_open(self, files, n_files, hint):
        paths = [f.get_path() for f in files if f.get_path()]
        self.open_paths(paths, None)

    def open_paths(self, paths: list[str], window: Optional[PboWindow]):
        for p in paths:
            if os.path.isdir(p):
                continue
            # уже открыт — просто показать окно
            existing = next((w for w in self.get_windows()
                             if isinstance(w, PboWindow) and w.archive
                             and _same_file(w.archive.path, p)), None)
            if existing:
                existing.present()
                continue
            target = window
            if target is None or target.archive is not None:
                target = next((w for w in self.get_windows()
                               if isinstance(w, PboWindow) and w.archive is None and not w.busy), None)
            if target is None:
                target = PboWindow(self)
            target.present()
            target.load(p)
            window = None
        if not self.get_windows():
            PboWindow(self).present()

    def add_recent(self, path: str):
        self.last_dir = os.path.dirname(path)
        self.settings.set("last_dir", self.last_dir)
        try:
            Gtk.RecentManager.get_default().add_item(Gio.File.new_for_path(path).get_uri())
        except Exception:  # noqa: BLE001
            pass

    def _about(self, *_a):
        d = Adw.AboutDialog(
            application_name="PboViewer",
            application_icon=APP_ID,
            version=__version__,
            developer_name="DiabloZet",
            developers=["DiabloZet (порт на GTK 4 / Linux)", "Thomas «Steez» Croizet (оригинальный PboViewer)"],
            license_type=Gtk.License.GPL_3_0,
            comments="Просмотр и редактирование архивов PBO (DayZ / Arma) без распаковки: "
                     "перетаскивание файлов в архив и из него, замена, удаление, префикс.",
            website="https://github.com/SteezCram/PboViewer",
        )
        d.present(self.get_active_window())


def run_gui(argv: list[str], task: Optional[tuple[str, list[str]]] = None) -> int:
    app = PboApp(task)
    return app.run(argv)
