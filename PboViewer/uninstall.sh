#!/bin/sh
# Удаление пользовательской установки PboViewer (сделанной install.sh)
set -u
PREFIX="${PREFIX:-$HOME/.local}"
SHARE="${XDG_DATA_HOME:-$PREFIX/share}"
APP_ID="org.pboviewer.PboViewer"

rm -rf "$SHARE/pboviewer"
rm -f "$PREFIX/bin/pboviewer" \
      "$SHARE/applications/$APP_ID.desktop" \
      "$SHARE/mime/packages/pboviewer.xml" \
      "$SHARE/kio/servicemenus/pboviewer-pbo.desktop" \
      "$SHARE/kio/servicemenus/pboviewer-folder.desktop" \
      "$SHARE/nautilus-python/extensions/pboviewer_nautilus.py" \
      "$SHARE"/nemo/actions/pboviewer-*.nemo_action
find "$SHARE/icons/hicolor" \( -name "$APP_ID.png" -o -name "application-x-pbo.png" \) -delete 2>/dev/null
command -v update-mime-database >/dev/null && update-mime-database "$SHARE/mime" || true
command -v update-desktop-database >/dev/null && update-desktop-database -q "$SHARE/applications" || true
[ -f "${XDG_CONFIG_HOME:-$HOME/.config}/mimeapps.list" ] && sed -i "/^application\/x-pbo=/d" "${XDG_CONFIG_HOME:-$HOME/.config}/mimeapps.list"
rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/pboviewer"
echo "PboViewer удалён. Настройки остались в ~/.config/pboviewer (удалите вручную при желании)."
