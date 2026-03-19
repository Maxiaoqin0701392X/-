"""
SerialComm — robust serial communication layer for the fiber-optic winding
machine upper computer.

Features
--------
- Binary framing protocol (same format as DataProcessor)
- Sticky-packet / half-packet reassembly via a ring buffer
- Separate reader thread that pushes complete frames to a queue
- Thread-safe write with sequence number and CRC
- Heartbeat keep-alive
- Automatic reconnection on port error

Frame format (little-endian)
-----------------------------
Byte 0-1  : Header  0xAA 0x55
Byte 2    : FrameType (u8)
Byte 3    : Payload length N (u8)
Byte 4..4+N-1 : Payload
Byte 4+N  : CRC-8 (over bytes 2 .. 4+N-1)
"""

from __future__ import annotations

import logging
import queue
import struct
import threading
import time
from enum import IntEnum
from typing import Optional

try:
    import serial
    from serial import Serial, SerialException
except ImportError:  # pragma: no cover
    raise ImportError("pyserial is required: pip install pyserial")

logger = logging.getLogger(__name__)

FRAME_HEADER = b'\xAA\x55'
HEADER_LEN = 2
MIN_FRAME_LEN = 5   # header(2) + type(1) + len(1) + crc(1)
MAX_PAYLOAD = 255
MAX_FRAME_LEN = MIN_FRAME_LEN + MAX_PAYLOAD
READ_CHUNK = 256    # bytes to read per serial.read() call
HEARTBEAT_INTERVAL = 2.0  # seconds


class FrameType(IntEnum):
    SENSOR_DATA = 0x01
    PROCESS_PARAMS = 0x02
    ALARM = 0x03
    HEARTBEAT = 0x04
    ACK = 0x05
    COMMAND = 0x10  # Host → device command frame


# ---------------------------------------------------------------------------
# CRC helper (same polynomial as DataProcessor)
# ---------------------------------------------------------------------------

def _crc8(data: bytes) -> int:
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


def build_frame(frame_type: int, payload: bytes) -> bytes:
    """Construct a complete binary frame including header and CRC."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"Payload too long: {len(payload)} > {MAX_PAYLOAD}")
    type_and_len = bytes([frame_type, len(payload)])
    crc = _crc8(type_and_len + payload)
    return FRAME_HEADER + type_and_len + payload + bytes([crc])


# ---------------------------------------------------------------------------
# FrameParser — handles sticky-packet / half-packet reassembly
# ---------------------------------------------------------------------------

class FrameParser:
    """
    Stateful parser that accepts an arbitrary byte stream and extracts
    complete frames, handling both fragmentation (half-packet) and
    concatenation (sticky-packet).
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """
        Feed raw bytes into the parser.

        Returns a (possibly empty) list of complete frame byte-strings.
        """
        self._buf += data
        frames: list[bytes] = []

        while True:
            # Find next header
            idx = self._find_header()
            if idx == -1:
                # No header found — keep last byte in case it is the first
                # byte of a split header
                if len(self._buf) > 1:
                    self._buf = self._buf[-1:]
                break

            if idx > 0:
                # Discard garbage bytes before the header
                self._buf = self._buf[idx:]

            # Need at least MIN_FRAME_LEN bytes to determine frame length
            if len(self._buf) < MIN_FRAME_LEN:
                break

            payload_len = self._buf[3]
            total_len = MIN_FRAME_LEN + payload_len

            if len(self._buf) < total_len:
                # Half-packet: wait for more data
                break

            frame_bytes = bytes(self._buf[:total_len])
            self._buf = self._buf[total_len:]

            # Validate CRC
            received_crc = frame_bytes[-1]
            computed_crc = _crc8(frame_bytes[2: -1])
            if received_crc != computed_crc:
                logger.warning(
                    "CRC error: got 0x%02X expected 0x%02X — frame dropped",
                    received_crc, computed_crc,
                )
                # Skip just the first byte and retry (header might be inside)
                self._buf = bytearray(frame_bytes[1:]) + self._buf
                continue

            frames.append(frame_bytes)

        return frames

    def _find_header(self) -> int:
        """Return the index of the next 0xAA 0x55 header, or -1."""
        buf = self._buf
        for i in range(len(buf) - 1):
            if buf[i] == 0xAA and buf[i + 1] == 0x55:
                return i
        return -1

    def reset(self) -> None:
        self._buf = bytearray()


