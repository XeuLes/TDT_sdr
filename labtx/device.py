"""LabTX device identification and Linux session leases."""

from contextlib import ExitStack
import hashlib
from pathlib import Path
import re
import subprocess


def probe_device(uri, *, required=True):
    try:
        result = subprocess.run(
            ["iio_info", "-u", uri], capture_output=True, text=True, errors="replace",
            timeout=3, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if required:
            raise RuntimeError(f"Cannot open IIO context {uri}: {exc}") from exc
        return None
    match = re.search(r"^\s*(?:hw_serial|hw-serial|serial):\s*(\S+)", result.stdout, re.MULTILINE | re.IGNORECASE)
    if match and match[1].lower().strip("0") not in ("", "unknown", "none"):
        return f"serial:{match[1].lower()}"
    if uri.startswith("ip:"):
        # For boards exposing no hardware serial, the neighbour MAC identifies
        # aliases on the bench LAN. Addresses across a router are not evidence.
        try:
            result = subprocess.run(
                ["ip", "neigh", "show", "to", uri[3:]], capture_output=True, text=True,
                timeout=1, check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        match = re.search(r"\blladdr ([a-f0-9:]+)\b", result.stdout, re.IGNORECASE)
        if match:
            return f"mac:{match[1].lower()}"
    return None


class TxLeases:
    def __init__(self):
        self._stack = ExitStack()
        self._handles = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._stack.close()
        self._handles.clear()

    @property
    def fds(self):
        return tuple(handle.fileno() for handle in self._handles.values())

    def acquire(self, *keys):
        import fcntl
        import os

        root = Path("/tmp") / f"rm-sdr-labtx-{os.getuid()}"
        root.mkdir(mode=0o700, exist_ok=True)
        with ExitStack() as pending:
            acquired = {}
            for key in sorted(set(keys) - set(self._handles)):
                digest = hashlib.sha256(key.encode()).hexdigest()[:32]
                handle = pending.enter_context((root / digest).open("a+b"))
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError(f"TX device is reserved by another LabTX session: {key}") from exc
                acquired[key] = handle
            self._stack.enter_context(pending.pop_all())
            self._handles.update(acquired)


