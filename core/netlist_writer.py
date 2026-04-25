"""
Генерация постаревшего netlist (_aged.cir) и файла моделей (models_aged.txt).
Также: create_aged_asc() — создание постаревшей .asc-схемы LTspice.

Логика (netlist):
  1. Копируем netlist построчно.
  2. Строку .lib заменяем так, чтобы она ссылалась на models_aged.txt.
  3. Строки транзисторов (M...) получают новое имя модели (NMOS1, PMOS2, …).
  4. Строки .model* записываем ТОЛЬКО в models_aged.txt с исправленными
     параметрами vth0 и u0.

Логика (asc):
  1. Читаем .asc построчно.
  2. SYMATTR Value / SYMATTR SpiceModel → заменяем имя модели постаревшим.
  3. TEXT-строки с .lib → заменяем путь на models_aged.txt.
"""

from __future__ import annotations
import re
from pathlib import Path

from core.aging import AgingResults, vth_category
from core.netlist_parser import NetlistData, Transistor
from utils.file_io import write_text


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _aged_model_name(base_model: str, category: int) -> str:
    """NMOS → NMOS1 (или NMOS если category==0)."""
    if category == 0:
        return base_model
    return base_model.rstrip("0123456789") + str(category)


def _vth_multiplier(category: int) -> float:
    return {1: 1.0125, 2: 1.0375, 3: 1.0625}.get(category, 1.0)


def _find_token_index(tokens: list[str], keyword: str) -> int:
    """Ищет индекс токена, содержащего keyword (без учёта регистра)."""
    for i, tok in enumerate(tokens):
        if keyword.lower() in tok.lower():
            return i
    return -1


def _replace_param_in_line(line: str, keyword: str, new_value: str) -> str:
    """Заменяет значение параметра keyword в строке."""
    tokens = line.split()
    idx = _find_token_index(tokens, keyword)
    if idx == -1:
        return line
    tok = tokens[idx]
    if "=" in tok:
        key_part = tok.split("=")[0]
        tokens[idx] = f"{key_part}={new_value}"
    elif idx + 1 < len(tokens):
        tokens[idx + 1] = new_value
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Построение карты «имя транзистора → (новая модель, категория)»
# ---------------------------------------------------------------------------

def _build_transistor_model_map(
    netlist: NetlistData,
    aging: AgingResults,
    chosen_names: list[str],
) -> dict[str, tuple[str, int]]:
    """
    Возвращает: {transistor_name: (new_model_name, category)}
    Например: {"M1": ("NMOS2", 2), "M4": ("PMOS1", 1)}
    """
    chosen_upper = {n.upper() for n in chosen_names}
    result: dict[str, tuple[str, int]] = {}

    for t in netlist.transistors:
        if t.name not in chosen_upper:
            continue
        ar = aging.by_name(t.name)
        if ar is None:
            continue

        base = t.model.upper()  # NMOS / PMOS / CMOSN / CMOSP

        if t.is_nmos:
            if ar.hci_delta_vth_v == 0:
                result[t.name] = (base, 0)
                continue
            # vth сдвигается вверх для NMOS
            # ищем vth в оригинальной модели через tox — у нас нет прямого доступа,
            # поэтому оцениваем категорию через delta / типичное значение 0.4 В
            typical_vth = 0.4
            ratio = (typical_vth + ar.hci_delta_vth_v) / typical_vth
            cat = vth_category(ratio)
            result[t.name] = (_aged_model_name(base, cat), cat)

        elif t.is_pmos:
            if ar.nbti_delta_vth_v == 0:
                result[t.name] = (base, 0)
                continue
            typical_vth = -0.4
            ratio = (typical_vth - ar.nbti_delta_vth_v) / typical_vth
            cat = vth_category(ratio)
            result[t.name] = (_aged_model_name(base, cat), cat)

    return result


# ---------------------------------------------------------------------------
# Основная функция записи
# ---------------------------------------------------------------------------

