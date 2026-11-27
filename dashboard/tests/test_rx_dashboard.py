import json
import math
import struct
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from dashboard import __main__ as entry
from dashboard import flowgraph
from dashboard.frames import application_frame, crc8, crc16
from dashboard.receiver import RADIO_RECEIVE_COMMAND, RadioReceiveProcess, ReceiverController
from dashboard.rx_telemetry import read_gain, spectrum
from dashboard.server import Channel, DashboardServer, DashboardState


def sample_position():
    return application_frame(0x0A01, struct.pack("<12H", 1234, 567, *([0] * 10)), 7)


class DashboardDecodeTests(unittest.TestCase):
    def test_multipart_crc_and_map_coordinates(self):
        state = DashboardState()
        timestamp = time.time()
        frame = sample_position()
        wrong_header = bytes([0xA5, 0, 0, 0, (crc8([0xA5, 0, 0, 0]) + 1) % 256])
        wrong_frame = frame[:-2] + bytes([frame[-2] ^ 1, frame[-1]])
        state.feed(5555, wrong_header + wrong_frame + frame[:12], timestamp)
        state.feed(5555, frame[12:], timestamp)
        report = state.snapshot()["channels"][5555]
        self.assertEqual(report["counters"]["bad_header_crc"], 1)
        self.assertEqual(report["counters"]["bad_frame_crc"], 1)
        self.assertEqual(report["frames_per_sec"], 1)
        self.assertEqual(report["valid_frames"], 1)
        self.assertEqual(report["frames"]["0x0A01"]["positions_cm"][0], (1234, 567))

    def test_legacy_buffs_and_jam_key_separate(self):
        state = DashboardState()
        head = struct.pack("<BHB", 0xA5, 36, 1)
        frame = head + bytes([crc8(head)]) + struct.pack("<H", 0x0A05) + bytes(36)
        frame += struct.pack("<H", crc16(frame))
        state.feed(5555, frame)
        state.feed(6667, application_frame(0x0A06, b"TEST51", 9))
        report = state.snapshot()["channels"]
        self.assertEqual(report[5555]["frames"]["0x0A05"]["sentry_stance"], 0)
        self.assertNotIn("robot_states", report[5555]["frames"]["0x0A05"])
        self.assertEqual(report[6667]["frames"]["0x0A06"]["key"], "TEST51")

    def test_restarting_receiver_clears_old_side_and_branch_frames(self):
        state = DashboardState()
        state.update_rx({"run_id": "red", "rx": {"side": "red"}})
        state.feed(5555, sample_position())
        state.update_rx({"run_id": "blue", "rx": {"side": "blue"}})
        snapshot = state.snapshot()
        self.assertFalse(snapshot["channels"][5555]["frames"])
        self.assertEqual(snapshot["telemetry"]["rx"]["side"], "blue")

    def test_old_frame_remains_visible_but_rate_decays(self):
        channel = Channel(5555)
        channel.feed(sample_position(), 10.0)
        report = channel.snapshot(11.2)
        self.assertEqual(report["frames_per_sec"], 0)
        self.assertEqual(report["frames"]["0x0A01"]["stamp"], 10.0)


class SpectrumTests(unittest.TestCase):
    def test_frequency_and_dbfs_scaling(self):
        size, rate_hz, center_hz = 1024, 2_000_000, 432_600_000
        samples = 0.5 * np.exp(-2j * np.pi * 128 * np.arange(size) / size)
        data = spectrum(samples, center_hz, rate_hz, stamp=10)
        self.assertEqual(data["peak_hz"], center_hz - 250_000)
        self.assertAlmostEqual(data["peak_dbfs"], 20 * math.log10(0.5))
        self.assertAlmostEqual(data["rms_dbfs"], 20 * math.log10(0.5))
        self.assertEqual(data["clip_percent"], 0)
        self.assertEqual(len(data["bins_dbfs"]), size)

    def test_clipping_counts_either_component(self):
        samples = np.asarray([1 + 0j, 0 - 1j, 0.7 + 0.7j, 0], dtype=np.complex64)
        self.assertEqual(spectrum(samples, 0, 4, 0)["clip_percent"], 50)

    @patch("dashboard.rx_telemetry.shutil.which", return_value="iio_attr")
    @patch("dashboard.rx_telemetry.subprocess.run")
    def test_actual_iio_readback(self, run, _which):
        run.side_effect = [
            type("Result", (), {"stdout": "slow_attack\n"}),
            type("Result", (), {"stdout": "18.000000 dB\n"}),
        ]
        report = read_gain("ip:192.168.1.11")
        self.assertEqual(report["mode"], "slow_attack")
        self.assertEqual(report["gain_db"], 18)
        self.assertIn("voltage0", run.call_args.args[0])


