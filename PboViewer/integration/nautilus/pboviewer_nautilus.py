"""Пункты PboViewer в контекстном меню Nautilus (нужен пакет python-nautilus)."""
import subprocess

from gi.repository import GObject, Nautilus

PBOVIEWER = "@BIN@"


def _paths(files):
    out = []
    for f in files:
        loc = f.get_location()
        p = loc.get_path() if loc else None
        if p:
            out.append(p)
    return out


class PboViewerMenu(GObject.GObject, Nautilus.MenuProvider):
    def _item(self, name, label, icon, args, files):
        item = Nautilus.MenuItem(name=f"PboViewer::{name}", label=label, icon=icon)
        item.connect("activate", lambda *_: subprocess.Popen([PBOVIEWER, *args, *_paths(files)],
                                                             start_new_session=True))
        return item

    def get_file_items(self, *args):
        files = args[-1]
        if not files or any(f.get_uri_scheme() != "file" for f in files):
            return []
        if all(f.get_name().lower().endswith(".pbo") and not f.is_directory() for f in files):
            return [
                self._item("open", "Открыть архив в PboViewer", "org.pboviewer.PboViewer", [], files),
                self._item("unpack", "Распаковать PBO сюда", "archive-extract", ["--unpack"], files),
            ]
        if all(f.is_directory() for f in files):
            return [self._item("pack", "Упаковать папку в PBO", "org.pboviewer.PboViewer",
                               ["--pack"], files)]
        return []

    def get_background_items(self, *args):
        return []
