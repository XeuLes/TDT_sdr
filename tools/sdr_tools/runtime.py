"""GNU Radio runtime helpers; importing this module does not require GNU Radio."""

import signal
import sys
import threading


def connect_file_source(tb, blocks, gr, path, window, repeat, sample_rate, realtime=False):
    if sys.byteorder != "little":
        raise ValueError("GNU Radio raw cf32 playback requires a little-endian host")
    source = blocks.file_source(gr.sizeof_gr_complex, str(path), repeat, window.start, window.count)
    if realtime:
        throttle = blocks.throttle(gr.sizeof_gr_complex, sample_rate, True)
        tb.connect(source, throttle)
        return throttle
    return source


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
