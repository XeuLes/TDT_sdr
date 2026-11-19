"""Headerless little-endian complex float32 recording inspection and selection.

Raw IQ contains no sample rate or RF tuning metadata. Candidate RX profiles
are deliberately separate from the recording catalog's unknown parameters.
Inspection requires only the standard library; optional NumPy accelerates
scans and supplies spectrum measurements.
"""

from array import array
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys


TOOLS = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = TOOLS / "recordings.json"
RX_PROFILES = {
    "red": {"candidate_sample_rate_hz": 2_000_000, "candidate_center_frequency_hz": 432_600_000,
            "info": 600_000, "key1": -400_000, "key2": -100_000},
    "blue": {"candidate_sample_rate_hz": 2_000_000, "candidate_center_frequency_hz": 434_520_000,
             "info": -600_000, "key1": 400_000, "key2": 100_000},
}


def load_catalog(path=DEFAULT_CATALOG):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != "cf32_le" or not isinstance(data.get("recordings"), list):
        raise ValueError("catalog must declare format cf32_le and a recordings list")
    entries = {}
    for item in data["recordings"]:
        name = item["id"]
        if name in entries:
            raise ValueError(f"duplicate recording id: {name}")
        entries[name] = item
    return path, entries


def add_recording_arguments(parser):
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--file", type=Path, help="raw little-endian complex64 IQ file")
    source.add_argument("--recording", help="catalog id, e.g. red, blue, red-ganrao-1")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--recording-dir", type=Path, help="override catalog recording directory")
    parser.add_argument("--list", action="store_true", help="list catalog entries without opening SDR")
    parser.add_argument("--sample-rate", type=int, help="recording sample rate in complex samples/s")
    parser.add_argument("--start-sample", type=int, default=0, help="first complex sample, not byte offset")
    parser.add_argument("--samples", type=int, default=0, help="segment length; 0 selects to EOF")


def resolve_recording(args):
    if args.file is not None:
        return args.file.expanduser().resolve(), None
    if not args.recording:
        raise ValueError("choose --recording ID or --file PATH (or use --list)")
    catalog, entries = load_catalog(args.catalog)
    if args.recording not in entries:
        raise ValueError(f"unknown recording {args.recording!r}; choose {', '.join(entries)}")
    item = entries[args.recording]
    directory = catalog_directory(args, catalog)
    return directory / item["filename"], item


def catalog_directory(args, catalog):
    if args.recording_dir is not None:
        return args.recording_dir.expanduser().resolve()
    data = json.loads(catalog.read_text(encoding="utf-8"))
    directory = data["recording_directory"]
    if sys.platform == "win32":
        directory = data.get("recording_directory_windows", directory)
    return (catalog.parent / Path(directory).expanduser()).resolve()


def catalog_listing(args):
    catalog, entries = load_catalog(args.catalog)
    directory = catalog_directory(args, catalog)
    return [{**item, "path": str(directory / item["filename"]),
             "exists": (directory / item["filename"]).is_file()} for item in entries.values()]


@dataclass(frozen=True)
class SampleWindow:
    start: int
    count: int


def recording_window(path, start=0, count=0):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"recording not found: {path}; use --recording-dir or --file")
    size = path.stat().st_size
    if size == 0 or size % 8:
        raise ValueError("cf32_le file must be nonempty and its byte length divisible by 8")
    total = size // 8
    if start < 0 or start >= total or count < 0:
        raise ValueError("sample start/length is negative or starts beyond EOF")
    if count == 0:
        count = total - start
    if start + count > total:
        raise ValueError("requested segment exceeds EOF")
    return SampleWindow(start, count)


