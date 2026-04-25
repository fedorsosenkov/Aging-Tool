"""
Управление путями для режимов: обычный запуск и PyInstaller --onefile.

Правило:
  - Ресурсы (иконки, шаблоны) — resource_path()  → внутри _MEIPASS или рядом с main.py
  - Данные пользователя (exports/, config) — writable_path() → рядом с .exe / в %APPDATA%
"""

from __future__ import annotations
import sys
from pathlib import Path


def _exe_dir() -> Path:
    """Папка, где лежит .exe (или main.py при обычном запуске)."""
    if getattr(sys, "frozen", False):
        # PyInstaller: sys.executable = путь к .exe
        return Path(sys.executable).parent
    return Path(__file__).parent.parent   # aging_tool/


def resource_path(relative: str) -> Path:
    """
    Путь к файлу-ресурсу (иконки и т.п.).
    При сборке PyInstaller ресурсы распаковываются во временную папку _MEIPASS.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
    return base / relative


def writable_path(relative: str) -> Path:
    """
    Путь для записи файлов пользователя (exports/, отчёты, конфиг).
    Всегда рядом с .exe — НЕ внутри _MEIPASS (там файловая система read-only).
    Папка создаётся автоматически.
    """
    p = _exe_dir() / relative
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
