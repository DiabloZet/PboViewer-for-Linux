#!/usr/bin/env python3
"""Собирает встроенную базу ванильных классов DayZ из распакованных config.cpp.

    python3 tools/build_classdb.py <папка DZ> [выходной файл]

По умолчанию пишет pboviewer/vanilla_classes.tsv.gz.
Формат строки: имя \\t родитель \\t секция (CfgVehicles…) \\t путь к конфигу в DZ.
"""

import glob
import gzip
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pboviewer import config as C  # noqa: E402

SECTIONS = ["CfgPatches", "CfgVehicles", "CfgWeapons", "CfgMagazines", "CfgAmmo",
            "CfgNonAIVehicles", "CfgSoundSets", "CfgSoundShaders", "CfgSounds",
            "CfgActionSounds", "CfgCloudlets", "CfgSurfaces", "CfgVehicleClasses",
            "CfgLiquidDefinitions", "CfgAmmoTypes", "CfgWorlds", "CfgMods", "CfgSlots",
            "CfgHorticulture", "CfgEffectSets", "CfgSoundCurves", "CfgFaces", "CfgHeads"]
CANON = {s.lower(): s for s in SECTIONS}


def main():
    src = os.path.abspath(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "pboviewer", "vanilla_classes.tsv.gz")
    rows = {}
    base = os.path.dirname(src)
    for f in sorted(glob.glob(os.path.join(src, "**", "*.cpp"), recursive=True)):
        with open(f, encoding="utf-8", errors="replace") as fh:
            tree = C.parse_text(fh.read())
        rel = os.path.relpath(f, base).replace(os.sep, "\\")
        for path, c in C.walk_classes(tree):
            if len(path) != 1 or path[0].lower() not in CANON:
                continue
            sec = CANON[path[0].lower()]
            key = (sec, c.name.lower())
            # первым берём полное определение (с телом/родителем)
            if key not in rows or (c.parent and not rows[key][1]):
                rows[key] = (c.name, c.parent or "", sec, rel)
    lines = ["\t".join(r) for r in sorted(rows.values(), key=lambda r: (r[2], r[0].lower()))]
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"{out}: {len(lines)} классов")


if __name__ == "__main__":
    main()
