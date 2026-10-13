"""Встроенный текстовый редактор: .cfg, .cpp, .c, .xml… и бинарные config.bin (через derapify/rapify).

Подсветка синтаксиса и номера строк — если установлен GtkSourceView 5 (gtksourceview5),
иначе обычный Gtk.TextView. Горячие клавиши — как в привычных редакторах (см. HOTKEYS).
"""

from __future__ import annotations

import os
import re
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

try:
    gi.require_version("GtkSource", "5")
    from gi.repository import GtkSource  # noqa: E402
    GtkSource.init()
except (ValueError, ImportError):
    GtkSource = None

from . import config as C  # noqa: E402

TEXT_EXT = {
    "c", "cpp", "hpp", "h", "inc", "ext", "cfg", "bin", "rvmat", "txt", "xml", "json", "csv",
    "sqf", "sqm", "layout", "imageset", "styles", "html", "htm", "md", "ini", "log", "bisurf",
    "surface", "fsm", "hqf", "lip", "edds_meta", "emat", "meta", "ptc", "xob_meta", "conf",
    "yml", "yaml", "lua", "py", "sh", "bat", "cmd", "bikb", "bisign_meta", "particle",
}

HOTKEYS = (
    ("Ctrl+S", "сохранить (в PBO — сразу в архив)"),
    ("Ctrl+Shift+S", "сохранить копию на диск…"),
    ("Ctrl+Z / Ctrl+Shift+Z, Ctrl+Y", "отменить / повторить"),
    ("Ctrl+X / Ctrl+C / Ctrl+V / Ctrl+A", "вырезать / копировать / вставить / выделить всё"),
    ("Ctrl+F", "поиск"),
    ("Ctrl+H, Ctrl+R", "поиск и замена"),
    ("F3 / Shift+F3, Enter / Shift+Enter", "следующее / предыдущее совпадение"),
    ("Ctrl+G", "перейти к строке"),
    ("Ctrl+D", "дублировать строку (выделенные строки)"),
    ("Ctrl+Shift+K, Ctrl+E", "удалить строку"),
    ("Ctrl+L", "выделить строку"),
    ("Alt+↑ / Alt+↓", "переместить строку вверх / вниз"),
    ("Ctrl+/", "закомментировать / раскомментировать «//»"),
    ("Tab / Shift+Tab", "отступ / убрать отступ у выделенных строк"),
    ("Enter", "новая строка с сохранением отступа (после «{» — +1 отступ)"),
    ("Ctrl+Плюс / Ctrl+Минус / Ctrl+0", "крупнее / мельче / обычный шрифт"),
    ("F7", "проверить синтаксис конфига"),
    ("Ctrl+W, Esc в поиске", "закрыть окно / закрыть поиск"),
)


def is_text_name(name: str) -> bool:
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    stem = base[:-4] if base.endswith(".txt") else base
    if len(stem) > 2 and stem.startswith("$") and stem.endswith("$"):
        return True
    return "." in base and base.rsplit(".", 1)[-1] in TEXT_EXT


class Document:
    """Загружает байты в текст и обратно, запоминая формат (config.bin / кодировка / BOM)."""

    def __init__(self, data: bytes, name: str):
        self.name = name
        self.rap = C.is_rapified(data)
        self.bom = False
        self.encoding = "utf-8"
        if self.rap:
            self.text = C.derapify(data)
            return
        if b"\0" in data[:8192]:
            raise ValueError("Это двоичный файл, а не текст и не бинарный конфиг (raP)")
        if data.startswith(b"\xef\xbb\xbf"):
            self.bom, data = True, data[3:]
        try:
            self.text = data.decode("utf-8")
        except UnicodeDecodeError:
            self.encoding = "cp1251"
            self.text = data.decode("cp1251", errors="replace")

    @property
    def is_config(self) -> bool:
        n = self.name.lower()
        return self.rap or n.endswith((".cpp", ".hpp", ".rvmat", ".bisurf")) or \
            os.path.basename(n.replace("\\", "/")) == "config.bin"

    @property
    def format_label(self) -> str:
        if self.rap:
            return "бинарный конфиг (raP) — сохраняется обратно в .bin"
        return self.encoding.upper() + (" с BOM" if self.bom else "")

    def encode(self, text: str) -> bytes:
        if self.rap:
            return C.rapify(text)
        try:
            data = text.encode(self.encoding)
        except UnicodeEncodeError:
            self.encoding = "utf-8"
            data = text.encode("utf-8")
        return (b"\xef\xbb\xbf" if self.bom else b"") + data


