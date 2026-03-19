"""
Unit tests for the four core modules:
  - SystemState
  - DataProcessor
  - DataStorage
  - ParamCalculator
  - ProcessValidator
  - FlowController
  - SerialComm (FrameParser + build_frame)
  - config_loader
"""

from __future__ import annotations

import math
import queue
import struct
import tempfile
import time
import threading
import unittest
from pathlib import Path


# ---------------------------------------------------------------------------
# SystemState tests
# ---------------------------------------------------------------------------

class TestSystemState(unittest.TestCase):

    def setUp(self):
        from src.core.system_state import SystemState
        SystemState.reset_instance()
        self.state = SystemState.get_instance()

    def test_singleton(self):
        from src.core.system_state import SystemState
        s1 = SystemState.get_instance()
        s2 = SystemState.get_instance()
        self.assertIs(s1, s2)

    def test_update_sensor(self):
        self.state.update_sensor(tension_value=1.23, spindle_speed=500.0)
        self.assertAlmostEqual(self.state.sensor.tension_value, 1.23)
        self.assertAlmostEqual(self.state.sensor.spindle_speed, 500.0)

    def test_update_sensor_bad_field(self):
        with self.assertRaises(AttributeError):
            self.state.update_sensor(nonexistent_field=0)

    def test_update_params(self):
        self.state.update_params(target_tension=2.0, total_layers=5)
        self.assertEqual(self.state.params.total_layers, 5)
        self.assertAlmostEqual(self.state.params.target_tension, 2.0)

    def test_add_alarm_and_clear(self):
        from src.core.system_state import AlarmLevel
        self.state.add_alarm(AlarmLevel.WARNING, 100, "test warning")
        self.assertEqual(len(self.state.alarms), 1)
        self.state.clear_alarms()
        self.assertEqual(len(self.state.alarms), 0)

    def test_has_active_alarms(self):
        from src.core.system_state import AlarmLevel
        self.assertFalse(self.state.has_active_alarms())
        self.state.add_alarm(AlarmLevel.INFO, 1, "info")
        self.assertFalse(self.state.has_active_alarms(AlarmLevel.WARNING))
        self.state.add_alarm(AlarmLevel.ERROR, 2, "err")
        self.assertTrue(self.state.has_active_alarms(AlarmLevel.WARNING))

    def test_alarm_history_pruning(self):
        from src.core.system_state import AlarmLevel
        self.state._max_alarm_history = 5
        for i in range(10):
            self.state.add_alarm(AlarmLevel.INFO, i, f"alarm {i}")
        self.assertEqual(len(self.state.alarms), 5)

    def test_thread_safety(self):
        errors = []

        def writer():
            for _ in range(100):
                try:
                    self.state.update_sensor(tension_value=1.0)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# DataProcessor tests
# ---------------------------------------------------------------------------

