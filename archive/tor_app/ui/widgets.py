"""共通ウィジェット。"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSlider, QWidget


class ValueSlider(QWidget):
    """0〜1 の値を 0.01 刻みで操作するスライダー（名前・値表示つき）。"""

    valueChanged = pyqtSignal(float)

    def __init__(self, label: str, value: float = 0.0, checkable: bool = False, checked: bool = True,
                 tooltip: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.check = None
        if checkable:
            self.check = QCheckBox()
            self.check.setChecked(checked)
            self.check.setToolTip("チェックすると値を手動で固定します")
            self.check.toggled.connect(self._on_toggled)
            layout.addWidget(self.check)
        self.name = QLabel(label)
        self.name.setMinimumWidth(62)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(round(value * 100))
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(32)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.name)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)
        if tooltip:
            self.setToolTip(tooltip)
        self.slider.valueChanged.connect(self._on_changed)
        self._update_label()
        if self.check is not None:
            self.slider.setEnabled(checked)

    def value(self) -> float:
        return self.slider.value() / 100

    def setValue(self, value: float, emit: bool = False) -> None:
        self.slider.blockSignals(not emit)
        self.slider.setValue(round(value * 100))
        self.slider.blockSignals(False)
        self._update_label()

    def isChecked(self) -> bool:
        return self.check is None or self.check.isChecked()

    def _update_label(self) -> None:
        self.value_label.setText(f"{self.value():.2f}")

    def _on_changed(self, _raw: int) -> None:
        self._update_label()
        self.valueChanged.emit(self.value())

    def _on_toggled(self, checked: bool) -> None:
        self.slider.setEnabled(checked)
        self.valueChanged.emit(self.value())
