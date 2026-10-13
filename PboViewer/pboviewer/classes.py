"""Поиск классов в модах: скрипты Enforce (.c) и конфиги (config.cpp / config.bin).

scan_archive(PboArchive) / scan_path(папка или .pbo) -> список ClassInfo.
ClassIndex связывает найденные классы со встроенной базой DayZ (classdb), строит цепочку
наследования и даёт краткое описание (для чего используется родительский класс).
"""

from __future__ import annotations

import bisect
import os
import re
import textwrap
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from . import classdb
from . import config as C
from . import pbo as P

SCRIPT_EXT = (".c",)
CONFIG_EXT = (".cpp", ".hpp", ".h", ".inc", ".ext")
MAX_TEXT = 32 * 1024 * 1024


@dataclass
class ClassInfo:
    name: str
    parent: Optional[str]
    kind: str            # "script" | "modded" | "config" | "vanilla" | "builtin"
    file: str = ""       # путь внутри PBO (или путь на диске)
    line: int = 0
    section: str = ""    # для конфигов: CfgVehicles и т.п.
    pbo: str = ""        # путь к PBO на диске ("" — файл с диска или встроенная база)
    description: str = ""
    desc_from: str = ""  # класс, чьё описание показано
    chain: tuple = ()

    @property
    def kind_label(self) -> str:
        return {"script": "скрипт", "modded": "modded", "config": "конфиг",
                "vanilla": "DayZ", "builtin": "DayZ"}.get(self.kind, self.kind)

    @property
    def location(self) -> str:
        if self.pbo:
            return f"{os.path.basename(self.pbo)}\\{self.file}"
        return self.file


# ---------------------------------------------------------------------------
# Текст файла
# ---------------------------------------------------------------------------

def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


def file_text(data: bytes) -> str:
    """Текст файла; бинарный конфиг переводится в текст."""
    if C.is_rapified(data):
        return C.derapify(data)
    return decode_text(data)


