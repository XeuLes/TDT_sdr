"""Fixed RMUC 2026 V2.2.0 RF profile and communication protocol V2.0.0."""

SAMPLE_RATE = 2_000_000
REFERENCE_RATE = 1_000_000
REFERENCE_SPS = 47
TX_SPS = 94
BT = .35
AMPLITUDE = .2
PERIOD_SAMPLES = 600_000  # 300 ms: 140 * 3 bytes is divisible by 15.
TICK_SAMPLES = 200_000
PROJECT_LO = {"red": 432_600_000, "blue": 434_520_000}
WAVES = {
    "info": {"red": 433_200_000, "blue": 433_920_000, "bandwidth": 540_000,
             "sensitivity": 1.5628, "power_dbm": -60, "bytes_per_second": 1400},
    "jam1": {"red": 432_200_000, "blue": 434_920_000, "bandwidth": 940_000,
             "sensitivity": 2.8194, "power_dbm": -10, "bytes_per_second": 1350},
    "jam2": {"red": 432_500_000, "blue": 434_620_000, "bandwidth": 860_000,
             "sensitivity": 2.5681, "power_dbm": -10, "bytes_per_second": 1350},
    "jam3": {"red": 432_800_000, "blue": 434_320_000, "bandwidth": 250_000,
             "sensitivity": .6517, "power_dbm": -10, "bytes_per_second": 1350},
}


def select_waves(waves):
    selected = list(dict.fromkeys(waves))
    if not selected or any(w not in WAVES for w in selected):
        raise ValueError("wave must be info, jam1, jam2 or jam3")
    if sum(w != "info" for w in selected) > 1:
        raise ValueError("select at most one interference level, optionally together with info")
    return sorted(selected, key=lambda w: list(WAVES).index(w))


def official_description(side, waves):
    selected = select_waves(waves)
    strongest = max(WAVES[w]["power_dbm"] for w in selected)
    return {"profile": "RMUC 2026 V2.2.0 / communication V2.0.0", "side": side,
            "sample_rate_hz": SAMPLE_RATE, "tx_sps": TX_SPS, "rx_sps": REFERENCE_SPS,
            "bit_duration_us": 47, "bt": BT, "gaussian_span_symbols": 4,
            "tx_lo_hz": PROJECT_LO[side], "period_seconds": .3,
            "waves": [{"wave": w, "rf_center_hz": WAVES[w][side],
                       "baseband_center_hz": WAVES[w][side] - PROJECT_LO[side],
                       "nominal_bandwidth_hz": WAVES[w]["bandwidth"],
                       "sensitivity_at_1m": WAVES[w]["sensitivity"],
                       "sensitivity_at_tx_rate": WAVES[w]["sensitivity"] / 2,
                       "target_power_dbm": WAVES[w]["power_dbm"],
                       "relative_amplitude": 10 ** ((WAVES[w]["power_dbm"] - strongest) / 20),
                       "application_bytes_per_second": WAVES[w]["bytes_per_second"]}
                      for w in selected]}
