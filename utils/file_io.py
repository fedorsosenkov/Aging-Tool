"""
Утилиты для чтения файлов с автоопределением кодировки.
"""

from __future__ import annotations
from pathlib import Path


_RUS_CHARS = set("аоуыэяеёюибвгдйжзклмнпрстфхцчшщАОУЫЭЯЕЁЮИБВГДЙЖЗКЛМНПРСТФХЦЧШЩ")


def _has_cyrillic(text: str) -> bool:
    return any(ch in _RUS_CHARS for ch in text)


def read_lines(path: str | Path) -> list[str]:
    """
    Читает файл и возвращает список строк.
    Автоматически определяет кодировку: utf-16-le (если путь содержит
    кириллицу или файл начинается с нулевых байт) или utf-8/cp1251.
    """
    path = Path(path)
    raw = path.read_bytes()

    # Проверка BOM или нулевых байт → utf-16-le
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(raw) > 1 and raw[1] == 0):
        return path.read_text(encoding="utf-16-le").splitlines()

    # Путь содержит кириллицу → скорее всего utf-16-le
    if _has_cyrillic(str(path)):
        try:
            return path.read_text(encoding="utf-16-le").splitlines()
        except UnicodeDecodeError:
            pass

    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return path.read_text(encoding=enc).splitlines()
        except UnicodeDecodeError:
            continue

    # Последний fallback
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def write_text(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    Path(path).write_text(content, encoding=encoding)
