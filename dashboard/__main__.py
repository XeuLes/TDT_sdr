#!/usr/bin/env python3
"""Start the receive dashboard and the project's ROS 2 receiver node."""

import argparse
from contextlib import nullcontext
import ipaddress
import os
import signal
import sys

from .inputs import subscribe_ros
from .receiver import RadioReceiveProcess, ReceiverController
from .server import DashboardServer, DashboardState, Subscriber


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m dashboard", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="IPv4 address to listen on")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--preview", action="store_true", help="serve the page without the receiver processes or SDR subscriber")
    parser.add_argument("--rx-python", default=os.environ.get("PYTHON_BIN"),
                        help="Python executable with GNU Radio, PyQt5, NumPy and PyZMQ")
    parser.add_argument("--ros-inputs", action="store_true", help="subscribe to SHARK vision, fusion and referee ROS 2 topics")
    args = parser.parse_args(argv)
    try:
        ipaddress.IPv4Address(args.host)
        state = DashboardState()
        subscriber = nullcontext() if args.preview else Subscriber(state)
        ros = subscribe_ros(state) if args.ros_inputs else nullcontext()
        controller = None if args.preview else ReceiverController(state, args.rx_python)
        radio_node = None if args.preview else RadioReceiveProcess()

        def stop(_signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        with (subscriber, ros,
              DashboardServer((args.host, args.port), state, controller, radio_node) as server,
              radio_node or nullcontext(), controller or nullcontext()):
            print(f"SDR Dashboard http://{args.host}:{server.server_port}/", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        return 0
    except (OSError, ImportError, ValueError) as exc:
        print(f"Dashboard: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

