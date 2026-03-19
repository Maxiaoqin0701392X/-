"""
DataProcessor — reads raw frames from a queue, parses them and pushes updates
into SystemState.

The processor runs in its own daemon thread so the UI and communication
threads are never blocked by parsing logic.

Frame format (binary, little-endian)
--------------------------------------
Byte 0-1 : Header magic  0xAA 0x55
Byte 2    : Frame type    (see FrameType enum)
Byte 3    : Payload length N (bytes)
Byte 4..4+N-1 : Payload
Byte 4+N  : CRC-8 checksum (over bytes 2 .. 4+N-1)
"""

from __future__ import annotations

import logging
import queue
import struct
import threading
from enum import IntEnum
from typing import Optional

from src.core.system_state import AlarmLevel, SystemState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Frame type registry
# ---------------------------------------------------------------------------

class FrameType(IntEnum):
    SENSOR_DATA = 0x01      # 传感器数据帧
    PROCESS_PARAMS = 0x02   # 工艺参数帧
    ALARM = 0x03            # 报警帧
    HEARTBEAT = 0x04        # 心跳帧
    ACK = 0x05              # 确认帧


FRAME_HEADER = b'\xAA\x55'
HEADER_LEN = 2
TYPE_LEN = 1
PAYLEN_LEN = 1
CRC_LEN = 1
MIN_FRAME_LEN = HEADER_LEN + TYPE_LEN + PAYLEN_LEN + CRC_LEN  # 5 bytes


# ---------------------------------------------------------------------------
# CRC helper
# ---------------------------------------------------------------------------

