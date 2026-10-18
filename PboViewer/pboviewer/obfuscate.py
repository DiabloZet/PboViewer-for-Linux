"""Минификация скриптов Enforce Script (.c) для своих модов.

Задача — затруднить чтение исходника при копировании мода, не меняя его поведение.
Безопасный набор преобразований:
  - удаление комментариев (// и /* */), с учётом строк;
  - удаление пустых строк и лишних пробелов/отступов;
  - схлопывание повторяющихся пробелов вне строк.

Намеренно НЕ переименовываются классы, функции, методы и глобальные идентификаторы:
в DayZ они связаны по именам (modded class, вызовы по строке, ссылки из конфигов),
и переименование сломало бы мод. Это минификация, а не стойкая защита: распаковать
и прочитать логику всё равно можно, но «просто скопировать красиво оформленный код» — нет.

minify_script(text) -> text — для одного файла.
"""

from __future__ import annotations

import re

# Строки препроцессора должны остаться на отдельной строке и без изменений внутри.
_PREPROC = re.compile(r"^\s*#")


def _strip_comments(text: str) -> str:
    """Удаляет // и /* */ комментарии, сохраняя содержимое строк и переводы строк."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            if j < 0:
                i = n
            else:
                # сохраняем переводы строк из блочного комментария, чтобы не сбить номера строк препроцессора
                out.append("\n" * text.count("\n", i, j + 2))
                i = j + 2
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                j += 1
            out.append(text[i:min(j + 1, n)])
            i = j + 1
        else:
            k = i
            while k < n and text[k] not in '/"':
                k += 1
            if k == i:
                k += 1
            out.append(text[i:k])
            i = k
    return "".join(out)


def _collapse_ws_outside_strings(line: str) -> str:
    """Схлопывает подряд идущие пробелы/табы вне строк в один пробел."""
    out = []
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c == '"':
            j = i + 1
            while j < n and line[j] != '"':
                if line[j] == "\\":
                    j += 1
                j += 1
            out.append(line[i:min(j + 1, n)])
            i = j + 1
        elif c in " \t":
            out.append(" ")
            while i < n and line[i] in " \t":
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def minify_script(text: str) -> str:
    """Минифицирует один скрипт .c (Enforce Script). Поведение кода не меняется."""
    had_bom = text.startswith("﻿")
    if had_bom:
        text = text[1:]
    text = _strip_comments(text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = []
    in_directive = False  # многострочная директива препроцессора (продолжение через «\»)
    for line in text.split("\n"):
        if in_directive or _PREPROC.match(line):
            rline = line.rstrip()
            lines.append(rline)  # директивы препроцессора — без изменений
            in_directive = rline.endswith("\\")
            continue
        line = _collapse_ws_outside_strings(line).strip()
        if line:
            lines.append(line)
    result = "\n".join(lines)
    if result and not result.endswith("\n"):
        result += "\n"
    return ("﻿" if had_bom else "") + result
