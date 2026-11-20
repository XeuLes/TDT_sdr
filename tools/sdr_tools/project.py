"""Read and adapt the existing receiver flowgraphs without changing their files."""

import ast
from pathlib import Path

from .profiles import WAVES

PROJECT = Path(__file__).resolve().parents[2]
RECEIVERS = {"red": "GFSK_RX_RED.py", "blue": "GFSK2_RX_BLUE.py"}
ATTRIBUTES = {
    "info": ("freq_xlating_fir_filter_xxx_0_0_0_2", "low_pass_filter_0", "robomaster_gfsk_receiver_0"),
    "jam1": ("freq_xlating_fir_filter_xxx_0_0_0", "low_pass_filter_0_1", "robomaster_gfsk_receiver_noise_0"),
    "jam2": ("freq_xlating_fir_filter_xxx_0_0_0_1", "low_pass_filter_0_1_1", "robomaster_gfsk_receiver_noise_0_1"),
}


def receiver_tree(side):
    path = PROJECT / RECEIVERS[side]
    return path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def number(node, names):
    if isinstance(node, ast.Name):
        return names[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
        a, b = number(node.left, names), number(node.right, names)
        return a * b if isinstance(node.op, ast.Mult) else a / b
    return ast.literal_eval(node)


def source_configuration(side):
    path, tree = receiver_tree(side)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    names, assigned = {}, {}
    for node in ast.walk(init):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("sps", "samp_rate"):
                    names[target.id] = number(node.value, names)
                if isinstance(target, ast.Attribute):
                    assigned[target.attr] = node.value
    lo = next(number(n.args[0], names) for n in ast.walk(init)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "set_frequency")
    rate = next(number(n.args[0], names) for n in ast.walk(init)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "set_samplerate")
    result = {"file": str(path), "lo_hz": lo, "sample_rate_hz": rate, "sps": names["sps"], "channels": {}}
    for wave, (complex_attr, float_attr, decoder_attr) in ATTRIBUTES.items():
        channel = assigned[complex_attr]
        post = assigned[float_attr]
        receiver = assigned[decoder_attr]
        result["channels"][wave] = {
            "offset_hz": number(channel.args[2], names), "decimation": number(channel.args[0], names),
            "cutoff_hz": number(channel.args[1].args[2], names),
            "transition_hz": number(channel.args[1].args[3], names),
            "post_cutoff_hz": number(post.args[1].args[2], names),
            "post_transition_hz": number(post.args[1].args[3], names),
            "threshold": number(receiver.args[0], names),
        }
    second = result["channels"]["jam2"]
    result["channels"]["jam3"] = {**second, "offset_hz": WAVES["jam3"][side] - lo,
        "cutoff_hz": 125000, "post_cutoff_hz": 125000}
    return result


def adapted_tree(side, sps=None, uri=None, jam3=False):
    path, tree = receiver_tree(side)
    config = source_configuration(side)
    counts = {"decoders": 0, "source": 0, "jam3": 0}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if sps is not None and any(isinstance(t, ast.Name) and t.id == "sps" for t in node.targets):
                node.value = ast.Constant(sps)
            if jam3 and isinstance(node.value, ast.Call):
                targets = [t.attr for t in node.targets if isinstance(t, ast.Attribute)]
                if ATTRIBUTES["jam2"][0] in targets:
                    node.value.args[1].args[2] = ast.Constant(125000)
                    node.value.args[2] = ast.Constant(config["channels"]["jam3"]["offset_hz"])
                    counts["jam3"] += 1
                if ATTRIBUTES["jam2"][1] in targets:
                    node.value.args[1].args[2] = ast.Constant(125000)
                    counts["jam3"] += 1
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name in ("gfsk_receiver", "gfsk_receiver_noise"):
                if sps is not None:
                    node.args[1] = ast.Constant(sps)
                    node.args[0] = ast.Constant(number(node.args[0], {}) * sps / config["sps"])
                counts["decoders"] += 1
            elif name == "fmcomms2_source_fc32":
                if uri is not None:
                    node.args[0] = ast.Constant(uri)
                counts["source"] += 1
            elif name == "pub_sink":
                # Six shared arguments cover GNU Radio 3.10.1 and newer.
                node.args = node.args[:6]
    if counts["decoders"] != 3 or counts["source"] != 1 or (jam3 and counts["jam3"] != 2):
        raise ValueError("project flowgraph structure changed; review the small adapter before launching")
    return path, ast.fix_missing_locations(tree)
