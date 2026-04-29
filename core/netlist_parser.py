"""
Парсинг SPICE-netlist (.cir/.net/.txt) и Spice Error Log (.log).
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from utils.file_io import read_lines


# ---------------------------------------------------------------------------
# Структуры данных
# ---------------------------------------------------------------------------

@dataclass
class Transistor:
    """Описание одного МОП-транзистора из netlist."""
    name: str           # M1, M2, …
    drain: str
    gate: str
    source: str
    bulk: str
    model: str          # NMOS / PMOS / CMOSN / CMOSP
    length_m: float     # длина канала в метрах
    width_m: float      # ширина канала в метрах
    raw_line: str       # исходная строка из netlist

    @property
    def is_nmos(self) -> bool:
        return "N" in self.model.upper()

    @property
    def is_pmos(self) -> bool:
        return "P" in self.model.upper()


@dataclass
class NetlistData:
    """Полное содержимое netlist."""
    lines: list[str]                          # все строки файла
    schema_path: str                          # путь к .asc схеме (первая строка)
    transistors: list[Transistor] = field(default_factory=list)
    tox_nmos: Optional[float] = None          # толщина оксида затвора NMOS (м)
    tox_pmos: Optional[float] = None          # толщина оксида затвора PMOS (м)
    supply_voltage_v: Optional[float] = None  # напряжение питания (В)


@dataclass
class LogMeasurement:
    """Одно измерение из Spice Error Log."""
    name: str    # hci_m1, maxhci_m1, maxid_m1, nbti_m4, …
    value: float
    time_start: float
    time_end: float


@dataclass
class LogData:
    """Содержимое Spice Error Log."""
    measurements: dict[str, LogMeasurement]   # name → measurement
    temperature_c: float = 27.0
    sim_time_s: float = 0.0


# ---------------------------------------------------------------------------
# Вспомогательные функции разбора значений с суффиксами
# ---------------------------------------------------------------------------

_SUFFIX_MAP = {
    "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
    "m": 1e-3,  "k": 1e3,   "meg": 1e6, "g": 1e9,
}

_SPICE_VALUE_RE = re.compile(
    r"([+-]?\d+(?:\.\d*)?(?:e[+-]?\d+)?)\s*(meg|[fpnumkgMG])?",
    re.IGNORECASE,
)


def parse_spice_value(token: str) -> float:
    """Разбирает SPICE-значение вида '1.2u', '3.9e-9', '100n' → float (СИ)."""
    token = token.strip()
    m = _SPICE_VALUE_RE.fullmatch(token)
    if not m:
        raise ValueError(f"Не удалось разобрать значение: {token!r}")
    number = float(m.group(1))
    suffix = (m.group(2) or "").lower()
    multiplier = _SUFFIX_MAP.get(suffix, 1.0)
    return number * multiplier


def _extract_param(token: str) -> float:
    """Извлекает числовое значение из токена вида 'l=100n' или '100n'."""
    if "=" in token:
        token = token.split("=", 1)[1]
    return parse_spice_value(token)


# ---------------------------------------------------------------------------
# Парсинг netlist
# ---------------------------------------------------------------------------

def _find_param_token(tokens: list[str], prefix: str) -> Optional[str]:
    """Ищет токен, начинающийся с prefix= (без учёта регистра)."""
    for tok in tokens:
        if tok.lower().startswith(prefix.lower() + "="):
            return tok
    return None


def _parse_transistor_line(raw: str) -> Optional[Transistor]:
    """
    Разбирает строку вида:
      M1 drain gate source bulk NMOS l=100n w=1u ...
    Возвращает Transistor или None, если строка не транзисторная.
    """
    tokens = raw.split()
    if not tokens or tokens[0][0] not in ("M", "m"):
        return None
    if len(tokens) < 7:
        return None

    name = tokens[0].upper()
    drain, gate, source, bulk = tokens[1], tokens[2], tokens[3], tokens[4]
    model = tokens[5]

    tok_l = _find_param_token(tokens[6:], "l")
    tok_w = _find_param_token(tokens[6:], "w")

    try:
        length_m = _extract_param(tok_l) if tok_l else 0.0
        width_m = _extract_param(tok_w) if tok_w else 0.0
    except ValueError:
        length_m = width_m = 0.0

    return Transistor(
        name=name,
        drain=drain,
        gate=gate,
        source=source,
        bulk=bulk,
        model=model,
        length_m=length_m,
        width_m=width_m,
        raw_line=raw,
    )


def _parse_tox(lines: list[str]) -> tuple[Optional[float], Optional[float]]:
    """
    Ищет toxe (эффективная толщина оксида) для NMOS и PMOS в строках .model.

    Поддерживает оба формата SPICE-моделей:
      - слитный:     toxe=3.9e-9
      - с пробелами: toxe = 3.9e-9   (три отдельных токена — как в PTM/BSIM4)
    """
    tox_n: Optional[float] = None
    tox_p: Optional[float] = None
    current_type: Optional[str] = None

    for line in lines:
        low = line.lower()
        # Определяем тип модели на строках .model
        if ".model" in low:
            if "nmos" in low:
                current_type = "nmos"
            elif "pmos" in low:
                current_type = "pmos"
            else:
                current_type = None

        if not current_type or "tox" not in low:
            continue

        tokens = line.split()
        for i, tok in enumerate(tokens):
            tl = tok.lower()

            # Формат 1: toxe=3.9e-9  (всё в одном токене)
            if tl.startswith("tox") and "=" in tok:
                try:
                    val = _extract_param(tok)
                    if current_type == "nmos" and tox_n is None:
                        tox_n = val
                    elif current_type == "pmos" and tox_p is None:
                        tox_p = val
                except ValueError:
                    pass
                break   # нашли — берём первое вхождение в строке

            # Формат 2: toxe = 3.9e-9  (три отдельных токена)
            if tl.startswith("tox") and i + 2 < len(tokens) and tokens[i + 1] == "=":
                try:
                    val = parse_spice_value(tokens[i + 2])
                    if current_type == "nmos" and tox_n is None:
                        tox_n = val
                    elif current_type == "pmos" and tox_p is None:
                        tox_p = val
                except ValueError:
                    pass
                break   # нашли — берём первое вхождение в строке

    return tox_n, tox_p


def _parse_supply_voltage(lines: list[str]) -> Optional[float]:
    """
    Ищет напряжение питания в netlist.

    Поддерживаемые форматы источников напряжения:
      Vdd VDD 0 1.8
      V1  VDD 0 DC 1.8
      Vcc net 0 1.2

    Берёт максимальное положительное DC-напряжение среди всех источников,
    один из узлов которых — земля (0 / gnd / vss).
    """
    _GROUND = {"0", "gnd", "vss", "agnd", "dgnd"}
    candidates: list[float] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped[0] not in ("V", "v"):
            continue
        tokens = stripped.split()
        if len(tokens) < 4:
            continue
        node_p, node_n = tokens[1].lower(), tokens[2].lower()
        if node_p not in _GROUND and node_n not in _GROUND:
            continue  # ни один узел не земля — пропускаем

        # Ищем числовое значение: следующий токен после необязательного "DC"
        val_tokens = [t for t in tokens[3:] if t.upper() not in ("DC", "AC")]
        if not val_tokens:
            continue
        try:
            v = parse_spice_value(val_tokens[0])
            if v > 0:
                candidates.append(v)
        except ValueError:
            pass
    return max(candidates) if candidates else None


def parse_netlist(path: str | Path) -> NetlistData:
    """Читает netlist и возвращает структурированные данные."""
    lines = read_lines(path)

    schema_path = ""
    if lines:
        schema_path = lines[0].lstrip("* ").strip()

    transistors: list[Transistor] = []
    for line in lines:
        stripped = line.strip()
        if stripped and stripped[0] in ("M", "m"):
            t = _parse_transistor_line(stripped)
            if t:
                transistors.append(t)

    tox_n, tox_p = _parse_tox(lines)
    supply_v = _parse_supply_voltage(lines)

    return NetlistData(
        lines=lines,
        schema_path=schema_path,
        transistors=transistors,
        tox_nmos=tox_n,
        tox_pmos=tox_p,
        supply_voltage_v=supply_v,
    )


# ---------------------------------------------------------------------------
# Парсинг Spice Error Log
# ---------------------------------------------------------------------------

_MEAS_RE = re.compile(
    r"^(\w+):\s+\w+\(.*?\)\s*=\s*([+-]?\S+)\s+FROM\s+([+-]?\S+)\s+TO\s+([+-]?\S+)",
    re.IGNORECASE,
)
_TEMP_RE = re.compile(r"^\.temp\s+([+-]?\d+(?:\.\d*)?)", re.IGNORECASE)


def parse_log(path: str | Path, first_measure_name: str, n_measures: int) -> LogData:
    """
    Читает Spice Error Log.

    Parameters
    ----------
    first_measure_name:
        Имя первого ожидаемого измерения (нижний регистр), с которого
        начинается блок данных (например 'hci_m1' или 'maxnbtim4').
    n_measures:
        Количество строк измерений в блоке.
    """
    lines = read_lines(path)
    measurements: dict[str, LogMeasurement] = {}
    temperature_c = 27.0
    sim_time_s = 0.0

    # Ищем температуру
    for line in lines:
        m = _TEMP_RE.match(line.strip())
        if m:
            temperature_c = float(m.group(1))
            break

    # Ищем блок измерений начиная с first_measure_name
    block_start = -1
    for i, line in enumerate(lines):
        if line.strip().lower().startswith(first_measure_name.lower()):
            block_start = i
            break

    if block_start == -1:
        # Читаем все .measure строки как fallback
        for line in lines:
            m = _MEAS_RE.match(line.strip())
            if m:
                name = m.group(1).lower()
                try:
                    value = float(m.group(2))
                    t_start = float(m.group(3))
                    t_end = float(m.group(4))
                    measurements[name] = LogMeasurement(name, value, t_start, t_end)
                    sim_time_s = max(sim_time_s, t_end)
                except ValueError:
                    pass
    else:
        for line in lines[block_start: block_start + n_measures]:
            m = _MEAS_RE.match(line.strip())
            if m:
                name = m.group(1).lower()
                try:
                    value = float(m.group(2))
                    t_start = float(m.group(3))
                    t_end = float(m.group(4))
                    measurements[name] = LogMeasurement(name, value, t_start, t_end)
                    sim_time_s = max(sim_time_s, t_end)
                except ValueError:
                    pass

    return LogData(
        measurements=measurements,
        temperature_c=temperature_c,
        sim_time_s=sim_time_s,
    )


# ---------------------------------------------------------------------------
# Генерация .inc файла с командами для LTSpice
# ---------------------------------------------------------------------------

_LEN_NM_MAP = [
    ("u", 1e-6), ("m", 1e-3), ("n", 1e-9), ("p", 1e-12),
]


def _length_to_nm(length_m: float) -> float:
    return length_m * 1e9


def generate_inc_lines(transistors: list[Transistor]) -> list[str]:
    """
    Генерирует строки .inc файла с B-источниками (HCI) и .measure (NBTI/HCI).
    Логика полностью соответствует оригинальной функции el_read().
    """
    lines: list[str] = []
    for t in transistors:
        num = t.name.lstrip("M")
        if t.is_nmos:
            length_nm = _length_to_nm(t.length_m)
            if length_nm <= 0:
                continue
            lines += [
                f"B{num}Ib 0 N{num}Ib I=(1e-5)*abs(Id({t.name}))"
                f"*pwr((1/{length_nm}), 2.5)"
                f"*exp(5.7*(V({t.drain})-V({t.source})))"
                f"*exp(-1.21*(V({t.drain})-V({t.gate})))",
                f"R{num}Ib N{num}Ib 0 1",
                f".measure tran HCI_{t.name} integ I(B{num}Ib)",
                f".measure tran maxHCI_{t.name} max I(B{num}Ib)",
                f".measure tran maxId_{t.name} max abs(Id({t.name}))",
            ]
        elif t.is_pmos:
            # Оригинальная формула el_read(): измеряем Vsg*Vdg и ∫|Vsg|=∫|Vgs|
            # (source-gate и drain-gate), а не drain-source/bulk-source
            lines += [
                f".measure tran maxNBTI{t.name} max "
                f"((V({t.source})-V({t.gate}))*(V({t.drain})-V({t.gate})))",
                f".measure tran NBTI{t.name} integ ABS(V({t.source})-V({t.gate}))",
            ]
    return lines