class ReceiverConfigurationTests(unittest.TestCase):
    def test_original_graph_descriptions_by_side(self):
        for side, filename, lo in (("red", "GFSK_RX_RED.py", 432600000),
                                   ("blue", "GFSK2_RX_BLUE.py", 434520000)):
            path = flowgraph.PROJECT / flowgraph.RECEIVER_FILES[side]
            report = flowgraph.describe_graph(path, side)
            self.assertEqual(path.name, filename)
            self.assertEqual(report["lo_hz"], lo)
            self.assertEqual(report["sample_rate_hz"], 2_000_000)
            self.assertEqual(report["channels"]["info"]["zmq_port"], 5555)
            self.assertEqual(report["channels"]["jam2"]["zmq_port"], 6667)

    def test_launcher_calls_original_graph_main_with_monitored_factory(self):
        for side, filename in flowgraph.RECEIVER_FILES.items():
            path = flowgraph.PROJECT / filename
            blocks = {"raw": Mock(), **{wave: Mock() for wave in flowgraph.CHANNEL_BLOCKS}}
            graph = SimpleNamespace(**{flowgraph.IQ_SOURCE: blocks["raw"],
                                       **{name: blocks[wave] for wave, name in flowgraph.CHANNEL_BLOCKS.items()}})
            graph_class = Mock(return_value=graph)
            module = SimpleNamespace(zeromq=Mock(), main=Mock(side_effect=lambda top_block_cls: top_block_cls()),
                                     **{path.stem: graph_class})
            spec = SimpleNamespace(name=path.stem, loader=Mock())
            with (patch.object(flowgraph, "spec_from_file_location", return_value=spec) as find,
                  patch.object(flowgraph, "module_from_spec", return_value=module),
                  patch.dict(flowgraph.sys.modules),
                  patch.object(flowgraph, "ReceiveTelemetry") as telemetry):
                flowgraph.run_flowgraph(path, {"side": side})
            find.assert_called_once_with(path.stem, path)
            spec.loader.exec_module.assert_called_once_with(module)
            graph_class.assert_called_once_with()
            telemetry.return_value.__enter__.return_value.attach.assert_called_once_with(graph, blocks)

    def test_legacy_pub_sink_tries_original_arguments_first(self):
        sink = object()
        factory = Mock(side_effect=[TypeError("old signature"), sink])
        args = (1, 1, "tcp://127.0.0.1:5555", 100, False, -1, "", True, True)
        self.assertIs(flowgraph.compatible_pub_sink(factory)(*args), sink)
        self.assertEqual([call.args for call in factory.call_args_list], [args, args[:6]])

    def test_managed_receiver_requires_fresh_iq_from_the_new_process(self):
        state = DashboardState()
        state.set_receiver("blue", "starting", pid=2, reset=True)
        state.update_rx({"run_id": "old", "rx": {"side": "red", "pid": 1}})
        state.feed(5555, sample_position())
        self.assertFalse(state.snapshot()["channels"][5555]["frames"])
        self.assertFalse(state.snapshot()["telemetry"])
        report = {"run_id": "new", "rx": {"side": "blue", "pid": 2},
                  "spectra": {"raw": {"stamp": time.time()}}}
        state.update_rx(report)
        self.assertTrue(state.has_live_iq(2, "blue"))
        self.assertFalse(state.has_live_iq(2, "red"))
        state.feed(5555, sample_position())
        self.assertEqual(state.snapshot()["channels"][5555]["valid_frames"], 1)

    @patch("dashboard.receiver.subprocess.Popen")
    def test_switch_runs_dashboard_flowgraph_and_rolls_back_startup_failure(self, popen):
        red, blue, restored = (Mock(pid=pid) for pid in (10, 11, 12))
        for child in (red, blue, restored):
            child.poll.return_value = None
        popen.side_effect = [red, blue, OSError("start error"), restored]
        state = DashboardState()
        with patch.object(state, "has_live_iq", return_value=True):
            with ReceiverController(state, "rx-python") as controller:
                popen.assert_not_called()
                self.assertEqual(controller.switch("red"), "red")
                self.assertEqual(controller.switch("blue"), "blue")
                red.terminate.assert_called_once()
                self.assertEqual(popen.call_args.args[0], ["rx-python", "-u", "-m", "dashboard.flowgraph", "blue"])
                with self.assertRaisesRegex(RuntimeError, "start error"):
                    controller.switch("red")
                blue.terminate.assert_called_once()
                control = state.snapshot()["receiver"]
                self.assertEqual((control["side"], control["pid"], control["status"]), ("blue", 12, "running"))
                self.assertIn("已恢复原阵营", control["error"])
        restored.terminate.assert_called_once()


