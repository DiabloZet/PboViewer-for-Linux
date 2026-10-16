"""Вынос скриптов и конфигов из PBO в папку и возврат правок обратно.

extract_pbo / extract_folder: файлы .c, .cpp, .bin (настраивается) каждого PBO копируются в
<папка_pbo>/pbo_sources/<имя_pbo>/ с путями как внутри архива; рядом пишется .pbo-source.json.

pack_mod / pack_all: изменённые и новые файлы из этой папки вписываются в исходный PBO на месте
(остальное содержимое архива не трогается). config.bin.cpp (вынос текстом) собирается в config.bin.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from typing import Callable, Optional

from . import bisign as B
from . import config as C
from . import pbo as P

MANIFEST = ".pbo-source.json"
OUT_DIR = "pbo_sources"
DEFAULT_EXTS = ("c", "cpp", "bin")
TEXT_BIN_SUFFIX = ".cpp"  # config.bin -> config.bin.cpp

Log = Callable[[str], None]


class SourcesError(Exception):
    pass


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _noop(_msg: str) -> None:
    pass


def default_out(pbo_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(pbo_path)), OUT_DIR)


def list_pbos(folder: str) -> list[str]:
    folder = os.path.abspath(folder)
    return sorted(os.path.join(folder, n) for n in os.listdir(folder)
                  if n.lower().endswith(".pbo") and os.path.isfile(os.path.join(folder, n)))


def is_mod_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, MANIFEST))


def mod_dirs(root: str) -> list[str]:
    """Папки модов: сама root (если в ней манифест) или её подпапки с манифестом."""
    root = os.path.abspath(root)
    if is_mod_dir(root):
        return [root]
    if not os.path.isdir(root):
        return []
    return sorted(os.path.join(root, d) for d in os.listdir(root) if is_mod_dir(os.path.join(root, d)))


# ---------------------------------------------------------------------------
# Вынос
# ---------------------------------------------------------------------------

def extract_pbo(path: str, out_root: Optional[str] = None, exts=DEFAULT_EXTS, text_bin: bool = False,
                force: bool = False, log: Log = _noop) -> int:
    """Возвращает число вынесенных файлов (0 — нечего выносить или папка уже есть)."""
    exts = {e.lower().lstrip(".") for e in exts}
    archive = P.PboArchive.open(path)
    out_root = out_root or default_out(archive.path)
    name = os.path.basename(archive.path)
    dest = os.path.join(out_root, os.path.splitext(name)[0])
    if is_mod_dir(dest) and not force:
        log(f"{name}: пропущен — {dest} уже есть (правки не трогаю)")
        return 0
    files = {}
    with open(archive.path, "rb") as f:
        for e in archive.entries:
            ext = e.basename.rsplit(".", 1)[-1].lower() if "." in e.basename else ""
            rel = P.safe_relpath(e.name)
            if ext not in exts or not rel:
                continue
            data = b"".join(archive.iter_data(e, f))
            as_text = text_bin and ext == "bin" and C.is_rapified(data)
            if as_text:
                data = C.derapify(data).encode("utf-8")
                rel += TEXT_BIN_SUFFIX
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as out:
                out.write(data)
            files[P.to_pbo_path(e.name)] = {"file": rel.replace(os.sep, "/"), "sha1": _sha1(data),
                                            "bin_text": as_text}
    if not files:
        log(f"{name}: нет файлов {', '.join(sorted(exts))}")
        return 0
    manifest = {
        "pbo": archive.path,
        "pbo_name": name,
        "prefix": archive.prefix or "",
        "pbo_sha1": archive.stored_checksum.hex() if archive.stored_checksum else "",
        "files": files,
    }
    with open(os.path.join(dest, MANIFEST), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    log(f"{name} → {dest} ({len(files)} файл(ов))")
    return len(files)


def extract_many(pbos: list[str], out_root: Optional[str] = None, exts=DEFAULT_EXTS,
                 text_bin: bool = False, force: bool = False, log: Log = _noop,
                 progress: Optional[Callable[[int, int], None]] = None) -> tuple[int, int]:
    """(вынесено файлов, ошибок)."""
    total = errors = 0
    for i, p in enumerate(pbos):
        try:
            total += extract_pbo(p, out_root, exts, text_bin, force, log)
        except (P.PboError, C.ConfigError, OSError) as e:
            errors += 1
            log(f"ОШИБКА {os.path.basename(p)}: {e}")
        if progress:
            progress(i + 1, len(pbos))
    return total, errors


# ---------------------------------------------------------------------------
# Возврат в PBO
# ---------------------------------------------------------------------------

def pack_mod(mod_dir: str, pbo_dir: Optional[str] = None, include_all: bool = False,
             backup: bool = True, dry_run: bool = False, key: Optional[B.PrivateKey] = None,
             sign_version: int = 3, log: Log = _noop) -> bool:
    """True — PBO изменён."""
    with open(os.path.join(mod_dir, MANIFEST), encoding="utf-8") as fh:
        manifest = json.load(fh)
    name = manifest["pbo_name"]
    pbo_path = os.path.join(pbo_dir, name) if pbo_dir else manifest["pbo"]
    if not os.path.isfile(pbo_path):
        # PBO могли перенести вместе с папкой pbo_sources
        near = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(mod_dir))), name)
        if not pbo_dir and os.path.isfile(near):
            pbo_path = near
        else:
            raise SourcesError(f"{name}: не найден {pbo_path}")
    tmp = tempfile.mkdtemp(prefix="pbo-pack-")
    try:
        return _pack(mod_dir, manifest, pbo_path, include_all, backup, dry_run, key, sign_version, log, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _pack(mod_dir, manifest, pbo_path, include_all, backup, dry_run, key, sign_version, log, tmp) -> bool:
    name = manifest["pbo_name"]
    archive = P.PboArchive.open(pbo_path)
    if manifest.get("pbo_sha1") and archive.stored_checksum and \
            archive.stored_checksum.hex() != manifest["pbo_sha1"]:
        log(f"{name}: внимание — PBO изменился после выноса, заменяю только файлы из папки")
    known = {v["file"]: (inner, v) for inner, v in manifest["files"].items()}
    plan = archive.edit()
    changes = []
    for root, dirs, names in os.walk(mod_dir):
        dirs.sort()
        for n in sorted(names):
            full = os.path.join(root, n)
            rel = os.path.relpath(full, mod_dir).replace(os.sep, "/")
            if rel == MANIFEST:
                continue
            with open(full, "rb") as fh:
                data = fh.read()
            if rel in known:
                inner, info = known[rel]
                if not include_all and _sha1(data) == info["sha1"]:
                    continue
                bin_text = bool(info.get("bin_text"))
            else:
                bin_text = rel.endswith(".bin" + TEXT_BIN_SUFFIX)
                inner = P.to_pbo_path(rel[:-len(TEXT_BIN_SUFFIX)] if bin_text else rel)
            src = full
            if bin_text:
                try:
                    binary = C.rapify(data.decode("utf-8-sig"))
                except C.ConfigError as e:
                    raise SourcesError(f"{name}: {rel}: {e} — PBO не изменён") from None
                src = os.path.join(tmp, f"{len(changes)}.bin")
                with open(src, "wb") as out:
                    out.write(binary)
            res = plan.add_file(inner, src)
            changes.append((rel, inner, data, res, bin_text))
    if not changes:
        log(f"{name}: изменений нет")
        return False
    log(f"{name}: изменённых файлов {len(changes)}")
    for rel, inner, _d, res, _t in changes:
        log(f"  {'заменён' if res == 'replaced' else 'добавлен'}: {inner}")
    for rel, (inner, _i) in known.items():
        if not os.path.exists(os.path.join(mod_dir, rel)):
            log(f"  внимание: {rel} удалён из папки, в PBO оставлен как есть")
    if dry_run:
        log("  пробный запуск, PBO не изменён")
        return False
    plan.commit(backup=backup)
    archive = P.PboArchive.open(pbo_path)
    for rel, inner, data, _res, bin_text in changes:
        manifest["files"][P.to_pbo_path(inner)] = {"file": rel, "sha1": _sha1(data), "bin_text": bin_text}
    manifest["pbo"] = archive.path
    manifest["pbo_sha1"] = archive.stored_checksum.hex() if archive.stored_checksum else ""
    with open(os.path.join(mod_dir, MANIFEST), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    log(f"  записано в {name}" + (f" (старая версия — {name}.bak)" if backup else ""))
    if key is not None:
        log(f"  подпись: {os.path.basename(B.sign_pbo(pbo_path, key, sign_version))}")
    return True


def pack_many(dirs: list[str], pbo_dir: Optional[str] = None, include_all: bool = False,
              backup: bool = True, dry_run: bool = False, key: Optional[B.PrivateKey] = None,
              sign_version: int = 3, log: Log = _noop,
              progress: Optional[Callable[[int, int], None]] = None) -> tuple[int, int]:
    """(изменено PBO, ошибок)."""
    changed = errors = 0
    for i, m in enumerate(dirs):
        try:
            changed += pack_mod(m, pbo_dir, include_all, backup, dry_run, key, sign_version, log)
        except (SourcesError, P.PboError, C.ConfigError, B.SignError, OSError, ValueError, KeyError) as e:
            errors += 1
            log(f"ОШИБКА {os.path.basename(m)}: {e}")
        if progress:
            progress(i + 1, len(dirs))
    return changed, errors


# ---------------------------------------------------------------------------
# Что делать с выделенным в файловом менеджере
# ---------------------------------------------------------------------------

def _uniq(items: list[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        k = os.path.abspath(x)
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def extract_targets(paths: list[str], whole_folder: bool) -> list[str]:
    """PBO для выноса. whole_folder=False: выделенные PBO (у папки — все PBO в ней);
    True: все PBO в папке выделенного PBO / в выделенной папке."""
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            out += list_pbos(p)
        elif whole_folder:
            out += list_pbos(os.path.dirname(p))
        else:
            out.append(p)
    return _uniq(out)


def pack_targets(paths: list[str], whole_folder: bool) -> list[str]:
    """Папки модов для возврата в PBO.

    PBO → его папка в pbo_sources (whole_folder: все моды в pbo_sources рядом);
    папка мода → она сама; папка с модами или с pbo_sources внутри → все моды в ней."""
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            src = default_out(p)
            if whole_folder:
                out += mod_dirs(src)
            else:
                one = os.path.join(src, os.path.splitext(os.path.basename(p))[0])
                out += [one] if is_mod_dir(one) else []
        elif is_mod_dir(p) and not whole_folder:
            out.append(p)
        elif is_mod_dir(p):
            out += mod_dirs(os.path.dirname(p))
        else:
            found = mod_dirs(p)
            if not found and os.path.isdir(os.path.join(p, OUT_DIR)):
                found = mod_dirs(os.path.join(p, OUT_DIR))
            out += found
    return _uniq(out)
