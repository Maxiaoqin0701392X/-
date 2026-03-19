"""
SystemState — thread-safe singleton that holds the shared runtime state for the
fiber-optic winding machine upper computer.

Structure
---------
- SensorData      : real-time measurements from hardware (FPGA / STM32)
- ProcessParams   : winding process configuration parameters
- CommStatus      : communication link health for each peripheral
- AlarmEntry      : individual alarm record
- SystemState     : singleton that aggregates all of the above with a per-field
                    RLock so multiple producer/consumer threads can update
                    independently without dead-locking each other
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class AlarmLevel(Enum):
    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    CRITICAL = auto()


@dataclass
class AlarmEntry:
    level: AlarmLevel
    code: int
    message: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class SensorData:
    """Real-time sensor readings sent up from FPGA / STM32."""
    tension_value: float = 0.0          # 张力传感器读数 (N)
    spindle_speed: float = 0.0          # 主轴转速 (rpm)
    winding_position: float = 0.0       # 排线装置位置 (mm)
    layers_completed: int = 0           # 已绕层数
    turns_completed: int = 0            # 已绕圈数
    fpga_status_code: int = 0           # FPGA状态寄存器原始值
    stm32_status_code: int = 0          # STM32状态寄存器原始值
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProcessParams:
    """Winding process configuration parameters."""
    target_tension: float = 0.0         # 目标张力 (N)
    spindle_target_speed: float = 0.0   # 主轴目标转速 (rpm)
    winding_target_speed: float = 0.0   # 排线目标速度 (mm/s)
    total_layers: int = 0               # 总绕线层数
    turns_per_layer: int = 0            # 每层圈数
    overlap_width: float = 0.0          # 重叠宽度 (mm)
    overlap_position: float = 0.0       # 重叠位置 (mm)
    fiber_diameter: float = 0.125       # 光纤直径 (mm)
    initial_tension: float = 0.0        # 初始张力值 (N)
    enable_flag: bool = False           # FPGA使能标志


@dataclass
class CommStatus:
    """Communication link health for every connected peripheral."""
    fpga_connected: bool = False
    stm32_connected: bool = False
    raspberry_connected: bool = False
    serial_port: str = ""
    last_fpga_heartbeat: float = 0.0
    last_stm32_heartbeat: float = 0.0
    rx_bytes: int = 0
    tx_bytes: int = 0
    error_count: int = 0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class SystemState:
    """
    Thread-safe singleton that centralises all runtime state.

    Usage
    -----
    state = SystemState.get_instance()
    state.update_sensor(tension_value=1.23)
    print(state.sensor.tension_value)
    """

    _instance: Optional["SystemState"] = None
    _creation_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._sensor_lock = threading.RLock()
        self._params_lock = threading.RLock()
        self._comm_lock = threading.RLock()
        self._alarm_lock = threading.RLock()

        self._sensor = SensorData()
        self._params = ProcessParams()
        self._comm = CommStatus()
        self._alarms: List[AlarmEntry] = []
        self._max_alarm_history: int = 200

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "SystemState":
        if cls._instance is None:
            with cls._creation_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (useful in unit tests)."""
        with cls._creation_lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Sensor data
    # ------------------------------------------------------------------

    @property
    def sensor(self) -> SensorData:
        with self._sensor_lock:
            return self._sensor

    def update_sensor(self, **kwargs) -> None:
        """Update one or more fields of SensorData atomically."""
        with self._sensor_lock:
            for key, value in kwargs.items():
                if hasattr(self._sensor, key):
                    setattr(self._sensor, key, value)
                else:
                    raise AttributeError(f"SensorData has no field '{key}'")
            self._sensor.timestamp = time.time()

    # ------------------------------------------------------------------
    # Process parameters
    # ------------------------------------------------------------------

    @property
    def params(self) -> ProcessParams:
        with self._params_lock:
            return self._params

    def update_params(self, **kwargs) -> None:
        """Update one or more fields of ProcessParams atomically."""
        with self._params_lock:
            for key, value in kwargs.items():
                if hasattr(self._params, key):
                    setattr(self._params, key, value)
                else:
                    raise AttributeError(f"ProcessParams has no field '{key}'")

    # ------------------------------------------------------------------
    # Communication status
    # ------------------------------------------------------------------

    @property
    def comm(self) -> CommStatus:
        with self._comm_lock:
            return self._comm

    def update_comm(self, **kwargs) -> None:
        """Update one or more fields of CommStatus atomically."""
        with self._comm_lock:
            for key, value in kwargs.items():
                if hasattr(self._comm, key):
                    setattr(self._comm, key, value)
                else:
                    raise AttributeError(f"CommStatus has no field '{key}'")

    # ------------------------------------------------------------------
    # Alarms
    # ------------------------------------------------------------------

    @property
    def alarms(self) -> List[AlarmEntry]:
        with self._alarm_lock:
            return list(self._alarms)

    def add_alarm(self, level: AlarmLevel, code: int, message: str) -> None:
        """Append an alarm entry; oldest entries are pruned at max_alarm_history."""
        with self._alarm_lock:
            entry = AlarmEntry(level=level, code=code, message=message)
            self._alarms.append(entry)
            if len(self._alarms) > self._max_alarm_history:
                self._alarms = self._alarms[-self._max_alarm_history:]

    def clear_alarms(self) -> None:
        with self._alarm_lock:
            self._alarms.clear()

    def has_active_alarms(self, min_level: AlarmLevel = AlarmLevel.WARNING) -> bool:
        with self._alarm_lock:
            return any(a.level.value >= min_level.value for a in self._alarms)
