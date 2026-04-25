"""
Главное окно приложения — пошаговый мастер анализа старения МОП-транзисторов.

Шаг 1: Выбор netlist-файла
Шаг 2: Выбор транзисторов для анализа
Шаг 3: Сохранение .inc файла и инструкции для LTSpice
Шаг 4: Загрузка Spice Error Log и запуск расчёта
Шаг 5: Просмотр результатов и создание постаревшего netlist
"""

from __future__ import annotations
import webbrowser
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QListWidget,
    QListWidgetItem, QFrame, QSplitter, QTextEdit,
    QStackedWidget, QProgressBar, QAbstractItemView,
    QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QIcon, QColor, QPalette

from core.netlist_parser import parse_netlist, NetlistData, parse_log, generate_inc_lines
from core.aging import run_aging_analysis, AgingResults
from core.netlist_writer import write_aged_netlist, create_aged_asc
from core.report import generate_html_report, generate_temp_html_report
from core.temperature_analysis import (
    compare_at_temperature,
    write_temp_comparison_log,
    TemperatureComparisonResult,
)
from ui.dialogs import AgingParamsDialog, TemperatureDialog, show_error, ask_yes_no
from utils.file_io import write_text
from utils.export import export_to_csv, export_to_txt, export_temp_to_csv, export_temp_to_txt
from utils.ltspice_locator import (
    find_ltspice_exe,
    open_in_ltspice,
    save_ltspice_path,
    get_auto_open_ltspice,
    set_auto_open_ltspice,
    prepare_ltspice_workdir,
)


# ---------------------------------------------------------------------------
# Стили
# ---------------------------------------------------------------------------

STYLESHEET = """
QMainWindow, QWidget {
    background-color: #f8f9fa;
    color: #1e293b;
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
}

/* Шаги-индикаторы */
#stepIndicator {
    background: #ffffff;
    border-bottom: 1px solid #e2e8f0;
    padding: 0 24px;
}

/* Карточки-шаги */
.StepCard {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 24px;
}

/* Кнопки */
QPushButton {
    border-radius: 8px;
    padding: 8px 20px;
    font-weight: 600;
    font-size: 13px;
    border: none;
}
QPushButton#primaryBtn {
    background: #2563eb;
    color: white;
}
QPushButton#primaryBtn:hover { background: #1d4ed8; }
QPushButton#primaryBtn:disabled { background: #93c5fd; }

QPushButton#secondaryBtn {
    background: #f1f5f9;
    color: #1e293b;
    border: 1px solid #e2e8f0;
}
QPushButton#secondaryBtn:hover { background: #e2e8f0; }

QPushButton#dangerBtn {
    background: #fee2e2;
    color: #dc2626;
    border: 1px solid #fecaca;
}
QPushButton#dangerBtn:hover { background: #fecaca; }

QPushButton#successBtn {
    background: #dcfce7;
    color: #16a34a;
    border: 1px solid #bbf7d0;
}
QPushButton#successBtn:hover { background: #bbf7d0; }

/* Список транзисторов */
QListWidget {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    background: #ffffff;
    padding: 4px;
}
QListWidget::item {
    padding: 8px 12px;
    border-radius: 6px;
    margin: 2px;
}
QListWidget::item:selected {
    background: #eff6ff;
    color: #2563eb;
    font-weight: 600;
}
QListWidget::item:hover { background: #f8fafc; }

/* Текстовое поле лога */
QTextEdit {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    background: #1e293b;
    color: #e2e8f0;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 12px;
    padding: 12px;
}

/* Разделитель */
QFrame[frameShape="4"],
QFrame[frameShape="5"] {
    color: #e2e8f0;
}

/* Прогресс-бар */
QProgressBar {
    border: none;
    background: #e2e8f0;
    border-radius: 4px;
    height: 6px;
    text-align: center;
}
QProgressBar::chunk {
    background: #2563eb;
    border-radius: 4px;
}

/* Метка файла */
#fileLabel {
    background: #f1f5f9;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 10px 14px;
    color: #64748b;
}
#fileLabel[hasFile=true] {
    color: #16a34a;
    background: #f0fdf4;
    border-color: #bbf7d0;
}

/* Заголовки секций */
#sectionTitle {
    font-size: 15px;
    font-weight: 700;
    color: #1e293b;
    margin-bottom: 4px;
}
#sectionHint {
    color: #64748b;
    font-size: 12px;
    margin-bottom: 16px;
}

/* Результаты */
#resultCard {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 8px;
}
"""


# ---------------------------------------------------------------------------
# Индикатор шагов
# ---------------------------------------------------------------------------

