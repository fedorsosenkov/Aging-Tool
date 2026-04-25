# Схема работы MOS Aging Analyzer

## Общий поток (Mermaid)

```mermaid
flowchart TD
    subgraph INPUT["Входные данные"]
        NL["📄 SPICE Netlist\n(.cir / .net / .txt)"]
        PARAMS["⚙️ Параметры расчёта\n(срок службы, |Vth| PMOS)"]
    end

    subgraph STEP1["Шаг 1 — Загрузка netlist"]
        PARSE["parse_netlist()\nnetlist_parser.py"]
        NL --> PARSE
        PARSE --> TRANS["Список транзисторов\n(Transistor: имя, D/G/S/B, тип, L, W)"]
        PARSE --> TOX["Толщина оксида\ntox_nmos, tox_pmos"]
    end

    subgraph STEP2["Шаг 2 — Выбор транзисторов"]
        UI_LIST["QListWidget\nMulti-select (Ctrl+клик)"]
        TRANS --> UI_LIST
        UI_LIST --> CHOSEN["chosen_transistors: list[str]"]
    end

    subgraph STEP3["Шаг 3 — Подготовка симуляции"]
        GEN["generate_inc_lines()\nnetlist_parser.py"]
        CHOSEN --> GEN
        GEN -->|"NMOS"| BSRC["B-источники тока\n+ .measure HCI_Mx\n+ .measure maxHCI_Mx\n+ .measure maxId_Mx"]
        GEN -->|"PMOS"| MEAS["\.measure maxNBTI Mx\n+ .measure NBTI Mx"]
        BSRC --> INC["📄 *-1.inc файл\n(рядом с netlist)"]
        MEAS --> INC

        INC -->|".include *.inc\nна схему LTSpice"| LTSPICE["🔬 LTSpice\nSimulate → Run"]
        LTSPICE --> LOG["📋 Spice Error Log\n(.log файл)"]
    end

    subgraph STEP4["Шаг 4 — Расчёт старения"]
        PARSE_LOG["parse_log()\nnetlist_parser.py"]
        LOG --> PARSE_LOG
        PARAMS --> PARSE_LOG
        PARSE_LOG --> LOGDATA["LogData\n(measurements, temperature, sim_time)"]

        LOGDATA --> RUN["run_aging_analysis()\naging.py"]
        TOX --> RUN
        CHOSEN --> RUN

        RUN -->|"is_pmos"| NBTI["calculate_nbti()\n→ ΔVth_NBTI, mobility_factor"]
        RUN -->|"is_nmos"| HCI["calculate_hci()\n→ ΔVth_HCI, mobility_factor"]

        NBTI --> RESULTS["AgingResults\n(list[TransistorAgingResult])"]
        HCI --> RESULTS
    end

    subgraph STEP5["Шаг 5 — Результаты и экспорт"]
        RESULTS --> HTML["generate_html_report()\nreport.py\n→ aging_report.html"]
        RESULTS --> AGED["write_aged_netlist()\nnetlist_writer.py"]
        AGED --> CIR["📄 *_aged.cir\n(новые имена моделей)"]
        AGED --> MODELS["📄 models_aged.txt\n(изменённые vth0, u0)"]

        HTML -->|"webbrowser.open()"| BROWSER["🌐 Браузер\n(HTML-отчёт)"]
        CIR -->|"os.startfile()"| LTSPICE2["🔬 LTSpice\n(повторная симуляция\nс учётом деградации)"]
        MODELS --> LTSPICE2
    end
```

---

## Интерфейс — навигация по шагам

```mermaid
stateDiagram-v2
    [*] --> Шаг1: Запуск приложения

    Шаг1: Шаг 1 · Загрузка Netlist
    Шаг2: Шаг 2 · Выбор транзисторов
    Шаг3: Шаг 3 · Инструкция LTSpice
    Шаг4: Шаг 4 · Загрузка Log + Расчёт
    Шаг5: Шаг 5 · Результаты

    Шаг1 --> Шаг2: Далее (файл выбран)
    Шаг2 --> Шаг3: Далее (транзисторы выбраны)\n+ генерация .inc
    Шаг3 --> Шаг4: Далее
    Шаг4 --> Шаг5: автоматически после расчёта

    Шаг5 --> Шаг4: Назад
    Шаг4 --> Шаг3: Назад
    Шаг3 --> Шаг2: Назад
    Шаг2 --> Шаг1: Назад

    Шаг5 --> [*]: Закрытие окна
```

---

## Физические расчёты

```mermaid
flowchart LR
    subgraph HCI["HCI (NMOS)"]
        direction TB
        H1["∫ i(Bib) dt — из .log"] --> H2["t_hci = integ / (max_hci × 0.75)"]
        H2 --> H3["ratio = t_hci / t_sim"]
        H3 --> H4["ΔVth = 1.3 · 0.22 · |Id/W · (i_hci/Id)² · t_life · ratio|^0.27"]
        H4 --> H5["factor_u0 = 1 + 0.24·10⁻¹⁶ · (ΔVth · Cox / q)"]
    end

    subgraph NBTI["NBTI (PMOS)"]
        direction TB
        N1["∫|Vgs| dt и max|Vgs| — из .log"] --> N2["t_nbti = integ / max_val"]
        N2 --> N3["ratio = t_nbti / t_sim"]
        N3 --> N4["Kv = f(Vgs, Vth, tox, T)"]
        N4 --> N5["ΔVth = 0.7 · (Kv · √(ratio · 2 · t_life))^(1/3)"]
        N5 --> N6["factor_u0 = 1 + 0.24·10⁻¹⁶ · (ΔVth · Cox / q)"]
    end

    subgraph CAT["Категоризация модели"]
        direction TB
        C1["ratio = Vth_new / Vth_old"]
        C1 --> C2{"ratio"}
        C2 -->|"1.000–1.025"| CA1["Категория 1 → NMOS1 / PMOS1\nvth0 × 1.0125"]
        C2 -->|"1.025–1.050"| CA2["Категория 2 → NMOS2 / PMOS2\nvth0 × 1.0375"]
        C2 -->|"1.050–1.750"| CA3["Категория 3 → NMOS3 / PMOS3\nvth0 × 1.0625"]
        C2 -->|"> 1.750"| CA0["Без изменения"]
    end
```

---

## Точки экспорта

| Файл | Когда создаётся | Формат |
|---|---|---|
| `*-1.inc` | Шаг 2→3 (generate_inc_lines) | Текст SPICE |
| `aging_report.html` | Автоматически после расчёта | HTML |
| `*_aged.cir` | По кнопке «Создать постаревший netlist» | SPICE netlist |
| `models_aged.txt` | Вместе с `_aged.cir` | SPICE .model |

> **Примечание по кнопке «Далее»:**  
> На шаге 4 кнопка «Далее» видна, но при нажатии ничего не происходит  
> (`elif step == 3: return` в `_go_next()`). Переход на шаг 5 происходит  
> автоматически после успешного расчёта через `_on_analysis_done()`.  
> Это является UX-проблемой — кнопка должна быть скрыта на этом шаге.
