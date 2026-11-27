"""Telemetry from the vision, fusion and referee processes."""

from contextlib import contextmanager
from functools import partial
import json
import math
from threading import Thread


INPUT_TOPICS = {
    "vision": "/rm_radar_algorithm/telemetry",
    "fusion": "/rm_radar_integration/status",
    "referee": "/rm_referee_serial_node/status",
    "referee-bridge": "/rm_referee_serial_node/referee_bridge",
}
SCHEMAS = {"vision": "shark.radar.telemetry.v1", "fusion": "shark.radar.fusion.v1"}
REFEREE_EVENTS = ("GameStatus", "RadarMarkProgress", "RadarDecisionSync", "DartStatus", "RadarCommand0121")
ROBOT_IDS = frozenset(side + role for side in "RB" for role in "123467")
MAX_REPORT_SIZE = 256 * 1024


def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number in telemetry")
    return value


def decode_report(raw):
    if len(raw) > MAX_REPORT_SIZE:
        raise ValueError("telemetry message too large")
    return json.loads(raw, parse_float=finite_float, parse_constant=finite_float)


def normalize_position(record):
    if not isinstance(record, dict):
        raise ValueError("robot entry must be an object")
    if record.get("valid") is False:
        return record
    position = record.get("position_cm")
    scale = 1
    if position is None:
        position = record.get("position_m")
        scale = 100
    if position is None:
        return record
    if not isinstance(position, dict):
        raise ValueError("position must be an object with x and y")
    x, y = position.get("x"), position.get("y")
    if (type(x) not in (int, float) or type(y) not in (int, float)
            or not (0 <= x <= 2800 / scale and 0 <= y <= 1500 / scale)):
        raise ValueError("position outside the 28 m by 15 m field or invalid x/y")
    return {**record, "position_cm": {"x": x * scale, "y": y * scale}}


def normalize_report(source, report):
    if source not in INPUT_TOPICS or not isinstance(report, dict):
        raise ValueError("expected a telemetry source and a JSON object")
    if source == "referee-bridge":
        if report.get("type") not in REFEREE_EVENTS or not isinstance(report.get("payload"), dict):
            raise ValueError("expected a supported referee event type and payload object")
    if source not in SCHEMAS:
        return report
    if report.get("schema") != SCHEMAS[source]:
        raise ValueError(f"expected schema {SCHEMAS[source]}")
    if report.get("frame_id", "rm_field") != "rm_field":
        raise ValueError("expected frame_id rm_field")
    robots = report.get("robots", {})
    if not isinstance(robots, dict):
        raise ValueError("robots must be an object keyed by robot ID")
    positions = {}
    for name, robot in robots.items():
        if name not in ROBOT_IDS:
            continue
        if not isinstance(robot, dict):
            raise ValueError("robot entry must be an object")
        if source == "fusion":
            final = robot.get("final")
            if final is not None:
                positions[name] = {"final": normalize_position(final)}
        else:
            positions[name] = normalize_position(robot)
    return {**report, "robots": positions}


@contextmanager
def subscribe_ros(state):
    """Opt in to reading the same std_msgs/String JSON topics as SHARK."""
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    context = Context()
    rclpy.init(args=[], context=context)
    try:
        node = rclpy.create_node("rm_sdr_dashboard", context=context)
        executor = SingleThreadedExecutor(context=context)

        def on_message(source, message):
            try:
                report = decode_report(message.data)
                if (source == "referee-bridge" and isinstance(report, dict)
                        and report.get("type") not in REFEREE_EVENTS):
                    return
                state.update_input(source, report)
            except (ValueError, RecursionError) as exc:
                node.get_logger().warning(f"{source} telemetry: {exc}")

        try:
            qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
            for source, topic in INPUT_TOPICS.items():
                node.create_subscription(String, topic, partial(on_message, source), qos)
            executor.add_node(node)
            worker = Thread(target=executor.spin, name="rx-dashboard-ros", daemon=True)
            worker.start()
            try:
                yield
            finally:
                executor.shutdown(timeout_sec=2)
                worker.join(2)
        finally:
            node.destroy_node()
    finally:
        context.try_shutdown()