def _crc8(data: bytes) -> int:
    """Simple CRC-8 (polynomial 0x07) checksum."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x07
            else:
                crc <<= 1
            crc &= 0xFF
    return crc


# ---------------------------------------------------------------------------
# DataProcessor
# ---------------------------------------------------------------------------

class DataProcessor:
    """
    Reads raw byte frames from *data_queue*, parses them, and updates
    SystemState accordingly.

    Parameters
    ----------
    data_queue : queue.Queue[bytes]
        Producer side is normally the SerialComm layer after it has
        reassembled complete frames from the stream.
    state : SystemState | None
        Defaults to the global singleton when None.
    poll_timeout : float
        Seconds to block-wait on queue.get before looping again.
    """

    def __init__(
        self,
        data_queue: Optional[queue.Queue] = None,
        state: Optional[SystemState] = None,
        poll_timeout: float = 0.1,
    ) -> None:
        self._queue: queue.Queue = data_queue if data_queue is not None else queue.Queue()
        self._state: SystemState = state if state is not None else SystemState.get_instance()
        self._poll_timeout = poll_timeout
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def queue(self) -> queue.Queue:
        """The queue that producers should push raw frame bytes into."""
        return self._queue

    def start(self) -> None:
        """Start the background processing thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="DataProcessor",
            daemon=True,
        )
        self._thread.start()
        logger.info("DataProcessor started")

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the processing thread to stop and wait for it to finish."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("DataProcessor stopped")

    def enqueue(self, frame: bytes) -> None:
        """Convenience method — put a raw frame on the queue."""
        self._queue.put(frame)

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while self._running:
            try:
                frame = self._queue.get(timeout=self._poll_timeout)
            except queue.Empty:
                continue

            try:
                self._process_frame(frame)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Frame processing error: %s", exc)
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------
    # Frame parsing
    # ------------------------------------------------------------------

    def _process_frame(self, frame: bytes) -> None:
        if len(frame) < MIN_FRAME_LEN:
            logger.debug("Frame too short (%d bytes) — discarded", len(frame))
            return

        if frame[:2] != FRAME_HEADER:
            logger.debug("Bad header — discarded")
            return

        frame_type = frame[2]
        payload_len = frame[3]
        expected_len = MIN_FRAME_LEN + payload_len  # 5 + payload_len

        if len(frame) < expected_len:
            logger.debug("Frame truncated — discarded")
            return

        payload = frame[4: 4 + payload_len]
        received_crc = frame[4 + payload_len]
        computed_crc = _crc8(frame[2: 4 + payload_len])

        if received_crc != computed_crc:
            logger.warning(
                "CRC mismatch (got 0x%02X, expected 0x%02X) — discarded",
                received_crc,
                computed_crc,
            )
            self._state.update_comm(error_count=self._state.comm.error_count + 1)
            return

        dispatcher = {
            FrameType.SENSOR_DATA: self._parse_sensor_data,
            FrameType.PROCESS_PARAMS: self._parse_process_params,
            FrameType.ALARM: self._parse_alarm,
            FrameType.HEARTBEAT: self._parse_heartbeat,
        }
        handler = dispatcher.get(frame_type)
        if handler:
            handler(payload)
        else:
            logger.debug("Unknown frame type 0x%02X — ignored", frame_type)

    # ------------------------------------------------------------------
    # Frame-type handlers
    # ------------------------------------------------------------------

    def _parse_sensor_data(self, payload: bytes) -> None:
        """
        Sensor frame payload (26 bytes, little-endian):
          f32 tension_value
          f32 spindle_speed
          f32 winding_position
          u16 layers_completed
          u16 turns_completed
          u16 fpga_status_code
          u16 stm32_status_code
        """
        fmt = "<fffHHHH"
        expected = struct.calcsize(fmt)
        if len(payload) < expected:
            logger.warning("Sensor payload too short (%d < %d)", len(payload), expected)
            return
        (
            tension,
            spindle,
            position,
            layers,
            turns,
            fpga_code,
            stm32_code,
        ) = struct.unpack_from(fmt, payload)
        self._state.update_sensor(
            tension_value=tension,
            spindle_speed=spindle,
            winding_position=position,
            layers_completed=layers,
            turns_completed=turns,
            fpga_status_code=fpga_code,
            stm32_status_code=stm32_code,
        )

    def _parse_process_params(self, payload: bytes) -> None:
        """
        Process params frame payload (28 bytes, little-endian):
          f32 target_tension
          f32 spindle_target_speed
          f32 winding_target_speed
          u16 total_layers
          u16 turns_per_layer
          f32 overlap_width
          f32 overlap_position
          f32 fiber_diameter
        """
        fmt = "<fffHHfff"
        expected = struct.calcsize(fmt)
        if len(payload) < expected:
            logger.warning("Params payload too short (%d < %d)", len(payload), expected)
            return
        (
            target_tension,
            spindle_speed,
            winding_speed,
            total_layers,
            turns_per_layer,
            overlap_width,
            overlap_pos,
            fiber_diam,
        ) = struct.unpack_from(fmt, payload)
        self._state.update_params(
            target_tension=target_tension,
            spindle_target_speed=spindle_speed,
            winding_target_speed=winding_speed,
            total_layers=total_layers,
            turns_per_layer=turns_per_layer,
            overlap_width=overlap_width,
            overlap_position=overlap_pos,
            fiber_diameter=fiber_diam,
        )

    def _parse_alarm(self, payload: bytes) -> None:
        """
        Alarm frame payload:
          u8  level  (0=INFO, 1=WARNING, 2=ERROR, 3=CRITICAL)
          u16 code
          remaining bytes: UTF-8 message string
        """
        if len(payload) < 3:
            return
        level_byte, code = struct.unpack_from("<BH", payload)
        message = payload[3:].decode("utf-8", errors="replace")
        level_map = {
            0: AlarmLevel.INFO,
            1: AlarmLevel.WARNING,
            2: AlarmLevel.ERROR,
            3: AlarmLevel.CRITICAL,
        }
        level = level_map.get(level_byte, AlarmLevel.WARNING)
        self._state.add_alarm(level=level, code=code, message=message)

    def _parse_heartbeat(self, payload: bytes) -> None:
        """
        Heartbeat frame payload (1 byte):
          u8  source  (0=FPGA, 1=STM32, 2=Raspberry)
        """
        import time as _time
        if len(payload) < 1:
            return
        source = payload[0]
        now = _time.time()
        if source == 0:
            self._state.update_comm(fpga_connected=True, last_fpga_heartbeat=now)
        elif source == 1:
            self._state.update_comm(stm32_connected=True, last_stm32_heartbeat=now)