def write_aged_netlist(
    netlist: NetlistData,
    aging: AgingResults,
    chosen_names: list[str],
    source_cir_path: str | Path,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    """
    Создаёт постаревший netlist и файл моделей.

    Returns
    -------
    (aged_cir_path, models_path)
    """
    source = Path(source_cir_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    aged_cir  = out_dir / (source.stem + "_aged.cir")
    models_txt = out_dir / "models_aged.txt"

    model_map = _build_transistor_model_map(netlist, aging, chosen_names)

    # Карта: base_model_upper → {cat → new_name}
    # Нужна чтобы при обработке .model знать все варианты имён
    unique_categories: dict[str, set[int]] = {}
    for tname, (new_model, cat) in model_map.items():
        t = next((x for x in netlist.transistors if x.name == tname), None)
        if t is None:
            continue
        base = t.model.upper()
        unique_categories.setdefault(base, set()).add(cat)

    # Соответствие транзистор → mobility_factor
    mobility_map: dict[str, float] = {}
    for ar in aging.transistors:
        if ar.channel == "NMOS":
            mobility_map[ar.name] = ar.hci_mobility_factor
        else:
            mobility_map[ar.name] = ar.nbti_mobility_factor

    # --- Обходим строки netlist ---
    cir_lines: list[str] = []
    models_blocks: list[str] = []
    in_model_block = False
    current_base_model = ""
    current_model_lines: list[str] = []

    lib_replaced = False

    def flush_model_block():
        nonlocal current_model_lines, current_base_model
        if not current_model_lines:
            return
        base = current_base_model.upper()
        cats = unique_categories.get(base, {0})
        for cat in sorted(cats):
            new_name = _aged_model_name(base, cat) if cat else base
            # Первая строка .model с новым именем
            first = current_model_lines[0]
            first_new = first.replace(
                current_base_model, new_name.lower(), 1
            ).replace(
                current_base_model.upper(), new_name, 1
            )
            block = [first_new]
            for raw_line in current_model_lines[1:]:
                line = raw_line
                if "vth" in line.lower() and cat:
                    mul = _vth_multiplier(cat)
                    toks = line.split()
                    vi = _find_token_index(toks, "vth")
                    if vi != -1:
                        tok = toks[vi]
                        if "=" in tok:
                            try:
                                val = float(tok.split("=")[1])
                                toks[vi] = tok.split("=")[0] + "=" + f"{val * mul:.4f}"
                                line = " ".join(toks)
                            except ValueError:
                                pass
                if "u0" in line.lower() and cat != 0:
                    # U0 корректируется индивидуально для каждой категории:
                    # берём mobility_factor только транзисторов этой категории.
                    def _same_type(ar: object) -> bool:
                        ch = getattr(ar, "channel", "")
                        return (
                            (ch == "NMOS" and (base.startswith("N") or ("CMOS" in base and "N" in base)))
                            or (ch == "PMOS" and (base.startswith("P") or ("CMOS" in base and "P" in base)))
                        )

                    # Транзисторы данной категории и данного типа
                    relevant = [
                        ar for ar in aging.transistors
                        if _same_type(ar) and model_map.get(ar.name, (None, -1))[1] == cat
                    ]
                    if not relevant:
                        # Фоллбэк: все транзисторы этого типа
                        relevant = [ar for ar in aging.transistors if _same_type(ar)]

                    if relevant:
                        avg_factor = sum(
                            ar.hci_mobility_factor if ar.channel == "NMOS"
                            else ar.nbti_mobility_factor
                            for ar in relevant
                        ) / len(relevant)
                        toks = line.split()
                        ui = _find_token_index(toks, "u0")
                        if ui != -1:
                            tok = toks[ui]
                            try:
                                if "=" in tok:
                                    val = float(tok.split("=")[1])
                                    toks[ui] = tok.split("=")[0] + "=" + f"{val / avg_factor:.5f}"
                                elif ui + 1 < len(toks):
                                    val = float(toks[ui + 1])
                                    toks[ui + 1] = f"{val / avg_factor:.5f}"
                                line = " ".join(toks)
                            except ValueError:
                                pass
                block.append(line)
            models_blocks.append("\n".join(block) + "\n")
        current_model_lines = []
        current_base_model = ""

    lines = netlist.lines
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        low = stripped.lower()

        # Строка .lib → меняем путь, в cir пишем новый
        if ".lib" in low and not lib_replaced:
            new_line = stripped.replace("standard.mos", "models_aged.txt") + "\n"
            cir_lines.append(new_line)
            lib_replaced = True
            i += 1
            continue

        # Начало .model блока
        if ".model" in low and ("nmos" in low or "pmos" in low):
            flush_model_block()
            in_model_block = True
            # Определяем базовое имя модели
            toks = stripped.split()
            current_base_model = toks[1] if len(toks) > 1 else ""
            current_model_lines = [stripped]
            # Не пишем эту строку в cir
            i += 1
            continue

        # Продолжение .model блока (строки с +)
        if in_model_block:
            if stripped.startswith("+"):
                current_model_lines.append(stripped)
                i += 1
                continue
            else:
                flush_model_block()
                in_model_block = False

        # Строка транзистора — заменяем имя модели
        if stripped and stripped[0].upper() == "M" and len(stripped.split()) >= 6:
            toks = stripped.split()
            tname = toks[0].upper()
            if tname in model_map:
                new_model, _ = model_map[tname]
                # Заменяем токен модели (позиция 5)
                old_model = toks[5]
                toks[5] = new_model if new_model else old_model
                cir_lines.append(" ".join(toks) + "\n")
                i += 1
                continue

        cir_lines.append(line if line.endswith("\n") else line + "\n")
        i += 1

    flush_model_block()

    # Записываем файлы
    write_text(aged_cir, "".join(cir_lines))
    write_text(models_txt, "\n".join(models_blocks))

    return aged_cir, models_txt


# ---------------------------------------------------------------------------
# Создание постаревшей .asc-схемы LTspice
# ---------------------------------------------------------------------------

def create_aged_asc(
    asc_path: str | Path,
    netlist: NetlistData,
    aging: AgingResults,
    chosen_names: list[str],
    output_dir: str | Path,
) -> Path:
    """
    Копирует .asc-схему LTspice в {stem}_aged.asc и подставляет постаревшие
    имена моделей транзисторов + обновляет ссылку на .lib.

    Формат LTspice .asc (релевантные строки):
        SYMBOL nmos x y R0
        SYMATTR InstName M1
        SYMATTR Value CMOSN          ← имя модели
        TEXT x y Left 2 ;.lib path  ← ссылка на файл моделей

    Returns
    -------
    Path к созданному _aged.asc
    """
    asc = Path(asc_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    aged_asc = out_dir / (asc.stem + "_aged.asc")

    model_map = _build_transistor_model_map(netlist, aging, chosen_names)
    # Ключи приводим к верхнему регистру для сравнения
    model_map_upper: dict[str, tuple[str, int]] = {
        k.upper(): v for k, v in model_map.items()
    }

    lib_replaced = False
    current_inst: str = ""

    out_lines: list[str] = []

    raw_text = asc.read_text(encoding="utf-8", errors="replace")
    for raw_line in raw_text.splitlines(keepends=True):
        stripped = raw_line.rstrip("\r\n")
        low = stripped.lower()
        eol = raw_line[len(stripped):]   # "\n" или "\r\n" или ""

        # Начало нового блока компонента — сбрасываем InstName
        if low.startswith("symbol "):
            current_inst = ""
            out_lines.append(raw_line)
            continue

        # Запоминаем имя экземпляра
        if low.startswith("symattr instname "):
            prefix_len = low.index("symattr instname ") + len("symattr instname ")
            current_inst = stripped[prefix_len:].strip()
            out_lines.append(raw_line)
            continue

        # Заменяем имя модели в SYMATTR Value или SYMATTR SpiceModel
        is_value      = low.startswith("symattr value ")
        is_spicemodel = low.startswith("symattr spicemodel ")
        if (is_value or is_spicemodel) and current_inst.upper() in model_map_upper:
            new_model, _ = model_map_upper[current_inst.upper()]
            if new_model:
                keyword = "symattr value " if is_value else "symattr spicemodel "
                idx = low.index(keyword) + len(keyword)
                rest = stripped[idx:]
                # Заменяем только первое слово (имя модели)
                words = rest.split(None, 1)
                if words:
                    tail = (" " + words[1]) if len(words) > 1 else ""
                    stripped = stripped[:idx] + new_model + tail
            out_lines.append(stripped + eol)
            continue

        # TEXT-строки с .lib → меняем путь на models_aged.txt (первое вхождение)
        # LTspice использует «!» для SPICE-директив и «;» для комментариев
        if low.startswith("text ") and ".lib" in low and not lib_replaced:
            for prefix_char in ("!", ";"):
                idx = stripped.find(prefix_char)
                if idx != -1:
                    text_part = stripped[idx + 1:]
                    new_text = re.sub(
                        r'(\.lib\s+)["\']?[^\s"\']+["\']?',
                        r'\1models_aged.txt',
                        text_part,
                        flags=re.IGNORECASE,
                    )
                    if new_text != text_part:
                        stripped = stripped[:idx + 1] + new_text
                        lib_replaced = True
                    break
            out_lines.append(stripped + eol)
            continue

        out_lines.append(raw_line)

    # Если в .asc не нашлось ни одной TEXT-строки с .lib — добавляем директиву
    if not lib_replaced:
        out_lines.append("TEXT -208 -32 Left 2 !.lib models_aged.txt\n")

    aged_asc.write_text("".join(out_lines), encoding="utf-8")
    return aged_asc
