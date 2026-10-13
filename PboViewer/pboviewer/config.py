"""Конфиги Bohemia: текстовый config.cpp и бинарный (rapified) config.bin.

derapify(data) -> текст в стиле CfgConvert;  rapify(text) -> байты config.bin.

Формат config.bin:
    "\\0raP" + u32 0 + u32 8 + u32 смещение_enum
    тело класса: cstring родитель, compressed-int число записей, записи...
    записи: 0 класс (имя, u32 смещение тела), 1 значение (подтип, имя, значение),
            2 массив (имя, массив), 3 внешний класс «class X;», 4 «delete X;»,
            5 добавление в массив «x[] += {...}» (u32 1, имя, массив)
    в конце — секция enum: u32 количество, пары (cstring, u32)
"""

from __future__ import annotations

import bisect
import re
import struct
from dataclasses import dataclass, field
from typing import Optional, Union

RAP_MAGIC = b"\0raP"

Value = Union[str, int, float, list]


class ConfigError(Exception):
    def __init__(self, message: str, line: int = 0):
        super().__init__(f"строка {line}: {message}" if line else message)
        self.line = line


@dataclass
class CfgClass:
    name: str
    parent: Optional[str] = None
    # записи: ("class", CfgClass) ("extern", имя) ("delete", имя)
    #         ("value", имя, значение) ("array", имя, список) ("append", имя, список)
    entries: list = field(default_factory=list)
    line: int = 0

    def classes(self) -> list["CfgClass"]:
        return [e[1] for e in self.entries if e[0] == "class"]


def is_rapified(data: bytes) -> bool:
    return data[:4] == RAP_MAGIC


# ---------------------------------------------------------------------------
# Чтение config.bin
# ---------------------------------------------------------------------------

class _BinReader:
    def __init__(self, data: bytes):
        self.d = data

    def cstr(self, pos: int) -> tuple[str, int]:
        end = self.d.find(b"\0", pos)
        if end < 0:
            raise ConfigError("config.bin повреждён: строка без завершающего нуля")
        raw = self.d[pos:end]
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            s = raw.decode("cp1251", errors="replace")
        return s, end + 1

    def cint(self, pos: int) -> tuple[int, int]:
        val, shift = 0, 0
        while True:
            if pos >= len(self.d):
                raise ConfigError("config.bin повреждён: обрыв данных")
            b = self.d[pos]
            pos += 1
            val |= (b & 0x7F) << shift
            if not b & 0x80:
                return val, pos
            shift += 7

    def u32(self, pos: int) -> tuple[int, int]:
        return struct.unpack_from("<I", self.d, pos)[0], pos + 4

    def array(self, pos: int) -> tuple[list, int]:
        n, pos = self.cint(pos)
        out = []
        for _ in range(n):
            t = self.d[pos]
            pos += 1
            if t in (0, 4):
                v, pos = self.cstr(pos)
            elif t == 1:
                v = struct.unpack_from("<f", self.d, pos)[0]
                pos += 4
            elif t == 2:
                v = struct.unpack_from("<i", self.d, pos)[0]
                pos += 4
            elif t == 3:
                v, pos = self.array(pos)
            elif t == 6:
                v = struct.unpack_from("<q", self.d, pos)[0]
                pos += 8
            else:
                raise ConfigError(f"config.bin: неизвестный тип элемента массива {t}")
            out.append(v)
        return out, pos

    def body(self, cls: CfgClass, pos: int, depth: int = 0) -> None:
        if depth > 256:
            raise ConfigError("config.bin повреждён: слишком глубокая вложенность")
        parent, pos = self.cstr(pos)
        cls.parent = parent or None
        n, pos = self.cint(pos)
        for _ in range(n):
            t = self.d[pos]
            pos += 1
            if t == 0:
                name, pos = self.cstr(pos)
                off, pos = self.u32(pos)
                sub = CfgClass(name)
                self.body(sub, off, depth + 1)
                cls.entries.append(("class", sub))
            elif t == 1:
                sub_t = self.d[pos]
                pos += 1
                name, pos = self.cstr(pos)
                if sub_t in (0, 4):
                    v, pos = self.cstr(pos)
                elif sub_t == 1:
                    v = struct.unpack_from("<f", self.d, pos)[0]
                    pos += 4
                elif sub_t == 2:
                    v = struct.unpack_from("<i", self.d, pos)[0]
                    pos += 4
                elif sub_t == 6:
                    v = struct.unpack_from("<q", self.d, pos)[0]
                    pos += 8
                else:
                    raise ConfigError(f"config.bin: неизвестный тип значения {sub_t}")
                cls.entries.append(("value", name, v))
            elif t == 2:
                name, pos = self.cstr(pos)
                arr, pos = self.array(pos)
                cls.entries.append(("array", name, arr))
            elif t == 3:
                name, pos = self.cstr(pos)
                cls.entries.append(("extern", name))
            elif t == 4:
                name, pos = self.cstr(pos)
                cls.entries.append(("delete", name))
            elif t == 5:
                pos += 4
                name, pos = self.cstr(pos)
                arr, pos = self.array(pos)
                cls.entries.append(("append", name, arr))
            else:
                raise ConfigError(f"config.bin: неизвестный тип записи {t}")