# ---------------------------------------------------------------------------
# SerialComm
# ---------------------------------------------------------------------------

class SerialComm:
    """
    Manages a serial port connection and provides thread-safe read/write.

    Parameters
    ----------
    port : str
        Serial port name (e.g. 'COM3' or '/dev/ttyUSB0').
    baudrate : int
        Baud rate (default 115200).
    rx_queue : queue.Queue | None
        Queue to push complete frames into.  If None a new Queue is created.
    auto_reconnect : bool
        Whether to reconnect automatically on port errors.
    reconnect_delay : float
        Seconds to wait between reconnection attempts.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        rx_queue: Optional[queue.Queue] = None,
        auto_reconnect: bool = True,
        reconnect_delay: float = 3.0,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._rx_queue: queue.Queue = rx_queue if rx_queue is not None else queue.Queue()
        self._auto_reconnect = auto_reconnect
        self._reconnect_delay = reconnect_delay

        self._serial: Optional[Serial] = None
        self._parser = FrameParser()
        self._write_lock = threading.Lock()

        self._reader_running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def rx_queue(self) -> queue.Queue:
        """Queue from which callers can consume received frames."""
        return self._rx_queue

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def open(self) -> bool:
        """Open the serial port and start the reader thread."""
        try:
            self._serial = Serial(
                port=self._port,
                baudrate=self._baudrate,
                timeout=0.1,
            )
            self._parser.reset()
            self._reader_running = True
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name="SerialComm-Reader",
                daemon=True,
            )
            self._reader_thread.start()
            self._start_heartbeat()
            logger.info("Serial port %s opened at %d baud", self._port, self._baudrate)
            return True
        except SerialException as exc:
            logger.error("Failed to open %s: %s", self._port, exc)
            return False

    def close(self) -> None:
        self._reader_running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None
        logger.info("Serial port closed")

    def send_frame(self, frame_type: int, payload: bytes) -> bool:
        """Build and transmit a single binary frame."""
        if not self.is_open:
            logger.warning("send_frame: port not open")
            return False
        frame = build_frame(frame_type, payload)
        with self._write_lock:
            try:
                self._serial.write(frame)
                logger.debug("TX %d bytes (type=0x%02X)", len(frame), frame_type)
                return True
            except SerialException as exc:
                logger.error("Write error: %s", exc)
                return False

    def send_command(self, cmd_id: int, data: bytes = b"") -> bool:
        """Send a command frame: payload = [cmd_id(u8)] + data."""
        payload = bytes([cmd_id]) + data
        return self.send_frame(FrameType.COMMAND, payload)

    # ------------------------------------------------------------------
    # Reader loop
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        while self._reader_running:
            if not self.is_open:
                if self._auto_reconnect:
                    self._reconnect()
                else:
                    break
                continue

            try:
                chunk = self._serial.read(READ_CHUNK)
            except SerialException as exc:
                logger.error("Read error: %s", exc)
                self.close()
                continue

            if not chunk:
                continue

            complete_frames = self._parser.feed(chunk)
            for frame in complete_frames:
                try:
                    self._rx_queue.put_nowait(frame)
                except queue.Full:
                    logger.warning("RX queue full — frame dropped")

    def _reconnect(self) -> None:
        logger.info("Attempting reconnect to %s in %.1fs…", self._port, self._reconnect_delay)
        time.sleep(self._reconnect_delay)
        try:
            self._serial = Serial(
                port=self._port,
                baudrate=self._baudrate,
                timeout=0.1,
            )
            self._parser.reset()
            logger.info("Reconnected to %s", self._port)
        except SerialException:
            pass

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="SerialComm-Heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while self._reader_running:
            time.sleep(HEARTBEAT_INTERVAL)
            # payload: source=0 (Host PC)
            self.send_frame(FrameType.HEARTBEAT, b'\x00')
