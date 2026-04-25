"""
Экспорт результатов анализа старения в CSV и TXT.
Файлы сохраняются в подпапку exports/ рядом с netlist-файлом.

Также содержит export_temp_to_csv / export_temp_to_txt —
экспорт результатов температурного анализа (сравнение
Свежий → T₀ → T_new).
"""

from __future__ import annotations
import csv
from datetime import datetime
from pathlib import Path

from core.aging import AgingResults


def _exports_dir(base_dir: str | Path) -> Path:
    """
    Создаёт и возвращает папку exports/ рядом с netlist-файлом.
    При сборке PyInstaller --onefile папка создаётся рядом с .exe,
    а не внутри временной _MEIPASS (там нет права на запись).
    """
    d = Path(base_dir) / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def export_to_csv(results: AgingResults, base_dir: str | Path, target_years: float) -> Path:
    """
    Сохраняет результаты анализа в CSV-файл.

    Parameters
    ----------
    results     : AgingResults — результаты расчёта старения
    base_dir    : папка, в которой будет создана подпапка exports/
    target_years: срок службы (лет) — записывается в заголовок

    Returns
    -------
    Path к созданному файлу.
    """
    out_dir = _exports_dir(base_dir)
    filename = out_dir / f"aging_results_{_timestamp()}.csv"

    fieldnames = [
        "transistor",
        "channel",
        "delta_vth_mv",
        "active_time_s",
        "ratio",
        "mobility_factor",
        "sim_time_s",
        "temperature_c",
        "target_years",
    ]

    with filename.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for ar in results.transistors:
            if ar.channel == "NMOS":
                delta_mv     = ar.hci_delta_vth_v * 1000
                active_time  = ar.hci_active_time_s
                ratio        = ar.hci_ratio
                mob_factor   = ar.hci_mobility_factor
            else:
                delta_mv     = ar.nbti_delta_vth_v * 1000
                active_time  = ar.nbti_active_time_s
                ratio        = ar.nbti_ratio
                mob_factor   = ar.nbti_mobility_factor

            writer.writerow({
                "transistor":    ar.name,
                "channel":       ar.channel,
                "delta_vth_mv":  f"{delta_mv:.4f}",
                "active_time_s": f"{active_time:.6e}",
                "ratio":         f"{ratio:.6e}",
                "mobility_factor": f"{mob_factor:.8f}",
                "sim_time_s":    f"{results.sim_time_s:.6e}",
                "temperature_c": f"{results.temperature_c:.1f}",
                "target_years":  f"{target_years:.1f}",
            })

    return filename


