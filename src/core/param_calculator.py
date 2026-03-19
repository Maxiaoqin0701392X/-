"""
ParamCalculator — derives winding process parameters from raw inputs and
provides helper calculations for the fiber-optic winding machine.

Key calculations
----------------
- 排线速度 (winding speed) from spindle speed, fiber diameter and pitch
- S-curve velocity profile (acceleration / deceleration ramps)
- 张力补偿系数 (tension compensation coefficient)
- 层间位移 (inter-layer displacement)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class SCurveProfile:
    """Result of an S-curve motion profile calculation."""
    t_accel: float          # acceleration phase duration (s)
    t_const: float          # constant-speed phase duration (s)
    t_decel: float          # deceleration phase duration (s)
    total_time: float       # total move time (s)
    peak_speed: float       # peak velocity (units/s)
    # Sampled (time, velocity) pairs for plotting
    profile_points: List[Tuple[float, float]]


class ParamCalculator:
    """
    Stateless helper class — all methods are static/class-methods so that
    they can be used without instantiation.
    """

    # ------------------------------------------------------------------
    # Winding speed / pitch
    # ------------------------------------------------------------------

    @staticmethod
    def calc_winding_speed(
        spindle_rpm: float,
        fiber_diameter_mm: float,
        pitch_mm: float,
    ) -> float:
        """
        Calculate the required winding (排线) linear speed.

        Parameters
        ----------
        spindle_rpm : float
            Main spindle rotation speed (rev/min).
        fiber_diameter_mm : float
            Outer diameter of the optical fiber (mm).
        pitch_mm : float
            Axial advance per revolution (mm/rev).  Equal to fiber_diameter
            for a single-layer close-wind; larger for helical winding.

        Returns
        -------
        float
            Linear speed of the traversal stage (mm/s).
        """
        if spindle_rpm <= 0:
            return 0.0
        rps = spindle_rpm / 60.0
        speed_mm_per_s = rps * pitch_mm
        return speed_mm_per_s

    @staticmethod
    def calc_turns_per_layer(
        winding_length_mm: float,
        fiber_diameter_mm: float,
        overlap_ratio: float = 0.0,
    ) -> int:
        """
        Estimate the number of turns per winding layer.

        Parameters
        ----------
        winding_length_mm : float
            Axial length of the winding bobbin (mm).
        fiber_diameter_mm : float
            Fiber outer diameter (mm).
        overlap_ratio : float
            Fractional overlap (0 = no overlap, 0.1 = 10 % overlap).

        Returns
        -------
        int
            Estimated turns per layer (rounded down).
        """
        if fiber_diameter_mm <= 0:
            return 0
        effective_pitch = fiber_diameter_mm * (1.0 - overlap_ratio)
        if effective_pitch <= 0:
            return 0
        return max(0, int(winding_length_mm / effective_pitch))

    @staticmethod
    def calc_tension_compensation(
        base_tension: float,
        layer_index: int,
        bobbin_radius_mm: float,
        fiber_diameter_mm: float,
    ) -> float:
        """
        Adjust target tension for each successive layer because the effective
        radius increases as layers accumulate.

        Uses the inverse-radius model:  T_n = T_0 * R_0 / R_n

        Parameters
        ----------
        base_tension : float
            Target tension for the first layer (N).
        layer_index : int
            0-based layer index.
        bobbin_radius_mm : float
            Bare bobbin radius (mm).
        fiber_diameter_mm : float
            Fiber outer diameter (mm).

        Returns
        -------
        float
            Adjusted tension (N).
        """
        if bobbin_radius_mm <= 0:
            return base_tension
        current_radius = bobbin_radius_mm + layer_index * fiber_diameter_mm
        if current_radius <= 0:
            return base_tension
        return base_tension * (bobbin_radius_mm / current_radius)

    # ------------------------------------------------------------------
    # S-curve velocity profile
    # ------------------------------------------------------------------

    @staticmethod
    def calc_s_curve_profile(
        distance: float,
        max_speed: float,
        accel: float,
        sample_count: int = 100,
    ) -> SCurveProfile:
        """
        Generate a trapezoidal (simplified S-curve) motion profile.

        The profile has three phases:
          1. Linear acceleration from 0 to max_speed
          2. Constant speed at max_speed (may be zero if distance is short)
          3. Linear deceleration from max_speed to 0

        Parameters
        ----------
        distance : float
            Total travel distance (mm).
        max_speed : float
            Target peak speed (mm/s).
        accel : float
            Acceleration / deceleration magnitude (mm/s²).
        sample_count : int
            Number of (time, velocity) samples to include.

        Returns
        -------
        SCurveProfile
        """
        if max_speed <= 0 or accel <= 0 or distance <= 0:
            return SCurveProfile(0, 0, 0, 0, 0, [])

        t_ramp = max_speed / accel
        d_ramp = 0.5 * accel * t_ramp ** 2

        if 2 * d_ramp > distance:
            # Not enough room to reach max_speed — triangular profile
            d_ramp = distance / 2.0
            t_ramp = math.sqrt(2 * d_ramp / accel)
            peak_speed = accel * t_ramp
            t_accel = t_ramp
            t_const = 0.0
            t_decel = t_ramp
        else:
            peak_speed = max_speed
            t_accel = t_ramp
            d_const = distance - 2 * d_ramp
            t_const = d_const / max_speed
            t_decel = t_ramp

        total_time = t_accel + t_const + t_decel

        # Sample the profile
        points: List[Tuple[float, float]] = []
        for i in range(sample_count + 1):
            t = total_time * i / sample_count
            if t <= t_accel:
                v = accel * t
            elif t <= t_accel + t_const:
                v = peak_speed
            else:
                dt = t - t_accel - t_const
                v = max(0.0, peak_speed - accel * dt)
            points.append((t, v))

        return SCurveProfile(
            t_accel=t_accel,
            t_const=t_const,
            t_decel=t_decel,
            total_time=total_time,
            peak_speed=peak_speed,
            profile_points=points,
        )

    # ------------------------------------------------------------------
    # PID tuning helpers
    # ------------------------------------------------------------------

    @staticmethod
    def calc_pid_output(
        error: float,
        integral: float,
        derivative: float,
        kp: float,
        ki: float,
        kd: float,
        output_min: float = -100.0,
        output_max: float = 100.0,
    ) -> float:
        """
        Compute PID output clamped to [output_min, output_max].

        Parameters
        ----------
        error : float      Current error (setpoint - measurement).
        integral : float   Accumulated integral of error * dt.
        derivative : float d(error)/dt.
        kp, ki, kd : float PID gains.
        """
        output = kp * error + ki * integral + kd * derivative
        return max(output_min, min(output_max, output))

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def calc_bobbin_fill_factor(
        total_turns: int,
        winding_length_mm: float,
        bobbin_radius_mm: float,
        fiber_diameter_mm: float,
    ) -> float:
        """
        Calculate the winding fill factor (0.0–1.0).

        fill = (total fiber cross-section area) / (winding window area)
        """
        if winding_length_mm <= 0 or fiber_diameter_mm <= 0:
            return 0.0
        fiber_area = math.pi * (fiber_diameter_mm / 2) ** 2
        total_fiber_area = total_turns * fiber_area
        winding_window = winding_length_mm * bobbin_radius_mm * 2 * math.pi
        return min(1.0, total_fiber_area / winding_window)
