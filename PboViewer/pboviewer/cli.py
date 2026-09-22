"""Командная строка PboViewer (работает без графики)."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from . import __version__
from . import pbo as P

COMMANDS = ("list", "ls", "extract", "unpack", "pack", "add", "replace", "delete", "rm",
            "rename", "mv", "prefix", "verify", "info")


def _progress(label):
    if not sys.stderr.isatty():
        return None

    def cb(done, total):
        pct = int(done * 100 / max(total, 1))
        sys.stderr.write(f"\r{label}: {pct:3d}%")
        if done >= total:
            sys.stderr.write("\n")
        sys.stderr.flush()
    return cb


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pboviewer",
        description="Просмотр и редактирование PBO (DayZ/Arma). Без команды — открывает окно.")
    p.add_argument("--version", action="version", version=f"PboViewer {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="КОМАНДА")

    s = sub.add_parser("list", aliases=["ls"], help="список файлов")
    s.add_argument("pbo")
    s.add_argument("-l", "--long", action="store_true", help="размер, дата, сжатие")

    s = sub.add_parser("info", help="свойства и сводка")
    s.add_argument("pbo")

    s = sub.add_parser("extract", help="извлечь отдельные файлы/папки")
    s.add_argument("pbo")
    s.add_argument("paths", nargs="+", help="пути внутри PBO (файл или папка)")
    s.add_argument("-o", "--out", default=".", help="папка назначения (по умолчанию текущая)")

    s = sub.add_parser("unpack", help="распаковать весь PBO")
    s.add_argument("pbo")
    s.add_argument("-o", "--out", help="папка (по умолчанию рядом с PBO)")

    s = sub.add_parser("pack", help="упаковать папку в PBO")
    s.add_argument("folder")
    s.add_argument("-o", "--out", help="файл PBO (по умолчанию <папка>.pbo)")
    s.add_argument("--prefix", help="префикс (иначе из $PBOPREFIX$)")

    for name, hlp in (("add", "добавить файлы/папки (существующие заменяются)"),):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("pbo")
        s.add_argument("sources", nargs="+")
        s.add_argument("-t", "--to", default="", help="папка внутри PBO")
        s.add_argument("--backup", action="store_true", help="сохранить .bak")

    s = sub.add_parser("replace", help="заменить файл внутри PBO")
    s.add_argument("pbo")
    s.add_argument("inner", help="путь внутри PBO")
    s.add_argument("source", help="новый файл")
    s.add_argument("-o", "--out", help="записать в другой PBO (по умолчанию — на месте)")
    s.add_argument("--backup", action="store_true")

    s = sub.add_parser("delete", aliases=["rm"], help="удалить файлы/папки из PBO")
    s.add_argument("pbo")
    s.add_argument("paths", nargs="+")
    s.add_argument("--backup", action="store_true")

    s = sub.add_parser("rename", aliases=["mv"], help="переименовать/переместить внутри PBO")
    s.add_argument("pbo")
    s.add_argument("old")
    s.add_argument("new")
    s.add_argument("--backup", action="store_true")

    s = sub.add_parser("prefix", help="показать или задать префикс")
    s.add_argument("pbo")
    s.add_argument("value", nargs="?")

    s = sub.add_parser("verify", help="проверить SHA1")
    s.add_argument("pbo")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    cmd = {"ls": "list", "rm": "delete", "mv": "rename"}.get(args.cmd, args.cmd)
    try:
        return _run(cmd, args)
    except (P.PboError, OSError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1


def _run(cmd, args) -> int:
    if cmd == "pack":
        folder = args.folder.rstrip("/")
        out = args.out or folder + ".pbo"
        a = P.pack_folder(folder, out, prefix=args.prefix, backup=os.path.exists(out),
                          progress=_progress("Упаковка"))
        print(f"{out}: {len(a.entries)} файлов, префикс: {a.prefix or '—'}")
        return 0

    a = P.PboArchive.open(args.pbo)

    if cmd == "list":
        for e in a.entries:
            if args.long:
                ts = datetime.fromtimestamp(e.timestamp).strftime("%Y-%m-%d %H:%M") if e.timestamp else " " * 16
                print(f"{e.size:>12}  {ts}  {'LZSS' if e.compressed else '    '}  {e.name}")
            else:
                print(e.name)
    elif cmd == "info":
        print(f"Файл:     {a.path}")
        print(f"Файлов:   {len(a.entries)} ({P.human_size(a.total_size)})")
        for k, v in a.props:
            print(f"{k + ':':9} {v}")
        print(f"SHA1:     {a.stored_checksum.hex() if a.stored_checksum else 'нет'}")
    elif cmd == "extract":
        pairs = []
        for inner in args.paths:
            e = a.find(inner)
            if e:
                pairs.append((e, os.path.join(args.out, e.basename)))
                continue
            under = a.under(inner)
            if not under:
                raise P.PboError(f"Не найдено в архиве: {inner}")
            base = P.to_pbo_path(inner)
            top = base.rsplit("\\", 1)[-1]
            for e in under:
                rel = P.to_pbo_path(e.name)[len(base) + 1:]
                pairs.append((e, os.path.join(args.out, top, P.safe_relpath(rel))))
        a.extract(pairs, _progress("Извлечение"))
        print(f"Извлечено файлов: {len(pairs)}")
    elif cmd == "unpack":
        out = args.out or os.path.splitext(a.path)[0]
        a.extract_all(out, _progress("Распаковка"))
        print(out)
    elif cmd == "add":
        plan = a.edit()
        for src in args.sources:
            name = os.path.basename(os.path.abspath(src))
            if os.path.isdir(src):
                for inner, r in plan.add_tree(src, P.join_pbo(args.to, name)):
                    print(f"{'заменён ' if r == 'replaced' else 'добавлен'} {inner}")
            else:
                inner = P.join_pbo(args.to, name)
                r = plan.add_file(inner, src)
                print(f"{'заменён ' if r == 'replaced' else 'добавлен'} {inner}")
        plan.commit(backup=args.backup, progress=_progress("Запись"))
    elif cmd == "replace":
        if not a.find(args.inner):
            raise P.PboError(f"Не найдено в архиве: {args.inner}")
        plan = a.edit()
        plan.add_file(args.inner, args.source)
        plan.commit(dest=args.out, backup=args.backup, progress=_progress("Запись"))
        print(f"заменён {args.inner}")
    elif cmd == "delete":
        plan = a.edit()
        n = sum(plan.delete(p) for p in args.paths)
        if not n:
            raise P.PboError("Ничего не найдено для удаления")
        plan.commit(backup=args.backup, progress=_progress("Запись"))
        print(f"удалено файлов: {n}")
    elif cmd == "rename":
        plan = a.edit()
        n = plan.rename(args.old, args.new)
        if not n:
            raise P.PboError(f"Не найдено в архиве: {args.old}")
        plan.commit(backup=args.backup, progress=_progress("Запись"))
        print(f"переименовано файлов: {n}")
    elif cmd == "prefix":
        if args.value is None:
            print(a.prefix or "")
        else:
            plan = a.edit()
            plan.set_prop("prefix", args.value or None)
            plan.commit()
    elif cmd == "verify":
        ok = a.verify(_progress("SHA1"))
        print({None: "контрольной суммы нет", True: "OK", False: "НЕ СОВПАДАЕТ"}[ok])
        return 0 if ok is not False else 2
    return 0
