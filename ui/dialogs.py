"""
Диалоговые окна приложения.
"""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QDoubleSpinBox,
    QLabel, QVBoxLayout, QSpinBox, QMessageBox, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


class AgingParamsDialog(QDialog):
    """
    Диалог ввода параметров расчёта старения.

    Parameters
    ----------
    vth_parsed : float | None
        Если передано — пороговое напряжение PMOS, успешно извлечённое из .model
        блока netlist. Поле |Vth| PMOS отображается как read-only с пометкой
        «из модели SPICE».
        Если None — netlist ещё не загружен или vth0 не найден; поле активно.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        vth_parsed: float | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Параметры расчёта")
        self.setMinimumWidth(440)
        self._vth_parsed = vth_parsed
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Параметры анализа старения")
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        if self._vth_parsed is not None:
            hint_text = (
                "Пороговое напряжение Vth автоматически извлечено из блоков .model "
                "в загруженном netlist-файле и недоступно для редактирования."
            )
        else:
            hint_text = (
                "Netlist не загружен или блоки .model не содержат vth0 — "
                "введите пороговое напряжение PMOS вручную.\n"
                "Для L ≥ 100 нм: Vth ≈ VTH0 + 0.05.  Для 32 нм: Vth ≈ 0.46 В."
            )
        hint = QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(12)

        self._years_spin = QSpinBox()
        self._years_spin.setRange(1, 50)
        self._years_spin.setValue(10)
        self._years_spin.setSuffix(" лет")
        form.addRow("Время наработки:", self._years_spin)

        # --- Vth PMOS ---
        from PyQt6.QtWidgets import QHBoxLayout
        vth_row = QHBoxLayout()
        vth_row.setSpacing(8)

        self._vth_spin = QDoubleSpinBox()
        self._vth_spin.setRange(0.01, 2.0)
        self._vth_spin.setSingleStep(0.01)
        self._vth_spin.setDecimals(3)
        self._vth_spin.setSuffix(" В")

        if self._vth_parsed is not None:
            self._vth_spin.setValue(self._vth_parsed)
            self._vth_spin.setEnabled(False)
            self._vth_spin.setToolTip(
                f"Значение {self._vth_parsed:.4f} В взято из блока .model в netlist"
            )
            badge = QLabel("✓ из модели")
            badge.setStyleSheet(
                "color: #16a34a; font-size: 11px; font-weight: 600;"
                "background: #dcfce7; border-radius: 4px; padding: 2px 6px;"
            )
            vth_row.addWidget(self._vth_spin)
            vth_row.addWidget(badge)
        else:
            self._vth_spin.setValue(0.46)
            badge = QLabel("⚠ вручную")
            badge.setStyleSheet(
                "color: #b45309; font-size: 11px; font-weight: 600;"
                "background: #fef3c7; border-radius: 4px; padding: 2px 6px;"
            )
            vth_row.addWidget(self._vth_spin)
            vth_row.addWidget(badge)

        vth_container = QWidget()
        vth_container.setLayout(vth_row)
        form.addRow("|Vth| PMOS:", vth_container)

        # --- HCI exponent ---
        self._n_hci_spin = QDoubleSpinBox()
        self._n_hci_spin.setRange(0.10, 0.90)
        self._n_hci_spin.setSingleStep(0.01)
        self._n_hci_spin.setDecimals(2)
        self._n_hci_spin.setValue(0.27)
        form.addRow("Показатель HCI по времени (n):", self._n_hci_spin)

        # --- σ mobility ---
        self._sigma_spin = QDoubleSpinBox()
        self._sigma_spin.setRange(0.01, 10.0)
        self._sigma_spin.setSingleStep(0.01)
        self._sigma_spin.setDecimals(2)
        self._sigma_spin.setValue(0.24)
        self._sigma_spin.setSuffix(" × 10⁻¹⁶ м²")
        form.addRow("Коэф. деградации подвижности σ:", self._sigma_spin)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def years(self) -> float:
        return float(self._years_spin.value())

    @property
    def vth_pmos(self) -> float:
        """Возвращает vth_pmos: из модели (если распарсен) или введённое вручную."""
        return self._vth_spin.value()

    @property
    def n_hci(self) -> float:
        return self._n_hci_spin.value()

    @property
    def sigma_mobility(self) -> float:
        return self._sigma_spin.value() * 1e-16


class TemperatureDialog(QDialog):
    """
    Диалог ввода новой температуры для повторного расчёта деградации.
    Позволяет задать T в °C или K.
    """

    def __init__(self, t0_c: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Расчёт с учётом температуры")
        self.setMinimumWidth(420)
        self._t0_c = t0_c
        self._build_ui()

    def _build_ui(self) -> None:
        from PyQt6.QtWidgets import QRadioButton, QButtonGroup, QHBoxLayout
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Анализ температурного влияния")
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        hint = QLabel(
            f"Базовая температура (из log-файла): {self._t0_c:.1f} °C\n"
            "Введите температуру для сравнения. "
            "Программа пересчитает ΔVth и коэффициенты деградации подвижности."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(12)

        self._temp_spin = QDoubleSpinBox()
        self._temp_spin.setRange(-55.0, 300.0)
        self._temp_spin.setSingleStep(5.0)
        self._temp_spin.setDecimals(1)
        self._temp_spin.setValue(85.0)
        self._temp_spin.setSuffix(" °C")
        form.addRow("Новая температура (°C):", self._temp_spin)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def temperature_c(self) -> float:
        return self._temp_spin.value()


def show_error(parent: QWidget | None, message: str, title: str = "Ошибка") -> None:
    QMessageBox.critical(parent, title, message)


def show_info(parent: QWidget | None, message: str, title: str = "Информация") -> None:
    QMessageBox.information(parent, message=message, title=title)


def ask_yes_no(parent: QWidget | None, message: str, title: str = "Вопрос") -> bool:
    reply = QMessageBox.question(
        parent, title, message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes
