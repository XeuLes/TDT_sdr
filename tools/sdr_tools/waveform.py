"""Timed official 2-GFSK IQ. GNU Radio supplies the Gaussian filter taps."""

from .profiles import AMPLITUDE, BT, PERIOD_SAMPLES, PROJECT_LO, SAMPLE_RATE, TICK_SAMPLES, TX_SPS, WAVES, select_waves
from .protocol import scheduled_packets


def packet_schedule(groups):
    packet_samples = 27 * 8 * TX_SPS
    def arrange(initial):
        finish = initial
        result = []
        for tick, group in enumerate(groups):
            finish = max(finish, tick * TICK_SAMPLES)
            for packet in group:
                result.append((finish, packet))
                finish += packet_samples
        return result, finish
    _, finish = arrange(0)
    # Account for the preceding period's final packet spilling past time zero.
    result, _ = arrange(max(0, finish - PERIOD_SAMPLES))
    return result


def timed_symbols(groups):
    import numpy as np
    nrz = np.zeros(PERIOD_SAMPLES, dtype=np.float64)
    gate = np.zeros(PERIOD_SAMPLES, dtype=np.float64)
    for start, packet in packet_schedule(groups):
        bits = np.unpackbits(np.frombuffer(packet, dtype=np.uint8)).astype(float)
        values = np.repeat(bits * 2 - 1, TX_SPS)
        split = min(len(values), PERIOD_SAMPLES - start)
        nrz[start:start + split] = values[:split]
        gate[start:start + split] = 1
        if split < len(values):
            nrz[:len(values) - split] = values[split:]
            gate[:len(values) - split] = 1
    # Start/end the repeating IQ period inside the common RF-off interval.
    rotation = 192000
    nrz, gate = np.roll(nrz, -rotation), np.roll(gate, -rotation)
    if gate[0] or gate[-1]:
        raise ValueError("waveform period boundary must be inside RF silence")
    return nrz, gate


def official_iq(payloads, side, waves, taps=None):
    import numpy as np
    if taps is None:
        from gnuradio.filter import firdes
        taps = firdes.gaussian(1.0, TX_SPS, BT, 4 * TX_SPS)
    taps = np.asarray(taps, dtype=np.float64)
    if len(taps) != 4 * TX_SPS or not np.all(np.isfinite(taps)) or abs(taps.sum() - 1) > 1e-5:
        raise ValueError("expected normalized GNU Radio four-symbol Gaussian taps")
    selected = select_waves(waves)
    strongest = max(WAVES[w]["power_dbm"] for w in selected)
    result = np.zeros(PERIOD_SAMPLES, dtype=np.complex128)
    time = np.arange(PERIOD_SAMPLES, dtype=np.float64) / SAMPLE_RATE
    response = np.fft.fft(taps, PERIOD_SAMPLES)
    for wave in selected:
        nrz, gate = timed_symbols(scheduled_packets(payloads, wave))
        filtered = np.fft.ifft(np.fft.fft(nrz) * response).real
        filtered = np.roll(filtered, -(len(taps) // 2))
        sensitivity = WAVES[wave]["sensitivity"] / 2
        phase = np.cumsum(filtered) * sensitivity
        phase += 2 * np.pi * (WAVES[wave][side] - PROJECT_LO[side]) * time
        amplitude = AMPLITUDE * 10 ** ((WAVES[wave]["power_dbm"] - strongest) / 20)
        result += amplitude * np.exp(1j * phase) * gate
    if not np.all(np.isfinite(result)) or np.max(np.abs(result)) > 1:
        raise ValueError("invalid or clipped IQ waveform")
    return result.astype(np.complex64)
