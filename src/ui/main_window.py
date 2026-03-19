"""
MainWindow — PySide6 main window for the fiber-optic winding machine upper
computer.

Layout
------
┌─────────────────────────────────────────────────────────┐
│  Menu bar                                               │
├─────────────────────────────────┬───────────────────────┤
│  Tension plot  (centre)         │  Status panel (right) │
│                                 │  - Comm status        │
│                                 │  - Sensor values      │
│                                 │  - Alarms             │
├─────────────────────────────────┴───────────────────────┤
│  Control bar: [Start] [Pause] [Resume] [Stop] [E-Stop]  │
├─────────────────────────────────────────────────────────┤
│  Status bar: machine state, serial port                 │
└─────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from src.config.config_loader import AppConfig, load_config
from src.core.flow_controller import FlowController, MachineState
from src.core.system_state import AlarmLevel, SystemState
from src.ui.widgets.status_panel import StatusPanelWidget
from src.ui.widgets.tension_plot import TensionPlotWidget


_STATE_COLORS = {
    MachineState.IDLE: "#888888",
    MachineState.INITIALISING: "#4488FF",
    MachineState.RUNNING: "#00CC44",
    MachineState.PAUSED: "#FFAA00",
    MachineState.FINISHING: "#AAAAFF",
    MachineState.COMPLETED: "#00FFAA",
    MachineState.ERROR: "#FF3333",
}

_STATE_LABELS_ZH = {
    MachineState.IDLE: "待机",
    MachineState.INITIALISING: "初始化中",
    MachineState.RUNNING: "运行中",
    MachineState.PAUSED: "已暂停",
    MachineState.FINISHING: "收尾中",
    MachineState.COMPLETED: "已完成",
    MachineState.ERROR: "故障",
}


class MainWindow(QMainWindow):
    """
    Application main window.

    Parameters
    ----------
    config : AppConfig | None
        Application configuration.  Loads default config when None.
    state : SystemState | None
        Shared runtime state singleton.
    flow_controller : FlowController | None
        Process flow controller.
    """

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        state: Optional[SystemState] = None,
        flow_controller: Optional[FlowController] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._cfg = config or load_config()
        self._state = state or SystemState.get_instance()
        self._flow = flow_controller or FlowController(state=self._state)

        self._setup_appearance()
        self._setup_ui()
        self._setup_menu()
        self._setup_status_bar()
        self._setup_timer()

        self._flow.register_callback(self._on_state_change)

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _setup_appearance(self) -> None:
        self.setWindowTitle(self._cfg.ui.window_title)
        self.resize(self._cfg.ui.window_width, self._cfg.ui.window_height)

        if self._cfg.ui.dark_theme:
            palette = QPalette()
            palette.setColor(QPalette.Window, QColor("#1a1a2e"))
            palette.setColor(QPalette.WindowText, QColor("#FFFFFF"))
            palette.setColor(QPalette.Base, QColor("#0f3460"))
            palette.setColor(QPalette.AlternateBase, QColor("#16213e"))
            palette.setColor(QPalette.Text, QColor("#FFFFFF"))
            palette.setColor(QPalette.Button, QColor("#0f3460"))
            palette.setColor(QPalette.ButtonText, QColor("#FFFFFF"))
            QApplication.setPalette(palette)
            self.setStyleSheet(
                "QMainWindow, QWidget { background-color: #1a1a2e; color: #FFFFFF; }"
                "QPushButton { background-color: #0f3460; color: #FFFFFF; "
                "              border: 1px solid #444; border-radius: 4px; "
                "              padding: 6px 14px; }"
                "QPushButton:hover { background-color: #1a5276; }"
                "QPushButton:disabled { color: #555555; background-color: #0a0a1a; }"
                "QGroupBox { border: 1px solid #333; border-radius: 4px; "
                "            margin-top: 6px; padding-top: 8px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #88AAFF; }"
                "QStatusBar { background-color: #0d0d1a; color: #AAAAAA; }"
            )

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(8, 8, 8, 4)

        # --- Splitter: plot | status panel ---
        splitter = QSplitter(Qt.Horizontal)

        self._tension_plot = TensionPlotWidget(
            history_len=self._cfg.ui.tension_plot_history,
            target_tension=self._cfg.process.target_tension,
        )
        splitter.addWidget(self._tension_plot)

        self._status_panel = StatusPanelWidget()
        self._status_panel.setMaximumWidth(320)
        splitter.addWidget(self._status_panel)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter, stretch=1)

        # --- Control bar ---
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setSpacing(8)

        self._btn_start = QPushButton("▶  启动")
        self._btn_pause = QPushButton("⏸  暂停")
        self._btn_resume = QPushButton("▶  继续")
        self._btn_stop = QPushButton("⏹  停止")
        self._btn_estop = QPushButton("🛑  紧急停止")
        self._btn_reset = QPushButton("↺  复位")

        self._btn_estop.setStyleSheet(
            "QPushButton { background-color: #8B0000; color: #FFFFFF; "
            "font-weight: bold; border: 2px solid #FF0000; }"
            "QPushButton:hover { background-color: #CC0000; }"
        )

        for btn in (self._btn_start, self._btn_pause, self._btn_resume,
                    self._btn_stop, self._btn_estop, self._btn_reset):
            ctrl_bar.addWidget(btn)

        ctrl_bar.addStretch()

        self._machine_state_label = QLabel("待机")
        self._machine_state_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #888888; padding: 4px 10px;"
        )
        ctrl_bar.addWidget(self._machine_state_label)

        main_layout.addLayout(ctrl_bar)

        # --- Wire up buttons ---
        self._btn_start.clicked.connect(self._on_start)
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_resume.clicked.connect(self._on_resume)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_estop.clicked.connect(self._on_estop)
        self._btn_reset.clicked.connect(self._on_reset)

        self._refresh_button_states()

    def _setup_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("文件(&F)")
        action_exit = QAction("退出(&X)", self)
        action_exit.triggered.connect(self.close)
        file_menu.addAction(action_exit)

        view_menu = menu_bar.addMenu("视图(&V)")
        action_clear_plot = QAction("清除曲线", self)
        action_clear_plot.triggered.connect(self._tension_plot.clear)
        view_menu.addAction(action_clear_plot)

        action_clear_alarms = QAction("清除报警", self)
        action_clear_alarms.triggered.connect(self._on_clear_alarms)
        view_menu.addAction(action_clear_alarms)

    def _setup_status_bar(self) -> None:
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._sb_port_label = QLabel("串口: 未连接")
        self._sb_rx_label = QLabel("RX: 0")
        self._sb_tx_label = QLabel("TX: 0")
        self._statusbar.addPermanentWidget(self._sb_port_label)
        self._statusbar.addPermanentWidget(self._sb_rx_label)
        self._statusbar.addPermanentWidget(self._sb_tx_label)

    def _setup_timer(self) -> None:
        interval_ms = max(50, int(1000 / max(1, self._cfg.ui.refresh_rate_hz)))
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(interval_ms)
        self._refresh_timer.timeout.connect(self._refresh_ui)
        self._refresh_timer.start()

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh_ui(self) -> None:
        sensor = self._state.sensor
        comm = self._state.comm
        alarms = self._state.alarms

        # Update tension plot
        self._tension_plot.update_tension(sensor.timestamp, sensor.tension_value)

        # Update status panel
        self._status_panel.update_comm_status(comm)
        self._status_panel.update_sensor_data(sensor)
        self._status_panel.update_alarms(alarms)

        # Update status bar
        port = comm.serial_port or "未连接"
        self._sb_port_label.setText(f"串口: {port}")
        self._sb_rx_label.setText(f"RX: {comm.rx_bytes}")
        self._sb_tx_label.setText(f"TX: {comm.tx_bytes}")

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        if not self._flow.start():
            QMessageBox.warning(self, "启动失败",
                                "无法启动：请检查参数或解除故障后复位。")
        self._refresh_button_states()

    def _on_pause(self) -> None:
        self._flow.pause()
        self._refresh_button_states()

    def _on_resume(self) -> None:
        self._flow.resume()
        self._refresh_button_states()

    def _on_stop(self) -> None:
        self._flow.stop()
        self._refresh_button_states()

    def _on_estop(self) -> None:
        reply = QMessageBox.question(
            self, "紧急停止",
            "确认立即停机？\n此操作将中断当前绕线进程。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._flow.emergency_stop()
            self._refresh_button_states()

    def _on_reset(self) -> None:
        self._flow.reset()
        self._refresh_button_states()

    def _on_clear_alarms(self) -> None:
        self._state.clear_alarms()

    # ------------------------------------------------------------------
    # State change callback (called from FlowController thread)
    # ------------------------------------------------------------------

    def _on_state_change(self, old: MachineState, new: MachineState) -> None:
        # Schedule the label update on the main thread
        color = _STATE_COLORS.get(new, "#FFFFFF")
        label = _STATE_LABELS_ZH.get(new, new.name)
        self._machine_state_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {color}; padding: 4px 10px;"
        )
        self._machine_state_label.setText(label)
        self._refresh_button_states()

    # ------------------------------------------------------------------
    # Button enable/disable logic
    # ------------------------------------------------------------------

    def _refresh_button_states(self) -> None:
        s = self._flow.machine_state
        self._btn_start.setEnabled(s == MachineState.IDLE)
        self._btn_pause.setEnabled(s == MachineState.RUNNING)
        self._btn_resume.setEnabled(s == MachineState.PAUSED)
        self._btn_stop.setEnabled(s in (MachineState.RUNNING, MachineState.PAUSED))
        self._btn_estop.setEnabled(s not in (MachineState.IDLE, MachineState.COMPLETED,
                                             MachineState.ERROR))
        self._btn_reset.setEnabled(s in (MachineState.ERROR, MachineState.COMPLETED))
