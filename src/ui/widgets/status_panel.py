"""
StatusPanelWidget — displays the current FPGA / STM32 connection status,
sensor readings and active alarms in a compact panel.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.system_state import AlarmLevel, CommStatus, SensorData

_CONNECTED_STYLE = "color: #00FF80; font-weight: bold;"
_DISCONNECTED_STYLE = "color: #FF4444; font-weight: bold;"
_VALUE_STYLE = "color: #FFFFFF; font-size: 14px;"
_LABEL_STYLE = "color: #AAAAAA; font-size: 11px;"

_ALARM_COLORS = {
    AlarmLevel.INFO: QColor("#80C0FF"),
    AlarmLevel.WARNING: QColor("#FFCC00"),
    AlarmLevel.ERROR: QColor("#FF6644"),
    AlarmLevel.CRITICAL: QColor("#FF0000"),
}


class StatusPanelWidget(QWidget):
    """
    Compact status panel showing:
    - FPGA / STM32 / Raspberry Pi connection LEDs
    - Key sensor values (tension, spindle speed, position, layers, turns)
    - Rolling alarm list
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # --- Connection status group ---
        conn_group = QGroupBox("通信状态")
        conn_group.setStyleSheet("QGroupBox { color: #AAAAAA; }")
        conn_layout = QGridLayout(conn_group)

        self._fpga_label = self._make_status_label("FPGA")
        self._stm32_label = self._make_status_label("STM32")
        self._rpi_label = self._make_status_label("树莓派")

        conn_layout.addWidget(QLabel("FPGA:", styleSheet=_LABEL_STYLE), 0, 0)
        conn_layout.addWidget(self._fpga_label, 0, 1)
        conn_layout.addWidget(QLabel("STM32:", styleSheet=_LABEL_STYLE), 1, 0)
        conn_layout.addWidget(self._stm32_label, 1, 1)
        conn_layout.addWidget(QLabel("树莓派:", styleSheet=_LABEL_STYLE), 2, 0)
        conn_layout.addWidget(self._rpi_label, 2, 1)

        root.addWidget(conn_group)

        # --- Sensor readings group ---
        sensor_group = QGroupBox("传感器数据")
        sensor_group.setStyleSheet("QGroupBox { color: #AAAAAA; }")
        sensor_layout = QGridLayout(sensor_group)

        fields = [
            ("张力 (N):", "_tension_val"),
            ("主轴转速 (rpm):", "_spindle_val"),
            ("排线位置 (mm):", "_position_val"),
            ("已绕层数:", "_layers_val"),
            ("已绕圈数:", "_turns_val"),
            ("FPGA状态:", "_fpga_status_val"),
            ("STM32状态:", "_stm32_status_val"),
        ]
        for row, (label_text, attr) in enumerate(fields):
            lbl = QLabel(label_text, styleSheet=_LABEL_STYLE)
            val = QLabel("—", styleSheet=_VALUE_STYLE)
            setattr(self, attr, val)
            sensor_layout.addWidget(lbl, row, 0)
            sensor_layout.addWidget(val, row, 1)

        root.addWidget(sensor_group)

        # --- Alarm list ---
        alarm_group = QGroupBox("报警列表")
        alarm_group.setStyleSheet("QGroupBox { color: #AAAAAA; }")
        alarm_layout = QVBoxLayout(alarm_group)

        self._alarm_list = QListWidget()
        self._alarm_list.setMaximumHeight(160)
        self._alarm_list.setStyleSheet(
            "QListWidget { background: #111111; color: #FFFFFF; "
            "font-size: 11px; border: 1px solid #333333; }"
        )
        alarm_layout.addWidget(self._alarm_list)
        root.addWidget(alarm_group)

        root.addStretch()

    # ------------------------------------------------------------------
    # Update methods (called from UI refresh timer)
    # ------------------------------------------------------------------

    def update_comm_status(self, comm: CommStatus) -> None:
        self._set_conn(self._fpga_label, comm.fpga_connected)
        self._set_conn(self._stm32_label, comm.stm32_connected)
        self._set_conn(self._rpi_label, comm.raspberry_connected)

    def update_sensor_data(self, sensor: SensorData) -> None:
        self._tension_val.setText(f"{sensor.tension_value:.3f}")
        self._spindle_val.setText(f"{sensor.spindle_speed:.1f}")
        self._position_val.setText(f"{sensor.winding_position:.2f}")
        self._layers_val.setText(str(sensor.layers_completed))
        self._turns_val.setText(str(sensor.turns_completed))
        self._fpga_status_val.setText(f"0x{sensor.fpga_status_code:04X}")
        self._stm32_status_val.setText(f"0x{sensor.stm32_status_code:04X}")

    def update_alarms(self, alarms: list) -> None:
        self._alarm_list.clear()
        for alarm in reversed(alarms[-30:]):
            import datetime
            ts_str = datetime.datetime.fromtimestamp(alarm.timestamp).strftime("%H:%M:%S")
            text = f"[{ts_str}] [{alarm.level.name}] {alarm.message}"
            item = QListWidgetItem(text)
            item.setForeground(_ALARM_COLORS.get(alarm.level, QColor("white")))
            self._alarm_list.addItem(item)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_status_label(text: str) -> QLabel:
        lbl = QLabel(f"{text}: ● 未连接")
        lbl.setStyleSheet(_DISCONNECTED_STYLE)
        return lbl

    @staticmethod
    def _set_conn(label: QLabel, connected: bool) -> None:
        if connected:
            label.setText("● 已连接")
            label.setStyleSheet(_CONNECTED_STYLE)
        else:
            label.setText("● 未连接")
            label.setStyleSheet(_DISCONNECTED_STYLE)
