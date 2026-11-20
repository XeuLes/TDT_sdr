"""Known capture presets; only CRC-matched files have automatic TX settings."""

import hashlib
import json
from pathlib import Path
import sys

CATALOG = Path(__file__).resolve().parents[1] / "config" / "recordings.json"


def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def recording_directory(override=None):
    if override is not None:
        return Path(override).expanduser().resolve()
    key = "directory_windows" if sys.platform == "win32" else "directory_linux"
    return (CATALOG.parent / catalog()[key]).resolve()


def get_recording(recording, directory=None, require_usable=True):
    entries = {row["id"]: row for row in catalog()["recordings"]}
    if recording not in entries:
        raise ValueError("unknown recording; use --list")
    row = dict(entries[recording])
    if require_usable and not row["usable"]:
        raise ValueError("this recording has no validated playback preset; choose one of the five usable entries")
    row["path"] = str(recording_directory(directory) / row["filename"])
    if row["usable"]:
        if row["playback_sample_rate_hz"] != 2000000 or row["receiver_sps"] != 52:
            raise ValueError("capture preset must retain its validated 2 MSa/s / SPS 52 interpretation")
        if row["tx_lo_hz"] + row["iq_offset_hz"] != row["target_rf_hz"]:
            raise ValueError("capture TX LO plus baseband offset must equal the selected RF destination")
    return row


def check_recording(row):
    path = Path(row["path"])
    if not path.is_file():
        raise ValueError(f"recording not found: {path}; use --recording-dir")
    if path.stat().st_size != row["complex_samples"] * 8:
        raise ValueError("file length differs from the validated recording")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != row["sha256"]:
        raise ValueError("file SHA-256 differs from the validated recording")
    return path


def list_recordings(directory=None):
    return [get_recording(row["id"], directory, require_usable=False) for row in catalog()["recordings"]]
