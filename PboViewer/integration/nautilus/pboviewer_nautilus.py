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

    def _scripts_menu(self, files, on_pbo):
        """Подменю «Скрипты и конфиги»: вынос из PBO и возврат правок."""
        top = Nautilus.MenuItem(name="PboViewer::scripts", label="Скрипты и конфиги (PboViewer)",
                                icon="org.pboviewer.PboViewer")
        sub = Nautilus.Menu()
        top.set_submenu(sub)
        items = []
        if on_pbo:
            items.append(("extract", "Вынести скрипты и конфиги из этого PBO", "archive-extract",
                          "--extract-scripts"))
        items += [
            ("extract-all", "Вынести скрипты и конфиги из всех PBO в папке", "archive-extract",
             "--extract-scripts-all"),
            ("pack", "Вернуть правки в этот PBO" if on_pbo else "Вернуть правки этой папки в её PBO",
             "archive-insert", "--pack-scripts"),
            ("pack-all", "Вернуть правки во все PBO папки", "archive-insert", "--pack-scripts-all"),
        ]
        for name, label, icon, flag in items:
            sub.append_item(self._item(f"scripts-{name}", label, icon, [flag], files))
        return top

    def get_file_items(self, *args):
        files = args[-1]
        if not files or any(f.get_uri_scheme() != "file" for f in files):
            return []
        if all(f.get_name().lower().endswith(".pbo") and not f.is_directory() for f in files):
            return [
                self._item("open", "Открыть архив в PboViewer", "org.pboviewer.PboViewer", [], files),
                self._item("unpack", "Распаковать PBO сюда", "archive-extract", ["--unpack"], files),
                self._scripts_menu(files, on_pbo=True),
            ]
        if all(f.is_directory() for f in files):
            return [self._item("pack", "Упаковать папку в PBO", "org.pboviewer.PboViewer",
                               ["--pack"], files),
                    self._scripts_menu(files, on_pbo=False)]
        return []

    def get_background_items(self, *args):
        # правый клик по пустому месту в папке
        folder = args[-1]
        if folder is None or folder.get_uri_scheme() != "file":
            return []
        return [self._scripts_menu([folder], on_pbo=False)]
