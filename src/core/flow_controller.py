"""
FlowController — orchestrates the high-level winding process state machine.

States
------
IDLE → INITIALISING → RUNNING → PAUSED → FINISHING → COMPLETED
                                        ↘ ERROR

Transitions are driven by external commands (start / pause / resume / stop /
emergency_stop) and by automatic checks against SystemState.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum, auto
from typing import Callable, List, Optional

from src.core.system_state import AlarmLevel, SystemState
from src.core.process_validator import ProcessValidator, Severity

logger = logging.getLogger(__name__)


class MachineState(Enum):
    IDLE = auto()
    INITIALISING = auto()
    RUNNING = auto()
    PAUSED = auto()
    FINISHING = auto()
    COMPLETED = auto()
    ERROR = auto()


# Type alias for state-change callbacks
StateChangeCallback = Callable[[MachineState, MachineState], None]


class FlowController:
    """
    Manages the winding process lifecycle.

    Parameters
    ----------
    state : SystemState | None
        Shared runtime state singleton.
    validator : ProcessValidator | None
        Parameter/sensor validator (defaults to ProcessValidator()).
    check_interval : float
        Seconds between automated safety checks while running.
    """

    def __init__(
        self,
        state: Optional[SystemState] = None,
        validator: Optional[ProcessValidator] = None,
        check_interval: float = 1.0,
    ) -> None:
        self._state = state or SystemState.get_instance()
        self._validator = validator or ProcessValidator()
        self._check_interval = check_interval

        self._machine_state = MachineState.IDLE
        self._lock = threading.RLock()
        self._callbacks: List[StateChangeCallback] = []

        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------

    @property
    def machine_state(self) -> MachineState:
        with self._lock:
            return self._machine_state

    def register_callback(self, cb: StateChangeCallback) -> None:
        """Register a callback invoked on every state transition."""
        self._callbacks.append(cb)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """
        Transition from IDLE / PAUSED to INITIALISING / RUNNING.
        Returns True if the transition was accepted.
        """
        with self._lock:
            if self._machine_state not in (MachineState.IDLE,):
                logger.warning("start() ignored in state %s", self._machine_state)
                return False

            # Pre-flight parameter validation
            result = self._validator.validate_params(self._state.params)
            for issue in result.errors:
                self._state.add_alarm(
                    AlarmLevel.ERROR, 1000,
                    f"Pre-start check failed — {issue.field}: {issue.message}",
                )
            if not result.is_ok:
                logger.error("Pre-start validation failed:\n%s", result)
                self._transition(MachineState.ERROR)
                return False

            for issue in result.warnings:
                self._state.add_alarm(
                    AlarmLevel.WARNING, 1001,
                    f"Pre-start warning — {issue.field}: {issue.message}",
                )

            self._transition(MachineState.INITIALISING)
            # Simulate init then go RUNNING
            threading.Thread(target=self._do_init, daemon=True).start()
            return True

    def pause(self) -> bool:
        with self._lock:
            if self._machine_state != MachineState.RUNNING:
                return False
            self._transition(MachineState.PAUSED)
            return True

    def resume(self) -> bool:
        with self._lock:
            if self._machine_state != MachineState.PAUSED:
                return False
            self._transition(MachineState.RUNNING)
            return True

    def stop(self) -> bool:
        """Graceful stop — finish the current layer then halt."""
        with self._lock:
            if self._machine_state not in (MachineState.RUNNING, MachineState.PAUSED):
                return False
            self._transition(MachineState.FINISHING)
            threading.Thread(target=self._do_finish, daemon=True).start()
            return True

    def emergency_stop(self) -> None:
        """Immediate halt regardless of current state."""
        with self._lock:
            logger.critical("EMERGENCY STOP triggered")
            self._state.add_alarm(AlarmLevel.CRITICAL, 9999, "Emergency stop activated")
            self._stop_monitor()
            self._transition(MachineState.ERROR)

    def reset(self) -> bool:
        """Reset from ERROR or COMPLETED back to IDLE."""
        with self._lock:
            if self._machine_state not in (MachineState.ERROR, MachineState.COMPLETED):
                return False
            self._state.clear_alarms()
            self._transition(MachineState.IDLE)
            return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _transition(self, new_state: MachineState) -> None:
        old_state = self._machine_state
        self._machine_state = new_state
        logger.info("State: %s → %s", old_state.name, new_state.name)
        for cb in self._callbacks:
            try:
                cb(old_state, new_state)
            except Exception as exc:  # noqa: BLE001
                logger.warning("State-change callback error: %s", exc)

    def _do_init(self) -> None:
        time.sleep(0.5)  # Simulate hardware initialisation
        with self._lock:
            if self._machine_state == MachineState.INITIALISING:
                self._transition(MachineState.RUNNING)
                self._start_monitor()

    def _do_finish(self) -> None:
        time.sleep(0.5)  # Simulate finishing sequence
        with self._lock:
            self._stop_monitor()
            if self._machine_state == MachineState.FINISHING:
                self._transition(MachineState.COMPLETED)

    # ------------------------------------------------------------------
    # Safety monitor thread
    # ------------------------------------------------------------------

    def _start_monitor(self) -> None:
        if self._monitor_running:
            return
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="FlowController-Monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _stop_monitor(self) -> None:
        self._monitor_running = False

    def _monitor_loop(self) -> None:
        while self._monitor_running:
            time.sleep(self._check_interval)
            if self._machine_state != MachineState.RUNNING:
                continue
            result = self._validator.validate_sensor(
                self._state.sensor,
                self._state.params,
            )
            for issue in result.issues:
                if issue.severity == Severity.ERROR:
                    self._state.add_alarm(
                        AlarmLevel.ERROR, 2000,
                        f"Runtime sensor error — {issue.field}: {issue.message}",
                    )
                    with self._lock:
                        self._stop_monitor()
                        self._transition(MachineState.ERROR)
                    return
                elif issue.severity == Severity.WARNING:
                    self._state.add_alarm(
                        AlarmLevel.WARNING, 2001,
                        f"Runtime sensor warning — {issue.field}: {issue.message}",
                    )

            # Check for winding completion
            sensor = self._state.sensor
            params = self._state.params
            if (params.total_layers > 0 and
                    sensor.layers_completed >= params.total_layers):
                logger.info("All layers wound — triggering finish sequence")
                with self._lock:
                    self._stop_monitor()
                    self._transition(MachineState.FINISHING)
                threading.Thread(target=self._do_finish, daemon=True).start()
                return
