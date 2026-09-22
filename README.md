# PboViewer for Linux

**Редактор архивов PBO (DayZ / Arma) для Linux: открывает `.pbo` как обычный архив, файлы перетаскиваются внутрь и наружу без распаковки и перепаковки.**

![GTK 4](https://img.shields.io/badge/GTK-4-4a86cf) ![Wayland](https://img.shields.io/badge/Wayland-native-green) ![Arch Linux](https://img.shields.io/badge/Arch_Linux-PKGBUILD-1793d1) ![License](https://img.shields.io/badge/license-GPL--3.0-blue)

![PboViewer](docs/screenshot.png)

Написан на Python + GTK 4 / libadwaita, работает нативно под Wayland (KDE Plasma, GNOME, Hyprland, Sway).
Дополнительно встраивается в контекстное меню Dolphin, Nautilus и Nemo.

## Что умеет

- **Открыть PBO как архив**: навигация по папкам, поиск по всему архиву (Ctrl+F), сортировка по колонкам.
- **Перетащить файлы в окно**: из Dolphin/Nautilus/любого файлового менеджера файлы и папки добавляются
  в текущую папку архива. Если бросить на строку-папку, файлы попадут в эту папку.
  Файл с тем же именем **заменяется** (по умолчанию программа спросит подтверждение).
- **Перетащить файлы из окна наружу**: на рабочий стол или в папку файлового менеджера. Извлекаются
  только выбранные файлы или папки, архив целиком не распаковывается.
- **Замена без перепаковки** (как команда `replace`): новый PBO пишется потоково. Данные остальных файлов
  копируются блоками прямо из старого архива, затем файл атомарно подменяется. Если произойдёт сбой,
  исходный PBO останется целым. SHA1 в конце архива пересчитывается.
- **Правый клик по файлу в окне**: открыть, извлечь в…, копировать/вставить (Ctrl+C / Ctrl+V с файловым
  менеджером), скопировать путь внутри PBO (вместе с префиксом), заменить файлом…, переименовать (F2),
  удалить (Delete), добавить файлы/папку, новая папка.
- **Редактирование «на месте»**: двойной щелчок открывает файл в программе по умолчанию (Kate, VS Code…).
  После сохранения появится кнопка **«Обновить в архиве»**.
- Свойства PBO (префикс `prefix`, `product`, `version`), проверка контрольной суммы, распаковка всего архива,
  создание PBO из папки (учитывается `$PBOPREFIX$`), новый пустой PBO.
- Резервная копия `.bak` при каждом изменении (по умолчанию выключена, включается в меню).
- Читает сжатые (LZSS) записи. Добавленные файлы пишутся без сжатия.

### Контекстное меню файлового менеджера
Правый клик по `.pbo`:
- **Открыть архив в PboViewer**
- **Распаковать PBO сюда** (распаковывает в папку рядом, показывает окно с прогрессом)

Правый клик по папке → **Упаковать папку в PBO** (создаёт `<папка>.pbo` рядом).

| Файловый менеджер | Как появляется |
|---|---|
| Dolphin (KDE Plasma 6) | service menu, сразу после установки (иногда нужно перезапустить Dolphin) |
| Nautilus (GNOME) | нужен пакет `python-nautilus`, затем `nautilus -q` |
| Nemo (Cinnamon) | действия Nemo, `nemo --quit` |
| Любой другой (Thunar, PCManFM…) | пункт «Открыть с помощью → PboViewer»: программа регистрируется для типа `application/x-pbo` |

**Если пункты не появились (KDE):**
```sh
update-mime-database ~/.local/share/mime
kbuildsycoca6 --noincremental
systemctl --user restart plasma-plasmashell   # меню на рабочем столе рисует plasmashell
```
Проверка: `xdg-mime query filetype файл.pbo` должна вывести `application/x-pbo`.

Для Thunar можно добавить своё действие: *Правка → Настроить пользовательские действия*,
команда `pboviewer %F`, шаблон файлов `*.pbo`.

## Установка на Arch Linux

Зависимости: `python`, `python-gobject`, `gtk4`, `libadwaita`.

**Вариант 1: пакетом pacman (рекомендуется)**
```sh
git clone https://github.com/DiabloZet/pboviewer-gtk.git
cd pboviewer-gtk
makepkg -si
```
Удаление: `sudo pacman -R pboviewer-gtk`.

**Вариант 2: только для себя, без root**
```sh
sudo pacman -S --needed python python-gobject gtk4 libadwaita
git clone https://github.com/DiabloZet/pboviewer-gtk.git
cd pboviewer-gtk
./install.sh        # удаление: ./uninstall.sh
```
Ставит в `~/.local`. Пункты меню и ассоциация `.pbo` настраиваются автоматически.

**Запуск без установки:** `./bin/pboviewer [файл.pbo]`

## Командная строка

```sh
pboviewer mod.pbo                          # открыть окно
pboviewer list -l mod.pbo                  # список файлов
pboviewer info mod.pbo                     # префикс, число файлов, SHA1
pboviewer replace mod.pbo config.cpp ./config.cpp
pboviewer add mod.pbo ./scripts -t ""      # добавить/заменить папку
pboviewer delete mod.pbo "data\old.paa"
pboviewer rename mod.pbo "scripts\old" "scripts\new"
pboviewer extract mod.pbo config.cpp scripts -o ./out
pboviewer unpack mod.pbo [-o папка]
pboviewer pack ./mod [-o mod.pbo] [--prefix "Artemida\mod"]
pboviewer prefix mod.pbo [новый_префикс]
pboviewer verify mod.pbo
```

## Важно знать

- **Подписи `.bisign`** после любого изменения PBO становятся недействительными. Если на сервере включён
  `verifySignatures`, переподпишите архив.
- **Префикс `pboprefix` вместо `prefix`.** Старый C#-упаковщик (`PBOWriter.WritePBO`) берёт имя свойства
  из имени файла `$PBOPREFIX$` и пишет ключ `pboprefix`. DayZ ищет ключ `prefix` и такой префикс
  не увидит. PboViewer покажет предупреждение внизу окна; кнопка «Сохранить» в *Меню → Свойства PBO*
  исправит ключ. Новый упаковщик пишет `prefix` сразу.
- PBO не хранит пустые папки. «Новая папка» существует в окне, пока в неё что-нибудь не добавят.
- Временные файлы для перетаскивания/открытия лежат в `~/.cache/pboviewer` и чистятся автоматически
  через 12 часов.

## Горячие клавиши
Ctrl+O открыть · Ctrl+F поиск · Enter/двойной щелчок открыть · Backspace, Alt+↑ вверх ·
F2 переименовать · Delete удалить · Ctrl+C / Ctrl+V копировать / вставить · Shift+F10 меню · Ctrl+W закрыть

## Разработка
```sh
python3 -m unittest discover -s tests -v
```
Структура: `pboviewer/pbo.py` (формат PBO, потоковое редактирование), `app.py` (интерфейс), `cli.py`
(команды), `integration/` (меню Dolphin/Nautilus/Nemo), `data/` (ярлык, MIME, иконки).

## Благодарности и лицензия

Linux-версия: **DiabloZet**. Основана на [PboViewer](https://github.com/SteezCram/PboViewer)
от Thomas «Steez» Croizet. Лицензия [GPL-3.0](LICENSE).

---

### English

PboViewer for Linux is a GTK 4 / libadwaita PBO archive manager for DayZ and Arma modding, native on Wayland.
Open a `.pbo` like any archive, drag files in to add or replace them, drag files out to extract them.
Edits stream the untouched data straight from the old archive, so the PBO is never fully unpacked and
repacked, and the SHA1 is rewritten. It also covers prefix editing, LZSS reading, and file-manager menus
(Dolphin, Nautilus, Nemo), and ships a CLI and a PKGBUILD for Arch Linux.
