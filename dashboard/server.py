"""Receive monitoring, input reports and side selection over HTTP/SSE."""

from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import json
from pathlib import Path
from threading import Event, Lock, Thread
import time
from urllib.parse import urlsplit

from .frames import ApplicationParser, PAYLOAD_LENGTHS, frame_description
from .inputs import INPUT_TOPICS, MAX_REPORT_SIZE, decode_report, normalize_report
from .rx_bus import FRAME_PORTS, TELEMETRY_ENDPOINT

WEB_ROOT = Path(__file__).resolve().parent / "web"
INFO_COMMANDS = frozenset(range(0x0A01, 0x0A06))
JAM_COMMANDS = frozenset({0x0A06})


class Channel:
    def __init__(self, port):
        self.port = port
        self.parser = ApplicationParser()
        self.frames = {}
        self.arrivals = deque()
        self.last_fragment = None
        self.invalid_length = 0
        self.valid_frames = 0

    def feed(self, payload, stamp):
        self.last_fragment = stamp
        allowed = INFO_COMMANDS if self.port == 5555 else JAM_COMMANDS
        for command, sequence, data in self.parser.feed(payload):
            if command not in allowed:
                continue
            if len(data) != PAYLOAD_LENGTHS[command] and (command, len(data)) != (0x0A05, 36):
                self.invalid_length += 1
                continue
            frame = frame_description((command, sequence, data))
            self.frames[frame["command"]] = {**frame, "stamp": stamp}
            self.arrivals.append((stamp, frame["command"]))
            self.valid_frames += 1
        self.discard_old_arrivals(stamp)

    def discard_old_arrivals(self, stamp):
        while self.arrivals and self.arrivals[0][0] < stamp - 1.0:
            self.arrivals.popleft()

    def snapshot(self, stamp):
        self.discard_old_arrivals(stamp)
        rates = {cmd: 0 for cmd in self.frames}
        for _, cmd in self.arrivals:
            rates[cmd] += 1
        return {
            "port": self.port, "last_fragment": self.last_fragment,
            "counters": {**self.parser.summary(), "invalid_length": self.invalid_length},
            "frames_per_sec": len(self.arrivals),
            "valid_frames": self.valid_frames,
            "frames": {cmd: {**frame, "frames_per_sec": rates[cmd]} for cmd, frame in self.frames.items()},
        }


class DashboardState:
    def __init__(self):
        self.lock = Lock()
        self.channels = {port: Channel(port) for port in FRAME_PORTS}
        self.telemetry = {}
        self.inputs = {}
        self.receiver = None
        self.error = None

    def feed(self, port, data, stamp=None):
        with self.lock:
            if self.receiver is not None and not self.telemetry.get("spectra", {}).get("raw"):
                return
            self.channels[port].feed(data, time.time() if stamp is None else stamp)

    def update_rx(self, telemetry):
        with self.lock:
            if self.receiver is not None:
                pid = self.receiver["pid"]
                if not pid or telemetry.get("rx", {}).get("pid") != pid:
                    return
            old = self.telemetry.get("run_id")
            if old and old != telemetry["run_id"]:
                # SPS/side and the branch at 6667 can change when RX restarts.
                self.channels = {port: Channel(port) for port in FRAME_PORTS}
            self.telemetry = telemetry

    def set_receiver(self, side, status, pid=None, *, error=None, reset=False):
        with self.lock:
            self.receiver = {"side": side, "status": status, "pid": pid, "error": error}
            if reset:
                self.telemetry = {}
                self.channels = {port: Channel(port) for port in FRAME_PORTS}

    def has_live_iq(self, pid, side):
        with self.lock:
            rx = self.telemetry.get("rx", {})
            raw = self.telemetry.get("spectra", {}).get("raw", {})
            return (rx.get("pid") == pid and rx.get("side") == side and
                    time.time() - raw.get("stamp", 0) < 2)

    def update_input(self, source, report):
        report = normalize_report(source, report)
        with self.lock:
            entry = {"stamp": time.time(), "data": report}
            if source == "referee-bridge":
                self.inputs[source] = {**self.inputs.get(source, {}), report["type"]: entry}
            else:
                self.inputs[source] = entry

    def snapshot(self):
        with self.lock:
            now = time.time()
            channels = {key: receiver.snapshot(now) for key, receiver in self.channels.items()}
            return {"stamp": now, "channels": channels, "telemetry": self.telemetry,
                    "inputs": self.inputs.copy(), "receiver": self.receiver, "error": self.error}


