#!/bin/sh
# Установка PboViewer для текущего пользователя (в ~/.local), без root.
# Для установки в систему через pacman используйте: makepkg -si
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
SHARE="${XDG_DATA_HOME:-$PREFIX/share}"
LIB="$SHARE/pboviewer"
BIN="$PREFIX/bin/pboviewer"
APP_ID="org.pboviewer.PboViewer"

echo "==> Проверка зависимостей"
if ! python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Gtk, Adw" 2>/dev/null; then
    echo "Не найдены GTK4 / libadwaita для Python. Установите:"
    echo "    sudo pacman -S --needed python python-gobject gtk4 libadwaita"
    exit 1
fi

echo "==> Программа → $LIB"
rm -rf "$LIB"
mkdir -p "$LIB/pboviewer" "$PREFIX/bin"
cp "$SRC"/pboviewer/*.py "$LIB/pboviewer/"
python3 -m compileall -q "$LIB/pboviewer" || true

cat > "$BIN" <<EOF
#!/bin/sh
PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m pboviewer "\$@"
EOF
chmod 755 "$BIN"

subst() { sed "s|@BIN@|$BIN|g" "$1" > "$2"; }

echo "==> Ярлык, MIME-тип .pbo, иконки"
mkdir -p "$SHARE/applications" "$SHARE/mime/packages"
subst "$SRC/data/$APP_ID.desktop" "$SHARE/applications/$APP_ID.desktop"
cp "$SRC/data/pboviewer-mime.xml" "$SHARE/mime/packages/pboviewer.xml"
mkdir -p "$SHARE/icons"
cp -r "$SRC/data/icons/hicolor" "$SHARE/icons/"

echo "==> Контекстное меню: Dolphin (KDE)"
mkdir -p "$SHARE/kio/servicemenus"
for f in "$SRC"/integration/dolphin/*.desktop; do
    dst="$SHARE/kio/servicemenus/$(basename "$f")"
    subst "$f" "$dst"
    chmod +x "$dst"   # Plasma 6 требует исполняемый бит у пользовательских service menu
done

echo "==> Контекстное меню: Nautilus (GNOME, нужен python-nautilus)"
mkdir -p "$SHARE/nautilus-python/extensions"
subst "$SRC/integration/nautilus/pboviewer_nautilus.py" "$SHARE/nautilus-python/extensions/pboviewer_nautilus.py"

echo "==> Контекстное меню: Nemo (Cinnamon)"
mkdir -p "$SHARE/nemo/actions"
for f in "$SRC"/integration/nemo/*.nemo_action; do
    subst "$f" "$SHARE/nemo/actions/$(basename "$f")"
done

echo "==> Обновление баз"
command -v update-mime-database >/dev/null && update-mime-database "$SHARE/mime" || true
command -v update-desktop-database >/dev/null && update-desktop-database -q "$SHARE/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$SHARE/icons/hicolor" 2>/dev/null || true
command -v xdg-mime >/dev/null && xdg-mime default "$APP_ID.desktop" application/x-pbo || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

echo
echo "Готово. Команда: $BIN"
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *) echo "Совет: добавьте $PREFIX/bin в PATH, чтобы запускать просто «pboviewer»." ;;
esac
echo "Перезапустите файловый менеджер (dolphin / nautilus -q / nemo --quit), чтобы появились пункты меню."
