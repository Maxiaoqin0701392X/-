"""
main.py — entry point for the fiber-optic winding machine upper computer.

Usage
-----
    python main.py                       # use default config
    python main.py --config my.yaml      # custom config file
    python main.py --demo                # inject demo sensor data (no serial port required)
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
import threading

from src.config.config_loader import load_config, setup_logging
from src.core.system_state import SystemState


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="光纤绕线机上位机 — Fiber-optic winding machine upper computer"
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to config.yaml (defaults to src/config/config.yaml)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run in demo mode: simulate sensor data without a serial port",
    )
    return parser.parse_args()


def _demo_data_thread(state: SystemState, stop_event: threading.Event) -> None:
    """
    Simulate live sensor data so the UI can be demonstrated without hardware.
    Tension oscillates around a setpoint; spindle speed ramps up then stays steady.
    """
    t0 = time.time()
    while not stop_event.is_set():
        t = time.time() - t0
        tension = 1.5 + 0.2 * math.sin(2 * math.pi * 0.5 * t) + 0.05 * math.sin(2 * math.pi * 3 * t)
        spindle = min(500.0, 10.0 * t)
        layers = min(10, int(t / 10))
        turns = int((t % 10) * 20)
        state.update_sensor(
            tension_value=max(0.0, tension),
            spindle_speed=spindle,
            winding_position=(t * 0.5) % 50.0,
            layers_completed=layers,
            turns_completed=turns,
            fpga_status_code=0,
            stm32_status_code=0,
        )
        state.update_comm(
            fpga_connected=True,
            stm32_connected=True,
            raspberry_connected=True,
            serial_port="DEMO",
        )
        time.sleep(0.05)


def main() -> int:
    args = parse_args()

    # Load configuration
    try:
        cfg = load_config(args.config)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Configure logging
    setup_logging(cfg.logging)
    logger = logging.getLogger(__name__)
    logger.info("Starting fiber-optic winding upper computer")

    # Initialise shared state and apply config defaults
    state = SystemState.get_instance()
    state.update_params(
        target_tension=cfg.process.target_tension,
        spindle_target_speed=cfg.process.spindle_target_speed,
        winding_target_speed=cfg.process.winding_target_speed,
        total_layers=cfg.process.total_layers,
        turns_per_layer=cfg.process.turns_per_layer,
        overlap_width=cfg.process.overlap_width,
        overlap_position=cfg.process.overlap_position,
        fiber_diameter=cfg.process.fiber_diameter,
        initial_tension=cfg.process.initial_tension,
    )

    # Demo data thread (optional)
    stop_event = threading.Event()
    if args.demo:
        logger.info("Demo mode enabled — simulating sensor data")
        demo_thread = threading.Thread(
            target=_demo_data_thread,
            args=(state, stop_event),
            daemon=True,
        )
        demo_thread.start()

    # Launch UI
    from PySide6.QtWidgets import QApplication
    from src.core.flow_controller import FlowController
    from src.core.process_validator import ProcessValidator
    from src.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("光纤绕线上位机")

    validator = ProcessValidator(
        tension_min=cfg.validator.tension_min,
        tension_max=cfg.validator.tension_max,
        spindle_max=cfg.validator.spindle_max,
        winding_speed_max=cfg.validator.winding_speed_max,
    )
    flow = FlowController(state=state, validator=validator)

    window = MainWindow(config=cfg, state=state, flow_controller=flow)
    window.show()

    exit_code = app.exec()
    stop_event.set()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