class StepIndicator(QWidget):
    STEPS = [
        "1  Netlist",
        "2  Транзисторы",
        "3  LTSpice",
        "4  Log-файл",
        "5  Результаты",
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stepIndicator")
        self.setFixedHeight(52)
        self._current = 0
        self._labels: list[QLabel] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(0)

        for i, text in enumerate(self.STEPS):
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(52)
            lbl.setMinimumWidth(120)
            self._labels.append(lbl)
            layout.addWidget(lbl)
            if i < len(self.STEPS) - 1:
                sep = QLabel("›")
                sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
                sep.setStyleSheet("color: #cbd5e1; font-size: 16px;")
                layout.addWidget(sep)

        layout.addStretch()
        self._refresh()

    def set_step(self, step: int) -> None:
        self._current = step
        self._refresh()

    def _refresh(self) -> None:
        for i, lbl in enumerate(self._labels):
            if i < self._current:
                lbl.setStyleSheet(
                    "color: #16a34a; font-weight: 600; font-size: 12px;"
                )
            elif i == self._current:
                lbl.setStyleSheet(
                    "color: #2563eb; font-weight: 700; font-size: 12px;"
                    "border-bottom: 3px solid #2563eb;"
                )
            else:
                lbl.setStyleSheet("color: #94a3b8; font-size: 12px;")


# ---------------------------------------------------------------------------
# Рабочий поток для тяжёлых операций
# ---------------------------------------------------------------------------

class WorkerSignals(QObject):
    finished = pyqtSignal(object)   # результат
    error    = pyqtSignal(str)


class AnalysisWorker(QThread):
    def __init__(
        self,
        netlist: NetlistData,
        log_path: str,
        chosen: list[str],
        years: float,
        vth_pmos: float,
        first_measure: str,
        n_measures: int,
    ) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._netlist = netlist
        self._log_path = log_path
        self._chosen = chosen
        self._years = years
        self._vth_pmos = vth_pmos
        self._first_measure = first_measure
        self._n_measures = n_measures

    def run(self) -> None:
        try:
            log = parse_log(self._log_path, self._first_measure, self._n_measures)
            results = run_aging_analysis(
                transistors=self._netlist.transistors,
                log=log,
                chosen_names=self._chosen,
                tox_nmos=self._netlist.tox_nmos or 3.9e-9,
                tox_pmos=self._netlist.tox_pmos or 3.9e-9,
                vth_pmos=self._vth_pmos,
                target_years=self._years,
            )
            self.signals.finished.emit(results)
        except Exception as exc:
            self.signals.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Главное окно
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Анализ старения МОП-транзисторов")
        self.setMinimumSize(900, 640)
        self.resize(1060, 720)

        # Состояние
        self._netlist: NetlistData | None = None
        self._netlist_path: Path | None = None
        self._chosen_transistors: list[str] = []
        self._aging_results: AgingResults | None = None
        self._aging_params: tuple[float, float] = (10.0, 0.4)  # (years, vth_pmos)
        self._report_path: Path | None = None
        self._worker: AnalysisWorker | None = None
        # Log-данные сохраняются для повторного расчёта при другой температуре
        self._log_data = None   # LogData | None
        self._log_path: str = ""
        self._temp_comparisons: list | None = None

        self.setStyleSheet(STYLESHEET)
        self._build_ui()

    # ------------------------------------------------------------------
    # Директория схемы LTSpice
    # ------------------------------------------------------------------

    @property
    def _schema_dir(self) -> Path:
        """
        Директория, где лежит .asc-схема LTSpice.
        Путь берётся из первой строки netlist (NetlistData.schema_path).
        Фоллбэк: директория самого netlist-файла.
        """
        if self._netlist_path is None:
            return Path(".")
        if self._netlist and self._netlist.schema_path:
            p = Path(self._netlist.schema_path)
            if not p.is_absolute():
                p = self._netlist_path.parent / p   # относительный путь
            if p.parent.exists():
                return p.parent
        return self._netlist_path.parent

    # ------------------------------------------------------------------
    # Построение UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Шапка
        header = self._build_header()
        root.addWidget(header)

        # Индикатор шагов
        self._step_indicator = StepIndicator()
        root.addWidget(self._step_indicator)

        # Сплиттер: левая панель (шаги) + правая (лог)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #e2e8f0; }")

        # Левая панель — стек шагов
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_step1())
        self._stack.addWidget(self._build_step2())
        self._stack.addWidget(self._build_step3())
        self._stack.addWidget(self._build_step4())
        self._stack.addWidget(self._build_step5())

        left_wrapper = QWidget()
        lw_layout = QVBoxLayout(left_wrapper)
        lw_layout.setContentsMargins(24, 24, 12, 24)
        lw_layout.addWidget(self._stack)

        splitter.addWidget(left_wrapper)

        # Правая панель — лог
        right_wrapper = QWidget()
        rw_layout = QVBoxLayout(right_wrapper)
        rw_layout.setContentsMargins(12, 24, 24, 24)

        log_title = QLabel("Журнал")
        log_title.setObjectName("sectionTitle")
        rw_layout.addWidget(log_title)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        rw_layout.addWidget(self._log_view)

        splitter.addWidget(right_wrapper)
        splitter.setSizes([620, 380])

        root.addWidget(splitter, stretch=1)

        # Нижняя панель навигации
        nav = self._build_nav()
        root.addWidget(nav)

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(60)
        w.setStyleSheet("background: #1e293b;")
        layout = QHBoxLayout(w)
        layout.setContentsMargins(24, 0, 24, 0)

        title = QLabel("⚡  MOS Aging Analyzer")
        title.setStyleSheet("color: white; font-size: 16px; font-weight: 700;")
        layout.addWidget(title)
        layout.addStretch()

        subtitle = QLabel("Анализ деградации NBTI и HCI")
        subtitle.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(subtitle)
        return w

    def _build_nav(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(64)
        # Именованный селектор — стиль применяется ТОЛЬКО к самому виджету,
        # не каскадирует на дочерние QPushButton (исправляет белый прямоугольник)
        w.setObjectName("navBar")
        w.setStyleSheet("QWidget#navBar { background: #ffffff; border-top: 1px solid #e2e8f0; }")
        layout = QHBoxLayout(w)
        layout.setContentsMargins(24, 0, 24, 0)

        self._btn_back = QPushButton("← Назад")
        self._btn_back.setObjectName("secondaryBtn")
        # Инлайн-fallback: гарантирует читаемость независимо от системной темы
        self._btn_back.setStyleSheet(
            "QPushButton { background-color: #f1f5f9; color: #1e293b; "
            "border: 1px solid #e2e8f0; border-radius: 8px; "
            "padding: 8px 20px; font-weight: 600; font-size: 13px; }"
            "QPushButton:hover { background-color: #e2e8f0; }"
            "QPushButton:disabled { color: #94a3b8; background-color: #f8fafc; }"
        )
        self._btn_back.clicked.connect(self._go_back)
        self._btn_back.setEnabled(False)

        self._progress = QProgressBar()
        self._progress.setRange(0, 4)
        self._progress.setValue(0)
        self._progress.setFixedWidth(200)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)

        self._btn_next = QPushButton("Далее →")
        self._btn_next.setObjectName("primaryBtn")
        # Инлайн-fallback: синяя кнопка с белым текстом — гарантированно читаема
        self._btn_next.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: #ffffff; "
            "border-radius: 8px; padding: 8px 20px; "
            "font-weight: 600; font-size: 13px; border: none; }"
            "QPushButton:hover { background-color: #1d4ed8; }"
            "QPushButton:disabled { background-color: #93c5fd; color: #ffffff; }"
        )
        self._btn_next.clicked.connect(self._go_next)

        layout.addWidget(self._btn_back)
        layout.addStretch()
        layout.addWidget(self._progress)
        layout.addStretch()
        layout.addWidget(self._btn_next)
        return w

    # --- Шаг 1: выбор файла ---

    def _build_step1(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        lbl = QLabel("Шаг 1 — Загрузка netlist-файла")
        lbl.setObjectName("sectionTitle")
        layout.addWidget(lbl)

        hint = QLabel(
            "Откройте схему в LTSpice → меню View → SPICE Netlist.\n"
            "В папке со схемой появится файл .net (или сохраните как .cir)."
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        self._btn_open_netlist = QPushButton("📂  Выбрать файл netlist")
        self._btn_open_netlist.setObjectName("primaryBtn")
        self._btn_open_netlist.clicked.connect(self._open_netlist)
        btn_row.addWidget(self._btn_open_netlist)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._lbl_netlist_file = QLabel("Файл не выбран")
        self._lbl_netlist_file.setObjectName("fileLabel")
        self._lbl_netlist_file.setWordWrap(True)
        layout.addWidget(self._lbl_netlist_file)

        self._lbl_netlist_info = QLabel("")
        self._lbl_netlist_info.setStyleSheet("color: #64748b; font-size: 12px;")
        self._lbl_netlist_info.setWordWrap(True)
        layout.addWidget(self._lbl_netlist_info)

        layout.addStretch()
        return w

    # --- Шаг 2: выбор транзисторов ---

    def _build_step2(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        lbl = QLabel("Шаг 2 — Выбор транзисторов для анализа")
        lbl.setObjectName("sectionTitle")
        layout.addWidget(lbl)

        hint = QLabel(
            "Выберите транзисторы (Ctrl+клик для нескольких).\n"
            "N-канальные (NMOS) → расчёт HCI.  P-канальные (PMOS) → расчёт NBTI."
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._transistor_list = QListWidget()
        self._transistor_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        layout.addWidget(self._transistor_list, stretch=1)

        btn_row = QHBoxLayout()
        btn_all = QPushButton("Выбрать все")
        btn_all.setObjectName("secondaryBtn")
        btn_all.clicked.connect(self._transistor_list.selectAll)
        btn_none = QPushButton("Снять выбор")
        btn_none.setObjectName("secondaryBtn")
        btn_none.clicked.connect(self._transistor_list.clearSelection)
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return w

    # --- Шаг 3: инструкция LTSpice ---

    def _build_step3(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        lbl = QLabel("Шаг 3 — Подготовка симуляции в LTSpice")
        lbl.setObjectName("sectionTitle")
        layout.addWidget(lbl)

        hint = QLabel(
            "Программа создала .inc файл с командами для LTSpice.\n"
            "Добавьте директиву .include на схему и запустите симуляцию."
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._lbl_inc_file = QLabel("—")
        self._lbl_inc_file.setObjectName("fileLabel")
        self._lbl_inc_file.setWordWrap(True)
        layout.addWidget(self._lbl_inc_file)

        self._inc_preview = QTextEdit()
        self._inc_preview.setReadOnly(True)
        self._inc_preview.setMaximumHeight(180)
        layout.addWidget(self._inc_preview)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("📋  Копировать директиву .include")
        btn_copy.setObjectName("secondaryBtn")
        btn_copy.clicked.connect(self._copy_include)
        self._btn_open_ltspice = QPushButton("🔬  Открыть схему в LTSpice")
        self._btn_open_ltspice.setObjectName("primaryBtn")
        self._btn_open_ltspice.clicked.connect(self._open_in_ltspice)
        btn_row.addWidget(btn_copy)
        btn_row.addWidget(self._btn_open_ltspice)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        return w

    # --- Шаг 4: загрузка log + расчёт ---

    def _build_step4(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        lbl = QLabel("Шаг 4 — Загрузка результатов симуляции")
        lbl.setObjectName("sectionTitle")
        layout.addWidget(lbl)

        hint = QLabel(
            "После симуляции в LTSpice загрузите Spice Error Log (.log).\n"
            "Укажите параметры расчёта старения и нажмите «Рассчитать»."
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        self._btn_open_log = QPushButton("📂  Загрузить Error Log (.log)")
        self._btn_open_log.setObjectName("primaryBtn")
        self._btn_open_log.clicked.connect(self._open_log)
        btn_row.addWidget(self._btn_open_log)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._lbl_log_file = QLabel("Log-файл не выбран")
        self._lbl_log_file.setObjectName("fileLabel")
        self._lbl_log_file.setWordWrap(True)
        layout.addWidget(self._lbl_log_file)

        btn_params = QPushButton("⚙️  Параметры расчёта…")
        btn_params.setObjectName("secondaryBtn")
        btn_params.clicked.connect(self._open_params_dialog)
        self._lbl_params = QLabel(f"Срок службы: 10 лет  |  |Vth| PMOS: 0.400 В")
        self._lbl_params.setStyleSheet("color: #64748b; font-size: 12px;")

        p_row = QHBoxLayout()
        p_row.addWidget(btn_params)
        p_row.addWidget(self._lbl_params)
        p_row.addStretch()
        layout.addLayout(p_row)

        self._btn_calculate = QPushButton("🔍  Рассчитать старение")
        self._btn_calculate.setObjectName("primaryBtn")
        self._btn_calculate.setEnabled(False)
        self._btn_calculate.clicked.connect(self._run_analysis)
        layout.addWidget(self._btn_calculate)

        self._calc_progress = QProgressBar()
        self._calc_progress.setRange(0, 0)
        self._calc_progress.setFixedHeight(6)
        self._calc_progress.setTextVisible(False)
        self._calc_progress.setVisible(False)
        layout.addWidget(self._calc_progress)

        layout.addStretch()
        return w

    # --- Шаг 5: результаты ---

    def _build_step5(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        lbl = QLabel("Шаг 5 — Результаты анализа")
        lbl.setObjectName("sectionTitle")
        layout.addWidget(lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._results_container = QWidget()
        self._results_layout = QVBoxLayout(self._results_container)
        self._results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._results_container)
        layout.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        self._btn_open_report = QPushButton("🌐  Открыть HTML-отчёт")
        self._btn_open_report.setObjectName("successBtn")
        self._btn_open_report.setEnabled(False)
        self._btn_open_report.clicked.connect(self._open_report)

        self._btn_create_aged = QPushButton("🔧  Создать постаревший netlist")
        self._btn_create_aged.setObjectName("primaryBtn")
        self._btn_create_aged.setEnabled(False)
        self._btn_create_aged.clicked.connect(self._create_aged_netlist)

        btn_row.addWidget(self._btn_open_report)
        btn_row.addWidget(self._btn_create_aged)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._lbl_aged_file = QLabel("")
        self._lbl_aged_file.setStyleSheet("color: #16a34a; font-size: 12px;")
        layout.addWidget(self._lbl_aged_file)

        # Разделитель
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # --- Температурный анализ ---
        temp_row = QHBoxLayout()
        self._btn_temp_analysis = QPushButton("🌡️  Анализ температурного влияния")
        self._btn_temp_analysis.setObjectName("secondaryBtn")
        self._btn_temp_analysis.setEnabled(False)
        self._btn_temp_analysis.setToolTip(
            "Пересчитать деградацию при другой температуре "
            "и сравнить параметры ΔVth / μ при T₀ и T_new"
        )
        self._btn_temp_analysis.clicked.connect(self._analyze_temperature)
        temp_row.addWidget(self._btn_temp_analysis)
        temp_row.addStretch()
        layout.addLayout(temp_row)

        self._lbl_temp_result = QLabel("")
        self._lbl_temp_result.setStyleSheet("color: #64748b; font-size: 12px;")
        self._lbl_temp_result.setWordWrap(True)
        layout.addWidget(self._lbl_temp_result)

        # Кнопки экспорта температурного анализа
        temp_export_row = QHBoxLayout()
        self._btn_temp_csv = QPushButton("📊  CSV (темп.)")
        self._btn_temp_csv.setObjectName("secondaryBtn")
        self._btn_temp_csv.setEnabled(False)
        self._btn_temp_csv.clicked.connect(self._export_temp_csv)

        self._btn_temp_txt = QPushButton("📄  TXT (темп.)")
        self._btn_temp_txt.setObjectName("secondaryBtn")
        self._btn_temp_txt.setEnabled(False)
        self._btn_temp_txt.clicked.connect(self._export_temp_txt)

        self._btn_temp_html = QPushButton("🌐  HTML (темп.)")
        self._btn_temp_html.setObjectName("successBtn")
        self._btn_temp_html.setEnabled(False)
        self._btn_temp_html.clicked.connect(self._export_temp_html)

        temp_export_row.addWidget(self._btn_temp_csv)
        temp_export_row.addWidget(self._btn_temp_txt)
        temp_export_row.addWidget(self._btn_temp_html)
        temp_export_row.addStretch()
        layout.addLayout(temp_export_row)

        self._lbl_temp_export = QLabel("")
        self._lbl_temp_export.setStyleSheet("color: #64748b; font-size: 12px;")
        self._lbl_temp_export.setWordWrap(True)
        layout.addWidget(self._lbl_temp_export)

        # Кнопки экспорта
        export_row = QHBoxLayout()
        self._btn_export_csv = QPushButton("📊  Экспорт CSV")
        self._btn_export_csv.setObjectName("secondaryBtn")
        self._btn_export_csv.setEnabled(False)
        self._btn_export_csv.clicked.connect(self._export_csv)

        self._btn_export_txt = QPushButton("📄  Экспорт TXT")
        self._btn_export_txt.setObjectName("secondaryBtn")
        self._btn_export_txt.setEnabled(False)
        self._btn_export_txt.clicked.connect(self._export_txt)

        export_row.addWidget(self._btn_export_csv)
        export_row.addWidget(self._btn_export_txt)
        export_row.addStretch()
        layout.addLayout(export_row)

        self._lbl_export = QLabel("")
        self._lbl_export.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(self._lbl_export)

        return w

    # ------------------------------------------------------------------
    # Навигация
    # ------------------------------------------------------------------

    def _current_step(self) -> int:
        return self._stack.currentIndex()

    def _go_next(self) -> None:
        step = self._current_step()
        if step == 0:
            if not self._validate_step1():
                return
            self._populate_transistor_list()
        elif step == 1:
            if not self._validate_step2():
                return
            self._save_inc_file()
        elif step == 2:
            pass  # просто переходим к шагу 4
        elif step == 3:
            return  # расчёт запускается кнопкой «Рассчитать»
        elif step == 4:
            return

        next_step = min(step + 1, 4)
        self._stack.setCurrentIndex(next_step)
        self._step_indicator.set_step(next_step)
        self._progress.setValue(next_step)
        self._btn_back.setEnabled(True)
        # На шаге 4 (загрузка Log) и шаге 5 (результаты) кнопка «Далее» скрыта:
        # переход на шаг 5 происходит автоматически после расчёта.
        if next_step >= 3:
            self._btn_next.setVisible(False)
        else:
            self._btn_next.setVisible(True)
            self._btn_next.setEnabled(True)

    def _go_back(self) -> None:
        step = self._current_step()
        prev = max(step - 1, 0)
        self._stack.setCurrentIndex(prev)
        self._step_indicator.set_step(prev)
        self._progress.setValue(prev)
        self._btn_back.setEnabled(prev > 0)
        self._btn_next.setVisible(True)
        self._btn_next.setEnabled(True)

    # ------------------------------------------------------------------
    # Шаг 1: открытие netlist
    # ------------------------------------------------------------------

    def _open_netlist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбрать netlist",
            "",
            "SPICE Netlist (*.cir *.net *.txt);;Все файлы (*)",
        )
        if not path:
            return
        try:
            netlist = parse_netlist(path)
            self._netlist = netlist
            self._netlist_path = Path(path)
            self._lbl_netlist_file.setText(path)
            self._lbl_netlist_file.setProperty("hasFile", True)
            self._lbl_netlist_file.style().unpolish(self._lbl_netlist_file)
            self._lbl_netlist_file.style().polish(self._lbl_netlist_file)

            n = len(netlist.transistors)
            nmos = sum(1 for t in netlist.transistors if t.is_nmos)
            pmos = sum(1 for t in netlist.transistors if t.is_pmos)
            tox_n = f"{netlist.tox_nmos * 1e9:.2f} нм" if netlist.tox_nmos else "не найдено"
            tox_p = f"{netlist.tox_pmos * 1e9:.2f} нм" if netlist.tox_pmos else "не найдено"

            self._lbl_netlist_info.setText(
                f"Найдено транзисторов: {n}  (NMOS: {nmos}, PMOS: {pmos})\n"
                f"Tox NMOS: {tox_n}   Tox PMOS: {tox_p}"
            )
            self._log(f"✅ Загружен netlist: {path}")
            self._log(f"   Транзисторов: {n} (NMOS={nmos}, PMOS={pmos})")
        except Exception as exc:
            show_error(self, f"Ошибка чтения файла:\n{exc}")
            self._log(f"❌ Ошибка чтения netlist: {exc}")

    def _validate_step1(self) -> bool:
        if self._netlist is None:
            show_error(self, "Сначала выберите netlist-файл.")
            return False
        return True

    # ------------------------------------------------------------------
    # Шаг 2: выбор транзисторов
    # ------------------------------------------------------------------

    def _populate_transistor_list(self) -> None:
        self._transistor_list.clear()
        if self._netlist is None:
            return
        for t in self._netlist.transistors:
            ch = "N-канальный" if t.is_nmos else "P-канальный"
            label = f"{t.name}   [{ch}]   L={t.length_m * 1e9:.0f} нм   W={t.width_m * 1e6:.3f} мкм"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, t.name)
            color = QColor("#1d4ed8") if t.is_nmos else QColor("#9333ea")
            item.setForeground(color)
            self._transistor_list.addItem(item)

    def _validate_step2(self) -> bool:
        selected = self._transistor_list.selectedItems()
        if not selected:
            show_error(self, "Выберите хотя бы один транзистор.")
            return False
        self._chosen_transistors = [
            item.data(Qt.ItemDataRole.UserRole) for item in selected
        ]
        self._log(f"✅ Выбрано транзисторов: {len(self._chosen_transistors)}")
        self._log(f"   {', '.join(self._chosen_transistors)}")
        return True

    # ------------------------------------------------------------------
    # Шаг 3: сохранение .inc файла
    # ------------------------------------------------------------------

    def _save_inc_file(self) -> None:
        if self._netlist is None or not self._chosen_transistors:
            return
        chosen_upper = {n.upper() for n in self._chosen_transistors}
        chosen_transistors = [
            t for t in self._netlist.transistors if t.name in chosen_upper
        ]
        inc_lines = generate_inc_lines(chosen_transistors)

        # Путь рядом с netlist
        inc_path = str(self._schema_dir / (self._netlist_path.stem + "-1.inc"))
        write_text(inc_path, "\n".join(inc_lines))

        self._inc_path = inc_path
        self._lbl_inc_file.setText(inc_path)
        self._inc_preview.setText("\n".join(inc_lines))
        self._log(f"✅ Сохранён .inc файл: {inc_path}")

    def _copy_include(self) -> None:
        if hasattr(self, "_inc_path"):
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(f".include {self._inc_path}")
            self._log("📋 Скопирована директива .include в буфер обмена")

    def _open_in_ltspice(self) -> None:
        if self._netlist and self._netlist.schema_path:
            path = self._netlist.schema_path
            if not Path(path).exists():
                show_error(self, f"Файл схемы не найден:\n{path}")
                return
            self._launch_ltspice(path)

    def _launch_ltspice(self, file_path: str) -> None:
        """Открывает файл в LTSpice напрямую (минуя файловые ассоциации)."""
        ok = open_in_ltspice(file_path)
        if ok:
            self._log(f"🔬 Открываю в LTSpice: {file_path}")
            return

        # LTSpice не найден — просим пользователя указать путь один раз
        self._log("⚠️  LTSpice не найден автоматически. Укажите путь вручную.")
        exe, _ = QFileDialog.getOpenFileName(
            self,
            "Укажите путь к LTSpice.exe",
            r"C:\Program Files",
            "Исполняемые файлы (*.exe);;Все файлы (*)",
        )
        if not exe:
            return
        save_ltspice_path(exe)          # сохраняем — больше не будем спрашивать
        ok = open_in_ltspice(file_path, ltspice_exe=exe)
        if ok:
            self._log(f"🔬 Открываю в LTSpice: {file_path}")
        else:
            show_error(self, f"Не удалось запустить LTSpice:\n{exe}")

    # ------------------------------------------------------------------
    # Шаг 4: загрузка log, расчёт
    # ------------------------------------------------------------------

    def _open_log(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбрать Spice Error Log",
            str(self._netlist_path.parent) if self._netlist_path else "",
            "LTSpice Log (*.log);;Все файлы (*)",
        )
        if not path:
            return
        self._log_path = path
        self._lbl_log_file.setText(path)
        self._lbl_log_file.setProperty("hasFile", True)
        self._lbl_log_file.style().unpolish(self._lbl_log_file)
        self._lbl_log_file.style().polish(self._lbl_log_file)
        self._btn_calculate.setEnabled(True)
        self._log(f"✅ Загружен log-файл: {path}")

    def _open_params_dialog(self) -> None:
        dlg = AgingParamsDialog(self)
        dlg._years_spin.setValue(int(self._aging_params[0]))
        dlg._vth_spin.setValue(self._aging_params[1])
        if dlg.exec():
            self._aging_params = (dlg.years, dlg.vth_pmos)
            self._lbl_params.setText(
                f"Срок службы: {int(dlg.years)} лет  |  |Vth| PMOS: {dlg.vth_pmos:.3f} В"
            )
            self._log(f"⚙️ Параметры: {int(dlg.years)} лет, Vth={dlg.vth_pmos:.3f} В")

    def _run_analysis(self) -> None:
        if not self._log_path:
            show_error(self, "Загрузите Spice Error Log.")
            return

        # Определяем первое имя измерения и количество
        chosen_upper = {n.upper() for n in self._chosen_transistors}
        first_t = next(
            (t for t in self._netlist.transistors if t.name in chosen_upper), None
        )
        if first_t is None:
            show_error(self, "Не найдены выбранные транзисторы в netlist.")
            return

        if first_t.is_nmos:
            first_measure = f"hci_{first_t.name.lower()}"
            n_measures = len(self._chosen_transistors) * 3
        else:
            first_measure = f"maxnbti{first_t.name.lower()}"
            n_measures = len(self._chosen_transistors) * 2

        years, vth_pmos = self._aging_params
        self._calc_progress.setVisible(True)
        self._btn_calculate.setEnabled(False)
        self._log("🔍 Запускаю расчёт старения…")

        self._worker = AnalysisWorker(
            netlist=self._netlist,
            log_path=self._log_path,
            chosen=self._chosen_transistors,
            years=years,
            vth_pmos=vth_pmos,
            first_measure=first_measure,
            n_measures=n_measures,
        )
        self._worker.signals.finished.connect(self._on_analysis_done)
        self._worker.signals.error.connect(self._on_analysis_error)
        self._worker.start()

    def _on_analysis_done(self, results: AgingResults) -> None:
        self._calc_progress.setVisible(False)
        self._btn_calculate.setEnabled(True)
        self._aging_results = results
        self._log("✅ Расчёт завершён")

        # Сохраняем log-данные для повторного расчёта при другой температуре
        from core.netlist_parser import parse_log
        chosen_upper = {n.upper() for n in self._chosen_transistors}
        first_t = next(
            (t for t in self._netlist.transistors if t.name in chosen_upper), None
        )
        if first_t is not None:
            if first_t.is_nmos:
                fm = f"hci_{first_t.name.lower()}"
                nm = len(self._chosen_transistors) * 3
            else:
                fm = f"maxnbti{first_t.name.lower()}"
                nm = len(self._chosen_transistors) * 2
            try:
                self._log_data = parse_log(self._log_path, fm, nm)
            except Exception:
                self._log_data = None

        # Сохраняем HTML-отчёт
        report_path = self._schema_dir / "aging_report.html"
        generate_html_report(results, self._aging_params[0], report_path)
        self._report_path = report_path
        self._log(f"📄 Отчёт сохранён: {report_path}")

        # Переходим к шагу 5
        self._populate_results(results)
        self._stack.setCurrentIndex(4)
        self._step_indicator.set_step(4)
        self._progress.setValue(4)
        self._btn_next.setVisible(False)
        self._btn_back.setEnabled(True)
        self._btn_open_report.setEnabled(True)
        self._btn_create_aged.setEnabled(True)
        self._btn_export_csv.setEnabled(True)
        self._btn_export_txt.setEnabled(True)
        self._btn_temp_analysis.setEnabled(True)

    def _on_analysis_error(self, msg: str) -> None:
        self._calc_progress.setVisible(False)
        self._btn_calculate.setEnabled(True)
        show_error(self, f"Ошибка расчёта:\n{msg}")
        self._log(f"❌ Ошибка: {msg}")

    # ------------------------------------------------------------------
    # Шаг 5: отображение результатов
    # ------------------------------------------------------------------

    def _populate_results(self, results: AgingResults) -> None:
        # Очищаем предыдущие результаты
        while self._results_layout.count():
            child = self._results_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        def add_section(title: str, color: str) -> None:
            lbl = QLabel(title)
            lbl.setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {color};"
                f"margin-top: 8px; margin-bottom: 4px;"
            )
            self._results_layout.addWidget(lbl)

        def add_card(lines: list[tuple[str, str]]) -> None:
            card = QFrame()
            card.setObjectName("resultCard")
            card.setStyleSheet(
                "QFrame#resultCard { background: #fff; border: 1px solid #e2e8f0;"
                " border-radius: 10px; padding: 12px; margin-bottom: 6px; }"
            )
            cl = QVBoxLayout(card)
            cl.setSpacing(4)
            for label, value in lines:
                row = QHBoxLayout()
                lbl_l = QLabel(label)
                lbl_l.setStyleSheet("color: #64748b; font-size: 12px;")
                lbl_v = QLabel(value)
                lbl_v.setStyleSheet("font-weight: 600; font-size: 12px;")
                row.addWidget(lbl_l)
                row.addStretch()
                row.addWidget(lbl_v)
                cl.addLayout(row)
            self._results_layout.addWidget(card)

        if results.nmos_results():
            add_section("⚡ NMOS — Эффект горячих носителей (HCI)", "#1d4ed8")
            for ar in sorted(results.nmos_results(), key=lambda x: x.hci_delta_vth_v, reverse=True):
                add_card([
                    ("Транзистор", ar.name),
                    ("ΔVth", f"{ar.hci_delta_vth_v * 1000:.3f} мВ"),
                    ("Время в режиме HCI", f"{ar.hci_active_time_s:.3e} с"),
                    ("Отношение времени работы в режиме HCI ко времени моделирования", f"{ar.hci_ratio:.4e}"),
                    ("Коэф. деградации подвижности (÷u0)", f"{ar.hci_mobility_factor:.6f}"),
                ])

        if results.pmos_results():
            add_section("🔋 PMOS — Температурная нестабильность при отрицательном смещении (NBTI)", "#9333ea")
            for ar in sorted(results.pmos_results(), key=lambda x: x.nbti_delta_vth_v, reverse=True):
                add_card([
                    ("Транзистор", ar.name),
                    ("ΔVth", f"{ar.nbti_delta_vth_v * 1000:.3f} мВ"),
                    ("Время в режиме NBTI", f"{ar.nbti_active_time_s:.3e} с"),
                    ("Отношение времени работы в режиме NBTI ко времени моделирования", f"{ar.nbti_ratio:.4f}"),
                    ("Коэф. деградации подвижности (÷u0)", f"{ar.nbti_mobility_factor:.6f}"),
                ])

    def _open_report(self) -> None:
        if self._report_path and self._report_path.exists():
            webbrowser.open(self._report_path.as_uri())
            self._log(f"🌐 Открываю отчёт в браузере: {self._report_path}")

    def _create_aged_netlist(self) -> None:
        if self._aging_results is None or self._netlist is None:
            return
        out_dir = self._schema_dir
        try:
            # Шаг 1: всегда генерируем models_aged.txt (+ _aged.cir как запасной вариант)
            aged_cir, models_txt = write_aged_netlist(
                netlist=self._netlist,
                aging=self._aging_results,
                chosen_names=self._chosen_transistors,
                source_cir_path=self._netlist_path,
                output_dir=out_dir,
            )
            self._aged_cir_path = aged_cir
            self._models_txt_path = models_txt
            self._log(f"🔧 Файл моделей: {models_txt}")

            # Шаг 2: пробуем создать _aged.asc из оригинальной схемы LTspice
            aged_asc: Path | None = None
            if self._netlist and self._netlist.schema_path:
                asc_path = Path(self._netlist.schema_path)
                if not asc_path.is_absolute():
                    asc_path = self._netlist_path.parent / asc_path
                if asc_path.exists() and asc_path.suffix.lower() == ".asc":
                    try:
                        aged_asc = create_aged_asc(
                            asc_path=asc_path,
                            netlist=self._netlist,
                            aging=self._aging_results,
                            chosen_names=self._chosen_transistors,
                            output_dir=out_dir,
                        )
                        self._log(f"🔧 Постаревшая схема: {aged_asc}")
                    except Exception as exc_asc:
                        self._log(f"⚠️  Не удалось создать .asc: {exc_asc}")
                        aged_asc = None

            # Обновляем метку результата
            main_file = aged_asc if aged_asc else aged_cir
            self._lbl_aged_file.setText(
                f"✅ Создан: {main_file.name}   |   {models_txt.name}"
            )
            self._log(f"🔧 Открываемый файл: {main_file}")

            # Авто-открытие: читаем настройку
            should_open = get_auto_open_ltspice()
            if not should_open:
                should_open = ask_yes_no(
                    self,
                    "Открыть постаревшую схему в LTSpice?\n"
                    "(Настройку можно изменить в параметрах приложения)",
                )

            if should_open:
                self._open_aged_in_ltspice(
                    aged_asc if aged_asc else aged_cir,
                    models_txt,
                )

        except Exception as exc:
            show_error(self, f"Ошибка создания netlist:\n{exc}")
            self._log(f"❌ Ошибка создания netlist: {exc}")

    def _open_aged_in_ltspice(self, aged_file: Path, models_txt: Path) -> None:
        """
        Открывает постаревший файл в LTspice.

        • .asc — открываем напрямую: models_aged.txt лежит рядом в той же папке,
          LTspice найдёт его без дополнительных манипуляций.
        • .cir — создаём изолированную рабочую директорию, чтобы все
          относительные пути к .lib/.inc разрешились корректно.
        """
        if aged_file.suffix.lower() == ".asc":
            self._log(f"📂 Открываю схему в LTspice: {aged_file.name}")
            self._launch_ltspice(str(aged_file))
            return

        # --- .cir: используем temp workdir ---
        inc_files: list[Path] = []
        if hasattr(self, "_inc_path"):
            inc = Path(self._inc_path)
            if inc.exists():
                inc_files.append(inc)
        if self._netlist_path:
            for ext in ("*.inc", "*.lib"):
                inc_files.extend(self._netlist_path.parent.glob(ext))

        try:
            work_dir = prepare_ltspice_workdir(aged_file, models_txt, inc_files)
            work_cir = work_dir / aged_file.name
            self._log(f"📁 Рабочая директория: {work_dir}")
            self._launch_ltspice(str(work_cir))
        except Exception as exc:
            self._log(f"⚠️  Не удалось создать рабочую директорию: {exc}")
            self._log("   Открываю netlist напрямую (пути к .lib могут не разрешиться)")
            self._launch_ltspice(str(aged_file))

    # ------------------------------------------------------------------
    # Температурный анализ (шаг 5)
    # ------------------------------------------------------------------

    def _analyze_temperature(self) -> None:
        """
        Пересчитывает деградацию при новой температуре, выводит таблицу
        сравнения ΔVth / μ при T₀ и T_new, сохраняет *_temp.log.
        """
        if self._aging_results is None or self._netlist is None:
            return
        if self._log_data is None:
            show_error(
                self,
                "Нет данных log-файла для повторного расчёта.\n"
                "Убедитесь, что расчёт был выполнен корректно.",
            )
            return

        dlg = TemperatureDialog(
            t0_c=self._aging_results.temperature_c, parent=self
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        t_new = dlg.temperature_c
        years, vth_pmos = self._aging_params
        tox_nmos = self._netlist.tox_nmos or 3.9e-9
        tox_pmos = self._netlist.tox_pmos or 3.9e-9

        self._log(f"🌡️  Расчёт при T_new = {t_new:.1f} °C…")
        try:
            comparisons = compare_at_temperature(
                aging_results=self._aging_results,
                transistors=self._netlist.transistors,
                log=self._log_data,
                tox_nmos=tox_nmos,
                tox_pmos=tox_pmos,
                vth_pmos=vth_pmos,
                target_years=years,
                new_temp_c=t_new,
            )
        except Exception as exc:
            show_error(self, f"Ошибка расчёта при T_new:\n{exc}")
            self._log(f"❌ Ошибка температурного расчёта: {exc}")
            return

        # Сохраняем в файл *_temp.log
        t0 = self._aging_results.temperature_c
        temp_log_path = self._schema_dir / (
            self._netlist_path.stem + f"_T{int(t_new):+d}_temp.log"
        )
        write_temp_comparison_log(comparisons, temp_log_path, years)
        self._log(f"📄 Температурный отчёт: {temp_log_path}")

        # Отображаем результаты в журнале
        self._log("")
        self._log(f"{'─'*60}")
        self._log(f"  Сравнение деградации: T₀={t0:.1f}°C  vs  T_new={t_new:.1f}°C")
        self._log(f"{'─'*60}")
        header = f"  {'Транзистор':<10} {'Тип':<5} {'ΔVth(T0), мВ':>13} {'ΔVth(Tnew), мВ':>15} {'Δ, мВ':>8}"
        self._log(header)
        for c in comparisons:
            row = (
                f"  {c.name:<10} {c.channel:<5} "
                f"{c.delta_vth_t0_v * 1000:>13.4f} "
                f"{c.delta_vth_tnew_v * 1000:>15.4f} "
                f"{c.delta_vth_change_mv:>8.4f}"
            )
            self._log(row)
        self._log(f"{'─'*60}")

        # Обновляем метку под кнопкой
        self._lbl_temp_result.setText(
            f"✅ T₀={t0:.0f}°C → T_new={t_new:.0f}°C  |  Сохранено: {temp_log_path.name}"
        )

        # Сохраняем результаты и разблокируем кнопки экспорта
        self._temp_comparisons = comparisons
        self._btn_temp_csv.setEnabled(True)
        self._btn_temp_txt.setEnabled(True)
        self._btn_temp_html.setEnabled(True)

    # ------------------------------------------------------------------
    # Экспорт результатов
    # ------------------------------------------------------------------

    def _export_csv(self) -> None:
        if self._aging_results is None or self._netlist_path is None:
            return
        try:
            path = export_to_csv(
                self._aging_results,
                self._schema_dir,
                self._aging_params[0],
            )
            self._lbl_export.setText(f"✅ CSV сохранён: {path}")
            self._log(f"📊 Экспорт CSV: {path}")
        except Exception as exc:
            show_error(self, f"Ошибка экспорта CSV:\n{exc}")
            self._log(f"❌ Ошибка экспорта CSV: {exc}")

    def _export_txt(self) -> None:
        if self._aging_results is None or self._netlist_path is None:
            return
        try:
            path = export_to_txt(
                self._aging_results,
                self._schema_dir,
                self._aging_params[0],
            )
            self._lbl_export.setText(f"✅ TXT сохранён: {path}")
            self._log(f"📄 Экспорт TXT: {path}")
        except Exception as exc:
            show_error(self, f"Ошибка экспорта TXT:\n{exc}")
            self._log(f"❌ Ошибка экспорта TXT: {exc}")

    # ------------------------------------------------------------------
    # Экспорт температурного анализа
    # ------------------------------------------------------------------

    def _export_temp_csv(self) -> None:
        if self._temp_comparisons is None or self._netlist_path is None:
            return
        try:
            path = export_temp_to_csv(
                self._temp_comparisons,
                self._schema_dir,
                self._aging_params[0],
            )
            self._lbl_temp_export.setText(f"✅ CSV (темп.) сохранён: {path}")
            self._log(f"📊 Экспорт CSV температурного анализа: {path}")
        except Exception as exc:
            show_error(self, f"Ошибка экспорта CSV:\n{exc}")
            self._log(f"❌ Ошибка экспорта CSV (темп.): {exc}")

    def _export_temp_txt(self) -> None:
        if self._temp_comparisons is None or self._netlist_path is None:
            return
        try:
            path = export_temp_to_txt(
                self._temp_comparisons,
                self._schema_dir,
                self._aging_params[0],
            )
            self._lbl_temp_export.setText(f"✅ TXT (темп.) сохранён: {path}")
            self._log(f"📄 Экспорт TXT температурного анализа: {path}")
        except Exception as exc:
            show_error(self, f"Ошибка экспорта TXT:\n{exc}")
            self._log(f"❌ Ошибка экспорта TXT (темп.): {exc}")

    def _export_temp_html(self) -> None:
        if self._temp_comparisons is None or self._netlist_path is None:
            return
        try:
            t_new = self._temp_comparisons[0].t_new_c
            output_path = self._schema_dir / f"temp_report_T{int(t_new):+d}.html"
            path = generate_temp_html_report(
                self._temp_comparisons,
                self._aging_params[0],
                output_path,
            )
            self._lbl_temp_export.setText(f"✅ HTML (темп.) сохранён: {path.name}")
            self._log(f"🌐 Экспорт HTML температурного анализа: {path}")
            webbrowser.open(path.as_uri())
        except Exception as exc:
            show_error(self, f"Ошибка экспорта HTML:\n{exc}")
            self._log(f"❌ Ошибка экспорта HTML (темп.): {exc}")

    # ------------------------------------------------------------------
    # Журнал
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        self._log_view.append(msg)