class Subscriber:
    def __init__(self, state):
        import zmq

        self.zmq = zmq
        self.state = state
        self.stopped = Event()
        self.worker = Thread(target=self._run, name="rx-dashboard-subscriber", daemon=True)

    def __enter__(self):
        self.worker.start()
        return self

    def __exit__(self, *exc):
        self.stopped.set()
        self.worker.join(1)

    def _run(self):
        zmq = self.zmq
        with zmq.Context() as context:
            poll = zmq.Poller()
            ports = {}
            for port in (None, *FRAME_PORTS):
                socket = context.socket(zmq.SUB)
                socket.setsockopt(zmq.LINGER, 0)
                socket.setsockopt(zmq.SUBSCRIBE, b"")
                if port is None:
                    socket.setsockopt(zmq.CONFLATE, 1)
                socket.connect(TELEMETRY_ENDPOINT if port is None else f"tcp://127.0.0.1:{port}")
                ports[socket] = port
                poll.register(socket, zmq.POLLIN)
            try:
                while not self.stopped.is_set():
                    events = dict(poll.poll(100))
                    for socket, port in ports.items():
                        if socket in events:
                            if port is None:
                                self.state.update_rx(socket.recv_json())
                            else:
                                self.state.feed(port, socket.recv())
            except (zmq.ZMQError, ValueError, KeyError) as exc:
                with self.state.lock:
                    self.state.error = str(exc)
            finally:
                for socket in ports:
                    socket.close()


class DashboardServer(ThreadingHTTPServer):
    request_queue_size = 32

    def __init__(self, address, state, controller=None, radio_node=None):
        self.state = state
        self.controller = controller
        self.radio_node = radio_node
        self.stopped = Event()
        super().__init__(address, DashboardHandler)

    def snapshot(self):
        data = self.state.snapshot()
        data["receive_node"] = self.radio_node.snapshot() if self.radio_node is not None else None
        return data

    def server_close(self):
        self.stopped.set()
        super().server_close()


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/state":
            self.send_json(200, self.server.snapshot())
        elif path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.connection.settimeout(3)
            try:
                while not self.server.stopped.is_set():
                    self.wfile.write(b"data: " + encode(self.server.snapshot()) + b"\n\n")
                    self.wfile.flush()
                    self.server.stopped.wait(0.1)
            except OSError:
                return
        elif path in ("/", "/index.html", "/app.js", "/styles.css", "/field-red.jpg", "/field-blue.jpg"):
            super().do_GET()
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlsplit(self.path).path
        side_change = path == "/api/rx/side"
        source = path.removeprefix("/api/input/")
        if not side_change and (not path.startswith("/api/input/") or source not in INPUT_TOPICS):
            self.send_json(404, {"error": "unknown telemetry input"})
            return
        peer = self.client_address[0]
        if not ip_address(peer).is_loopback and peer != self.server.server_address[0]:
            self.send_json(403, {"error": "请在运行 Dashboard 的主机上提交，或通过 SSH 转发访问"})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "expected application/json"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_REPORT_SIZE:
                raise ValueError(f"expected a JSON body up to {MAX_REPORT_SIZE} bytes")
            self.connection.settimeout(3)
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError("incomplete JSON body")
            report = decode_report(body)
            if not side_change:
                self.server.state.update_input(source, report)
        except (OSError, ValueError, RecursionError) as exc:
            self.send_json(400, {"error": str(exc)})
            return
        if side_change:
            self.change_side(report)
        else:
            self.send_json(200, {"source": source, "accepted": True})

    def change_side(self, report):
        side = report.get("side") if isinstance(report, dict) else None
        if side not in ("red", "blue"):
            self.send_json(400, {"error": "side must be red or blue"})
            return
        if self.server.controller is None:
            self.send_json(409, {"error": "预览模式不启动接收流图"})
            return
        try:
            actual_side = self.server.controller.switch(side)
        except RuntimeError as exc:
            self.send_json(503, {"error": str(exc)})
        else:
            self.send_json(200, {"side": actual_side})

    def send_json(self, status, data):
        payload = encode(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass


def encode(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

