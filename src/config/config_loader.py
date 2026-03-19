"""
config_loader.py — Load and validate the YAML configuration file.

Usage
-----
    from src.config.config_loader import load_config, AppConfig

    cfg = load_config()                        # uses default path
    cfg = load_config("path/to/config.yaml")   # custom path

    print(cfg.serial.port)
    print(cfg.process.target_tension)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

# Default config file location (same directory as this module)
DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"


# ---------------------------------------------------------------------------
# Config section dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SerialConfig:
    port: str = "/dev/ttyUSB0"
    baudrate: int = 115200
    timeout: float = 0.1
    auto_reconnect: bool = True
    reconnect_delay: float = 3.0


@dataclass
class ProcessConfig:
    target_tension: float = 1.5
    spindle_target_speed: float = 500.0
    winding_target_speed: float = 10.0
    total_layers: int = 10
    turns_per_layer: int = 200
    overlap_width: float = 0.05
    overlap_position: float = 0.0
    fiber_diameter: float = 0.125
    initial_tension: float = 1.0


@dataclass
class ValidatorConfig:
    tension_min: float = 0.1
    tension_max: float = 5.0
    spindle_max: float = 3000.0
    winding_speed_max: float = 200.0


@dataclass
class PidChannelConfig:
    kp: float = 1.0
    ki: float = 0.1
    kd: float = 0.05


@dataclass
class PidConfig:
    tension: PidChannelConfig = field(default_factory=PidChannelConfig)
    spindle: PidChannelConfig = field(default_factory=PidChannelConfig)
    winding: PidChannelConfig = field(default_factory=PidChannelConfig)


@dataclass
class StorageConfig:
    db_path: str = "fiber_winding.db"
    max_sensor_records: int = 100_000


@dataclass
class UiConfig:
    window_title: str = "光纤绕线上位机"
    window_width: int = 1400
    window_height: int = 900
    refresh_rate_hz: int = 20
    tension_plot_history: int = 500
    dark_theme: bool = True


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "fiber_winding.log"
    max_bytes: int = 10_485_760
    backup_count: int = 3


@dataclass
class AppConfig:
    serial: SerialConfig = field(default_factory=SerialConfig)
    process: ProcessConfig = field(default_factory=ProcessConfig)
    validator: ValidatorConfig = field(default_factory=ValidatorConfig)
    pid: PidConfig = field(default_factory=PidConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _get(d: Dict, *keys, default=None):
    """Safe nested dict access."""
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key, default)
    return d


def load_config(path: Optional[str | Path] = None) -> AppConfig:
    """
    Load and parse the YAML configuration file.

    Parameters
    ----------
    path : str | Path | None
        Path to the config.yaml file.  When None the bundled default
        ``src/config/config.yaml`` is used.

    Returns
    -------
    AppConfig
        Populated configuration object.  Missing keys fall back to
        dataclass defaults so partial YAML files are safe.

    Raises
    ------
    FileNotFoundError
        If the given *path* does not exist.
    yaml.YAMLError
        If the file cannot be parsed.
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    logger.info("Loaded config from %s", config_path)

    # --- serial ---
    s = raw.get("serial", {})
    serial = SerialConfig(
        port=s.get("port", "/dev/ttyUSB0"),
        baudrate=s.get("baudrate", 115200),
        timeout=s.get("timeout", 0.1),
        auto_reconnect=s.get("auto_reconnect", True),
        reconnect_delay=s.get("reconnect_delay", 3.0),
    )

    # --- process ---
    p = raw.get("process", {})
    process = ProcessConfig(
        target_tension=p.get("target_tension", 1.5),
        spindle_target_speed=p.get("spindle_target_speed", 500.0),
        winding_target_speed=p.get("winding_target_speed", 10.0),
        total_layers=p.get("total_layers", 10),
        turns_per_layer=p.get("turns_per_layer", 200),
        overlap_width=p.get("overlap_width", 0.05),
        overlap_position=p.get("overlap_position", 0.0),
        fiber_diameter=p.get("fiber_diameter", 0.125),
        initial_tension=p.get("initial_tension", 1.0),
    )

    # --- validator ---
    v = raw.get("validator", {})
    validator = ValidatorConfig(
        tension_min=v.get("tension_min", 0.1),
        tension_max=v.get("tension_max", 5.0),
        spindle_max=v.get("spindle_max", 3000.0),
        winding_speed_max=v.get("winding_speed_max", 200.0),
    )

    # --- pid ---
    pid_raw = raw.get("pid", {})

    def _pid_ch(d: dict) -> PidChannelConfig:
        return PidChannelConfig(
            kp=d.get("kp", 1.0),
            ki=d.get("ki", 0.1),
            kd=d.get("kd", 0.05),
        )

    pid = PidConfig(
        tension=_pid_ch(pid_raw.get("tension", {})),
        spindle=_pid_ch(pid_raw.get("spindle", {})),
        winding=_pid_ch(pid_raw.get("winding", {})),
    )

    # --- storage ---
    st = raw.get("storage", {})
    storage = StorageConfig(
        db_path=st.get("db_path", "fiber_winding.db"),
        max_sensor_records=st.get("max_sensor_records", 100_000),
    )

    # --- ui ---
    u = raw.get("ui", {})
    ui = UiConfig(
        window_title=u.get("window_title", "光纤绕线上位机"),
        window_width=u.get("window_width", 1400),
        window_height=u.get("window_height", 900),
        refresh_rate_hz=u.get("refresh_rate_hz", 20),
        tension_plot_history=u.get("tension_plot_history", 500),
        dark_theme=u.get("dark_theme", True),
    )

    # --- logging ---
    lg = raw.get("logging", {})
    logging_cfg = LoggingConfig(
        level=lg.get("level", "INFO"),
        file=lg.get("file", "fiber_winding.log"),
        max_bytes=lg.get("max_bytes", 10_485_760),
        backup_count=lg.get("backup_count", 3),
    )

    return AppConfig(
        serial=serial,
        process=process,
        validator=validator,
        pid=pid,
        storage=storage,
        ui=ui,
        logging=logging_cfg,
    )


def setup_logging(cfg: LoggingConfig) -> None:
    """Configure the root logger from a LoggingConfig."""
    import logging.handlers
    import os

    level = getattr(logging, cfg.level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    handlers = [logging.StreamHandler()]

    log_dir = Path(cfg.file).parent
    if str(log_dir) != "." and not log_dir.exists():
        log_dir.mkdir(parents=True, exist_ok=True)

    handlers.append(
        logging.handlers.RotatingFileHandler(
            cfg.file,
            maxBytes=cfg.max_bytes,
            backupCount=cfg.backup_count,
            encoding="utf-8",
        )
    )

    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)
    logger.debug("Logging configured: level=%s, file=%s", cfg.level, cfg.file)