def _blank_comments(text: str, strings: bool = False, backslash: bool = False) -> str:
    """Заменяет комментарии (и, по желанию, строки) пробелами, сохраняя позиции и переводы строк."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"' and text[j] != "\n":
                if text[j] == "\\" and backslash:
                    j += 1
                j += 1
            j = min(j + 1, n)
            out.append('"' + " " * (j - i - 2) + '"' if strings and j - i >= 2 else text[i:j])
            i = j
        else:
            k = i
            while k < n and text[k] not in '/"':
                k += 1
            if k == i:
                k += 1
            out.append(text[i:k])
            i = k
    return "".join(out)


class _Lines:
    def __init__(self, text: str):
        self.nl = [m.start() for m in re.finditer("\n", text)]

    def __call__(self, pos: int) -> int:
        return bisect.bisect_left(self.nl, pos) + 1


# ---------------------------------------------------------------------------
# Сканирование
# ---------------------------------------------------------------------------

SCRIPT_CLASS_RE = re.compile(
    r"^[ \t]*(modded[ \t]+)?(?:sealed[ \t]+)?class[ \t]+([A-Za-z_]\w*)"
    r"(?:[ \t]*<[^>\n]*>)?(?:\s*(?:extends|:)\s*([A-Za-z_]\w*))?(\s*;)?",
    re.MULTILINE)

CONFIG_TOKEN_RE = re.compile(r"\bclass\s+(\w+)\s*(?::\s*(\w+))?\s*([{;])|[{}]")


def scan_script(text: str, file: str, pbo: str = "") -> list[ClassInfo]:
    clean = _blank_comments(text, strings=True, backslash=True)
    line = _Lines(clean)
    out = []
    for m in SCRIPT_CLASS_RE.finditer(clean):
        if m.group(4):  # предварительное объявление «class X;»
            continue
        modded = bool(m.group(1))
        name = m.group(2)
        parent = m.group(3) or (name if modded else None)
        out.append(ClassInfo(name, parent, "modded" if modded else "script", file,
                             line(m.start(2)), pbo=pbo))
    return out


def scan_config(text: str, file: str, pbo: str = "", max_depth: int = 1) -> list[ClassInfo]:
    """Классы конфига: секции верхнего уровня (CfgVehicles…) и их прямые потомки."""
    clean = _blank_comments(text, strings=True)
    line = _Lines(clean)
    stack: list[Optional[str]] = []
    out = []
    for m in CONFIG_TOKEN_RE.finditer(clean):
        tok = m.group(0)
        if tok == "{":
            stack.append(None)
        elif tok == "}":
            if stack:
                stack.pop()
        else:
            name, parent, brace = m.group(1), m.group(2), m.group(3)
            path = [s for s in stack if s is not None]
            if brace == "{":
                if len(stack) == len(path) and len(path) <= max_depth and len(path) >= 1:
                    out.append(ClassInfo(name, parent, "config", file, line(m.start(1)),
                                         section=" > ".join(path), pbo=pbo))
                stack.append(name if len(stack) == len(path) else None)
    return out


def is_script(name: str) -> bool:
    return name.lower().endswith(SCRIPT_EXT)


def is_config(name: str) -> bool:
    n = name.lower()
    return n.endswith(CONFIG_EXT) or n.endswith(".bin") and os.path.basename(n).startswith("config")


def scan_bytes(data: bytes, file: str, pbo: str = "") -> list[ClassInfo]:
    if is_script(file):
        return scan_script(decode_text(data), file, pbo)
    if C.is_rapified(data):
        return scan_config(C.derapify(data), file, pbo)
    if is_config(file):
        return scan_config(decode_text(data), file, pbo)
    return []


def scan_archive(archive: P.PboArchive, errors: Optional[list] = None) -> list[ClassInfo]:
    out = []
    with open(archive.path, "rb") as f:
        for e in archive.entries:
            if not (is_script(e.name) or is_config(e.name)) or e.size > MAX_TEXT:
                continue
            try:
                data = b"".join(archive.iter_data(e, f))
                out += scan_bytes(data, e.name, archive.path)
            except Exception as ex:  # noqa: BLE001
                if errors is not None:
                    errors.append(f"{e.name}: {ex}")
    return out


def scan_path(path: str, progress: Optional[Callable[[int, int], None]] = None,
              errors: Optional[list] = None) -> list[ClassInfo]:
    """Рекурсивно: все .pbo в папке, а также распакованные .c / config.cpp / config.bin."""
    path = os.path.abspath(path)
    if os.path.isfile(path):
        files = [path]
    else:
        files = []
        for root, dirs, names in os.walk(path):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for n in sorted(names):
                low = n.lower()
                if low.endswith(".pbo") or is_script(low) or is_config(low):
                    files.append(os.path.join(root, n))
    out = []
    for i, f in enumerate(files):
        try:
            if f.lower().endswith(".pbo"):
                out += scan_archive(P.PboArchive.open(f), errors)
            elif os.path.getsize(f) <= MAX_TEXT:
                with open(f, "rb") as fh:
                    out += scan_bytes(fh.read(), f)
        except Exception as ex:  # noqa: BLE001
            if errors is not None:
                errors.append(f"{f}: {ex}")
        if progress:
            progress(i + 1, len(files))
    return out


def builtin_classes() -> list[ClassInfo]:
    """Встроенная база: описанные классы, иерархия скриптов и ванильные конфиги DayZ."""
    out, seen = [], set()
    for rows in classdb.vanilla().values():
        for name, parent, section, file in rows:
            out.append(ClassInfo(name, parent or None, "vanilla", file, section=section))
            seen.add(name.lower())
    for name in classdb.builtin_names():
        if name.lower() not in seen:
            out.append(ClassInfo(name, classdb.script_parent(name), "builtin", "встроенная база",
                                 section="скрипты DayZ"))
    return out


# ---------------------------------------------------------------------------
# Индекс: цепочки наследования и описания
# ---------------------------------------------------------------------------

class ClassIndex:
    def __init__(self, classes: Iterable[ClassInfo] = ()):
        self.classes: list[ClassInfo] = []
        self.by_name: dict[str, list[ClassInfo]] = {}
        self.add(classes)

    def add(self, classes: Iterable[ClassInfo]) -> None:
        new = list(classes)
        for c in new:
            self.by_name.setdefault(c.name.lower(), []).append(c)
        self.classes += new
        for c in new:
            self.resolve(c)

    def parent_of(self, name: str, section: str = "") -> Optional[str]:
        k = name.lower()
        sec = section.split(" > ")[0].lower()
        cands = [c for c in self.by_name.get(k, ()) if c.kind != "modded"]
        if sec:  # конфиг: сначала классы той же секции
            cands = [c for c in cands if c.section.split(" > ")[0].lower() == sec] or cands
        else:    # скрипт: сначала скриптовые классы
            cands = [c for c in cands if c.kind == "script"] or cands
        for c in cands:
            if c.parent and c.parent.lower() != k:
                return c.parent
        rows = classdb.vanilla().get(k, ())
        if sec:
            for r in rows:
                if r[2].lower() == sec and r[1]:
                    return r[1]
        sp = classdb.script_parent(name)
        if sp:
            return sp
        for r in rows:
            if r[1]:
                return r[1]
        return None

    def chain(self, info: ClassInfo) -> list[str]:
        out = [info.name]
        seen = {info.name.lower()}
        cur = info.parent
        if cur and cur.lower() == info.name.lower():  # modded class X — родитель сам X
            cur = self.parent_of(info.name, info.section)
        while cur and cur.lower() not in seen and len(out) < 40:
            out.append(cur)
            seen.add(cur.lower())
            cur = self.parent_of(cur, info.section)
        return out

    def resolve(self, info: ClassInfo) -> None:
        chain = self.chain(info)
        info.chain = tuple(chain)
        own = classdb.description(info.name)
        if own and info.kind in ("vanilla", "builtin", "modded"):
            info.description, info.desc_from = own, info.name
            return
        for anc in chain[1:]:
            d = classdb.description(anc)
            if d:
                info.description, info.desc_from = d, anc
                return
        if own:
            info.description, info.desc_from = own, info.name
            return
        if info.kind == "vanilla":
            cat = classdb.folder_category(info.file)
            if cat:
                info.description = f"Ванильный объект DayZ: {cat}."
                return
        sec = info.section.split(" > ")[0].lower()
        if sec in classdb.SECTION_DESCRIPTIONS:
            info.description = classdb.SECTION_DESCRIPTIONS[sec]
        elif info.kind in ("script", "modded") and chain[-1].lower() == info.name.lower():
            info.description = "Самостоятельный скриптовый класс (без родителя)."


def matches(info: ClassInfo, query: str, mode: str = "all") -> bool:
    """mode: "name" — по имени класса (часть, без учёта регистра);
    "desc" — по описанию (все слова запроса); "all" — имя, родитель, описание, файл."""
    q = query.casefold().strip()
    if not q:
        return True
    if mode == "name":
        return q in info.name.casefold()
    words = q.split()
    desc = info.description.casefold()
    if mode == "desc":
        return all(w in desc for w in words)
    hay = " ".join((info.name, info.parent or "", info.description, info.file, info.section,
                    " ".join(info.chain))).casefold()
    return all(w in hay for w in words)


def search(classes: Iterable[ClassInfo], query: str, mode: str = "all") -> list[ClassInfo]:
    q = query.casefold().strip()
    found = [c for c in classes if matches(c, query, mode)]

    def rank(c: ClassInfo):
        n = c.name.casefold()
        return (0 if n == q else 1 if n.startswith(q) else 2 if q in n else 3,
                0 if c.kind in ("script", "modded", "config") else 1, n)
    found.sort(key=rank)
    return found


# ---------------------------------------------------------------------------
# Исходный код класса
# ---------------------------------------------------------------------------

def class_source(text: str, line: int, max_lines: int = 600) -> str:
    """Текст класса начиная со строки line (1-based) до парной закрывающей скобки."""
    lines = text.split("\n")
    if not 1 <= line <= len(lines):
        return ""
    start = sum(len(x) + 1 for x in lines[:line - 1])
    clean = _blank_comments(text, strings=True)
    brace = clean.find("{", start)
    semi = clean.find(";", start)
    if brace < 0 or (0 <= semi < brace):
        end = semi + 1 if semi >= 0 else len(text)
    else:
        depth, end = 0, len(text)
        for i in range(brace, len(clean)):
            ch = clean[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    if clean[end:end + 1] == ";":
                        end += 1
                    break
    snippet = textwrap.dedent(text[start:end]).split("\n")
    if len(snippet) > max_lines:
        snippet = snippet[:max_lines] + [f"… (ещё {len(snippet) - max_lines} строк)"]
    return "\n".join(snippet)