class TestDataProcessor(unittest.TestCase):

    def setUp(self):
        from src.core.system_state import SystemState
        SystemState.reset_instance()
        self.state = SystemState.get_instance()

    def _make_frame(self, frame_type, payload):
        from src.communication.serial_comm import build_frame, _crc8
        return build_frame(frame_type, payload)

    def test_parse_sensor_frame(self):
        from src.core.data_processor import DataProcessor, FrameType
        dp = DataProcessor(state=self.state)
        fmt = "<fffHHHH"
        payload = struct.pack(fmt, 1.5, 300.0, 12.5, 3, 60, 0, 0)
        frame = self._make_frame(FrameType.SENSOR_DATA, payload)
        dp._process_frame(frame)
        self.assertAlmostEqual(self.state.sensor.tension_value, 1.5, places=4)
        self.assertAlmostEqual(self.state.sensor.spindle_speed, 300.0, places=2)
        self.assertEqual(self.state.sensor.layers_completed, 3)

    def test_parse_alarm_frame(self):
        from src.core.data_processor import DataProcessor, FrameType
        from src.core.system_state import AlarmLevel
        dp = DataProcessor(state=self.state)
        payload = struct.pack("<BH", 2, 500) + b"Test alarm"
        frame = self._make_frame(FrameType.ALARM, payload)
        dp._process_frame(frame)
        alarms = self.state.alarms
        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0].level, AlarmLevel.ERROR)
        self.assertEqual(alarms[0].code, 500)
        self.assertIn("Test alarm", alarms[0].message)

    def test_bad_header_discarded(self):
        from src.core.data_processor import DataProcessor
        dp = DataProcessor(state=self.state)
        dp._process_frame(b'\x00\x00\x01\x00\x00')
        self.assertAlmostEqual(self.state.sensor.tension_value, 0.0)

    def test_crc_mismatch_increments_error_count(self):
        from src.core.data_processor import DataProcessor, FrameType
        dp = DataProcessor(state=self.state)
        fmt = "<fffHHHH"
        payload = struct.pack(fmt, 1.5, 300.0, 12.5, 3, 60, 0, 0)
        frame = bytearray(self._make_frame(FrameType.SENSOR_DATA, payload))
        frame[-1] ^= 0xFF  # corrupt CRC
        dp._process_frame(bytes(frame))
        self.assertEqual(self.state.comm.error_count, 1)

    def test_start_stop(self):
        from src.core.data_processor import DataProcessor
        dp = DataProcessor(state=self.state)
        dp.start()
        self.assertTrue(dp._running)
        dp.stop()
        self.assertFalse(dp._running)

    def test_queue_processing(self):
        from src.core.data_processor import DataProcessor, FrameType
        dp = DataProcessor(state=self.state)
        dp.start()
        fmt = "<fffHHHH"
        payload = struct.pack(fmt, 2.5, 400.0, 5.0, 1, 20, 0, 0)
        frame = self._make_frame(FrameType.SENSOR_DATA, payload)
        dp.enqueue(frame)
        time.sleep(0.3)
        dp.stop()
        self.assertAlmostEqual(self.state.sensor.tension_value, 2.5, places=3)


# ---------------------------------------------------------------------------
# DataStorage tests
# ---------------------------------------------------------------------------

