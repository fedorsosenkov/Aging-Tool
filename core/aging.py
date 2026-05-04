"""
Физические модели старения транзисторов: NBTI и HCI.
Все формулы сохранены из оригинального кода без изменений.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field

import numpy as np

from core.netlist_parser import LogData, Transistor


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

_K_BOLTZMANN = 1.380649e-23   # Дж/К
_Q_ELECTRON  = 1.60219e-19    # Кл
_EPSILON_0   = 8.85e-12       # Ф/м
_EPSILON_SI02 = 3.9           # относительная диэлектрическая проницаемость SiO2
_SECONDS_PER_YEAR = 31_536_000


# ---------------------------------------------------------------------------
# Результаты расчёта
# ---------------------------------------------------------------------------

@dataclass
class TransistorAgingResult:
    name: str
    channel: str                  # "NMOS" или "PMOS"

    # Общее
    sim_time_s: float = 0.0

    # NBTI (только PMOS)
    nbti_active_time_s: float = 0.0
    nbti_ratio: float = 0.0       # доля времени в режиме NBTI
    nbti_delta_vth_v: float = 0.0 # сдвиг порогового напряжения
    nbti_mobility_factor: float = 1.0  # знаменатель для u0
    nbti_n_it_m2: float = 0.0     # концентрация интерфейсных ловушек [м⁻²]
    # Сырые данные из LTspice (NBTI)
    nbti_integ_v_s: float = 0.0   # ∫ |Vsg| dt  [В·с]
    nbti_max_vsg_vdg: float = 0.0 # max(Vsg·Vdg) [В²]

    # HCI (только NMOS)
    hci_active_time_s: float = 0.0
    hci_ratio: float = 0.0
    hci_delta_vth_v: float = 0.0
    hci_mobility_factor: float = 1.0
    hci_n_it_m2: float = 0.0      # концентрация интерфейсных ловушек [м⁻²]
    # Сырые данные из LTspice (HCI)
    hci_integ_a_s: float = 0.0    # ∫ I_hci dt  [А·с]
    hci_max_hci_a: float = 0.0    # max I_hci   [А]
    hci_max_id_a: float = 0.0     # max |Id|    [А]


@dataclass
class AgingResults:
    transistors: list[TransistorAgingResult] = field(default_factory=list)
    temperature_c: float = 27.0
    sim_time_s: float = 0.0
    supply_voltage_v: float | None = None

    @property
    def temperature_k(self) -> float:
        return 273 + self.temperature_c

    def by_name(self, name: str) -> TransistorAgingResult | None:
        for t in self.transistors:
            if t.name == name:
                return t
        return None

    def nmos_results(self) -> list[TransistorAgingResult]:
        return [t for t in self.transistors if t.channel == "NMOS"]

    def pmos_results(self) -> list[TransistorAgingResult]:
        return [t for t in self.transistors if t.channel == "PMOS"]


# ---------------------------------------------------------------------------
# NBTI-расчёт
# ---------------------------------------------------------------------------

def _nbti_kv(vgs_max: float, vth: float, tox_m: float, kelvin: float) -> float:
    """
    Коэффициент Kv для модели NBTI.
    Формула сохранена из оригинала без изменений.
    """
    eps_ox = _EPSILON_SI02 * _EPSILON_0
    cox = eps_ox / tox_m
    factor1 = (tox_m * _Q_ELECTRON / eps_ox) ** 3
    factor2 = (1e26) ** 2
    factor3 = cox
    factor4 = abs(vgs_max) - vth
    factor5 = (math.exp(-7.850731e-20 / (_K_BOLTZMANN * kelvin)) / 1e10) ** 0.5
    factor6 = math.exp(2 * (abs(vgs_max) - vth) / (tox_m * 0.355e9))
    return factor1 * factor2 * factor3 * factor4 * factor5 * factor6


def calculate_nbti(
    transistor: Transistor,
    log: LogData,
    tox_pmos: float,
    vth_pmos: float,
    target_years: float,
    sigma_mobility: float = 0.24e-16,
) -> TransistorAgingResult:
    """
    Рассчитывает деградацию PMOS-транзистора по модели NBTI.
    """
    result = TransistorAgingResult(
        name=transistor.name,
        channel="PMOS",
        sim_time_s=log.sim_time_s,
    )
    kelvin = 273 + log.temperature_c
    years_s = _SECONDS_PER_YEAR * target_years
    n_exp = 1 / 6  # показатель степени NBTI

    name_lower = transistor.name.lower()
    key_integ = f"nbti{name_lower}"
    key_max   = f"maxnbti{name_lower}"

    meas_integ = log.measurements.get(key_integ)
    meas_max   = log.measurements.get(key_max)

    if meas_integ is None or meas_max is None or log.sim_time_s == 0:
        return result

    integ_val = meas_integ.value   # ∫|Vsg| dt = ∫|Vgs| dt
    max_val   = meas_max.value     # max(Vsg · Vdg) — как в оригинале el_read()

    if max_val == 0:
        return result

    active_time = integ_val / max_val
    ratio = active_time / log.sim_time_s

    kv = _nbti_kv(abs(max_val), vth_pmos, tox_pmos, kelvin)
    raw = kv * ((ratio * 2 * years_s) ** 0.5)
    delta_vth = 0.7 * (raw ** (2 * n_exp)) if raw > 0 else 0.0

    eps_ox = _EPSILON_SI02 * _EPSILON_0
    n_it = delta_vth * eps_ox / (tox_pmos * _Q_ELECTRON)
    mobility_factor = 1 + sigma_mobility * n_it

    result.nbti_active_time_s = active_time
    result.nbti_ratio = ratio
    result.nbti_delta_vth_v = delta_vth
    result.nbti_mobility_factor = mobility_factor
    result.nbti_n_it_m2 = n_it
    result.nbti_integ_v_s = integ_val
    result.nbti_max_vsg_vdg = max_val
    return result


# ---------------------------------------------------------------------------
# HCI-расчёт
# ---------------------------------------------------------------------------

def calculate_hci(
    transistor: Transistor,
    log: LogData,
    tox_nmos: float,
    target_years: float,
    n_hci: float = 0.27,
    sigma_mobility: float = 0.24e-16,
) -> TransistorAgingResult:
    """
    Рассчитывает деградацию NMOS-транзистора по модели HCI.
    """
    result = TransistorAgingResult(
        name=transistor.name,
        channel="NMOS",
        sim_time_s=log.sim_time_s,
    )
    years_s = _SECONDS_PER_YEAR * target_years
    c22 = 0.22
    m_exp = 2
    n_exp = n_hci

    name_lower = transistor.name.lower()
    key_integ  = f"hci_{name_lower}"
    key_max    = f"maxhci_{name_lower}"
    key_maxid  = f"maxid_{name_lower}"

    meas_integ = log.measurements.get(key_integ)
    meas_max   = log.measurements.get(key_max)
    meas_maxid = log.measurements.get(key_maxid)

    if None in (meas_integ, meas_max, meas_maxid) or log.sim_time_s == 0:
        return result

    integ_val = meas_integ.value   # ∫i(Bib) dt
    max_hci   = meas_max.value     # max i(Bib)
    max_id    = meas_maxid.value   # max Id

    if max_hci == 0 or max_id == 0 or transistor.width_m == 0:
        return result

    active_time = abs(integ_val / (max_hci * 0.75))
    ratio = abs(integ_val / (max_hci * 0.75 * log.sim_time_s))

    # ΔVth = 1.3 * c22 * |Id_max/W * (i_max/Id_max)^m * years * ratio|^n
    delta_vth = 1.3 * c22 * abs(
        (max_id / transistor.width_m)
        * ((max_hci / max_id) ** m_exp)
        * years_s
        * ratio
    ) ** n_exp

    eps_ox = _EPSILON_SI02 * _EPSILON_0
    n_it = delta_vth * eps_ox / (tox_nmos * _Q_ELECTRON)
    mobility_factor = 1 + sigma_mobility * n_it

    result.hci_active_time_s = active_time
    result.hci_ratio = ratio
    result.hci_delta_vth_v = delta_vth
    result.hci_mobility_factor = mobility_factor
    result.hci_n_it_m2 = n_it
    result.hci_integ_a_s = integ_val
    result.hci_max_hci_a = max_hci
    result.hci_max_id_a = max_id
    return result


# ---------------------------------------------------------------------------
# Основная точка входа
# ---------------------------------------------------------------------------

def run_aging_analysis(
    transistors: list[Transistor],
    log: LogData,
    chosen_names: list[str],
    tox_nmos: float,
    tox_pmos: float,
    vth_pmos: float,
    target_years: float,
    supply_voltage_v: float | None = None,
    n_hci: float = 0.27,
    sigma_mobility: float = 0.24e-16,
) -> AgingResults:
    """
    Запускает расчёт старения для всех выбранных транзисторов.
    """
    results = AgingResults(
        temperature_c=log.temperature_c,
        sim_time_s=log.sim_time_s,
        supply_voltage_v=supply_voltage_v,
    )

    chosen_upper = [n.upper() for n in chosen_names]
    chosen_map = {t.name: t for t in transistors if t.name in chosen_upper}

    for name in chosen_upper:
        t = chosen_map.get(name)
        if t is None:
            continue
        if t.is_pmos:
<<<<<<< Updated upstream
            res = calculate_nbti(t, log, tox_pmos, vth_pmos, target_years, sigma_mobility)
=======
            # Предпочитаем vth0, запарсенный из .model блока; пользовательское
            # значение используется только как резерв
            vth_use = abs(t.vth0) if t.vth0 is not None else vth_pmos
            res = calculate_nbti(t, log, tox_pmos, vth_use, target_years, sigma_mobility)
>>>>>>> Stashed changes
        else:
            res = calculate_hci(t, log, tox_nmos, target_years, n_hci, sigma_mobility)
        results.transistors.append(res)

    return results


# ---------------------------------------------------------------------------
# Категоризация деградации (для замены модели в netlist)
# ---------------------------------------------------------------------------

def vth_category(ratio: float) -> int:
    """
    Возвращает номер категории деградации порогового напряжения (1–3)
    или 0, если деградация незначительна (ratio ≤ 1).

    Категории:
      1 → сдвиг Vth  0–2.5 %   (ratio 1.00 – 1.025)
      2 → сдвиг Vth  2.5–5 %   (ratio 1.025 – 1.05)
      3 → сдвиг Vth  > 5 %     (ratio > 1.05, включая любые экстремальные значения)

    ratio = new_vth / old_vth  (для PMOS: |Vth_aged| / |Vth_fresh|)
    """
    if 1 < ratio <= 1.025:
        return 1
    elif 1.025 < ratio <= 1.05:
        return 2
    elif ratio > 1.05:
        return 3
    return 0
