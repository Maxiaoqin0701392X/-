"""
ProcessValidator — validates winding process parameters and runtime state
before and during the fiber-optic winding process.

A ValidationResult is returned for every check.  The caller can decide
whether to proceed, warn the operator, or abort based on the severity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional

from src.core.system_state import ProcessParams, SensorData


class Severity(Enum):
    OK = auto()
    WARNING = auto()
    ERROR = auto()


@dataclass
class ValidationIssue:
    severity: Severity
    field: str
    message: str


@dataclass
class ValidationResult:
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        return not any(i.severity == Severity.ERROR for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == Severity.WARNING for i in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    def add(self, severity: Severity, field: str, message: str) -> None:
        self.issues.append(ValidationIssue(severity=severity, field=field, message=message))

    def __str__(self) -> str:
        lines = [f"[{i.severity.name}] {i.field}: {i.message}" for i in self.issues]
        return "\n".join(lines) if lines else "OK"


class ProcessValidator:
    """
    Validates ProcessParams and runtime SensorData against configurable
    bounds.

    Parameters
    ----------
    tension_min : float    Minimum allowable tension (N)
    tension_max : float    Maximum allowable tension (N)
    spindle_max : float    Maximum spindle speed (rpm)
    winding_speed_max : float  Maximum winding speed (mm/s)
    """

    def __init__(
        self,
        tension_min: float = 0.1,
        tension_max: float = 5.0,
        spindle_max: float = 3000.0,
        winding_speed_max: float = 200.0,
    ) -> None:
        self.tension_min = tension_min
        self.tension_max = tension_max
        self.spindle_max = spindle_max
        self.winding_speed_max = winding_speed_max

    # ------------------------------------------------------------------
    # Parameter validation (pre-start checks)
    # ------------------------------------------------------------------

    def validate_params(self, params: ProcessParams) -> ValidationResult:
        """
        Validate process configuration parameters before starting a winding
        session.
        """
        result = ValidationResult()

        if params.target_tension <= 0:
            result.add(Severity.ERROR, "target_tension",
                       "Target tension must be > 0 N")
        elif params.target_tension < self.tension_min:
            result.add(Severity.WARNING, "target_tension",
                       f"Target tension {params.target_tension:.3f} N is below "
                       f"minimum {self.tension_min} N")
        elif params.target_tension > self.tension_max:
            result.add(Severity.ERROR, "target_tension",
                       f"Target tension {params.target_tension:.3f} N exceeds "
                       f"maximum {self.tension_max} N")

        if params.spindle_target_speed <= 0:
            result.add(Severity.ERROR, "spindle_target_speed",
                       "Spindle speed must be > 0 rpm")
        elif params.spindle_target_speed > self.spindle_max:
            result.add(Severity.ERROR, "spindle_target_speed",
                       f"Spindle speed {params.spindle_target_speed:.0f} rpm "
                       f"exceeds maximum {self.spindle_max:.0f} rpm")

        if params.winding_target_speed < 0:
            result.add(Severity.ERROR, "winding_target_speed",
                       "Winding speed must be >= 0")
        elif params.winding_target_speed > self.winding_speed_max:
            result.add(Severity.ERROR, "winding_target_speed",
                       f"Winding speed {params.winding_target_speed:.1f} mm/s "
                       f"exceeds maximum {self.winding_speed_max:.1f} mm/s")

        if params.total_layers <= 0:
            result.add(Severity.ERROR, "total_layers",
                       "Total layers must be > 0")

        if params.turns_per_layer <= 0:
            result.add(Severity.ERROR, "turns_per_layer",
                       "Turns per layer must be > 0")

        if params.fiber_diameter <= 0:
            result.add(Severity.ERROR, "fiber_diameter",
                       "Fiber diameter must be > 0 mm")
        elif params.fiber_diameter > 1.0:
            result.add(Severity.WARNING, "fiber_diameter",
                       f"Fiber diameter {params.fiber_diameter:.3f} mm seems unusually large")

        if params.overlap_width < 0:
            result.add(Severity.ERROR, "overlap_width",
                       "Overlap width must be >= 0 mm")

        return result

    # ------------------------------------------------------------------
    # Runtime sensor validation
    # ------------------------------------------------------------------

    def validate_sensor(
        self,
        sensor: SensorData,
        params: Optional[ProcessParams] = None,
    ) -> ValidationResult:
        """
        Validate live sensor data against expected operating ranges.
        """
        result = ValidationResult()

        if sensor.tension_value < 0:
            result.add(Severity.ERROR, "tension_value",
                       "Tension sensor reading is negative — sensor fault?")
        elif sensor.tension_value > self.tension_max * 1.5:
            result.add(Severity.ERROR, "tension_value",
                       f"Tension {sensor.tension_value:.3f} N far exceeds maximum — "
                       "possible fiber break or sensor fault")
        elif params is not None and params.target_tension > 0:
            deviation = abs(sensor.tension_value - params.target_tension)
            threshold = params.target_tension * 0.2
            if deviation > threshold:
                result.add(Severity.WARNING, "tension_value",
                           f"Tension deviation {deviation:.3f} N exceeds 20 % of target")

        if sensor.spindle_speed < 0:
            result.add(Severity.ERROR, "spindle_speed",
                       "Spindle speed is negative")
        elif sensor.spindle_speed > self.spindle_max:
            result.add(Severity.ERROR, "spindle_speed",
                       f"Spindle speed {sensor.spindle_speed:.0f} rpm exceeds maximum")

        if sensor.fpga_status_code != 0:
            result.add(Severity.WARNING, "fpga_status_code",
                       f"FPGA reported non-zero status: 0x{sensor.fpga_status_code:04X}")

        if sensor.stm32_status_code != 0:
            result.add(Severity.WARNING, "stm32_status_code",
                       f"STM32 reported non-zero status: 0x{sensor.stm32_status_code:04X}")

        return result

    # ------------------------------------------------------------------
    # Process step (工序) validation
    # ------------------------------------------------------------------

    def validate_process_step(
        self,
        step_name: str,
        current_layer: int,
        expected_layer: int,
        layers_completed: int,
        total_layers: int,
    ) -> ValidationResult:
        """
        Validate that a process step transition is legal.
        """
        result = ValidationResult()

        if current_layer != expected_layer:
            result.add(Severity.ERROR, "current_layer",
                       f"Step '{step_name}' expected layer {expected_layer}, "
                       f"but current layer is {current_layer}")

        if layers_completed > total_layers:
            result.add(Severity.ERROR, "layers_completed",
                       f"Layers completed ({layers_completed}) exceeds "
                       f"total layers ({total_layers})")
        elif layers_completed == total_layers:
            result.add(Severity.WARNING, "layers_completed",
                       "All layers completed — winding should be finishing")

        return result