class TestDataStorage(unittest.TestCase):

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmpfile.close()
        from src.core.data_storage import DataStorage
        self.db = DataStorage(db_path=self._tmpfile.name)

    def tearDown(self):
        self.db.close()
        Path(self._tmpfile.name).unlink(missing_ok=True)

    def test_create_and_end_session(self):
        sid = self.db.start_session("unit test")
        self.assertIsInstance(sid, int)
        self.assertGreater(sid, 0)
        self.db.end_session(sid)

    def test_insert_and_query_sensor(self):
        sid = self.db.start_session()
        self.db.insert_sensor_record(
            session_id=sid,
            tension=1.5, spindle_speed=300.0, position=10.0,
            layers=2, turns=40,
        )
        rows = self.db.query_sensor_records(sid)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["tension_value"], 1.5)

    def test_insert_alarm(self):
        sid = self.db.start_session()
        self.db.insert_alarm(sid, "WARNING", 101, "test alarm")
        rows = self.db.query_alarms(sid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message"], "test alarm")

    def test_tension_series(self):
        sid = self.db.start_session()
        for i in range(5):
            self.db.insert_sensor_record(
                sid, tension=float(i), spindle_speed=0, position=0, layers=0, turns=0
            )
        series = self.db.query_tension_series(sid)
        self.assertEqual(len(series), 5)
        # Should be ascending by timestamp
        for (t1, _), (t2, _) in zip(series, series[1:]):
            self.assertLessEqual(t1, t2)

    def test_process_params_insert(self):
        sid = self.db.start_session()
        params = {"target_tension": 1.5, "spindle_target_speed": 500.0,
                  "total_layers": 10, "turns_per_layer": 200}
        self.db.insert_process_params(sid, params)


# ---------------------------------------------------------------------------
# ParamCalculator tests
# ---------------------------------------------------------------------------

class TestParamCalculator(unittest.TestCase):

    def setUp(self):
        from src.core.param_calculator import ParamCalculator
        self.calc = ParamCalculator

    def test_winding_speed_zero_rpm(self):
        self.assertEqual(self.calc.calc_winding_speed(0, 0.125, 0.125), 0.0)

    def test_winding_speed(self):
        # 300 rpm × 0.125 mm/rev  = 0.625 mm/s
        speed = self.calc.calc_winding_speed(300, 0.125, 0.125)
        self.assertAlmostEqual(speed, 300 / 60 * 0.125, places=6)

    def test_turns_per_layer(self):
        turns = self.calc.calc_turns_per_layer(25.0, 0.125, 0.0)
        self.assertEqual(turns, 200)

    def test_tension_compensation(self):
        # layer 0 should return base tension unchanged
        t = self.calc.calc_tension_compensation(1.5, 0, 5.0, 0.125)
        self.assertAlmostEqual(t, 1.5)
        # deeper layers should have lower tension
        t1 = self.calc.calc_tension_compensation(1.5, 1, 5.0, 0.125)
        self.assertLess(t1, 1.5)

    def test_s_curve_total_distance(self):
        profile = self.calc.calc_s_curve_profile(100.0, 20.0, 5.0)
        # Integrate velocity profile to get total distance (rough check)
        pts = profile.profile_points
        dist = sum(
            0.5 * (pts[i][1] + pts[i + 1][1]) * (pts[i + 1][0] - pts[i][0])
            for i in range(len(pts) - 1)
        )
        self.assertAlmostEqual(dist, 100.0, delta=2.0)

    def test_pid_output_clamping(self):
        out = self.calc.calc_pid_output(1000.0, 0, 0, kp=1.0, ki=0, kd=0,
                                        output_min=-10, output_max=10)
        self.assertEqual(out, 10.0)


# ---------------------------------------------------------------------------
# ProcessValidator tests
# ---------------------------------------------------------------------------

class TestProcessValidator(unittest.TestCase):

    def setUp(self):
        from src.core.process_validator import ProcessValidator
        self.v = ProcessValidator()

    def _valid_params(self):
        from src.core.system_state import ProcessParams
        return ProcessParams(
            target_tension=1.5,
            spindle_target_speed=500.0,
            winding_target_speed=10.0,
            total_layers=10,
            turns_per_layer=200,
            fiber_diameter=0.125,
        )

    def test_valid_params_pass(self):
        result = self.v.validate_params(self._valid_params())
        self.assertTrue(result.is_ok)

    def test_zero_tension_fails(self):
        p = self._valid_params()
        p.target_tension = 0.0
        result = self.v.validate_params(p)
        self.assertFalse(result.is_ok)

    def test_excessive_spindle_speed(self):
        p = self._valid_params()
        p.spindle_target_speed = 9999.0
        result = self.v.validate_params(p)
        self.assertFalse(result.is_ok)

    def test_negative_fiber_diameter(self):
        p = self._valid_params()
        p.fiber_diameter = -1.0
        result = self.v.validate_params(p)
        self.assertFalse(result.is_ok)

    def test_sensor_tension_deviation_warning(self):
        from src.core.system_state import SensorData
        p = self._valid_params()
        sensor = SensorData(tension_value=0.5)  # far from target 1.5
        result = self.v.validate_sensor(sensor, p)
        self.assertTrue(result.has_warnings)


# ---------------------------------------------------------------------------
# FlowController tests
# ---------------------------------------------------------------------------

class TestFlowController(unittest.TestCase):

    def setUp(self):
        from src.core.system_state import SystemState
        from src.core.flow_controller import FlowController, MachineState
        from src.core.process_validator import ProcessValidator
        SystemState.reset_instance()
        self.state = SystemState.get_instance()
        # Set valid params so start() passes validation
        self.state.update_params(
            target_tension=1.5, spindle_target_speed=500.0,
            winding_target_speed=10.0, total_layers=10,
            turns_per_layer=200, fiber_diameter=0.125,
        )
        self.flow = FlowController(state=self.state)
        self.MachineState = MachineState

    def test_initial_state_idle(self):
        self.assertEqual(self.flow.machine_state, self.MachineState.IDLE)

    def test_start_transitions(self):
        result = self.flow.start()
        self.assertTrue(result)
        time.sleep(0.8)
        self.assertEqual(self.flow.machine_state, self.MachineState.RUNNING)

    def test_pause_resume(self):
        self.flow.start()
        time.sleep(0.8)
        self.flow.pause()
        self.assertEqual(self.flow.machine_state, self.MachineState.PAUSED)
        self.flow.resume()
        self.assertEqual(self.flow.machine_state, self.MachineState.RUNNING)

    def test_stop_transitions_to_completed(self):
        self.flow.start()
        time.sleep(0.8)
        self.flow.stop()
        time.sleep(0.8)
        self.assertEqual(self.flow.machine_state, self.MachineState.COMPLETED)

    def test_emergency_stop(self):
        self.flow.start()
        time.sleep(0.8)
        self.flow.emergency_stop()
        self.assertEqual(self.flow.machine_state, self.MachineState.ERROR)

    def test_reset_from_error(self):
        self.flow.start()
        time.sleep(0.8)
        self.flow.emergency_stop()
        result = self.flow.reset()
        self.assertTrue(result)
        self.assertEqual(self.flow.machine_state, self.MachineState.IDLE)

    def test_start_fails_with_invalid_params(self):
        self.state.update_params(target_tension=0.0)
        result = self.flow.start()
        self.assertFalse(result)
        self.assertEqual(self.flow.machine_state, self.MachineState.ERROR)

    def test_callbacks_fired(self):
        transitions = []
        self.flow.register_callback(lambda old, new: transitions.append((old, new)))
        self.flow.start()
        time.sleep(0.8)
        self.assertGreater(len(transitions), 0)


# ---------------------------------------------------------------------------
# FrameParser (SerialComm) tests
# ---------------------------------------------------------------------------

class TestFrameParser(unittest.TestCase):

    def setUp(self):
        from src.communication.serial_comm import FrameParser, build_frame, FrameType
        self.FrameParser = FrameParser
        self.build_frame = build_frame
        self.FrameType = FrameType

    def _heartbeat_frame(self):
        return self.build_frame(self.FrameType.HEARTBEAT, b'\x00')

    def test_single_frame(self):
        parser = self.FrameParser()
        frame = self._heartbeat_frame()
        results = parser.feed(frame)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], frame)

    def test_half_packet(self):
        parser = self.FrameParser()
        frame = self._heartbeat_frame()
        results = parser.feed(frame[:3])
        self.assertEqual(results, [])
        results = parser.feed(frame[3:])
        self.assertEqual(len(results), 1)

    def test_sticky_packets(self):
        parser = self.FrameParser()
        f1 = self._heartbeat_frame()
        f2 = self.build_frame(self.FrameType.HEARTBEAT, b'\x01')
        results = parser.feed(f1 + f2)
        self.assertEqual(len(results), 2)

    def test_garbage_before_frame(self):
        parser = self.FrameParser()
        frame = self._heartbeat_frame()
        results = parser.feed(b'\xFF\xFE\x00' + frame)
        self.assertEqual(len(results), 1)

    def test_corrupt_crc_dropped(self):
        parser = self.FrameParser()
        frame = bytearray(self._heartbeat_frame())
        frame[-1] ^= 0xFF
        results = parser.feed(bytes(frame))
        self.assertEqual(results, [])

    def test_build_frame_max_payload(self):
        payload = bytes(255)
        frame = self.build_frame(0x01, payload)
        self.assertEqual(frame[3], 255)

    def test_build_frame_overflow(self):
        from src.communication.serial_comm import build_frame
        with self.assertRaises(ValueError):
            build_frame(0x01, bytes(256))


