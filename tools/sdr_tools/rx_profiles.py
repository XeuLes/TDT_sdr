"""Candidate receive configurations; none asserts a recording's provenance."""

from sdr_tools.recordings import RX_PROFILES


RECEIVE_PROFILES = {
    "rm-sdr": {"label": "RM_sdr raw IQ / SPS 47", "sample_rate": 2_000_000,
               "decimation": 2, "sps": 47},
    "rm-sdr-52": {"label": "RM_sdr raw IQ / SPS 52 timing candidate", "sample_rate": 2_000_000,
                  "decimation": 2, "sps": 52},
    "whu-raw": {"label": "WHU raw IQ / 3 MSa/s", "sample_rate": 3_000_000,
                "decimation": 3, "sps": 52, "channel_width": 950_000,
                "transition_width": 40_000,
                "offsets": {"red": {"info": 500_000, "key1": -500_000, "key2": -200_000},
                            "blue": {"info": -500_000, "key1": 500_000, "key2": 200_000}}},
    "info-centered": {"label": "Centered information IQ / 1 MSa/s", "sample_rate": 1_000_000,
                      "decimation": 1, "sps": 52, "channel_width": 950_000,
                      "transition_width": 40_000, "only": "info",
                      "offsets": {"red": {"info": 0}, "blue": {"info": 0}}},
    "whu-raw-47": {"label": "WHU raw layout / 2026 V2.2 SPS 47", "sample_rate": 3_000_000,
                   "decimation": 3, "sps": 47, "channel_width": 950_000,
                   "transition_width": 40_000,
                   "offsets": {"red": {"info": 500_000, "key1": -500_000, "key2": -200_000},
                               "blue": {"info": -500_000, "key1": 500_000, "key2": 200_000}}},
    "info-centered-47": {"label": "Centered information IQ / 2026 V2.2 SPS 47", "sample_rate": 1_000_000,
                         "decimation": 1, "sps": 47, "channel_width": 950_000,
                         "transition_width": 40_000, "only": "info",
                         "offsets": {"red": {"info": 0}, "blue": {"info": 0}}},
}


# This recording test uses the user-reported 47 us bit duration. Each default
# reaches 1 MSa/s at the symbol decoder, so SPS is 47 in all three layouts.
DEFAULT_TEST_PROFILES = ("rm-sdr", "whu-raw-47", "info-centered-47")


def apply_receive_profile(args, side):
    """Fill omitted options; explicit CLI settings retain priority."""
    profile = RECEIVE_PROFILES.get(args.profile, {})
    for name in ("sample_rate", "decimation", "sps", "channel_width", "transition_width", "only"):
        if getattr(args, name) is None and name in profile:
            setattr(args, name, profile[name])
    if args.decimation is None:
        args.decimation = 2
    if args.sps is None:
        args.sps = 47
    if args.only is None:
        args.only = "all"
    if profile.get("only") == "info" and args.only != "info":
        raise ValueError("the centered information profile contains one channel; use --only info")
    offsets = profile.get("offsets", {}).get(side, RX_PROFILES[side])
    for name in ("info", "key1", "key2"):
        option = name + "_offset"
        if getattr(args, option) is None:
            setattr(args, option, offsets.get(name, RX_PROFILES[side][name]))