class DiskTarget:
    """Файл на диске."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.title = os.path.basename(path)
        self.subtitle = os.path.dirname(self.path)

    def load(self) -> bytes:
        with open(self.path, "rb") as f:
            return f.read()

    def save(self, data: bytes, done: Callable[[Optional[str]], None]) -> None:
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            if os.path.exists(self.path):
                try:
                    os.chmod(tmp, os.stat(self.path).st_mode & 0o7777)
                except OSError:
                    pass
            os.replace(tmp, self.path)
        except OSError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            done(str(e))
            return
        done(None)

    def same(self, other) -> bool:
        return isinstance(other, DiskTarget) and other.path == self.path


# ---------------------------------------------------------------------------
# Окно редактора
# ---------------------------------------------------------------------------

_editor_counter = [0]


class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app, target, line: int = 0):
        super().__init__(application=app, default_width=1000, default_height=720)
        self.app = app
        self.target = target
        self.saving = False
        self.font_size = 11
        self._matches: list[tuple[int, int]] = []
        _editor_counter[0] += 1
        self.css_class = f"pbo-editor-{_editor_counter[0]}"
        self.doc = Document(target.load(), target.title)
        self._build_ui()
        self._build_actions()
        self._set_text(self.doc.text)
        self._update_title()
        self.connect("close-request", self._on_close_request)
        if line:
            GLib.idle_add(self.goto_line, line)
        GLib.idle_add(self.view.grab_focus)

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        tv = Adw.ToolbarView()
        self.toasts.set_child(tv)

        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle()
        header.set_title_widget(self.title_widget)
        btn_save = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Сохранить (Ctrl+S)")
        btn_save.set_action_name("win.save")
        header.pack_start(btn_save)
        btn_undo = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Отменить (Ctrl+Z)")
        btn_undo.connect("clicked", lambda *_: self.undo())
        header.pack_start(btn_undo)
        btn_redo = Gtk.Button(icon_name="edit-redo-symbolic", tooltip_text="Повторить (Ctrl+Shift+Z)")
        btn_redo.connect("clicked", lambda *_: self.redo())
        header.pack_start(btn_redo)

        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("Сохранить", "win.save")
        sec.append("Сохранить копию на диск…", "win.save-copy")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Найти", "win.search")
        sec.append("Найти и заменить", "win.replace")
        sec.append("Перейти к строке…", "win.goto")
        sec.append("Проверить синтаксис конфига", "win.check")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Перенос строк", "win.wrap")
        sec.append("Горячие клавиши", "win.hotkeys")
        menu.append_section(None, sec)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Меню", primary=True))
        btn_find = Gtk.Button(icon_name="edit-find-replace-symbolic", tooltip_text="Найти и заменить (Ctrl+H)")
        btn_find.set_action_name("win.replace")
        header.pack_end(btn_find)
        tv.add_top_bar(header)

        # --- панель поиска / замены
        self.find_entry = Gtk.SearchEntry(placeholder_text="Найти", hexpand=True)
        self.find_entry.connect("search-changed", lambda *_: self._update_matches(select=True))
        self.find_entry.connect("activate", lambda *_: self.find_next())
        self.find_entry.connect("stop-search", lambda *_: self.close_search())
        fk = Gtk.EventControllerKey()
        fk.connect("key-pressed", self._on_find_key)
        self.find_entry.add_controller(fk)
        self.case_btn = Gtk.ToggleButton(label="Aa", tooltip_text="Учитывать регистр")
        self.case_btn.connect("toggled", lambda *_: self._update_matches(select=False))
        self.regex_btn = Gtk.ToggleButton(label=".*", tooltip_text="Регулярное выражение")
        self.regex_btn.connect("toggled", lambda *_: self._update_matches(select=False))
        prev_b = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Предыдущее (Shift+F3)")
        prev_b.connect("clicked", lambda *_: self.find_prev())
        next_b = Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Следующее (F3)")
        next_b.connect("clicked", lambda *_: self.find_next())
        self.match_label = Gtk.Label(css_classes=["dim-label", "caption", "numeric"], width_chars=12)
        close_b = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Закрыть (Esc)",
                             css_classes=["flat"])
        close_b.connect("clicked", lambda *_: self.close_search())
        row1 = Gtk.Box(spacing=6)
        for w in (self.find_entry, self.case_btn, self.regex_btn, prev_b, next_b, self.match_label, close_b):
            row1.append(w)

        self.replace_entry = Gtk.Entry(placeholder_text="Заменить на", hexpand=True)
        self.replace_entry.connect("activate", lambda *_: self.replace_one())
        rk = Gtk.EventControllerKey()
        rk.connect("key-pressed", self._on_find_key)
        self.replace_entry.add_controller(rk)
        rep_b = Gtk.Button(label="Заменить")
        rep_b.connect("clicked", lambda *_: self.replace_one())
        rep_all = Gtk.Button(label="Заменить все")
        rep_all.connect("clicked", lambda *_: self.replace_all())
        self.replace_row = Gtk.Box(spacing=6)
        for w in (self.replace_entry, rep_b, rep_all):
            self.replace_row.append(w)

        sbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=8,
                       margin_end=8, margin_top=6, margin_bottom=6)
        sbox.append(row1)
        sbox.append(self.replace_row)
        self.search_revealer = Gtk.Revealer(child=sbox, reveal_child=False)
        tv.add_top_bar(self.search_revealer)

        # --- текст
        if GtkSource is not None:
            self.buf = GtkSource.Buffer()
            self.view = GtkSource.View(buffer=self.buf, show_line_numbers=True,
                                       highlight_current_line=True, auto_indent=False,
                                       smart_home_end=GtkSource.SmartHomeEndType.BEFORE,
                                       tab_width=4)
            self.buf.set_highlight_matching_brackets(True)
            lang = self._language()
            if lang:
                self.buf.set_language(lang)
            self._apply_scheme()
            Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._apply_scheme())
        else:
            self.buf = Gtk.TextBuffer()
            self.view = Gtk.TextView(buffer=self.buf)
            tabs = Pango.TabArray.new(1, False)
            tabs.set_tab(0, Pango.TabAlign.LEFT, 4 * 9 * Pango.SCALE)
            self.view.set_tabs(tabs)
        self.buf.set_enable_undo(True)
        self.view.set_monospace(True)
        self.view.set_left_margin(6)
        self.view.add_css_class(self.css_class)
        self.match_tag = self.buf.create_tag("pbo-match", background="rgba(255,200,0,0.35)")
        self.buf.connect("modified-changed", lambda *_: self._update_title())
        self.buf.connect("notify::cursor-position", lambda *_: self._update_status())
        self.buf.connect("changed", self._on_changed)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True)
        tv.set_content(scroller)

        # --- статус
        bottom = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=4, margin_bottom=4)
        self.status = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label", "caption", "numeric"],
                                ellipsize=Pango.EllipsizeMode.END)
        self.fmt_label = Gtk.Label(css_classes=["dim-label", "caption"], label=self.doc.format_label)
        bottom.append(self.status)
        bottom.append(self.fmt_label)
        tv.add_bottom_bar(bottom)

        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._apply_font()

    def _language(self):
        mgr = GtkSource.LanguageManager.get_default()
        n = self.target.title.lower()
        ext = n.rsplit(".", 1)[-1] if "." in n else ""
        if ext in ("xml", "layout", "imageset", "styles", "html", "htm"):
            lang_id = "xml"
        elif ext == "json":
            lang_id = "json"
        elif ext in ("ini", "conf"):
            lang_id = "ini"
        elif ext in ("md",):
            lang_id = "markdown"
        elif ext in ("txt", "log", "csv"):
            return None
        else:
            lang_id = "cpp"
        return mgr.get_language(lang_id)

    def _apply_scheme(self):
        mgr = GtkSource.StyleSchemeManager.get_default()
        dark = Adw.StyleManager.get_default().get_dark()
        for sid in (("Adwaita-dark", "oblivion", "classic-dark") if dark else ("Adwaita", "classic")):
            scheme = mgr.get_scheme(sid)
            if scheme:
                self.buf.set_style_scheme(scheme)
                break

    def _apply_font(self):
        self.css.load_from_string(f".{self.css_class} {{ font-size: {self.font_size}pt; }}") \
            if hasattr(self.css, "load_from_string") else \
            self.css.load_from_data(f".{self.css_class} {{ font-size: {self.font_size}pt; }}".encode(), -1)

    def _build_actions(self):
        def add(name, cb):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_: cb())
            self.add_action(a)
        add("save", self.save)
        add("save-copy", self.save_copy)
        add("search", lambda: self.open_search(False))
        add("replace", lambda: self.open_search(True))
        add("goto", self.goto_dialog)
        add("check", self.check_syntax)
        add("hotkeys", self.show_hotkeys)
        wrap = Gio.SimpleAction.new_stateful("wrap", None, GLib.Variant("b", False))

        def toggle_wrap(a, _p):
            v = not a.get_state().get_boolean()
            a.set_state(GLib.Variant("b", v))
            self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if v else Gtk.WrapMode.NONE)
        wrap.connect("activate", toggle_wrap)
        self.add_action(wrap)

    # ------------------------------------------------------------------ текст

    def _set_text(self, text: str):
        self.buf.begin_irreversible_action()
        self.buf.set_text(text)
        self.buf.end_irreversible_action()
        self.buf.place_cursor(self.buf.get_start_iter())
        self.buf.set_modified(False)

    def get_text(self) -> str:
        return self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), True)

    def _update_title(self):
        mod = self.buf.get_modified()
        self.title_widget.set_title(("• " if mod else "") + self.target.title)
        self.title_widget.set_subtitle(self.target.subtitle)
        self.set_title(("• " if mod else "") + self.target.title + " — редактор PboViewer")

    def _update_status(self):
        it = self.buf.get_iter_at_mark(self.buf.get_insert())
        col = it.get_line_offset() + 1
        self.status.set_text(f"Строка {it.get_line() + 1}, столбец {col}   ·   "
                             f"строк: {self.buf.get_line_count()}")

    def _on_changed(self, *_):
        if self.search_revealer.get_reveal_child() and self.find_entry.get_text():
            if getattr(self, "_rematch", 0):
                GLib.source_remove(self._rematch)
            self._rematch = GLib.timeout_add(300, self._delayed_rematch)

    def _delayed_rematch(self):
        self._rematch = 0
        self._update_matches(select=False)
        return False

    def toast(self, text: str, timeout: int = 3):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout))

    def undo(self):
        if self.buf.get_can_undo():
            self.buf.undo()

    def redo(self):
        if self.buf.get_can_redo():
            self.buf.redo()

    # ------------------------------------------------------------------ строки

    def _line_iter(self, line: int) -> Gtk.TextIter:
        res = self.buf.get_iter_at_line(max(0, line))
        return res[1] if isinstance(res, tuple) else res

    def _line_end(self, line: int) -> Gtk.TextIter:
        it = self._line_iter(line)
        if not it.ends_line():
            it.forward_to_line_end()
        return it

    def _sel_lines(self) -> tuple[int, int]:
        b = self.buf
        if b.get_has_selection():
            s, e = b.get_selection_bounds()
            l1, l2 = s.get_line(), e.get_line()
            if e.starts_line() and l2 > l1:
                l2 -= 1
            return l1, l2
        line = b.get_iter_at_mark(b.get_insert()).get_line()
        return line, line

    def _text_lines(self, l1: int, l2: int) -> str:
        return self.buf.get_text(self._line_iter(l1), self._line_end(l2), True)

    def _select_lines(self, l1: int, l2: int):
        self.buf.select_range(self._line_iter(l1), self._line_end(l2))
        self.view.scroll_mark_onscreen(self.buf.get_insert())

    def duplicate_lines(self):
        l1, l2 = self._sel_lines()
        had_sel = self.buf.get_has_selection()
        col = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line_offset()
        text = self._text_lines(l1, l2)
        self.buf.begin_user_action()
        self.buf.insert(self._line_end(l2), "\n" + text)
        self.buf.end_user_action()
        n = l2 - l1 + 1
        if had_sel:
            self._select_lines(l1 + n, l2 + n)
        else:
            it = self._line_iter(l2 + 1)
            it.forward_chars(min(col, len(self._text_lines(l2 + 1, l2 + 1))))
            self.buf.place_cursor(it)
            self.view.scroll_mark_onscreen(self.buf.get_insert())

    def delete_lines(self):
        l1, l2 = self._sel_lines()
        last = self.buf.get_line_count() - 1
        start = self._line_iter(l1)
        if l2 < last:
            end = self._line_iter(l2 + 1)
        else:
            end = self.buf.get_end_iter()
            if l1 > 0:
                start = self._line_end(l1 - 1)
        self.buf.begin_user_action()
        self.buf.delete(start, end)
        self.buf.end_user_action()

    def move_lines(self, delta: int):
        l1, l2 = self._sel_lines()
        last = self.buf.get_line_count() - 1
        had_sel = self.buf.get_has_selection()
        col = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line_offset()
        if delta < 0 and l1 == 0 or delta > 0 and l2 >= last:
            return
        block = self._text_lines(l1, l2)
        if delta < 0:
            other = self._text_lines(l1 - 1, l1 - 1)
            a, b, new = l1 - 1, l2, block + "\n" + other
        else:
            other = self._text_lines(l2 + 1, l2 + 1)
            a, b, new = l1, l2 + 1, other + "\n" + block
        self.buf.begin_user_action()
        s, e = self._line_iter(a), self._line_end(b)
        self.buf.delete(s, e)
        self.buf.insert(self._line_iter(a), new)
        self.buf.end_user_action()
        n1, n2 = l1 + delta, l2 + delta
        if had_sel:
            self._select_lines(n1, n2)
        else:
            it = self._line_iter(n1)
            it.forward_chars(min(col, len(self._text_lines(n1, n1))))
            self.buf.place_cursor(it)
            self.view.scroll_mark_onscreen(self.buf.get_insert())

    def toggle_comment(self):
        l1, l2 = self._sel_lines()
        lines = [self._text_lines(i, i) for i in range(l1, l2 + 1)]
        code = [ln for ln in lines if ln.strip()]
        uncomment = bool(code) and all(ln.lstrip().startswith("//") for ln in code)
        indent = min((len(ln) - len(ln.lstrip()) for ln in code), default=0)
        self.buf.begin_user_action()
        for i, ln in zip(range(l1, l2 + 1), lines):
            if not ln.strip():
                continue
            if uncomment:
                pos = ln.find("//")
                cut = 3 if ln[pos + 2:pos + 3] == " " else 2
                s = self._line_iter(i)
                s.forward_chars(pos)
                e = s.copy()
                e.forward_chars(cut)
                self.buf.delete(s, e)
            else:
                s = self._line_iter(i)
                s.forward_chars(indent)
                self.buf.insert(s, "// ")
        self.buf.end_user_action()
        if l2 > l1:
            self._select_lines(l1, l2)

    def indent_lines(self, unindent: bool):
        l1, l2 = self._sel_lines()
        self.buf.begin_user_action()
        for i in range(l1, l2 + 1):
            s = self._line_iter(i)
            if unindent:
                ln = self._text_lines(i, i)
                if ln.startswith("\t"):
                    n = 1
                else:
                    n = len(ln) - len(ln.lstrip(" "))
                    n = min(n, 4)
                if n:
                    e = s.copy()
                    e.forward_chars(n)
                    self.buf.delete(s, e)
            elif self._text_lines(i, i).strip() or l1 == l2:
                self.buf.insert(s, "\t")
        self.buf.end_user_action()
        if l2 > l1 or self.buf.get_has_selection():
            self._select_lines(l1, l2)

    def newline_indent(self):
        b = self.buf
        if b.get_has_selection():
            s, e = b.get_selection_bounds()
            b.begin_user_action()
            b.delete(s, e)
            b.end_user_action()
        it = b.get_iter_at_mark(b.get_insert())
        line_start = self._line_iter(it.get_line())
        before = b.get_text(line_start, it, True)
        indent = before[:len(before) - len(before.lstrip(" \t"))]
        if before.rstrip().endswith("{"):
            indent += "\t"
        b.begin_user_action()
        b.insert_at_cursor("\n" + indent)
        b.end_user_action()
        self.view.scroll_mark_onscreen(b.get_insert())

    def goto_line(self, line: int):
        line = max(1, min(int(line), self.buf.get_line_count()))
        it = self._line_iter(line - 1)
        self.buf.place_cursor(it)
        self.view.scroll_to_iter(it, 0.1, True, 0.0, 0.3)
        self.view.grab_focus()
        return False

    # ------------------------------------------------------------------ клавиши

    def _latin(self, keyval: int, keycode: int, state) -> int:
        """Для кириллической раскладки: Ctrl+S должен работать и как Ctrl+Ы."""
        if keyval < 0x100 or keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            return Gdk.keyval_to_lower(keyval)
        if Gdk.keyval_to_unicode(keyval) == 0:
            return keyval
        try:
            res = self.get_display().translate_key(keycode, 0, 0)
            if res[0] and res[1]:
                return Gdk.keyval_to_lower(res[1])
        except (TypeError, AttributeError):
            pass
        return Gdk.keyval_to_lower(keyval)

    def _on_key(self, ctrl, keyval, keycode, state):
        mods = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK |
                        Gdk.ModifierType.ALT_MASK)
        c = bool(mods & Gdk.ModifierType.CONTROL_MASK)
        s = bool(mods & Gdk.ModifierType.SHIFT_MASK)
        a = bool(mods & Gdk.ModifierType.ALT_MASK)
        k = self._latin(keyval, keycode, state)
        in_view = self.view.has_focus()

        # команды окна (работают и из строки поиска)
        if c and not a:
            if k == Gdk.KEY_s:
                self.save_copy() if s else self.save()
                return True
            if k == Gdk.KEY_f and not s:
                self.open_search(False)
                return True
            if k in (Gdk.KEY_h, Gdk.KEY_r) and not s:
                self.open_search(True)
                return True
            if k == Gdk.KEY_g and not s:
                self.goto_dialog()
                return True
            if k in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self.zoom(1)
                return True
            if k in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self.zoom(-1)
                return True
            if k in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self.zoom(0)
                return True
            if k == Gdk.KEY_w and not s:
                self.close()
                return True
        if not c and not a and k == Gdk.KEY_F3:
            self.find_prev() if s else self.find_next()
            return True
        if not mods and k == Gdk.KEY_F7:
            self.check_syntax()
            return True
        if not in_view:
            return False

        # редактирование
        if c and not a:
            if k == Gdk.KEY_z and not s:
                self.undo()
                return True
            if (k == Gdk.KEY_z and s) or (k == Gdk.KEY_y and not s):
                self.redo()
                return True
            if k == Gdk.KEY_d and not s:
                self.duplicate_lines()
                return True
            if (k == Gdk.KEY_k and s) or (k == Gdk.KEY_e and not s):
                self.delete_lines()
                return True
            if k == Gdk.KEY_l and not s:
                l1, l2 = self._sel_lines()
                self._select_lines(l1, l2)
                return True
            if k in (Gdk.KEY_slash, Gdk.KEY_KP_Divide):
                self.toggle_comment()
                return True
        if a and not c and k in (Gdk.KEY_Up, Gdk.KEY_Down):
            self.move_lines(-1 if k == Gdk.KEY_Up else 1)
            return True
        if not c and not a:
            if k == Gdk.KEY_ISO_Left_Tab or (k == Gdk.KEY_Tab and s):
                self.indent_lines(True)
                return True
            if k == Gdk.KEY_Tab and self.buf.get_has_selection():
                l1, l2 = self._sel_lines()
                if l2 > l1:
                    self.indent_lines(False)
                    return True
            if k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not s:
                self.newline_indent()
                return True
        return False

    def zoom(self, d: int):
        self.font_size = 11 if d == 0 else max(6, min(40, self.font_size + d))
        self._apply_font()

    # ------------------------------------------------------------------ поиск

    def open_search(self, replace: bool):
        self.search_revealer.set_reveal_child(True)
        self.replace_row.set_visible(replace)
        if self.buf.get_has_selection():
            s, e = self.buf.get_selection_bounds()
            sel = self.buf.get_text(s, e, True)
            if sel and "\n" not in sel:
                self.find_entry.set_text(sel)
        self.find_entry.grab_focus()
        self.find_entry.select_region(0, -1)
        self._update_matches(select=False)

    def close_search(self):
        self.search_revealer.set_reveal_child(False)
        self.buf.remove_tag(self.match_tag, self.buf.get_start_iter(), self.buf.get_end_iter())
        self._matches = []
        self.view.grab_focus()

    def _on_find_key(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close_search()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK:
            self.find_prev()
            return True
        return False

    def _pattern(self) -> Optional[re.Pattern]:
        q = self.find_entry.get_text()
        if not q:
            return None
        flags = 0 if self.case_btn.get_active() else re.IGNORECASE
        try:
            return re.compile(q if self.regex_btn.get_active() else re.escape(q), flags | re.MULTILINE)
        except re.error:
            return None

    def _update_matches(self, select: bool):
        b = self.buf
        b.remove_tag(self.match_tag, b.get_start_iter(), b.get_end_iter())
        self._matches = []
        pat = self._pattern()
        if pat is None:
            self.match_label.set_text("" if not self.find_entry.get_text() else "ошибка")
            return
        text = self.get_text()
        for m in pat.finditer(text):
            if m.end() == m.start():
                continue
            self._matches.append((m.start(), m.end()))
            if len(self._matches) >= 20000:
                break
        for s, e in self._matches[:5000]:
            b.apply_tag(self.match_tag, b.get_iter_at_offset(s), b.get_iter_at_offset(e))
        if select and self._matches:
            cur = b.get_iter_at_mark(b.get_selection_bound() if b.get_has_selection() else b.get_insert())
            off = min(cur.get_offset(), b.get_iter_at_mark(b.get_insert()).get_offset())
            nxt = next((m for m in self._matches if m[0] >= off), self._matches[0])
            self._select_match(nxt, grab=False)
        self._label_matches()

    def _label_matches(self):
        if not self._matches:
            self.match_label.set_text("нет совпадений" if self.find_entry.get_text() else "")
            return
        cur = self._current_match_index()
        pos = f"{cur + 1} из " if cur is not None else ""
        self.match_label.set_text(f"{pos}{len(self._matches)}")

    def _current_match_index(self) -> Optional[int]:
        if not self.buf.get_has_selection():
            return None
        s, e = self.buf.get_selection_bounds()
        key = (s.get_offset(), e.get_offset())
        for i, m in enumerate(self._matches):
            if m == key:
                return i
        return None

    def _select_match(self, m, grab: bool = False):
        s = self.buf.get_iter_at_offset(m[0])
        e = self.buf.get_iter_at_offset(m[1])
        self.buf.select_range(s, e)
        self.view.scroll_to_iter(s, 0.1, False, 0.0, 0.0)
        self._label_matches()

    def find_next(self):
        if not self.search_revealer.get_reveal_child():
            self.open_search(False)
            return
        if not self._matches:
            self._update_matches(select=False)
        if not self._matches:
            return
        if self.buf.get_has_selection():
            _s, e = self.buf.get_selection_bounds()
            off = e.get_offset()
        else:
            off = self.buf.get_iter_at_mark(self.buf.get_insert()).get_offset()
        nxt = next((m for m in self._matches if m[0] >= off), self._matches[0])
        self._select_match(nxt)

    def find_prev(self):
        if not self.search_revealer.get_reveal_child():
            self.open_search(False)
            return
        if not self._matches:
            self._update_matches(select=False)
        if not self._matches:
            return
        if self.buf.get_has_selection():
            s, _e = self.buf.get_selection_bounds()
            off = s.get_offset()
        else:
            off = self.buf.get_iter_at_mark(self.buf.get_insert()).get_offset()
        prev = next((m for m in reversed(self._matches) if m[1] <= off), self._matches[-1])
        self._select_match(prev)

    def _replacement(self, m: re.Match) -> str:
        rep = self.replace_entry.get_text()
        if self.regex_btn.get_active():
            try:
                return m.expand(rep)
            except (re.error, IndexError):
                return rep
        return rep

    def replace_one(self):
        pat = self._pattern()
        if pat is None:
            return
        idx = self._current_match_index()
        if idx is None:
            self.find_next()
            return
        s_off, e_off = self._matches[idx]
        text = self.get_text()
        m = pat.match(text, s_off)
        rep = self._replacement(m) if m and m.end() == e_off else self.replace_entry.get_text()
        b = self.buf
        b.begin_user_action()
        s, e = b.get_iter_at_offset(s_off), b.get_iter_at_offset(e_off)
        b.delete(s, e)
        b.insert(b.get_iter_at_offset(s_off), rep)
        b.end_user_action()
        b.place_cursor(b.get_iter_at_offset(s_off + len(rep)))
        self._update_matches(select=False)
        self.find_next()

    def replace_all(self):
        pat = self._pattern()
        if pat is None:
            return
        text = self.get_text()
        found = [m for m in pat.finditer(text) if m.end() > m.start()]
        if not found:
            self.toast("Совпадений нет")
            return
        b = self.buf
        b.begin_user_action()
        for m in reversed(found):
            rep = self._replacement(m)
            b.delete(b.get_iter_at_offset(m.start()), b.get_iter_at_offset(m.end()))
            b.insert(b.get_iter_at_offset(m.start()), rep)
        b.end_user_action()
        self._update_matches(select=False)
        self.toast(f"Заменено: {len(found)}")

    # ------------------------------------------------------------------ диалоги

    def _dialog(self, heading, body, responses, callback, extra=None, default=None):
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

    def error(self, title: str, body: str):
        self._dialog(title, body, [("ok", "Закрыть", "")], lambda r: None)

    def goto_dialog(self):
        cur = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line() + 1
        entry = Gtk.Entry(text=str(cur), activates_default=True, input_purpose=Gtk.InputPurpose.DIGITS)

        def done(r):
            if r == "go":
                try:
                    self.goto_line(int(entry.get_text().strip()))
                except ValueError:
                    pass
        self._dialog("Перейти к строке", f"1 — {self.buf.get_line_count()}",
                     [("cancel", "Отмена", ""), ("go", "Перейти", "suggested")], done,
                     extra=entry, default="go")
        GLib.idle_add(lambda: (entry.grab_focus(), entry.select_region(0, -1), False)[-1])

    def show_hotkeys(self):
        grid = Gtk.Grid(column_spacing=18, row_spacing=6)
        for i, (k, v) in enumerate(HOTKEYS):
            grid.attach(Gtk.Label(label=k, xalign=1, css_classes=["monospace"]), 0, i, 1, 1)
            grid.attach(Gtk.Label(label=v, xalign=0, wrap=True), 1, i, 1, 1)
        self._dialog("Горячие клавиши редактора", "", [("ok", "Закрыть", "")], lambda r: None, extra=grid)

    def check_syntax(self) -> bool:
        if not self.doc.is_config:
            self.toast("Проверка доступна для config.cpp / config.bin")
            return True
        try:
            C.parse_text(self.get_text())
        except C.ConfigError as e:
            self._show_config_error(e)
            return False
        self.toast("Синтаксис конфига в порядке ✔")
        return True

    def _show_config_error(self, e: C.ConfigError):
        if e.line:
            self.goto_line(e.line)
            self._select_lines(e.line - 1, e.line - 1)
        self.error("Ошибка в конфиге", str(e))

    # ------------------------------------------------------------------ сохранение

    def save(self, then: Optional[Callable[[], None]] = None):
        if self.saving:
            self.toast("Сохранение уже идёт…")
            return
        text = self.get_text()
        try:
            data = self.doc.encode(text)
        except C.ConfigError as e:
            self._show_config_error(e)
            return
        self.saving = True
        self.status.set_text("Сохранение…")

        def done(err: Optional[str]):
            self.saving = False
            self._update_status()
            if err:
                self.error("Не удалось сохранить", err)
                return
            if self.get_text() == text:
                self.buf.set_modified(False)
            self.toast("Сохранено" + (" в архив" if not isinstance(self.target, DiskTarget) else ""))
            if then:
                then()
        self.target.save(data, done)

    def save_copy(self):
        name = self.target.title
        dlg = Gtk.FileDialog(title="Сохранить копию", initial_name=name)
        last = getattr(self.app, "last_dir", None)
        if last and os.path.isdir(last):
            dlg.set_initial_folder(Gio.File.new_for_path(last))

        def done(d, res):
            try:
                path = d.save_finish(res).get_path()
            except GLib.Error:
                return
            text = self.get_text()
            try:
                if self.doc.rap and not path.lower().endswith(".bin"):
                    data = text.encode("utf-8")  # текстовая копия бинарного конфига
                else:
                    data = self.doc.encode(text)
                with open(path, "wb") as f:
                    f.write(data)
            except C.ConfigError as e:
                self._show_config_error(e)
                return
            except OSError as e:
                self.error("Не удалось сохранить", str(e))
                return
            self.toast(f"Копия сохранена: {path}")
        dlg.save(self, None, done)

    def _on_close_request(self, *_a):
        if self.buf.get_modified() and not getattr(self, "_force_close", False):
            def resp(r):
                if r == "discard":
                    self._force_close = True
                    self.close()
                elif r == "save":
                    def after():
                        self._force_close = True
                        self.close()
                    self.save(after)
            self._dialog("Сохранить изменения?", f"«{self.target.title}» изменён.",
                         [("cancel", "Отмена", ""), ("discard", "Не сохранять", "destructive"),
                          ("save", "Сохранить", "suggested")], resp, default="save")
            return True
        if self.saving:
            self.toast("Дождитесь окончания сохранения")
            return True
        Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self.css)
        return False
