"""
DataStorage — SQLite-backed persistence layer for the fiber-optic winding
machine upper computer.

Tables
------
sensor_records   : time-series sensor snapshots
process_params   : historical process parameter configurations
alarm_log        : persisted alarm entries
winding_sessions : top-level session tracking
"""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "fiber_winding.db"


class DataStorage:
    """
    Wraps a SQLite database.  A single connection is kept open per instance
    and all methods are safe to call from a single thread.  For multi-thread
    usage construct one DataStorage per thread, pointing at the same file.

    Parameters
    ----------
    db_path : str | Path
        File path for the SQLite database.  Defaults to ``fiber_winding.db``
        in the current working directory.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._connect()
        self._create_tables()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        logger.info("Connected to SQLite database: %s", self._db_path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def _cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        cursor = self._conn.cursor()
        try:
            yield cursor
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()

    # ------------------------------------------------------------------
    # Schema creation
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        with self._cursor() as cur:
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS winding_sessions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time  REAL    NOT NULL,
                    end_time    REAL,
                    description TEXT,
                    status      TEXT    NOT NULL DEFAULT 'running'
                );

                CREATE TABLE IF NOT EXISTS sensor_records (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id       INTEGER REFERENCES winding_sessions(id),
                    timestamp        REAL    NOT NULL,
                    tension_value    REAL,
                    spindle_speed    REAL,
                    winding_position REAL,
                    layers_completed INTEGER,
                    turns_completed  INTEGER,
                    fpga_status_code INTEGER,
                    stm32_status_code INTEGER
                );

                CREATE TABLE IF NOT EXISTS process_params (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id           INTEGER REFERENCES winding_sessions(id),
                    timestamp            REAL    NOT NULL,
                    target_tension       REAL,
                    spindle_target_speed REAL,
                    winding_target_speed REAL,
                    total_layers         INTEGER,
                    turns_per_layer      INTEGER,
                    overlap_width        REAL,
                    overlap_position     REAL,
                    fiber_diameter       REAL
                );

                CREATE TABLE IF NOT EXISTS alarm_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER REFERENCES winding_sessions(id),
                    timestamp  REAL    NOT NULL,
                    level      TEXT    NOT NULL,
                    code       INTEGER NOT NULL,
                    message    TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_sensor_session
                    ON sensor_records(session_id, timestamp);

                CREATE INDEX IF NOT EXISTS idx_alarm_session
                    ON alarm_log(session_id, timestamp);
                """
            )
        logger.debug("Database tables created / verified")

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def start_session(self, description: str = "") -> int:
        """Create a new winding session record and return its ID."""
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO winding_sessions (start_time, description) VALUES (?, ?)",
                (time.time(), description),
            )
            session_id = cur.lastrowid
        logger.info("Started winding session %d", session_id)
        return session_id

    def end_session(self, session_id: int, status: str = "completed") -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE winding_sessions SET end_time=?, status=? WHERE id=?",
                (time.time(), status, session_id),
            )
        logger.info("Ended winding session %d with status '%s'", session_id, status)

    # ------------------------------------------------------------------
    # Sensor records
    # ------------------------------------------------------------------

    def insert_sensor_record(
        self,
        session_id: int,
        tension: float,
        spindle_speed: float,
        position: float,
        layers: int,
        turns: int,
        fpga_code: int = 0,
        stm32_code: int = 0,
        ts: Optional[float] = None,
    ) -> None:
        if ts is None:
            ts = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO sensor_records
                   (session_id, timestamp, tension_value, spindle_speed,
                    winding_position, layers_completed, turns_completed,
                    fpga_status_code, stm32_status_code)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (session_id, ts, tension, spindle_speed, position,
                 layers, turns, fpga_code, stm32_code),
            )

    def query_sensor_records(
        self,
        session_id: int,
        limit: int = 1000,
    ) -> List[sqlite3.Row]:
        with self._cursor() as cur:
            cur.execute(
                """SELECT * FROM sensor_records
                   WHERE session_id=?
                   ORDER BY timestamp DESC LIMIT ?""",
                (session_id, limit),
            )
            return cur.fetchall()

    def query_tension_series(
        self,
        session_id: int,
        limit: int = 500,
    ) -> List[Tuple[float, float]]:
        """Return [(timestamp, tension_value), ...] ordered ascending by time."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT timestamp, tension_value FROM sensor_records
                   WHERE session_id=?
                   ORDER BY timestamp ASC LIMIT ?""",
                (session_id, limit),
            )
            return [(row["timestamp"], row["tension_value"]) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Process params
    # ------------------------------------------------------------------

    def insert_process_params(
        self,
        session_id: int,
        params: Dict,
        ts: Optional[float] = None,
    ) -> None:
        if ts is None:
            ts = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO process_params
                   (session_id, timestamp, target_tension, spindle_target_speed,
                    winding_target_speed, total_layers, turns_per_layer,
                    overlap_width, overlap_position, fiber_diameter)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id, ts,
                    params.get("target_tension"),
                    params.get("spindle_target_speed"),
                    params.get("winding_target_speed"),
                    params.get("total_layers"),
                    params.get("turns_per_layer"),
                    params.get("overlap_width"),
                    params.get("overlap_position"),
                    params.get("fiber_diameter"),
                ),
            )

    # ------------------------------------------------------------------
    # Alarm log
    # ------------------------------------------------------------------

    def insert_alarm(
        self,
        session_id: int,
        level: str,
        code: int,
        message: str,
        ts: Optional[float] = None,
    ) -> None:
        if ts is None:
            ts = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO alarm_log (session_id, timestamp, level, code, message)
                   VALUES (?,?,?,?,?)""",
                (session_id, ts, level, code, message),
            )

    def query_alarms(
        self,
        session_id: int,
        limit: int = 100,
    ) -> List[sqlite3.Row]:
        with self._cursor() as cur:
            cur.execute(
                """SELECT * FROM alarm_log
                   WHERE session_id=?
                   ORDER BY timestamp DESC LIMIT ?""",
                (session_id, limit),
            )
            return cur.fetchall()
