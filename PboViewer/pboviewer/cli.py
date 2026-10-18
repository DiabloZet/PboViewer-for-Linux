"""Командная строка PboViewer (работает без графики)."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from . import __version__
from . import pbo as P

COMMANDS = ("list", "ls", "extract", "unpack", "pack", "add", "replace", "delete", "rm",
            "rename", "mv", "prefix", "verify", "info", "classes", "keygen", "sign",
            "checksign", "derap", "rap", "extract-scripts", "pack-scripts")


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
    s.add_argument("--no-debinarize", action="store_true",
                   help="не преобразовывать бинарные config.bin в текст .cpp")

    s = sub.add_parser("pack", help="упаковать папку в PBO")
    s.add_argument("folder")
    s.add_argument("-o", "--out", help="файл PBO (по умолчанию <папка>.pbo)")
    s.add_argument("--prefix", help="префикс (иначе из $PBOPREFIX$)")
    s.add_argument("--no-rapify", action="store_true",
                   help="не преобразовывать .cpp в бинарный .bin")
    s.add_argument("--obfuscate", action="store_true",
                   help="минифицировать скрипты .c (убрать комментарии и пробелы)")

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

    s = sub.add_parser("classes", help="классы мода (скрипты и конфиги) с родителями и описанием")
    s.add_argument("path", nargs="?", help="PBO или папка (рекурсивно); без пути — только встроенная база")
    s.add_argument("-s", "--search", default="", help="часть имени класса (без учёта регистра)")
    s.add_argument("-d", "--desc", action="store_true", help="искать по описанию, а не по имени")
    s.add_argument("-b", "--builtin", action="store_true", help="искать и во встроенной базе DayZ")
    s.add_argument("-r", "--read-class", metavar="CLASS", help="показать код класса")

    s = sub.add_parser("keygen", help="создать пару ключей .biprivatekey / .bikey (как DSCreateKey)")
    s.add_argument("name", help="имя ключа (authority)")
    s.add_argument("-o", "--out", default=".", help="папка для ключей")

    s = sub.add_parser("sign", help="подписать PBO: создать .bisign (как DSSignFile)")
    s.add_argument("key", help="файл .biprivatekey")
    s.add_argument("pbos", nargs="+")
    s.add_argument("-v", "--version", type=int, choices=(2, 3), default=3, help="версия подписи")

    s = sub.add_parser("checksign", help="проверить .bisign открытым ключом .bikey")
    s.add_argument("bikey")
    s.add_argument("pbo")
    s.add_argument("bisign", nargs="?")

    s = sub.add_parser("extract-scripts",
                       help="вынести .c/.cpp/.bin из PBO в pbo_sources/<имя_pbo>/ (для правки и pack-scripts)")
    s.add_argument("paths", nargs="*", default=["."], help="PBO или папки с PBO (по умолчанию текущая)")
    s.add_argument("-o", "--out", help="куда выносить (по умолчанию pbo_sources рядом с PBO)")
    s.add_argument("-e", "--ext", default="c,cpp,bin", help="расширения через запятую")
    s.add_argument("--text-bin", action="store_true", help="config.bin сохранять текстом (config.bin.cpp)")
    s.add_argument("--force", action="store_true", help="перезаписать уже вынесенные папки")

    s = sub.add_parser("pack-scripts", help="вернуть правки из pbo_sources обратно в PBO")
    s.add_argument("paths", nargs="*", default=["pbo_sources"],
                   help="папка мода или папка с несколькими модами (по умолчанию ./pbo_sources)")
    s.add_argument("--all", action="store_true", help="записать все файлы, а не только изменённые")
    s.add_argument("--pbo-dir", help="где лежат PBO, если их перенесли")
    s.add_argument("--key", help="переподписать этим .biprivatekey")
    s.add_argument("-v", "--sign-version", type=int, choices=(2, 3), default=3)
    s.add_argument("--no-backup", action="store_true", help="не создавать .bak")
    s.add_argument("--dry-run", action="store_true", help="только показать изменения")

    s = sub.add_parser("derap", help="config.bin → текст")
    s.add_argument("file")
    s.add_argument("-o", "--out", help="куда записать (по умолчанию stdout)")

    s = sub.add_parser("rap", help="текст config.cpp → config.bin")
    s.add_argument("file")
    s.add_argument("-o", "--out", help="файл .bin (по умолчанию рядом, с расширением .bin)")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    cmd = {"ls": "list", "rm": "delete", "mv": "rename"}.get(args.cmd, args.cmd)
    try:
        return _run(cmd, args)
    except (P.PboError, OSError, ValueError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1


def _run(cmd, args) -> int:
    if cmd == "pack":
        folder = args.folder.rstrip("/")
        out = args.out or folder + ".pbo"
        a = P.pack_folder(folder, out, prefix=args.prefix, backup=os.path.exists(out),
                          progress=_progress("Упаковка"), rapify_cpp=not args.no_rapify,
                          obfuscate_scripts=args.obfuscate,
                          log=lambda m: print(m, file=sys.stderr))
        print(f"{out}: {len(a.entries)} файлов, префикс: {a.prefix or '—'}")
        return 0

    if cmd in ("extract-scripts", "pack-scripts"):
        return _run_sources(cmd, args)
    if cmd in ("classes", "keygen", "sign", "checksign", "derap", "rap"):
        return _run_tools(cmd, args)

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
        a.extract_all(out, _progress("Распаковка"), debinarize=not args.no_debinarize,
                      log=lambda m: print(m, file=sys.stderr))
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


def _run_tools(cmd, args) -> int:
    from . import bisign as B
    from . import classes as K
    from . import config as C

    if cmd == "classes":
        errors: list = []
        found = K.scan_path(args.path, errors=errors) if args.path else []
        pool = found + (K.builtin_classes() if args.builtin or not args.path else [])
        K.ClassIndex(pool)
        if args.read_class:
            name = args.read_class.lower()
            shown = 0
            for c in pool:
                if c.name.lower() != name or not c.line:
                    continue
                text = _read_source(c)
                if text is None:
                    continue
                print("=" * 80)
                print(f"{c.name}  ({c.location}:{c.line})")
                print("=" * 80)
                print(K.class_source(text, c.line))
                shown += 1
            if not shown:
                raise P.PboError(f"Класс не найден: {args.read_class}")
            return 0
        res = K.search(pool, args.search, "desc" if args.desc else "name")
        for c in res:
            print(f"{c.name:<36} {(c.parent or '-'):<26} {c.kind_label:<7} "
                  f"{c.location + (':' + str(c.line) if c.line else ''):<50} {c.description}")
        print(f"Найдено классов: {len(res)}", file=sys.stderr)
        for e in errors:
            print(f"[ошибка] {e}", file=sys.stderr)
    elif cmd == "keygen":
        os.makedirs(args.out, exist_ok=True)
        key = B.generate_key(args.name)
        priv = os.path.join(args.out, args.name + ".biprivatekey")
        pub = os.path.join(args.out, args.name + ".bikey")
        if os.path.exists(priv):
            raise P.PboError(f"Уже существует: {priv}")
        key.save(priv)
        key.public.save(pub)
        print(priv)
        print(pub)
    elif cmd == "sign":
        key = B.PrivateKey.load(args.key)
        for p in args.pbos:
            print(B.sign_pbo(p, key, args.version))
    elif cmd == "checksign":
        pub = B.PublicKey.load(args.bikey)
        path = args.bisign or B.bisign_path(os.path.abspath(args.pbo), pub.name)
        ok = B.verify_pbo(args.pbo, pub, B.Signature.load(path))
        print("подпись верна" if ok else "ПОДПИСЬ НЕ СОВПАДАЕТ")
        return 0 if ok else 2
    elif cmd == "derap":
        with open(args.file, "rb") as f:
            text = C.derapify(f.read())
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            sys.stdout.write(text)
    elif cmd == "rap":
        with open(args.file, encoding="utf-8-sig") as f:
            data = C.rapify(f.read())
        out = args.out or os.path.splitext(args.file)[0] + ".bin"
        with open(out, "wb") as f:
            f.write(data)
        print(out)
    return 0


def _read_source(c):
    from . import classes as K
    try:
        if c.pbo:
            a = P.PboArchive.open(c.pbo)
            e = a.find(c.file)
            return K.file_text(a.read(e)) if e else None
        with open(c.file, "rb") as f:
            return K.file_text(f.read())
    except (OSError, P.PboError):
        return None


def _run_sources(cmd, args) -> int:
    from . import bisign as B
    from . import sources as S

    if cmd == "extract-scripts":
        pbos = []
        for p in args.paths:
            pbos += S.list_pbos(p) if os.path.isdir(p) else [p]
        if not pbos:
            raise P.PboError("PBO не найдены")
        exts = [x.strip() for x in args.ext.split(",") if x.strip()]
        total, errors = S.extract_many(pbos, args.out, exts, args.text_bin, args.force, print)
        print(f"Готово: файлов {total}, ошибок {errors}")
    else:
        dirs = []
        for p in args.paths:
            dirs += S.mod_dirs(p)
        if not dirs:
            raise P.PboError(f"Нет папок с {S.MANIFEST} — сначала extract-scripts")
        key = B.PrivateKey.load(args.key) if args.key else None
        changed, errors = S.pack_many(dirs, args.pbo_dir, args.all, not args.no_backup, args.dry_run,
                                      key, args.sign_version, print)
        print(f"Готово: изменено PBO {changed}, ошибок {errors}")
    return 1 if errors else 0
