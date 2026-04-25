"""
Анализ температурного влияния на деградацию МОП-транзисторов.

Алгоритм:
  - PMOS (NBTI): температура влияет через коэффициент Kv в модели NBTI —
    формула уже содержит kelvin, достаточно подставить новую T.
  - NMOS (HCI): применяется аррениусовская поправка к интегралу тока
    из оригинальной формулы: factor = 10 * exp(-Ea / (k * T)).
"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from core.aging import (
    AgingResults,
    TransistorAgingResult,
    calculate_nbti,
    calculate_hci,
    _K_BOLTZMANN,
)
from core.netlist_parser import LogData, LogMeasurement, Transistor


# ---------------------------------------------------------------------------
# Физические константы для HCI
# ---------------------------------------------------------------------------

_Ea_HCI = 9.6e-21  # Энергия активации HCI (Дж), из оригинального кода


# ---------------------------------------------------------------------------
# Структура результата сравнения
# ---------------------------------------------------------------------------

@dataclass
class TemperatureComparisonResult:
    """Сравнение параметров деградации при двух температурах."""
    name: str
    channel: str        # "NMOS" или "PMOS"
    t0_c: float         # базовая температура (из log-файла)
    t_new_c: float      # новая температура

    delta_vth_t0_v: float         # ΔVth при T0
    delta_vth_tnew_v: float       # ΔVth при T_new
    mobility_factor_t0: float     # знаменатель u0 при T0
    mobility_factor_tnew: float   # знаменатель u0 при T_new

    @property
    def delta_vth_change_mv(self) -> float:
        """Изменение ΔVth (T_new − T0), мВ."""
        return (self.delta_vth_tnew_v - self.delta_vth_t0_v) * 1000

    @property
    def mobility_change_pct(self) -> float:
        """Относительное изменение коэф. деградации подвижности, %."""
        if self.mobility_factor_t0 == 0:
            return 0.0
        return (
            (self.mobility_factor_tnew - self.mobility_factor_t0)
            / self.mobility_factor_t0
            * 100
        )


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _hci_temp_factor(kelvin: float) -> float:
    """
    Аррениусовский температурный коэффициент для HCI.
    Формула из оригинального кода: 10 * exp(-Ea / (k * T)).
    Модифицирует интегральное значение тока substrate-инжекции.
    """
    return 10.0 * math.exp(-_Ea_HCI / (_K_BOLTZMANN * kelvin))


def _recalculate_pmos_at_temp(
    transistor: Transistor,
    log: LogData,
    tox_pmos: float,
    vth_pmos: float,
    target_years: float,
    new_temp_c: float,
) -> TransistorAgingResult:
    """Пересчитывает NBTI-деградацию PMOS при новой температуре."""
    log_new = deepcopy(log)
    log_new.temperature_c = new_temp_c
    return calculate_nbti(transistor, log_new, tox_pmos, vth_pmos, target_years)


def _recalculate_nmos_at_temp(
    transistor: Transistor,
    log: LogData,
    tox_nmos: float,
    target_years: float,
    new_temp_c: float,
) -> TransistorAgingResult:
    """
    Пересчитывает HCI-деградацию NMOS при новой температуре.
    Применяет аррениусовскую поправку к интегральному значению тока.
    """
    name_lower = transistor.name.lower()
    key_integ = f"hci_{name_lower}"

    meas_integ = log.measurements.get(key_integ)
    if meas_integ is None or log.sim_time_s == 0:
        return calculate_hci(transistor, log, tox_nmos, target_years)

    kelvin_new = 273.0 + new_temp_c
    factor = _hci_temp_factor(kelvin_new)

    # Создаём копию LogData с поправленным integ-значением
    log_new = deepcopy(log)
    old_val = meas_integ.value
    log_new.measurements[key_integ] = LogMeasurement(
        name=key_integ,
        value=old_val * factor,
        time_start=meas_integ.time_start,
        time_end=meas_integ.time_end,
    )
    return calculate_hci(transistor, log_new, tox_nmos, target_years)


# ---------------------------------------------------------------------------
# Основная функция сравнения
# ---------------------------------------------------------------------------

def compare_at_temperature(
    aging_results: AgingResults,
    transistors: list[Transistor],
    log: LogData,
    tox_nmos: float,
    tox_pmos: float,
    vth_pmos: float,
    target_years: float,
    new_temp_c: float,
) -> list[TemperatureComparisonResult]:
    """
    Рассчитывает деградацию при новой температуре и строит таблицу сравнения.

    Parameters
    ----------
    aging_results : AgingResults
        Результаты при базовой температуре (из log-файла).
    transistors : list[Transistor]
        Список транзисторов из netlist (для физических параметров).
    log : LogData
        Исходный log-файл (измерения токов/напряжений).
    new_temp_c : float
        Новая температура в °C.

    Returns
    -------
    list[TemperatureComparisonResult]
    """
    trans_map = {t.name: t for t in transistors}
    comparisons: list[TemperatureComparisonResult] = []

    for ar in aging_results.transistors:
        t = trans_map.get(ar.name)
        if t is None:
            continue

        if ar.channel == "PMOS":
            ar_new = _recalculate_pmos_at_temp(
                t, log, tox_pmos, vth_pmos, target_years, new_temp_c
            )
            comp = TemperatureComparisonResult(
                name=ar.name,
                channel="PMOS",
                t0_c=aging_results.temperature_c,
                t_new_c=new_temp_c,
                delta_vth_t0_v=ar.nbti_delta_vth_v,
                delta_vth_tnew_v=ar_new.nbti_delta_vth_v,
                mobility_factor_t0=ar.nbti_mobility_factor,
                mobility_factor_tnew=ar_new.nbti_mobility_factor,
            )
        else:
            ar_new = _recalculate_nmos_at_temp(
                t, log, tox_nmos, target_years, new_temp_c
            )
            comp = TemperatureComparisonResult(
                name=ar.name,
                channel="NMOS",
                t0_c=aging_results.temperature_c,
                t_new_c=new_temp_c,
                delta_vth_t0_v=ar.hci_delta_vth_v,
                delta_vth_tnew_v=ar_new.hci_delta_vth_v,
                mobility_factor_t0=ar.hci_mobility_factor,
                mobility_factor_tnew=ar_new.hci_mobility_factor,
            )
        comparisons.append(comp)

    return comparisons


# ---------------------------------------------------------------------------
# Запись результатов сравнения в файл
# ---------------------------------------------------------------------------

def write_temp_comparison_log(
    comparisons: list[TemperatureComparisonResult],
    output_path: Path,
    target_years: float,
) -> None:
    """
    Сохраняет сравнительную таблицу параметров деградации в текстовый файл.
    Формат файла: *_temp.log
    """
    if not comparisons:
        return

    t0 = comparisons[0].t0_c
    t_new = comparisons[0].t_new_c

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("  Сравнение результатов старения при разных температурах")
    lines.append("=" * 80)
    lines.append(f"  Срок службы: {target_years:.0f} лет")
    lines.append(f"  T0   = {t0:>6.1f} °C  ({273 + t0:.1f} К)  — базовая (из log-файла)")
    lines.append(f"  T_new = {t_new:>6.1f} °C  ({273 + t_new:.1f} К)  — новая")
    lines.append("=" * 80)
    lines.append("")

    # Таблица
    col_w = [12, 6, 15, 16, 13, 11, 11, 9]
    header = (
        f"{'Транзистор':<{col_w[0]}} "
        f"{'Тип':<{col_w[1]}} "
        f"{'ΔVth(T0), мВ':>{col_w[2]}} "
        f"{'ΔVth(T_new), мВ':>{col_w[3]}} "
        f"{'Δ(ΔVth), мВ':>{col_w[4]}} "
        f"{'μ(T0)':>{col_w[5]}} "
        f"{'μ(T_new)':>{col_w[6]}} "
        f"{'Δμ, %':>{col_w[7]}}"
    )
    sep = "-" * (sum(col_w) + len(col_w))
    lines.append(header)
    lines.append(sep)

    for c in comparisons:
        row = (
            f"{c.name:<{col_w[0]}} "
            f"{c.channel:<{col_w[1]}} "
            f"{c.delta_vth_t0_v * 1000:>{col_w[2]}.4f} "
            f"{c.delta_vth_tnew_v * 1000:>{col_w[3]}.4f} "
            f"{c.delta_vth_change_mv:>{col_w[4]}.4f} "
            f"{c.mobility_factor_t0:>{col_w[5]}.6f} "
            f"{c.mobility_factor_tnew:>{col_w[6]}.6f} "
            f"{c.mobility_change_pct:>{col_w[7]}.4f}"
        )
        lines.append(row)

    lines.append("")
    lines.append("Обозначения:")
    lines.append(
        "  ΔVth  — сдвиг порогового напряжения за срок службы (мВ)"
    )
    lines.append(
        "  μ     — коэффициент деградации подвижности (u0_aged = u0 / μ)"
    )
    lines.append(
        "  Δ     — изменение при переходе T0 → T_new"
    )
    lines.append(
        "  NMOS: температурная поправка HCI по формуле Аррениуса."
    )
    lines.append(
        "  PMOS: температурная поправка NBTI через коэффициент Kv(T)."
    )

    output_path.write_text("\n".join(lines), encoding="utf-8")