def export_to_txt(results: AgingResults, base_dir: str | Path, target_years: float) -> Path:
    """
    Сохраняет результаты анализа в текстовый отчёт (TXT, UTF-8).

    Parameters
    ----------
    results     : AgingResults
    base_dir    : папка, в которой будет создана подпапка exports/
    target_years: срок службы (лет)

    Returns
    -------
    Path к созданному файлу.
    """
    out_dir = _exports_dir(base_dir)
    filename = out_dir / f"aging_results_{_timestamp()}.txt"

    lines: list[str] = []
    sep = "=" * 60

    lines.append(sep)
    lines.append("  MOS Aging Analyzer — Результаты анализа старения")
    lines.append(sep)
    lines.append(f"  Дата:              {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    lines.append(f"  Срок службы:       {target_years:.0f} лет")
    lines.append(f"  Температура:       {results.temperature_c:.1f} °C  /  {results.temperature_k:.1f} K")
    lines.append(f"  Время симуляции:   {results.sim_time_s:.4e} с")
    lines.append(f"  Транзисторов:      {len(results.transistors)}"
                 f"  (NMOS={len(results.nmos_results())}, PMOS={len(results.pmos_results())})")
    lines.append(sep)

    nmos = results.nmos_results()
    if nmos:
        lines.append("")
        lines.append("  NMOS — Эффект горячих носителей (HCI)")
        lines.append("-" * 60)
        lines.append(f"  {'Транзистор':<12} {'ΔVth, мВ':>10} {'Время HCI, с':>14} "
                     f"{'t_HCI/t_sim':>12} {'factor_u0':>12}")
        lines.append("-" * 60)
        for ar in sorted(nmos, key=lambda x: x.hci_delta_vth_v, reverse=True):
            lines.append(
                f"  {ar.name:<12} "
                f"{ar.hci_delta_vth_v * 1000:>10.4f} "
                f"{ar.hci_active_time_s:>14.4e} "
                f"{ar.hci_ratio:>10.4e} "
                f"{ar.hci_mobility_factor:>12.8f}"
            )

    pmos = results.pmos_results()
    if pmos:
        lines.append("")
        lines.append("  PMOS — Температурная нестабильность при отрицательном смещении (NBTI)")
        lines.append("-" * 60)
        lines.append(f"  {'Транзистор':<12} {'ΔVth, мВ':>10} {'Время NBTI, с':>14} "
                     f"{'t_NBTI/t_sim':>12} {'factor_u0':>12}")
        lines.append("-" * 60)
        for ar in sorted(pmos, key=lambda x: x.nbti_delta_vth_v, reverse=True):
            lines.append(
                f"  {ar.name:<12} "
                f"{ar.nbti_delta_vth_v * 1000:>10.4f} "
                f"{ar.nbti_active_time_s:>14.4e} "
                f"{ar.nbti_ratio:>10.4e} "
                f"{ar.nbti_mobility_factor:>12.8f}"
            )

    lines.append("")
    lines.append(sep)

    filename.write_text("\n".join(lines), encoding="utf-8")
    return filename


# ---------------------------------------------------------------------------
# Экспорт температурного анализа
# ---------------------------------------------------------------------------

def export_temp_to_csv(
    comparisons: list,
    base_dir: str | Path,
    target_years: float,
) -> Path:
    """
    Сохраняет результаты температурного анализа в CSV.

    Три сравнительных колонки для ΔVth и μ:
      «Свежий» (нет деградации) → «T₀» (базовая T из log) → «T_new» (новая T).

    Parameters
    ----------
    comparisons  : list[TemperatureComparisonResult]
    base_dir     : папка, в которой будет создана подпапка exports/
    target_years : срок службы (лет)

    Returns
    -------
    Path к созданному файлу.
    """
    out_dir = _exports_dir(base_dir)
    filename = out_dir / f"temp_analysis_{_timestamp()}.csv"

    fieldnames = [
        "transistor",
        "channel",
        "t0_c",
        "t_new_c",
        "target_years",
        # ΔVth (мВ)
        "delta_vth_fresh_mv",
        "delta_vth_t0_mv",
        "delta_vth_tnew_mv",
        "delta_vth_change_mv",
        # Коэффициент деградации подвижности
        "mobility_fresh",
        "mobility_t0",
        "mobility_tnew",
        "mobility_change_pct",
    ]

    with filename.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in comparisons:
            writer.writerow({
                "transistor":          c.name,
                "channel":             c.channel,
                "t0_c":                f"{c.t0_c:.1f}",
                "t_new_c":             f"{c.t_new_c:.1f}",
                "target_years":        f"{target_years:.1f}",
                # Свежий транзистор — базовые значения
                "delta_vth_fresh_mv":  "0.0000",
                "delta_vth_t0_mv":     f"{c.delta_vth_t0_v * 1000:.4f}",
                "delta_vth_tnew_mv":   f"{c.delta_vth_tnew_v * 1000:.4f}",
                "delta_vth_change_mv": f"{c.delta_vth_change_mv:.4f}",
                "mobility_fresh":      "1.00000000",
                "mobility_t0":         f"{c.mobility_factor_t0:.8f}",
                "mobility_tnew":       f"{c.mobility_factor_tnew:.8f}",
                "mobility_change_pct": f"{c.mobility_change_pct:.4f}",
            })

    return filename


def export_temp_to_txt(
    comparisons: list,
    base_dir: str | Path,
    target_years: float,
) -> Path:
    """
    Сохраняет результаты температурного анализа в TXT (фиксированные колонки).

    Колонки: Транзистор | Тип | Свежий ΔVth | T₀ ΔVth | T_new ΔVth | Δ |
             μ(свеж) | μ(T₀) | μ(T_new) | Δμ, %

    Parameters
    ----------
    comparisons  : list[TemperatureComparisonResult]
    base_dir     : папка, в которой будет создана подпапка exports/
    target_years : срок службы (лет)

    Returns
    -------
    Path к созданному файлу.
    """
    out_dir = _exports_dir(base_dir)
    filename = out_dir / f"temp_analysis_{_timestamp()}.txt"

    if not comparisons:
        filename.write_text("Нет данных для экспорта.", encoding="utf-8")
        return filename

    t0   = comparisons[0].t0_c
    tnew = comparisons[0].t_new_c

    lines: list[str] = []
    sep = "=" * 100

    lines.append(sep)
    lines.append("  MOS Aging Analyzer — Температурный анализ деградации")
    lines.append(sep)
    lines.append(f"  Дата:              {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    lines.append(f"  Срок службы:       {target_years:.0f} лет")
    lines.append(f"  T₀ (базовая):      {t0:.1f} °C  ({273 + t0:.1f} К)")
    lines.append(f"  T_new (новая):     {tnew:.1f} °C  ({273 + tnew:.1f} К)")
    lines.append(sep)

    # Ширины колонок
    cw = (12, 6, 12, 12, 12, 10, 10, 10, 10, 9)

    def _section(channel: str, label: str) -> list[str]:
        subset = sorted(
            [c for c in comparisons if c.channel == channel],
            key=lambda c: c.delta_vth_tnew_v,
            reverse=True,
        )
        if not subset:
            return []
        sec: list[str] = []
        sec.append("")
        sec.append(f"  {label}")
        sec.append("-" * 100)
        header = (
            f"  {'Транзистор':<{cw[0]}} {'Тип':<{cw[1]}} "
            f"{'Свежий, мВ':>{cw[2]}} {'T₀, мВ':>{cw[3]}} "
            f"{'T_new, мВ':>{cw[4]}} {'Δ, мВ':>{cw[5]}} "
            f"{'μ(свеж)':>{cw[6]}} {'μ(T₀)':>{cw[7]}} "
            f"{'μ(T_new)':>{cw[8]}} {'Δμ, %':>{cw[9]}}"
        )
        sec.append(header)
        sec.append("-" * 100)
        for c in subset:
            sec.append(
                f"  {c.name:<{cw[0]}} {c.channel:<{cw[1]}} "
                f"{'0.0000':>{cw[2]}} "
                f"{c.delta_vth_t0_v * 1000:>{cw[3]}.4f} "
                f"{c.delta_vth_tnew_v * 1000:>{cw[4]}.4f} "
                f"{c.delta_vth_change_mv:>{cw[5]}.4f} "
                f"{'1.000000':>{cw[6]}} "
                f"{c.mobility_factor_t0:>{cw[7]}.6f} "
                f"{c.mobility_factor_tnew:>{cw[8]}.6f} "
                f"{c.mobility_change_pct:>{cw[9]}.4f}"
            )
        return sec

    lines.extend(_section("NMOS", "NMOS — Эффект горячих носителей (HCI)"))
    lines.extend(_section("PMOS", "PMOS — Температурная нестабильность при отрицательном смещении (NBTI)"))

    lines.append("")
    lines.append(sep)
    lines.append("  Обозначения:")
    lines.append("    ΔVth (мВ) — сдвиг порогового напряжения за срок службы")
    lines.append("    μ         — коэффициент деградации подвижности (u0_aged = u0 / μ)")
    lines.append("    Δ         — изменение при переходе T₀ → T_new")
    lines.append("    Свежий    — транзистор без деградации (ΔVth=0, μ=1.0)")

    filename.write_text("\n".join(lines), encoding="utf-8")
    return filename
