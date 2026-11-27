"""Sample the live RX graph without creating a second IIO stream."""

import math
import shutil
import subprocess
from threading import Event, Thread
import time
import uuid

from .rx_bus import TELEMETRY_ENDPOINT

FFT_SIZE = 1024
UPDATE_PERIOD = 0.1


def spectrum(samples, center_hz, rate_hz, stamp):
    import numpy as np

    window = np.hanning(len(samples))
    fft = np.fft.fftshift(np.fft.fft(samples * window)) / window.sum()
    bins = 20 * np.log10(np.maximum(np.abs(fft), 1e-8))
    magnitude = np.abs(samples)
    peak_index = int(np.argmax(bins))
    clipped = (np.abs(samples.real) >= 0.99) | (np.abs(samples.imag) >= 0.99)
    return {
        "stamp": stamp, "center_hz": center_hz, "rate_hz": rate_hz,
        "bins_dbfs": bins.round(1).tolist(),
        "peak_dbfs": float(bins[peak_index]),
        "peak_hz": center_hz + (peak_index - len(fft) // 2) * rate_hz / len(fft),
        "rms_dbfs": float(20 * np.log10(max(float(np.sqrt(np.mean(magnitude ** 2))), 1e-8))),
        "clip_percent": float(100 * np.mean(clipped)),
    }


def read_gain(uri):
    executable = shutil.which("iio_attr")
    if executable is None:
        return {"error": "iio_attr 不可用", "stamp": time.time()}

    def attribute(name):
        result = subprocess.run(
            [executable, "-u", uri, "-i", "-c", "ad9361-phy", "voltage0", name],
            capture_output=True, text=True, errors="replace", timeout=1.0, check=True,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) != 1:
            raise ValueError(f"IIO {name} returned {len(lines)} values")
        return lines[0].strip()

    try:
        mode = attribute("gain_control_mode")
        gain = float(attribute("hardwaregain").split()[0])
        if not math.isfinite(gain):
            raise ValueError("IIO hardwaregain 未返回有效数值")
        return {"mode": mode, "gain_db": gain, "stamp": time.time()}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"error": str(exc), "stamp": time.time()}


class ReceiveTelemetry:
    def __init__(self, description, endpoint=TELEMETRY_ENDPOINT):
        self.description = description
        self.endpoint = endpoint
        self.run_id = uuid.uuid4().hex
        self.taps = {}
        self.running = Event()
        self.ready = Event()
        self.start_error = None
        self.gain = {}
        self.publisher = Thread(target=self._publish, name="rx-monitor-publish", daemon=True)
        self.readback = Thread(target=self._readback, name="rx-monitor-gain", daemon=True)

    def __enter__(self):
        self.publisher.start()
        if not self.ready.wait(3) or self.start_error:
            self.running.set()
            self.publisher.join(1)
            raise RuntimeError(f"无法启动接收遥测 {self.endpoint}: {self.start_error or '超时'}")
        self.readback.start()
        return self

    def __exit__(self, *exc):
        self.running.set()
        self.publisher.join(2)
        self.readback.join(2.5)

    def attach(self, graph, blocks):
        import numpy as np
        from gnuradio import gr

        class SampleTap(gr.sync_block):
            def __init__(self, name):
                gr.sync_block.__init__(self, name=f"rx monitor {name}", in_sig=[np.complex64], out_sig=None)
                self.next_sample = 0.0
                self.pending = np.empty(FFT_SIZE, dtype=np.complex64)
                self.pending_count = 0
                self.latest = None

            def work(self, input_items, output_items):
                incoming = input_items[0]
                now = time.monotonic()
                if now >= self.next_sample:
                    take = min(len(incoming), FFT_SIZE - self.pending_count)
                    self.pending[self.pending_count:self.pending_count + take] = incoming[:take]
                    self.pending_count += take
                    if self.pending_count == FFT_SIZE:
                        # Publish an immutable sample reference; the scheduler never waits for FFT/IIO.
                        self.latest = (time.time(), self.pending.copy())
                        self.pending_count = 0
                        self.next_sample = now + UPDATE_PERIOD
                return len(incoming)

        taps = {}
        for name, block in blocks.items():
            tap = SampleTap(name)
            graph.connect((block, 0), tap)
            taps[name] = tap
        self.taps = taps

    def _readback(self):
        while not self.running.is_set():
            self.gain = read_gain(self.description["uri"])
            self.running.wait(1)

    def _publish(self):
        try:
            import zmq

            with zmq.Context() as context, context.socket(zmq.PUB) as socket:
                socket.setsockopt(zmq.LINGER, 0)
                socket.setsockopt(zmq.SNDHWM, 2)
                socket.bind(self.endpoint)
                self.ready.set()
                seen, spectra = {}, {}
                rate_hz = self.description["sample_rate_hz"]
                lo_hz = self.description["lo_hz"]
                while not self.running.wait(UPDATE_PERIOD):
                    for name, tap in self.taps.items():
                        item = tap.latest
                        if item is None or seen.get(name) is item:
                            continue
                        seen[name] = item
                        stamp, samples = item
                        if not all(map(math.isfinite, (samples.real.min(), samples.real.max(),
                                                       samples.imag.min(), samples.imag.max()))):
                            continue
                        settings = self.description["channels"].get(name, {})
                        center = lo_hz + settings.get("offset_hz", 0)
                        rate = rate_hz / settings.get("decimation", 1)
                        spectra[name] = spectrum(samples, center, rate, stamp)
                    socket.send_json({
                        "run_id": self.run_id, "stamp": time.time(), "rx": self.description,
                        "spectra": spectra, "gain": self.gain,
                    }, flags=zmq.NOBLOCK)
        except Exception as exc:
            self.start_error = exc
            self.ready.set()
            if not self.running.is_set():
                print(f"RX monitor: {exc}", flush=True)

