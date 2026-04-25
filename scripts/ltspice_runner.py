"""
ltspice_runner.py — автоматическое обновление параметров LTSpice и запуск симуляции.

Workflow:
  1. Читает CSV с результатами анализа старения (из папки exports/).
  2. Обновляет файл models_aged.txt: корректирует vth0 и u0 для каждой модели.
  3. Запускает LTSpice в batch-режиме с постаревшим netlist (*_aged.cir).

Использование:
  python ltspice_runner.py --cir path/to/circuit_aged.cir --csv path/to/aging_results.csv
  python ltspice_runner.py --cir path/to/circuit_aged.cir  # без CSV — только запуск LTSpice
  python ltspice_runner.py --help

Требования:
  - LTSpice XVII или LTspice 24 установлен на компьютере.
  - Python 3.9+, стандартная библиотека (csv, pathlib, subprocess, argparse).
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Пути к LTSpice (перебираем типичные места установки)
# ---------------------------------------------------------------------------

_LTSPICE_CANDIDATES = [
    r"C:\Program Files\ADI\LTspice\LTspice.exe",
    r"C:\Program Files\LTC\LTspiceXVII\XVIIx64.exe",
    r"C:\Program Files (x86)\LTC\LTspiceXVII\XVIIx86.exe",
    r"C:\Users\{user}\AppData\Local\Programs\ADI\LTspice\LTspice.exe",
]


def find_ltspice() -> Optional[Path]:
    """Ищет исполняемый файл LTSpice на компьютере."""
    import os
    candidates = [
        p.replace("{user}", os.environ.get("USERNAME", "")) for p in _LTSPICE_CANDIDATES
    ]
    for c in candidates:
        p = Path(c)
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Структура данных из CSV
# ---------------------------------------------------------------------------

def read_aging_csv(csv_path: str | Path) -> list[dict]:
    """
    Читает CSV с результатами старения.

    Ожидаемые колонки (из utils/export.py):
      transistor, channel, delta_vth_mv, active_time_s, ratio,
      mobility_factor, sim_time_s, temperature_c, target_years
    """
    rows = []
    with Path(csv_path).open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "name":            row["transistor"].strip().upper(),
                "channel":         row["channel"].strip().upper(),
                "delta_vth_mv":    float(row["delta_vth_mv"]),
                "mobility_factor": float(row["mobility_factor"]),
            })
    return rows


# ---------------------------------------------------------------------------
# Обновление models_aged.txt
# ---------------------------------------------------------------------------

_SPICE_NUM_RE = re.compile(
    r"([+-]?\d+(?:\.\d*)?(?:e[+-]?\d+)?)",
    re.IGNORECASE,
)


def _replace_param_value(line: str, keyword: str, new_value: float, fmt: str = ".5f") -> str:
    """
    Заменяет числовое значение параметра keyword в строке netlist.

    Поддерживает форматы: 'keyword=value' и 'keyword value'.
    """
    tokens = line.split()
    for i, tok in enumerate(tokens):
        if keyword.lower() in tok.lower():
            if "=" in tok:
                key_part = tok.split("=")[0]
                tokens[i] = f"{key_part}={new_value:{fmt}}"
                return " ".join(tokens)
            elif i + 1 < len(tokens):
                m = _SPICE_NUM_RE.match(tokens[i + 1])
                if m:
                    tokens[i + 1] = f"{new_value:{fmt}}"
                    return " ".join(tokens)
    return line


def _parse_current_value(line: str, keyword: str) -> Optional[float]:
    """Извлекает текущее числовое значение параметра keyword из строки."""
    tokens = line.split()
    for i, tok in enumerate(tokens):
        if keyword.lower() in tok.lower():
            if "=" in tok:
                try:
                    return float(tok.split("=")[1])
                except ValueError:
                    pass
            elif i + 1 < len(tokens):
                m = _SPICE_NUM_RE.match(tokens[i + 1])
                if m:
                    try:
                        return float(m.group(1))
                    except ValueError:
                        pass
    return None


def update_models_file(
    models_path: str | Path,
    aging_rows: list[dict],
    verbose: bool = True,
) -> None:
    """
    Обновляет models_aged.txt: изменяет vth0 и u0 на основе данных из CSV.

    Алгоритм:
    - Для PMOS: находит строки с 'vth' и умножает значение на mobility_factor
      (используем delta_vth_mv для уточнения vth0).
    - Для NMOS и PMOS: находит строки с 'u0' и делит на mobility_factor.

    Оба типа изменений выполняются отдельно для каждого суффикса модели
    (NMOS1, NMOS2, NMOS3, PMOS1 …).
    """
    models_path = Path(models_path)
    if not models_path.exists():
        raise FileNotFoundError(f"Файл моделей не найден: {models_path}")

    lines = models_path.read_text(encoding="utf-8").splitlines()

    # Вычисляем средние mobility_factor по типу канала
    nmos_factors = [r["mobility_factor"] for r in aging_rows if r["channel"] == "NMOS"]
    pmos_factors = [r["mobility_factor"] for r in aging_rows if r["channel"] == "PMOS"]

    avg_nmos = sum(nmos_factors) / len(nmos_factors) if nmos_factors else 1.0
    avg_pmos = sum(pmos_factors) / len(pmos_factors) if pmos_factors else 1.0

    # Средний сдвиг Vth (мВ → В)
    nmos_dvth = [r["delta_vth_mv"] / 1000 for r in aging_rows if r["channel"] == "NMOS"]
    pmos_dvth = [r["delta_vth_mv"] / 1000 for r in aging_rows if r["channel"] == "PMOS"]
    avg_nmos_dvth = sum(nmos_dvth) / len(nmos_dvth) if nmos_dvth else 0.0
    avg_pmos_dvth = sum(pmos_dvth) / len(pmos_dvth) if pmos_dvth else 0.0

    if verbose:
        print(f"[models] NMOS: avg mobility_factor={avg_nmos:.6f}, avg ΔVth={avg_nmos_dvth*1000:.3f} мВ")
        print(f"[models] PMOS: avg mobility_factor={avg_pmos:.6f}, avg ΔVth={avg_pmos_dvth*1000:.3f} мВ")

    current_type: Optional[str] = None
    new_lines = []

    for line in lines:
        low = line.lower()

        # Определяем тип текущей .model секции
        if ".model" in low:
            if "nmos" in low:
                current_type = "nmos"
            elif "pmos" in low:
                current_type = "pmos"
            else:
                current_type = None

        if current_type == "nmos":
            if "vth" in low:
                old_val = _parse_current_value(line, "vth")
                if old_val is not None:
                    new_val = old_val + avg_nmos_dvth
                    line = _replace_param_value(line, "vth", new_val)
                    if verbose:
                        print(f"[models] NMOS vth: {old_val:.4f} → {new_val:.4f}")
            if "u0" in low:
                old_val = _parse_current_value(line, "u0")
                if old_val is not None:
                    new_val = old_val / avg_nmos
                    line = _replace_param_value(line, "u0", new_val)
                    if verbose:
                        print(f"[models] NMOS u0: {old_val:.4f} → {new_val:.4f}")

        elif current_type == "pmos":
            if "vth" in low:
                old_val = _parse_current_value(line, "vth")
                if old_val is not None:
                    # Для PMOS vth0 < 0, деградация увеличивает |Vth|
                    new_val = old_val - avg_pmos_dvth
                    line = _replace_param_value(line, "vth", new_val)
                    if verbose:
                        print(f"[models] PMOS vth: {old_val:.4f} → {new_val:.4f}")
            if "u0" in low:
                old_val = _parse_current_value(line, "u0")
                if old_val is not None:
                    new_val = old_val / avg_pmos
                    line = _replace_param_value(line, "u0", new_val)
                    if verbose:
                        print(f"[models] PMOS u0: {old_val:.4f} → {new_val:.4f}")

        new_lines.append(line)

    models_path.write_text("\n".join(new_lines), encoding="utf-8")
    if verbose:
        print(f"[models] ✅ Обновлён: {models_path}")


# ---------------------------------------------------------------------------
# Запуск LTSpice
# ---------------------------------------------------------------------------

def run_ltspice_batch(
    cir_path: str | Path,
    ltspice_exe: Optional[str | Path] = None,
    wait: bool = True,
    verbose: bool = True,
) -> subprocess.Popen:
    """
    Запускает LTSpice в batch-режиме (-b) для заданного .cir файла.

    Parameters
    ----------
    cir_path    : путь к netlist-файлу (_aged.cir)
    ltspice_exe : путь к LTSpice.exe (если None — ищется автоматически)
    wait        : ждать завершения симуляции (по умолчанию True)

    Returns
    -------
    subprocess.Popen — процесс LTSpice.
    """
    cir_path = Path(cir_path).resolve()
    if not cir_path.exists():
        raise FileNotFoundError(f"Netlist не найден: {cir_path}")

    if ltspice_exe is None:
        exe = find_ltspice()
        if exe is None:
            raise RuntimeError(
                "LTSpice не найден. Укажите путь через --ltspice или установите LTSpice.\n"
                "Типичные пути:\n"
                "  C:\\Program Files\\ADI\\LTspice\\LTspice.exe\n"
                "  C:\\Program Files\\LTC\\LTspiceXVII\\XVIIx64.exe"
            )
    else:
        exe = Path(ltspice_exe)

    cmd = [str(exe), "-b", str(cir_path)]
    if verbose:
        print(f"[ltspice] Запуск: {' '.join(cmd)}")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if wait:
        stdout, stderr = proc.communicate()
        if verbose:
            if proc.returncode == 0:
                print(f"[ltspice] ✅ Симуляция завершена (код {proc.returncode})")
            else:
                print(f"[ltspice] ❌ LTSpice завершился с ошибкой (код {proc.returncode})")
                if stderr:
                    print(stderr.decode(errors="replace"))
    return proc


# ---------------------------------------------------------------------------
# Генерация .param файла (для параметрических развёрток)
# ---------------------------------------------------------------------------

def generate_param_file(
    aging_rows: list[dict],
    output_path: str | Path,
    verbose: bool = True,
) -> Path:
    """
    Генерирует SPICE .param файл с delta_vth и mobility_factor для каждого транзистора.

    Формат:
      .param M1_dvth = 0.005
      .param M1_mob  = 1.000123
      ...

    Файл можно подключить директивой .include в схеме LTSpice.
    """
    output_path = Path(output_path)
    lines = ["; Параметры деградации транзисторов (авто-генерация ltspice_runner.py)", ""]

    for row in aging_rows:
        name    = row["name"]
        dvth    = row["delta_vth_mv"] / 1000  # мВ → В
        mob     = row["mobility_factor"]
        channel = row["channel"]
        lines.append(f"; {name} ({channel})")
        lines.append(f".param {name}_dvth = {dvth:.6f}")
        lines.append(f".param {name}_mob  = {mob:.8f}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    if verbose:
        print(f"[param] ✅ Создан .param файл: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Обновление параметров LTSpice и запуск симуляции постаревшей схемы.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  # Только запуск LTSpice (models_aged.txt уже готов):
  python ltspice_runner.py --cir circuit_aged.cir

  # Обновить models_aged.txt из CSV, затем запустить:
  python ltspice_runner.py --cir circuit_aged.cir --csv exports/aging_results_20240101_120000.csv

  # Обновить и создать .param файл (без запуска LTSpice):
  python ltspice_runner.py --cir circuit_aged.cir --csv results.csv --param-only

  # Указать путь к LTSpice вручную:
  python ltspice_runner.py --cir circuit_aged.cir --ltspice "C:\\LTspice\\LTspice.exe"
        """,
    )
    p.add_argument(
        "--cir", required=True, metavar="FILE",
        help="Путь к постаревшему netlist (_aged.cir)",
    )
    p.add_argument(
        "--csv", metavar="FILE",
        help="CSV с результатами старения (из exports/). "
             "Если не указан — models_aged.txt не обновляется.",
    )
    p.add_argument(
        "--models", metavar="FILE",
        help="Путь к models_aged.txt (по умолчанию: рядом с --cir)",
    )
    p.add_argument(
        "--ltspice", metavar="EXE",
        help="Путь к LTSpice.exe (если не найден автоматически)",
    )
    p.add_argument(
        "--param-only", action="store_true",
        help="Только создать .param файл, LTSpice не запускать",
    )
    p.add_argument(
        "--no-run", action="store_true",
        help="Не запускать LTSpice (только обновить файлы)",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Подавить вывод",
    )
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    verbose = not args.quiet

    cir_path = Path(args.cir)
    if not cir_path.exists():
        print(f"❌ Файл не найден: {cir_path}", file=sys.stderr)
        sys.exit(1)

    # Определяем путь к models_aged.txt
    if args.models:
        models_path = Path(args.models)
    else:
        models_path = cir_path.parent / "models_aged.txt"

    aging_rows: list[dict] = []

    # Шаг 1: Читаем CSV и обновляем models_aged.txt
    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            print(f"❌ CSV не найден: {csv_path}", file=sys.stderr)
            sys.exit(1)

        if verbose:
            print(f"[csv] Читаю: {csv_path}")
        aging_rows = read_aging_csv(csv_path)
        if verbose:
            print(f"[csv] Загружено транзисторов: {len(aging_rows)}")

        if models_path.exists():
            update_models_file(models_path, aging_rows, verbose=verbose)
        else:
            if verbose:
                print(f"[models] ⚠️  models_aged.txt не найден ({models_path}), пропускаю обновление.")

    # Шаг 2: Создаём .param файл
    if aging_rows:
        param_path = cir_path.parent / (cir_path.stem + "_aging.param")
        generate_param_file(aging_rows, param_path, verbose=verbose)

    # Шаг 3: Запускаем LTSpice
    if not args.param_only and not args.no_run:
        try:
            run_ltspice_batch(cir_path, ltspice_exe=args.ltspice, verbose=verbose)
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"❌ {exc}", file=sys.stderr)
            sys.exit(1)
    elif verbose:
        print("[ltspice] Пропускаю запуск (--param-only или --no-run)")

    if verbose:
        print("\n✅ Готово.")


if __name__ == "__main__":
    main()