class DashboardStartupTests(unittest.TestCase):
    def test_default_start_launches_ros_node_and_waits_for_a_side(self):
        child = Mock(pid=123)
        child.poll.return_value = None
        with (patch.object(entry, "Subscriber"),
              patch.object(entry, "DashboardServer") as server,
              patch.object(entry.signal, "signal"),
              patch.object(RadioReceiveProcess, "__exit__", return_value=None),
              patch("dashboard.receiver.subprocess.Popen", return_value=child) as popen):
            server.return_value.__enter__.return_value.server_port = 8765
            self.assertEqual(entry.main([]), 0)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][-4:], list(RADIO_RECEIVE_COMMAND))
        state, controller, node = server.call_args.args[1:]
        self.assertEqual(state.snapshot()["receiver"]["status"], "idle")
        self.assertIsNone(controller.child)
        self.assertEqual(node.snapshot()["status"], "running")


class DashboardHTTPTests(unittest.TestCase):
    def test_html_assets_snapshot_and_stream(self):
        state = DashboardState()
        state.feed(5555, sample_position())
        node = Mock()
        node.snapshot.return_value = {"status": "running", "pid": 321}
        with DashboardServer(("127.0.0.1", 0), state, radio_node=node) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            origin = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(origin, timeout=3) as response:
                    page = response.read().decode()
                    for text in ("无线电坐标", "视觉定位", "融合决策上报", "裁判与赛事策略"):
                        self.assertIn(text, page)
                    for control in ("showRadio", "showVision", "showFinal", "showLabels"):
                        self.assertRegex(page, rf'<input id="{control}" type="checkbox" checked\s*/>')
                with urlopen(origin + "/field-blue.jpg", timeout=3) as response:
                    self.assertEqual(response.read(2), b"\xff\xd8")
                with urlopen(origin + "/api/state", timeout=3) as response:
                    message = json.load(response)
                    self.assertEqual(message["receive_node"], {"status": "running", "pid": 321})
                    self.assertEqual(message["channels"]["5555"]["frames"]["0x0A01"]["positions_cm"][0], [1234, 567])

                def post(source, data):
                    request = Request(origin + "/api/input/" + source, data=json.dumps(data).encode(),
                                      headers={"Content-Type": "application/json"})
                    with urlopen(request, timeout=3) as response:
                        self.assertEqual(json.load(response), {"accepted": True, "source": source})

                post("vision", {
                    "schema": "shark.radar.telemetry.v1", "frame_id": "rm_field", "side": "red",
                    "vision": {"camera_fps": 25.0, "camera_frame_count": 101},
                    "robots": {"B1": {"valid": True, "position_m": {"x": 13, "y": 6}}},
                })
                post("fusion", {
                    "schema": "shark.radar.fusion.v1", "side": "red",
                    "robots": {"B1": {"final": {"position_cm": {"x": 1350, "y": 620}, "source": "vision"}}},
                    "tx_0305": {"success_count": 3128, "request_count": 3131},
                })
                post("referee", {"serial_open": True, "port": "/dev/shark-referee"})
                post("referee-bridge", {"type": "GameStatus", "payload": {"game_progress": 4}})
                post("referee-bridge", {"type": "DartStatus", "payload": {"selected_target": 1}})
                for invalid in ({"schema": "wrong"}, {"schema": "shark.radar.telemetry.v1", "n": float("inf")},
                                {"schema": "shark.radar.telemetry.v1", "robots": {"B1": {"position_m": {"x": 30, "y": 6}}}}):
                    with self.assertRaises(HTTPError) as error:
                        post("vision", invalid)
                    self.assertEqual(error.exception.code, 400)
                request = Request(origin + "/api/rx/side", data=b'{"side":"blue"}',
                                  headers={"Content-Type": "application/json"})
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=3)
                self.assertEqual(error.exception.code, 409)
                server.controller = Mock()
                server.controller.switch.return_value = "blue"
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(json.load(response), {"side": "blue"})
                server.controller.switch.assert_called_once_with("blue")
                with urlopen(origin + "/api/events", timeout=3) as response:
                    self.assertEqual(response.headers.get_content_type(), "text/event-stream")
                    line = response.readline()
                    self.assertTrue(line.startswith(b"data: "))
                    events = json.loads(line[6:])["inputs"]
                    vision = events["vision"]["data"]
                    self.assertEqual(vision["robots"]["B1"]["position_cm"], {"x": 1300, "y": 600})
                    self.assertEqual(vision["vision"]["camera_frame_count"], 101)
                    self.assertEqual(events["fusion"]["data"]["robots"]["B1"]["final"]["source"], "vision")
                    self.assertTrue(events["referee"]["data"]["serial_open"])
                    self.assertEqual(set(events["referee-bridge"]), {"GameStatus", "DartStatus"})
            finally:
                server.stopped.set()
                server.shutdown()
                worker.join(2)


if __name__ == "__main__":
    unittest.main()

