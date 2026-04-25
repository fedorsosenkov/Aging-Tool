"""
Поиск LTSpice, запуск файлов схемы/netlist, управление рабочей директорией.

Обеспечивает:
  - Поиск LTspice.exe через реестр / известные пути / конфиг.
  - Прямой запуск файлов в LTspice (минуя файловые ассоциации Windows).
  - Создание изолированной рабочей директории с копиями всех файлов
    проекта, чтобы LTspice мог найти .lib / .inc по относительным путям.
  - Настройка автоматического открытия LTspice после aging-расчёта.
"""

from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import tempfile
import winreg
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Конфиг — сохраняем путь к LTSpice рядом с exe/main.py
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    """Путь к файлу конфигурации (рядом с main.py или exe)."""
    base = Path(os.environ.get("APPDATA", Path.home())) / "AgingTool"
    base.mkdir(parents=True, exist_ok=True)
    return base / "config.json"


def _load_config() -> dict:
    p = _config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_config(data: dict) -> None:
    _config_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Поиск LTSpice
# ---------------------------------------------------------------------------

_KNOWN_PATHS = [
    r"C:\Program Files\ADI\LTspice\LTspice.exe",            # LTspice 24
    r"C:\Program Files\LTC\LTspiceXVII\XVIIx64.exe",        # XVII 64-bit
    r"C:\Program Files (x86)\LTC\LTspiceXVII\XVIIx86.exe",  # XVII 32-bit
    r"C:\Program Files\LTC\LTspice\LTspice.exe",
]


def _search_registry() -> Optional[Path]:
    """Ищет путь к LTSpice в реестре Windows."""
    keys_to_try = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ADI\LTspice"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\ADI\LTspice"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\LTC\LTspiceXVII"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\ADI\LTspice"),
    ]
    for hive, subkey in keys_to_try:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                for value_name in ("InstallPath", "Path", ""):
                    try:
                        val, _ = winreg.QueryValueEx(key, value_name)
                        candidates = [
                            Path(val) / "LTspice.exe",
                            Path(val) / "XVIIx64.exe",
                            Path(val),
                        ]
                        for c in candidates:
                            if c.exists() and c.suffix.lower() == ".exe":
                                return c
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def find_ltspice_exe() -> Optional[Path]:
    """
    Возвращает путь к LTSpice.exe или None.

    Порядок поиска:
    1. Сохранённый пользователем путь в конфиге
    2. Известные пути установки
    3. Реестр Windows
    """
    cfg = _load_config()
    saved = cfg.get("ltspice_exe")
    if saved:
        p = Path(saved)
        if p.exists():
            return p

    for candidate in _KNOWN_PATHS:
        p = Path(candidate)
        if p.exists():
            return p

    return _search_registry()


def save_ltspice_path(exe_path: str | Path) -> None:
    """Сохраняет путь к LTSpice в конфиг (один раз, навсегда)."""
    cfg = _load_config()
    cfg["ltspice_exe"] = str(Path(exe_path))
    _save_config(cfg)


# ---------------------------------------------------------------------------
# Запуск файла в LTSpice (минуя os.startfile и файловые ассоциации)
# ---------------------------------------------------------------------------

def open_in_ltspice(
    file_path: str | Path,
    ltspice_exe: Optional[str | Path] = None,
) -> bool:
    """
    Открывает файл (.cir / .asc / .net) в LTSpice напрямую,
    не используя os.startfile() (который может запустить Microcap).

    Returns True при успехе, False если LTSpice не найден.
    """
    file_path = Path(file_path)

    exe = Path(ltspice_exe) if ltspice_exe else find_ltspice_exe()
    if exe is None:
        return False

    subprocess.Popen([str(exe), str(file_path)])
    return True


# ---------------------------------------------------------------------------
# Настройка авто-открытия LTspice после aging-расчёта
# ---------------------------------------------------------------------------

def get_auto_open_ltspice() -> bool:
    """Возвращает настройку: открывать ли LTspice автоматически после aging."""
    return _load_config().get("auto_open_ltspice_after_aging", True)


def set_auto_open_ltspice(value: bool) -> None:
    """Сохраняет настройку автоматического открытия LTspice после aging."""
    cfg = _load_config()
    cfg["auto_open_ltspice_after_aging"] = value
    _save_config(cfg)


# ---------------------------------------------------------------------------
# Создание изолированной рабочей директории для LTspice
# ---------------------------------------------------------------------------

def prepare_ltspice_workdir(
    aged_cir: Path,
    models_txt: Path,
    extra_files: Optional[list[Path]] = None,
) -> Path:
    """
    Создаёт временную рабочую папку и копирует туда постаревший netlist
    со всеми зависимостями. Все .lib-пути внутри netlist приводятся к
    относительным именам файлов — LTspice найдёт их в той же папке.

    Параметры
    ---------
    aged_cir : Path
        Постаревший netlist (_aged.cir).
    models_txt : Path
        Файл постаревших моделей (models_aged.txt).
    extra_files : list[Path], optional
        Дополнительные файлы (.inc, .lib и т.д.).

    Возвращает
    ----------
    Path : путь к временной папке (содержит копии всех файлов).
    """
    work_dir = Path(tempfile.mkdtemp(prefix="spice_aged_"))

    # Копируем файл постаревших моделей
    shutil.copy2(models_txt, work_dir / models_txt.name)

    # Копируем постаревший netlist и исправляем .lib-пути
    dest_cir = work_dir / aged_cir.name
    content = aged_cir.read_text(encoding="utf-8", errors="replace")

    # Заменяем абсолютные пути к models_aged.txt на просто имя файла
    content = re.sub(
        r'(\.lib\s+)[^\s"]+[/\\](' + re.escape(models_txt.name) + r')',
        r'\1\2',
        content,
        flags=re.IGNORECASE,
    )
    dest_cir.write_text(content, encoding="utf-8")

    # Копируем дополнительные файлы (include, библиотеки и т.д.)
    if extra_files:
        for f in extra_files:
            if f and Path(f).exists():
                shutil.copy2(f, work_dir / Path(f).name)

    return work_dir