# ---------------------------------------------------------------------------
# Config loader tests
# ---------------------------------------------------------------------------

class TestConfigLoader(unittest.TestCase):

    def test_load_default_config(self):
        from src.config.config_loader import load_config
        cfg = load_config()
        self.assertIsNotNone(cfg)
        self.assertGreater(cfg.process.target_tension, 0)
        self.assertGreater(cfg.serial.baudrate, 0)
        self.assertGreater(cfg.process.total_layers, 0)

    def test_load_custom_config(self):
        import tempfile
        yaml_content = """
serial:
  port: "/dev/ttyUSB1"
  baudrate: 9600
process:
  target_tension: 3.0
  total_layers: 20
"""
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w",
                                         delete=False, encoding="utf-8") as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            from src.config.config_loader import load_config
            cfg = load_config(tmp_path)
            self.assertEqual(cfg.serial.port, "/dev/ttyUSB1")
            self.assertEqual(cfg.serial.baudrate, 9600)
            self.assertAlmostEqual(cfg.process.target_tension, 3.0)
            self.assertEqual(cfg.process.total_layers, 20)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_missing_file_raises(self):
        from src.config.config_loader import load_config
        with self.assertRaises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_partial_yaml_uses_defaults(self):
        import tempfile
        yaml_content = "serial:\n  port: COM5\n"
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w",
                                         delete=False, encoding="utf-8") as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            from src.config.config_loader import load_config
            cfg = load_config(tmp_path)
            self.assertEqual(cfg.serial.port, "COM5")
            # Defaults should still be present for non-specified fields
            self.assertGreater(cfg.process.total_layers, 0)
        finally:
            Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
