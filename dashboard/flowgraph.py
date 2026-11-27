"""Run one of RM_sdr's original RX flowgraphs with a Dashboard IQ tap."""

import argparse
import ast
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from .rx_bus import FRAME_PORTS, TELEMETRY_ENDPOINT
from .rx_telemetry import ReceiveTelemetry


PROJECT = Path(__file__).resolve().parents[1]
RECEIVER_FILES = {"red": "GFSK_RX_RED.py", "blue": "GFSK2_RX_BLUE.py"}
IQ_SOURCE = "iio_fmcomms2_source_0"
CHANNEL_BLOCKS = {
    "info": "freq_xlating_fir_filter_xxx_0_0_0_2",
    "jam1": "freq_xlating_fir_filter_xxx_0_0_0",
    "jam2": "freq_xlating_fir_filter_xxx_0_0_0_1",
}


def numeric_value(node, variables):
    if isinstance(node, ast.Name):
        return variables[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return numeric_value(node.left, variables) * numeric_value(node.right, variables)
    return ast.literal_eval(node)


def describe_graph(path, side):
    """Read the original flowgraph parameters for the spectrum axes."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    graph = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == path.stem)
    init = next(node for node in graph.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    variables, blocks, source_settings = {}, {}, {}
    for node in ast.walk(init):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("samp_rate", "sps"):
                    variables[target.id] = numeric_value(node.value, variables)
                elif isinstance(target, ast.Attribute) and isinstance(node.value, ast.Call):
                    blocks[target.attr] = node.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            source = node.func.value
            if isinstance(source, ast.Attribute) and source.attr == IQ_SOURCE:
                source_settings[node.func.attr] = node.args

    channels = {}
    for (wave, name), port in zip(CHANNEL_BLOCKS.items(), FRAME_PORTS):
        args = blocks[name].args
        channels[wave] = {"offset_hz": numeric_value(args[2], variables),
                          "decimation": numeric_value(args[0], variables), "zmq_port": port}
    return {
        "project_file": str(path), "side": side, "pid": os.getpid(), "sps": variables["sps"],
        "uri": ast.literal_eval(blocks[IQ_SOURCE].args[0]),
        "lo_hz": numeric_value(source_settings["set_frequency"][0], variables),
        "sample_rate_hz": numeric_value(source_settings["set_samplerate"][0], variables),
        "telemetry_endpoint": TELEMETRY_ENDPOINT, "channels": channels,
    }


def compatible_pub_sink(factory):
    def pub_sink(*args):
        try:
            return factory(*args)
        except TypeError:
            # Older GNU Radio exposes only the six common arguments. The
            # generated graphs use defaults for all three newer arguments.
            if len(args) != 9 or args[6:] != ("", True, True):
                raise
            return factory(*args[:6])

    return pub_sink


def run_flowgraph(path, description):
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    spec = spec_from_file_location(path.stem, path)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.zeromq = SimpleNamespace(pub_sink=compatible_pub_sink(module.zeromq.pub_sink))
    graph_type = getattr(module, path.stem)
    previous = sys.argv
    try:
        sys.argv = [str(path)]
        with ReceiveTelemetry(description) as monitor:
            def make_graph():
                graph = graph_type()
                blocks = {"raw": getattr(graph, IQ_SOURCE)}
                blocks.update((wave, getattr(graph, name)) for wave, name in CHANNEL_BLOCKS.items())
                monitor.attach(graph, blocks)
                return graph

            module.main(top_block_cls=make_graph)
    finally:
        sys.argv = previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("side", choices=tuple(RECEIVER_FILES))
    parser.add_argument("--describe", action="store_true", help="read flowgraph settings without opening the SDR")
    args = parser.parse_args(argv)
    path = PROJECT / RECEIVER_FILES[args.side]
    try:
        description = describe_graph(path, args.side)
        if args.describe:
            print(json.dumps(description, indent=2))
        else:
            run_flowgraph(path, description)
    except (OSError, ImportError, RuntimeError, TypeError, ValueError, KeyError, StopIteration) as exc:
        print(f"RX {path.name}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
