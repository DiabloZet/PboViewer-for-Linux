"""Чтение и запись архивов PBO (DayZ / Arma).

Формат:
    [заголовок-подпись: "" + 'Vers' + 4*int32] [свойства: пары cstring, завершаются ""]
    [записи файлов: cstring имя + 5*int32]... [пустая запись-терминатор]
    [данные файлов подряд, в порядке записей]
    [0x00 + SHA1(всего предыдущего содержимого)]

Изменение архива (добавление / замена / удаление / переименование) выполняется
потоково: данные незатронутых файлов копируются блоками прямо из исходного PBO
в новый временный файл, без распаковки на диск. Затем временный файл атомарно
подменяет исходный.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import time
from dataclasses import dataclass, replace as dc_replace
from typing import Callable, Iterable, Optional

VERS = 0x56657273  # 'sreV' — заголовок со свойствами
CPRS = 0x43707273  # 'srpC' — сжатие LZSS
ENCR = 0x456E6372  # 'rcnE' — зашифровано (не поддерживается)

CHUNK = 1024 * 1024
INT32_MAX = 0x7FFFFFFF

Progress = Optional[Callable[[int, int], None]]


class PboError(Exception):
    pass


# ---------------------------------------------------------------------------
# Пути внутри PBO
# ---------------------------------------------------------------------------

def to_pbo_path(path: str) -> str:
    """Приводит путь к виду PBO: разделитель '\\', без ведущих/хвостовых слэшей."""
    parts = [p for p in path.replace("/", "\\").split("\\") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PboError(f"Недопустимый путь внутри PBO: {path}")
    return "\\".join(parts)


def path_key(path: str) -> str:
    """Ключ сравнения: DayZ не различает регистр и тип слэша."""
    return to_pbo_path(path).lower()


def join_pbo(folder: str, name: str) -> str:
    folder = to_pbo_path(folder) if folder else ""
    name = to_pbo_path(name)
    return f"{folder}\\{name}" if folder else name


def safe_relpath(pbo_path: str) -> str:
    """Путь для записи на диск: без '..', абсолютных путей и пустых частей."""
    parts = [p for p in pbo_path.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return os.path.join(*parts) if parts else ""


def decode_name(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


def encode_name(name: str) -> bytes:
    return name.encode("utf-8")


# ---------------------------------------------------------------------------
# LZSS (упаковка Bohemia Interactive)
# ---------------------------------------------------------------------------

def lzss_decompress(data: bytes, out_len: int) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while len(out) < out_len and i < n:
        flags = data[i]
        i += 1
        for _ in range(8):
            if len(out) >= out_len or i >= n:
                break
            if flags & 1:
                out.append(data[i])
                i += 1
            else:
                if i + 1 >= n:
                    break
                b1, b2 = data[i], data[i + 1]
                i += 2
                rpos = len(out) - (b1 | ((b2 & 0xF0) << 4))
                rlen = (b2 & 0x0F) + 3
                for k in range(rlen):
                    if len(out) >= out_len:
                        break
                    p = rpos + k
                    out.append(0x20 if p < 0 else out[p])
            flags >>= 1
    if len(out) != out_len:
        raise PboError("Ошибка распаковки LZSS: неожиданный конец данных")
    return bytes(out)


# ---------------------------------------------------------------------------
# Модель
# ---------------------------------------------------------------------------

@dataclass
class PboEntry:
    name: str
    raw_name: bytes
    method: int = 0
    original_size: int = 0
    reserved: int = 0
    timestamp: int = 0
    data_size: int = 0
    offset: int = 0              # смещение данных в исходном PBO
    src_path: Optional[str] = None  # если задан — данные берутся из файла на диске

    @property
    def compressed(self) -> bool:
        if self.method == CPRS:
            return True
        return self.method == 0 and self.original_size not in (0, self.data_size)

    @property
    def size(self) -> int:
        """Реальный (распакованный) размер."""
        return self.original_size if self.compressed else self.data_size

    @property
    def key(self) -> str:
        return path_key(self.name)

    @property
    def basename(self) -> str:
        return self.name.rsplit("\\", 1)[-1]


class _Reader:
    """Буферизированное чтение заголовка."""

    def __init__(self, f):
        self.f = f
        self.buf = b""
        self.pos = 0
        self.consumed = 0

    def _fill(self, need: int) -> None:
        while len(self.buf) - self.pos < need:
            chunk = self.f.read(65536)
            if not chunk:
                raise PboError("Файл повреждён: заголовок обрывается")
            self.buf = self.buf[self.pos:] + chunk
            self.pos = 0

    def cstr(self) -> bytes:
        while True:
            idx = self.buf.find(b"\0", self.pos)
            if idx >= 0:
                s = self.buf[self.pos:idx]
                self.consumed += idx + 1 - self.pos
                self.pos = idx + 1
                return s
            if len(self.buf) - self.pos > 1024 * 1024:
                raise PboError("Файл повреждён: слишком длинная строка в заголовке")
            chunk = self.f.read(65536)
            if not chunk:
                raise PboError("Файл повреждён: заголовок обрывается")
            self.buf = self.buf[self.pos:] + chunk
            self.pos = 0

    def ints(self, count: int) -> tuple:
        self._fill(4 * count)
        vals = struct.unpack_from(f"<{count}I", self.buf, self.pos)
        self.pos += 4 * count
        self.consumed += 4 * count
        return vals


class PboArchive:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.has_header = True
        self.header_fields = (VERS, 0, 0, 0, 0)
        self.props: list[tuple[str, str]] = []
        self.entries: list[PboEntry] = []
        self.terminator_fields = (0, 0, 0, 0, 0)
        self.data_end = 0
        self.stored_checksum: Optional[bytes] = None
        self.file_size = 0

    # ---------------- чтение ----------------

    @classmethod
    def open(cls, path: str) -> "PboArchive":
        a = cls(os.path.abspath(path))
        a._load()
        return a

    def reload(self) -> None:
        self._load()

    def _load(self) -> None:
        self.props = []
        self.entries = []
        self.has_header = False
        self.stored_checksum = None
        self.file_size = os.path.getsize(self.path)
        with open(self.path, "rb") as f:
            r = _Reader(f)
            first = True
            while True:
                raw = r.cstr()
                fields = r.ints(5)
                method = fields[0]
                if raw == b"":
                    if first and method == VERS:
                        self.has_header = True
                        self.header_fields = fields
                        while True:
                            k = r.cstr()
                            if k == b"":
                                break
                            v = r.cstr()
                            self.props.append((decode_name(k), decode_name(v)))
                        first = False
                        continue
                    self.terminator_fields = fields
                    break
                first = False
                self.entries.append(PboEntry(
                    name=decode_name(raw), raw_name=raw, method=method,
                    original_size=fields[1], reserved=fields[2],
                    timestamp=fields[3], data_size=fields[4]))
            offset = r.consumed
        for e in self.entries:
            e.offset = offset
            offset += e.data_size
        self.data_end = offset
        if offset > self.file_size:
            raise PboError("Файл повреждён: данные выходят за конец файла")
        if self.file_size - offset >= 21:
            with open(self.path, "rb") as f:
                f.seek(offset)
                tail = f.read(21)
            if tail[0] == 0:
                self.stored_checksum = tail[1:]

    # ---------------- свойства ----------------

    def get_prop(self, key: str) -> Optional[str]:
        for k, v in self.props:
            if k.lower() == key.lower():
                return v
        return None

    @property
    def prefix(self) -> Optional[str]:
        return self.get_prop("prefix")

    @property
    def total_size(self) -> int:
        return sum(e.size for e in self.entries)

    def find(self, name: str) -> Optional[PboEntry]:
        k = path_key(name)
        for e in self.entries:
            if e.key == k:
                return e
        return None

    def under(self, folder: str) -> list[PboEntry]:
        """Все файлы внутри папки (рекурсивно). Пустая строка — весь архив."""
        k = path_key(folder)
        if not k:
            return list(self.entries)
        pre = k + "\\"
        return [e for e in self.entries if e.key.startswith(pre)]

    def list_dir(self, folder: str) -> tuple[list[str], list[PboEntry]]:
        """Подпапки (имена) и файлы непосредственно в папке."""
        k = path_key(folder)
        pre = k + "\\" if k else ""
        dirs: dict[str, str] = {}
        files = []
        for e in self.entries:
            ek = e.key
            if not ek.startswith(pre):
                continue
            rest_k = ek[len(pre):]
            rest = to_pbo_path(e.name)[len(pre):]
            if "\\" in rest_k:
                d = rest.split("\\", 1)[0]
                dirs.setdefault(d.lower(), d)
            else:
                files.append(e)
        return sorted(dirs.values(), key=str.lower), files

    # ---------------- извлечение ----------------

    def iter_data(self, entry: PboEntry, f=None) -> Iterable[bytes]:
        """Отдаёт распакованные данные записи блоками."""
        own = f is None
        if own:
            f = open(self.path, "rb")
        try:
            f.seek(entry.offset)
            if entry.method == ENCR:
                raise PboError(f"{entry.name}: зашифрованные файлы не поддерживаются")
            if entry.compressed:
                data = f.read(entry.data_size)
                yield lzss_decompress(data, entry.original_size)
                return
            remaining = entry.data_size
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    raise PboError("Неожиданный конец файла PBO")
                remaining -= len(chunk)
                yield chunk
        finally:
            if own:
                f.close()

    def read(self, entry: PboEntry) -> bytes:
        return b"".join(self.iter_data(entry))

    def extract(self, items: list[tuple[PboEntry, str]], progress: Progress = None,
                set_mtime: bool = True) -> None:
        """items: [(запись, путь назначения на диске)]."""
        total = sum(e.data_size for e, _ in items) or 1
        done = 0
        with open(self.path, "rb") as f:
            for e, dest in items:
                d = os.path.dirname(dest)
                if d:
                    os.makedirs(d, exist_ok=True)
                with open(dest, "wb") as out:
                    for chunk in self.iter_data(e, f):
                        out.write(chunk)
                if set_mtime and e.timestamp > 0:
                    try:
                        os.utime(dest, (e.timestamp, e.timestamp))
                    except OSError:
                        pass
                done += e.data_size
                if progress:
                    progress(done, total)

    def extract_all(self, dest_dir: str, progress: Progress = None,
                    write_props: bool = True) -> None:
        os.makedirs(dest_dir, exist_ok=True)
        if write_props:
            for k, v in self.props:
                name = f"${k.upper()}$"
                with open(os.path.join(dest_dir, name), "w", encoding="utf-8") as fh:
                    fh.write(v)
        items = [(e, os.path.join(dest_dir, safe_relpath(e.name)))
                 for e in self.entries if safe_relpath(e.name)]
        self.extract(items, progress)

    def verify(self, progress: Progress = None) -> Optional[bool]:
        """True/False — совпадает ли SHA1; None — контрольной суммы нет."""
        if self.stored_checksum is None:
            return None
        h = hashlib.sha1()
        total = self.data_end or 1
        done = 0
        with open(self.path, "rb") as f:
            remaining = self.data_end
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        return h.digest() == self.stored_checksum

    # ---------------- изменение ----------------

    def edit(self) -> "EditPlan":
        return EditPlan(self)


def _file_timestamp(path: str) -> int:
    try:
        return int(os.stat(path).st_mtime)
    except OSError:
        return int(time.time())


class EditPlan:
    """Набор изменений, применяемый одним проходом записи (commit)."""

    def __init__(self, archive: Optional[PboArchive]):
        self.archive = archive
        if archive is not None:
            self.items = [dc_replace(e) for e in archive.entries]
            self.props = list(archive.props)
            self.has_header = archive.has_header
            self.header_fields = archive.header_fields
            self.terminator_fields = archive.terminator_fields
        else:
            self.items = []
            self.props = []
            self.has_header = True
            self.header_fields = (VERS, 0, 0, 0, 0)
            self.terminator_fields = (0, 0, 0, 0, 0)

    # -- запросы --

    def index(self, name: str) -> int:
        k = path_key(name)
        for i, e in enumerate(self.items):
            if e.key == k:
                return i
        return -1

    def exists(self, name: str) -> bool:
        return self.index(name) >= 0

    def folder_exists(self, folder: str) -> bool:
        pre = path_key(folder) + "\\"
        return any(e.key.startswith(pre) for e in self.items)

    # -- операции --

    def add_file(self, internal_name: str, src_path: str) -> str:
        """Добавляет файл или заменяет существующий. Возвращает 'added' / 'replaced'."""
        internal_name = to_pbo_path(internal_name)
        if not internal_name:
            raise PboError("Пустое имя файла")
        size = os.path.getsize(src_path)
        if size > INT32_MAX:
            raise PboError(f"{os.path.basename(src_path)}: файлы больше 2 ГБ не поддерживаются")
        i = self.index(internal_name)
        new = PboEntry(name=internal_name, raw_name=encode_name(internal_name),
                       method=0, original_size=size, reserved=0,
                       timestamp=_file_timestamp(src_path), data_size=size,
                       src_path=os.path.abspath(src_path))
        if i >= 0:
            old = self.items[i]
            # имя и регистр сохраняем как в архиве
            new.name, new.raw_name = old.name, old.raw_name
            self.items[i] = new
            return "replaced"
        self.items.append(new)
        return "added"

    def add_tree(self, src_dir: str, internal_folder: str) -> list[tuple[str, str]]:
        """Добавляет папку рекурсивно. Возвращает [(внутренний путь, результат)]."""
        results = []
        for root, dirs, files in os.walk(src_dir):
            dirs.sort()
            for fn in sorted(files):
                full = os.path.join(root, fn)
                if not os.path.isfile(full):
                    continue
                rel = os.path.relpath(full, src_dir)
                name = join_pbo(internal_folder, rel)
                results.append((name, self.add_file(name, full)))
        return results

    def delete(self, name: str) -> int:
        """Удаляет файл или папку целиком. Возвращает число удалённых файлов."""
        k = path_key(name)
        pre = k + "\\"
        before = len(self.items)
        self.items = [e for e in self.items if not (e.key == k or e.key.startswith(pre))]
        return before - len(self.items)

    def rename(self, old: str, new: str) -> int:
        """Переименовывает/перемещает файл или папку. Возвращает число затронутых файлов."""
        old_p, new_p = to_pbo_path(old), to_pbo_path(new)
        if not new_p:
            raise PboError("Пустое имя")
        ok, nk = old_p.lower(), new_p.lower()
        if ok == nk and old_p == new_p:
            return 0
        if nk.startswith(ok + "\\"):
            raise PboError("Нельзя переместить папку внутрь самой себя")
        pre = ok + "\\"
        count = 0
        moved_keys = set()
        for i, e in enumerate(self.items):
            ek = e.key
            if ek == ok:
                target = new_p
            elif ek.startswith(pre):
                target = new_p + to_pbo_path(e.name)[len(old_p):]
            else:
                continue
            self.items[i] = dc_replace(e, name=target, raw_name=encode_name(target))
            moved_keys.add(path_key(target))
            count += 1
        if count:
            # конфликт: в месте назначения уже есть файлы с такими именами
            seen = set()
            for e in self.items:
                if e.key in seen:
                    raise PboError(f"Файл уже существует: {e.name}")
                seen.add(e.key)
        return count

    def set_prop(self, key: str, value: Optional[str]) -> None:
        self.props = [(k, v) for k, v in self.props if k.lower() != key.lower()]
        if value:
            self.props.append((key, value))

    # -- запись --

    def commit(self, dest: Optional[str] = None, backup: bool = False,
               progress: Progress = None) -> PboArchive:
        src_path = self.archive.path if self.archive else None
        dest = os.path.abspath(dest or src_path)
        if not dest:
            raise PboError("Не задан путь назначения")
        props = self.props
        has_header = self.has_header or bool(props)

        # актуализируем размеры внешних файлов прямо перед записью
        items = []
        for e in self.items:
            if e.src_path:
                size = os.path.getsize(e.src_path)
                e = dc_replace(e, original_size=size, data_size=size)
            items.append(e)

        d = os.path.dirname(dest) or "."
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, f".{os.path.basename(dest)}.{os.getpid()}.tmp")
        total = sum(e.data_size for e in items) or 1
        done = 0
        h = hashlib.sha1()

        def w(out, data: bytes):
            h.update(data)
            out.write(data)

        src = open(src_path, "rb") if src_path and any(not e.src_path for e in items) else None
        try:
            with open(tmp, "wb") as out:
                if has_header:
                    w(out, b"\0" + struct.pack("<5I", *self.header_fields))
                    for k, v in props:
                        w(out, encode_name(k) + b"\0" + encode_name(v) + b"\0")
                    w(out, b"\0")
                hdr = bytearray()
                for e in items:
                    hdr += e.raw_name + b"\0"
                    hdr += struct.pack("<5I", e.method, e.original_size, e.reserved,
                                       e.timestamp, e.data_size)
                hdr += b"\0" + struct.pack("<5I", *self.terminator_fields)
                w(out, bytes(hdr))

                for e in items:
                    if e.src_path:
                        fin = open(e.src_path, "rb")
                        close = True
                    else:
                        fin = src
                        fin.seek(e.offset)
                        close = False
                    try:
                        remaining = e.data_size
                        while remaining > 0:
                            chunk = fin.read(min(CHUNK, remaining))
                            if not chunk:
                                raise PboError(f"{e.name}: неожиданный конец данных")
                            w(out, chunk)
                            remaining -= len(chunk)
                            done += len(chunk)
                            if progress:
                                progress(done, total)
                    finally:
                        if close:
                            fin.close()
                out.write(b"\0" + h.digest())
                out.flush()
                os.fsync(out.fileno())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        finally:
            if src:
                src.close()

        if os.path.exists(dest):
            try:
                shutil.copymode(dest, tmp)
            except OSError:
                pass
            if backup:
                bak = dest + ".bak"
                try:
                    if os.path.lexists(bak):
                        os.unlink(bak)
                    os.link(dest, bak)  # мгновенно, старые данные остаются в .bak
                except OSError:
                    shutil.copy2(dest, bak)
        os.replace(tmp, dest)

        if self.archive is not None and self.archive.path == dest:
            self.archive.reload()
            return self.archive
        return PboArchive.open(dest)


# ---------------------------------------------------------------------------
# Упаковка папки
# ---------------------------------------------------------------------------

def read_folder_props(folder: str) -> list[tuple[str, str]]:
    """Читает $PBOPREFIX$ / $PREFIX$ / $PRODUCT$ ... из корня папки."""
    props: list[tuple[str, str]] = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return props
    for fn in names:
        full = os.path.join(folder, fn)
        base = fn[:-4] if fn.lower().endswith(".txt") else fn
        if not (len(base) > 2 and base.startswith("$") and base.endswith("$")) or not os.path.isfile(full):
            continue
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip().lstrip("﻿")
        key = base.strip("$").lower()
        if key == "pboprefix":
            key = "prefix"
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines and all("=" in ln for ln in lines):
            # формат key=value (как у Mikero)
            for ln in lines:
                k, v = ln.split("=", 1)
                props = [(a, b) for a, b in props if a.lower() != k.strip().lower()]
                props.append((k.strip().lower(), v.strip()))
        elif lines:
            props = [(a, b) for a, b in props if a.lower() != key]
            props.append((key, lines[0]))
    # prefix — первым
    props.sort(key=lambda kv: 0 if kv[0] == "prefix" else 1)
    return props


def is_prop_file(folder: str, path: str) -> bool:
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(folder):
        return False
    fn = os.path.basename(path)
    base = fn[:-4] if fn.lower().endswith(".txt") else fn
    return len(base) > 2 and base.startswith("$") and base.endswith("$")


def pack_folder(folder: str, dest: str, prefix: Optional[str] = None,
                backup: bool = False, progress: Progress = None) -> PboArchive:
    folder = os.path.abspath(folder)
    plan = EditPlan(None)
    plan.props = read_folder_props(folder)
    if prefix is not None:
        plan.set_prop("prefix", prefix)
    dest_abs = os.path.abspath(dest)
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for fn in sorted(files):
            full = os.path.join(root, fn)
            if os.path.abspath(full) == dest_abs or not os.path.isfile(full):
                continue
            if is_prop_file(folder, full):
                continue
            plan.add_file(os.path.relpath(full, folder), full)
    return plan.commit(dest, backup=backup, progress=progress)


def human_size(n: int) -> str:
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{int(f)} {u}" if u == "Б" else f"{f:.1f} {u}".replace(".", ",")
        f /= 1024
    return str(n)