def read_bin(data: bytes) -> CfgClass:
    if not is_rapified(data):
        raise ConfigError("Это не бинарный конфиг (нет сигнатуры raP)")
    root = CfgClass("")
    try:
        _BinReader(data).body(root, 16)
    except (IndexError, struct.error):
        raise ConfigError("config.bin повреждён: данные обрываются") from None
    return root


# ---------------------------------------------------------------------------
# Вывод текста
# ---------------------------------------------------------------------------

def format_float(v: float) -> str:
    if v != v or v in (float("inf"), float("-inf")):
        return "0.0"
    packed = struct.pack("<f", v)
    for p in range(1, 10):
        s = f"{v:.{p}g}"
        if struct.pack("<f", float(s)) == packed:
            break
    if "e" not in s and "." not in s:
        s += ".0"
    return s


def quote(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def _fmt_scalar(v) -> str:
    if isinstance(v, str):
        return quote(v)
    if isinstance(v, float):
        return format_float(v)
    return str(v)


def _fmt_array(arr: list, indent: str) -> str:
    if not arr:
        return "{}"
    if not any(isinstance(x, list) for x in arr) and len(arr) <= 6 and \
            sum(len(_fmt_scalar(x)) for x in arr) < 80:
        return "{" + ",".join(_fmt_scalar(x) for x in arr) + "}"
    inner = indent + "\t"
    parts = [inner + (_fmt_array(x, inner) if isinstance(x, list) else _fmt_scalar(x)) for x in arr]
    return "\n" + indent + "{\n" + ",\n".join(parts) + "\n" + indent + "}"


def write_text(root: CfgClass) -> str:
    out: list[str] = []

    def body(cls: CfgClass, ind: str):
        for e in cls.entries:
            kind = e[0]
            if kind == "class":
                c = e[1]
                head = f"{ind}class {c.name}" + (f": {c.parent}" if c.parent else "")
                if c.entries:
                    out.append(head)
                    out.append(ind + "{")
                    body(c, ind + "\t")
                    out.append(ind + "};")
                else:
                    out.append(head + " {};")
            elif kind == "extern":
                out.append(f"{ind}class {e[1]};")
            elif kind == "delete":
                out.append(f"{ind}delete {e[1]};")
            elif kind == "value":
                out.append(f"{ind}{e[1]}={_fmt_scalar(e[2])};")
            elif kind in ("array", "append"):
                op = "+=" if kind == "append" else "="
                out.append(f"{ind}{e[1]}[]{op}{_fmt_array(e[2], ind)};")

    body(root, "")
    return "\n".join(out) + "\n"


def derapify(data: bytes) -> str:
    return write_text(read_bin(data))


# ---------------------------------------------------------------------------
# Разбор текста
# ---------------------------------------------------------------------------

_INT_RE = re.compile(r"^[+-]?(?:0x[0-9a-fA-F]+|\d+)$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_IDENT_RE = re.compile(r"\w+")


def parse_scalar(raw: str) -> Value:
    s = raw.strip()
    if _INT_RE.match(s):
        v = int(s, 16) if "x" in s.lower() else int(s)
        if -0x80000000 <= v <= 0x7FFFFFFF:
            return v
        if 0x80000000 <= v <= 0xFFFFFFFF and "x" in s.lower():
            return v - 0x100000000
        return float(v)
    if _FLOAT_RE.match(s):
        return float(s)
    return s


class _Parser:
    def __init__(self, text: str):
        self.t = text
        self.i = 0
        self.n = len(text)
        self._nl = [m.start() for m in re.finditer("\n", text)]

    def line(self, pos: Optional[int] = None) -> int:
        return bisect.bisect_left(self._nl, self.i if pos is None else pos) + 1

    def err(self, msg: str):
        raise ConfigError(msg, self.line())

    def skip(self):
        t, n = self.t, self.n
        while self.i < n:
            c = t[self.i]
            if c in " \t\r\n﻿":
                self.i += 1
            elif t.startswith("//", self.i):
                j = t.find("\n", self.i)
                self.i = n if j < 0 else j + 1
            elif t.startswith("/*", self.i):
                j = t.find("*/", self.i + 2)
                if j < 0:
                    self.err("незакрытый комментарий /*")
                self.i = j + 2
            elif c == "#" and not t[t.rfind("\n", 0, self.i) + 1:self.i].strip():
                self.err("директивы препроцессора (#include, #define…) не поддерживаются — "
                         "уберите их перед сохранением в config.bin")
            else:
                break

    def peek(self) -> str:
        self.skip()
        return self.t[self.i] if self.i < self.n else ""

    def expect(self, ch: str):
        if self.peek() != ch:
            got = self.t[self.i] if self.i < self.n else "конец файла"
            self.err(f"ожидалось «{ch}», найдено «{got}»")
        self.i += 1

    def ident(self) -> str:
        self.skip()
        m = _IDENT_RE.match(self.t, self.i)
        if not m:
            got = self.t[self.i:self.i + 10] or "конец файла"
            self.err(f"ожидалось имя, найдено «{got}»")
        self.i = m.end()
        return m.group(0)

    def string(self) -> str:
        # курсор на открывающей кавычке; "" внутри — экранированная кавычка
        self.i += 1
        out = []
        while True:
            j = self.t.find('"', self.i)
            if j < 0:
                self.err("незакрытая строка")
            out.append(self.t[self.i:j])
            if self.t.startswith('""', j):
                out.append('"')
                self.i = j + 2
            else:
                self.i = j + 1
                break
        s = "".join(out)
        # "abc" \n "def" — склейка соседних строк
        save = self.i
        if self.peek() == '"':
            return s + self.string()
        self.i = save
        return s

    def raw_until(self, stops: str) -> str:
        start = self.i
        while self.i < self.n and self.t[self.i] not in stops:
            if self.t[self.i] == "\n":
                break
            self.i += 1
        return self.t[start:self.i].strip()

    def value(self, stops: str) -> Value:
        c = self.peek()
        if c == '"':
            return self.string()
        if c == "{":
            return self.array()
        raw = self.raw_until(stops)
        if not raw:
            self.err("пустое значение")
        return parse_scalar(raw)

    def array(self) -> list:
        self.expect("{")
        out: list = []
        if self.peek() == "}":
            self.i += 1
            return out
        while True:
            out.append(self.value(",}"))
            c = self.peek()
            if c == ",":
                self.i += 1
                if self.peek() == "}":  # висячая запятая
                    self.i += 1
                    return out
            elif c == "}":
                self.i += 1
                return out
            else:
                self.err("в массиве ожидалась «,» или «}»")

    def body(self, cls: CfgClass, top: bool):
        while True:
            c = self.peek()
            if c == "":
                if not top:
                    self.err(f"класс {cls.name} не закрыт «}};»")
                return
            if c == "}":
                if top:
                    self.err("лишняя «}»")
                self.i += 1
                self.expect(";")
                return
            if c == ";":
                self.i += 1
                continue
            line = self.line()
            word = self.ident()
            if word == "class":
                name = self.ident()
                parent = None
                c = self.peek()
                if c == ":":
                    self.i += 1
                    parent = self.ident()
                    c = self.peek()
                if c == ";":
                    self.i += 1
                    if parent:
                        self.err(f"«class {name}: {parent};» без тела")
                    cls.entries.append(("extern", name))
                    continue
                self.expect("{")
                sub = CfgClass(name, parent, line=line)
                self.body(sub, False)
                cls.entries.append(("class", sub))
            elif word == "delete":
                name = self.ident()
                self.expect(";")
                cls.entries.append(("delete", name))
            elif word in ("enum",):
                self.err("enum не поддерживается")
            else:
                if self.peek() == "[":
                    self.i += 1
                    self.expect("]")
                    c = self.peek()
                    if self.t.startswith("+=", self.i):
                        self.i += 2
                        kind = "append"
                    elif c == "=":
                        self.i += 1
                        kind = "array"
                    else:
                        self.err(f"после «{word}[]» ожидалось «=» или «+=»")
                    if self.peek() != "{":
                        self.err(f"массив {word}[] должен начинаться с «{{»")
                    cls.entries.append((kind, word, self.array()))
                else:
                    self.expect("=")
                    v = self.value(";}")
                    if isinstance(v, list):
                        self.err(f"«{word}» — массив, нужно писать «{word}[]=»")
                    cls.entries.append(("value", word, v))
                if self.peek() != "}":
                    self.expect(";")


def parse_text(text: str) -> CfgClass:
    root = CfgClass("")
    _Parser(text).body(root, True)
    return root


# ---------------------------------------------------------------------------
# Запись config.bin
# ---------------------------------------------------------------------------

def _cint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _cs(s: str) -> bytes:
    b = s.encode("utf-8")
    if b"\0" in b:
        raise ConfigError(f"строка содержит нулевой символ: {s[:30]!r}")
    return b + b"\0"


def _bin_array(arr: list) -> bytes:
    out = bytearray(_cint(len(arr)))
    for v in arr:
        if isinstance(v, list):
            out += b"\x03" + _bin_array(v)
        elif isinstance(v, str):
            out += b"\x00" + _cs(v)
        elif isinstance(v, float):
            out += b"\x01" + struct.pack("<f", v)
        else:
            out += b"\x02" + struct.pack("<i", v)
    return bytes(out)


def write_bin(root: CfgClass) -> bytes:
    out = bytearray(RAP_MAGIC + struct.pack("<III", 0, 8, 0))

    def body(cls: CfgClass):
        out.extend(_cs(cls.parent or ""))
        out.extend(_cint(len(cls.entries)))
        pending = []
        for e in cls.entries:
            kind = e[0]
            if kind == "class":
                out.extend(b"\x00" + _cs(e[1].name))
                pending.append((len(out), e[1]))
                out.extend(b"\0\0\0\0")
            elif kind == "value":
                v = e[2]
                if isinstance(v, str):
                    out.extend(b"\x01\x00" + _cs(e[1]) + _cs(v))
                elif isinstance(v, float):
                    out.extend(b"\x01\x01" + _cs(e[1]) + struct.pack("<f", v))
                else:
                    out.extend(b"\x01\x02" + _cs(e[1]) + struct.pack("<i", v))
            elif kind == "array":
                out.extend(b"\x02" + _cs(e[1]) + _bin_array(e[2]))
            elif kind == "append":
                out.extend(b"\x05" + struct.pack("<I", 1) + _cs(e[1]) + _bin_array(e[2]))
            elif kind == "extern":
                out.extend(b"\x03" + _cs(e[1]))
            elif kind == "delete":
                out.extend(b"\x04" + _cs(e[1]))
        for pos, sub in pending:
            struct.pack_into("<I", out, pos, len(out))
            body(sub)

    body(root)
    struct.pack_into("<I", out, 12, len(out))
    out.extend(struct.pack("<I", 0))  # пустая секция enum
    return bytes(out)


def rapify(text: str) -> bytes:
    return write_bin(parse_text(text))


# ---------------------------------------------------------------------------
# Обход дерева
# ---------------------------------------------------------------------------

def walk_classes(root: CfgClass, path: tuple = ()):
    """(путь родительских классов, класс) для всех классов дерева."""
    for c in root.classes():
        yield path, c
        yield from walk_classes(c, path + (c.name,))
