"""GNU Radio runtime helpers; importing this module does not require GNU Radio."""

import json
import math
from pathlib import Path
import signal
import threading

from .profiles import AMPLITUDE, SAMPLE_RATE, WAVES, select_waves

DEFAULT_URI = "ip:192.168.1.102"
POWER_CONFIG = Path(__file__).resolve().parents[1] / "config" / "power.json"


def validate_transport(uri, duration, attenuation=None):
    if not uri or not math.isfinite(duration) or duration < 0:
        raise ValueError("URI is required; duration must be finite and nonnegative")
    if attenuation is not None and (not math.isfinite(attenuation) or not 0 <= attenuation <= 89.75):
        raise ValueError("attenuation must be 0..89.75 dB")


def power_setting(side, waves, uri, manual=None, calibration=None):
    selected = select_waves(waves)
    target = max(WAVES[w]["power_dbm"] for w in selected)
    if manual is not None:
        validate_transport(uri, 0, manual)
        return {"attenuation_db": manual, "status": "manual; absolute RF power is not calibrated",
                "target_strongest_dbm": target}
    data = calibration if calibration is not None else json.loads(POWER_CONFIG.read_text(encoding="utf-8"))
    key = side + ":" + "+".join(selected)
    measured = data["measured_burst_dbm"].get(key)
    if measured is None:
        return {"attenuation_db": None, "status": "needs power.json calibration or explicit --attenuation for bench testing",
                "target_strongest_dbm": target, "calibration_key": key}
    if data["uri"] != uri or data["tx_channel"] != 1 or data["reference_amplitude"] != AMPLITUDE:
        raise ValueError("power calibration does not match this URI, TX1 or IQ amplitude")
    if not isinstance(measured, (int, float)) or not math.isfinite(measured):
        raise ValueError("measured burst power must be finite dBm")
    reference = data["reference_attenuation_db"]
    validate_transport(uri, 0, reference)
    attenuation = round((reference + measured - target) * 4) / 4
    validate_transport(uri, 0, attenuation)
    return {"attenuation_db": attenuation, "status": "calculated from measured RF-on power",
            "target_strongest_dbm": target, "predicted_strongest_dbm": measured + reference - attenuation,
            "calibration_key": key}


def tx_sink(uri, frequency, attenuation):
    from gnuradio import iio
    sink = iio.fmcomms2_sink_fc32(uri, [True, True, False, False], 32768, False)
    sink.set_len_tag_key("")
    sink.set_bandwidth(SAMPLE_RATE)
    sink.set_frequency(frequency)
    sink.set_samplerate(SAMPLE_RATE)
    sink.set_attenuation(0, attenuation)
    sink.set_filter_params("Auto", "", 0, 0)
    return sink


def run_graph(tb, duration=0):
    """Wait for finite-file EOF; duration/Ctrl+C also stop repeated playback."""
    previous = {}
    timer = None
    started = False

    def request_stop(_signum=None, _frame=None):
        tb.stop()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, request_stop)
    try:
        tb.start()
        started = True
        if duration:
            timer = threading.Timer(duration, request_stop)
            timer.daemon = True
            timer.start()
        tb.wait()
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
        if started:
            tb.stop()
            tb.wait()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
