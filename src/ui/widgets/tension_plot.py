"""
TensionPlotWidget — real-time tension curve display using pyqtgraph.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Tuple

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget


class TensionPlotWidget(QWidget):
    """
    Scrolling real-time plot showing the tension sensor reading over time.

    Parameters
    ----------
    history_len : int
        Number of samples to keep on screen.
    target_tension : float
        Initial setpoint line value.
    """

    def __init__(
        self,
        history_len: int = 500,
        target_tension: float = 1.5,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._history_len = history_len
        self._target_tension = target_tension

        self._times: Deque[float] = deque(maxlen=history_len)
        self._values: Deque[float] = deque(maxlen=history_len)
        self._t0: float | None = None

        self._setup_ui()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._plot_widget = pg.PlotWidget(title="张力曲线 (Tension)")
        self._plot_widget.setLabel("left", "张力 (N)")
        self._plot_widget.setLabel("bottom", "时间 (s)")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self._plot_widget.setBackground("#1e1e1e")
        self._plot_widget.getAxis("left").setTextPen("white")
        self._plot_widget.getAxis("bottom").setTextPen("white")

        # Actual tension curve (cyan)
        self._curve = self._plot_widget.plot(
            pen=pg.mkPen(color="#00FFFF", width=2),
            name="张力",
        )

        # Setpoint reference line (yellow dashed)
        self._setpoint_line = pg.InfiniteLine(
            pos=self._target_tension,
            angle=0,
            pen=pg.mkPen(color="#FFFF00", width=1, style=Qt.DashLine),
            label=f"目标 {self._target_tension:.2f} N",
            labelOpts={"color": "#FFFF00", "position": 0.95},
        )
        self._plot_widget.addItem(self._setpoint_line)

        layout.addWidget(self._plot_widget)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_tension(self, timestamp: float, value: float) -> None:
        """Append a new reading and refresh the plot."""
        if self._t0 is None:
            self._t0 = timestamp
        self._times.append(timestamp - self._t0)
        self._values.append(value)
        self._curve.setData(list(self._times), list(self._values))

    def set_target_tension(self, value: float) -> None:
        """Update the setpoint reference line."""
        self._target_tension = value
        self._setpoint_line.setPos(value)
        self._setpoint_line.label.setFormat(f"目标 {value:.2f} N")

    def clear(self) -> None:
        self._times.clear()
        self._values.clear()
        self._t0 = None
        self._curve.setData([], [])