def read_iq(path, start, count):
    with Path(path).open("rb") as stream:
        stream.seek(start * 8)
        data = stream.read(count * 8)
    if len(data) != count * 8:
        raise ValueError("recording changed or was truncated while reading")
    values = array("f")
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def inspect_recording(path, sample_rate=None, start=0, count=0, full_scan=False, block_samples=65536):
    if sample_rate is not None and (not math.isfinite(sample_rate) or sample_rate <= 0):
        raise ValueError("sample rate must be positive")
    if block_samples < 1:
        raise ValueError("inspection block size must be positive")
    path = Path(path).resolve()
    segment = recording_window(path, start, count)
    if full_scan or segment.count <= 3 * block_samples:
        ranges = [(segment.start + offset, min(block_samples, segment.count - offset))
                  for offset in range(0, segment.count, block_samples)]
        coverage = "full_segment"
    else:
        ranges = [(segment.start + offset, block_samples) for offset in
                  (0, (segment.count - block_samples) // 2, segment.count - block_samples)]
        coverage = "head_middle_tail"
    # GNU Radio installations already provide NumPy. Use bounded-size vectors
    # for full scans when available, with the same standard-library fallback.
    try:
        import numpy as np
    except ImportError:
        np = None
    examined = nonfinite = clipped = 0
    sum_i = sum_q = sum_power = peak = peak_component = 0.0
    for offset, length in ranges:
        values = read_iq(path, offset, length)
        if np is not None:
            pairs = np.asarray(values, dtype=np.float64).reshape(-1, 2)
            valid = np.all(np.isfinite(pairs), axis=1)
            examined += length
            nonfinite += int(length - np.count_nonzero(valid))
            pairs = pairs[valid]
            if len(pairs):
                ii, qq = pairs[:, 0], pairs[:, 1]
                sum_i += float(np.sum(ii))
                sum_q += float(np.sum(qq))
                sum_power += float(np.sum(ii * ii + qq * qq))
                peak = max(peak, float(np.max(np.hypot(ii, qq))))
                peak_component = max(peak_component, float(np.max(np.abs(pairs))))
                clipped += int(np.count_nonzero(np.any(np.abs(pairs) >= 0.98, axis=1)))
            continue
        for i, q in zip(values[::2], values[1::2]):
            examined += 1
            if not math.isfinite(i) or not math.isfinite(q):
                nonfinite += 1
                continue
            power = i * i + q * q
            sum_i += i
            sum_q += q
            sum_power += power
            peak = max(peak, math.sqrt(power))
            peak_component = max(peak_component, abs(i), abs(q))
            clipped += abs(i) >= 0.98 or abs(q) >= 0.98
    finite = examined - nonfinite
    return {
        "file": str(path), "format": "cf32_le", "format_status": "raw_IQ_interpretation",
        "bytes": path.stat().st_size, "complex_samples": path.stat().st_size // 8,
        "start_sample": segment.start, "segment_samples": segment.count,
        "sample_rate_hz": sample_rate,
        "segment_seconds": segment.count / sample_rate if sample_rate else None,
        "inspection_coverage": coverage, "examined_samples": examined,
        "nonfinite_samples": nonfinite, "peak_magnitude": peak,
        "peak_component": peak_component,
        "rms_magnitude": math.sqrt(sum_power / finite) if finite else None,
        "mean_i": sum_i / finite if finite else None,
        "mean_q": sum_q / finite if finite else None,
        "clipped_percent": 100 * clipped / finite if finite else None,
        "parameter_note": "sample rate is caller-supplied; raw IQ stores no sample rate or RF center",
    }


def spectrum_peaks(path, sample_rate, start=0, count=0, fft_size=8192, frames=12):
    """Welch-like sampled power spectrum; needs NumPy, never opens hardware."""
    import numpy as np

    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("spectrum needs a positive sample rate")
    segment = recording_window(path, start, count)
    n = min(fft_size, segment.count)
    if n < 16:
        raise ValueError("spectrum needs at least 16 complex samples")
    window = np.hanning(n)
    power = np.zeros(n, dtype=np.float64)
    for relative in np.linspace(0, segment.count - n, frames, dtype=np.int64):
        values = read_iq(path, segment.start + int(relative), n)
        data = np.asarray(values, dtype=np.float32).view(np.complex64)
        if not np.all(np.isfinite(data)):
            raise ValueError("nonfinite IQ in spectrum inspection")
        power += np.abs(np.fft.fftshift(np.fft.fft(data * window))) ** 2
    offsets = np.fft.fftshift(np.fft.fftfreq(n, 1 / sample_rate))
    # Suppress adjacent bins so one carrier does not occupy the whole result.
    remaining = power.copy()
    peaks = []
    for _ in range(6):
        k = int(np.argmax(remaining))
        if remaining[k] <= 0:
            break
        peaks.append({"offset_hz": float(offsets[k]),
                      "fraction_of_sample_rate": float(offsets[k] / sample_rate)})
        remaining[np.abs(offsets - offsets[k]) <= sample_rate / 100] = 0
    return {"assumed_sample_rate_hz": sample_rate, "fft_size": n, "frames": frames,
            "peaks": peaks, "note": "peaks are spectral energy, not identified RM channels"}
