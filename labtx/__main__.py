#!/usr/bin/env python3
"""Run the SHARK LabTX console with RM_sdr device ownership."""

import argparse
from http import HTTPStatus
import os
from pathlib import Path
import signal
from subprocess import SubprocessError
import sys
from threading import Event, Thread
from urllib.parse import unquote, urlsplit
import uuid

PACKAGE = Path(__file__).resolve().parent
VENDOR = PACKAGE / "vendor" / "shark_labtx"
sys.path.insert(0, str(VENDOR))

from apps.common.read_yaml import load_simple_yaml
from .device import TxLeases, probe_device

ROLES = ("broadcast_tx", "interference_tx")


def verify_devices(configured, selection, receiver_uri, probe=probe_device, known_roles=None):
    if not selection or any(configured.get(role) != uri for role, uri in selection.items()):
        raise ValueError("TX URI 与本次 LabTX 配置不一致，请修改配置后重启")
    uris = tuple(selection.values())
    if receiver_uri in uris or len(set(uris)) != len(uris):
        raise ValueError("TX 需要两个独立发射设备，E200 接收地址不能用作 TX")
    identities = {role: probe(uri) for role, uri in selection.items()}
    known = {identity for identity in identities.values() if identity}
    if known_roles and any(identity in known for role, identity in known_roles.items() if role not in selection):
        raise ValueError("两路 TX URI 指向相同的物理设备")
    if len(uris) > 1 and len(known) != len(uris):
        raise RuntimeError("双路发射需要确认两台设备的独立序列号或网卡地址")
    rx_identity = probe(receiver_uri, required=False)
    if rx_identity in known:
        raise ValueError("检测到发射地址与 E200 接收设备指向同一台硬件")
    return identities


def configure_environment(config, config_path):
    devices, tx = config["devices"], config["tx"]
    log_root = Path(config["app"]["log_root"]).expanduser() / uuid.uuid4().hex[:12]
    os.environ.update(
        RM_RADIO_WS=str(VENDOR), LAB_TX_CONFIG=str(config_path),
        RM_RADIO_LOG_ROOT=str(log_root), RM_RADIO_DASHBOARD_LOG_ROOT=str(log_root),
        RMSDR_LABTX_SESSION=log_root.name, RM_RADIO_LAB_TX_ANTENNA_CONFIRM="false",
        RM_RADIO_IIO_PREINIT="true", RM_RADIO_KILL_RESIDUAL="false",
        RM_RADIO_ALLOW_RESIDUAL="false", TX_URI="", RM_RADIO_INTERFERENCE_TX_URI="",
        BROADCAST_TX_URI=str(devices["broadcast_tx_uri"]).strip(),
        INTERFERENCE_TX_URI=str(devices["interference_tx_uri"]).strip(),
        BROADCAST_TX_RF_PORT=str(devices["broadcast_tx_rf_port"]),
        INTERFERENCE_TX_RF_PORT=str(devices["interference_tx_rf_port"]),
        BROADCAST_TX_ATTENUATION_DB=str(tx["broadcast_attenuation_db"]),
        INTERFERENCE_TX_ATTEND_GR=str(tx["interference_attenuation_db"]),
        INTERFERENCE_PASSWORD=str(tx["interference_password"]),
        RM_RADIO_TX_BUFFER_SIZE=str(tx["buffer_size"]),
        RM_RADIO_TX_PREFILL_PACKETS=str(tx["prefill_packets"]),
        RM_RADIO_SIDE=str(config["radio"]["side"]),
        INTERFERENCE_LEVEL=str(config["radio"]["interference_level"]),
    )
    os.environ.setdefault("ROS_DOMAIN_ID", "78")
    return log_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PACKAGE / "config.yaml")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--preview", action="store_true", help="preview and edit plans without opening SDRs")
    parser.add_argument("--describe", action="store_true", help="print configuration without starting a server")
    args = parser.parse_args(argv)
    try:
        config = load_simple_yaml(args.config)
        log_root = configure_environment(config, args.config.resolve())
        uris = dict(zip(ROLES, (os.environ["BROADCAST_TX_URI"], os.environ["INTERFERENCE_TX_URI"])))
        receiver = str(config["devices"]["rx1_uri"]).strip()
        if not receiver or not all(uris.values()) or len(set(uris.values())) != 2 or receiver in uris.values():
            raise ValueError("请配置两个不同的 TX URI，并与 RX URI 区分")
        if args.describe:
            print(f"LabTX http://127.0.0.1:{args.port} | TX {uris} | RX {receiver}")
            return 0
        if not args.preview:
            from apps.common.check_gnuradio_runtime import audit_runtime, validate

            errors = validate(audit_runtime())
            if errors:
                raise RuntimeError("\n".join(errors))
        from apps.lab_tx import radio_tx_dashboard as web

        class Handler(web.RadioTxHandler):
            def _send_json(self, payload, status=HTTPStatus.OK):
                if isinstance(payload, dict):
                    mute = payload.get("hardware_mute") or (payload.get("safety") or {}).get("hardware_mute")
                    if mute and mute.get("required"):
                        self.server.last_mute = mute
                    if "server" in payload:
                        payload["server"]["last_mute"] = self.server.last_mute
                    if payload.get("已启动") is True:
                        self.server.last_mute = None
                super()._send_json(payload, status)

            def do_GET(self):
                path = unquote(urlsplit(self.path).path)
                if path == "/api/calibration":
                    self._send_json(config["calibration"])
                    return
                if args.preview and path.startswith("/api/check"):
                    self._send_json({"error": "预览模式未连接设备"}, HTTPStatus.FORBIDDEN)
                    return
                if args.preview and path == "/api/tx-hardware-status":
                    self._send_json({"results": []})
                    return
                super().do_GET()

            def do_POST(self):
                if args.preview and unquote(urlsplit(self.path).path) not in (
                    "/api/validate", "/api/generate", "/api/randomize-broadcast",
                    "/api/randomize-password", "/api/lab-confirm",
                ):
                    self._send_json({"error": "预览模式未连接设备"}, HTTPStatus.FORBIDDEN)
                    return
                super().do_POST()

        log_root.mkdir(parents=True, exist_ok=True)
        web.GENERATED_ROOT = log_root / "generated"
        with TxLeases() as leases, web.RadioTxHttpServer(("127.0.0.1", args.port), Handler) as server:
            server.last_mute = None
            closing = Event()
            known_roles = {}

            def authorize(selection):
                if args.preview or closing.is_set():
                    raise PermissionError("当前会话不可启动射频发射")
                # Hold serial/MAC aliases as soon as the device is first used.
                identities = verify_devices(uris, selection, receiver, known_roles=known_roles)
                leases.acquire(*(identity for identity in identities.values() if identity))
                known_roles.update(identities)
                return leases.fds

            def check_exited_workers():
                while not closing.wait(0.4):
                    with server.tx_lock:
                        web.reap_exited_transmitters(server)

            server.authorize_devices = authorize
            watchdog = None
            if not args.preview:
                leases.acquire(*uris.values())
                for role, uri in uris.items():
                    identity = probe_device(uri, required=False)
                    if identity:
                        leases.acquire(identity)
                        known_roles[role] = identity
                watchdog = Thread(target=check_exited_workers, daemon=True, name="labtx-exit-watchdog")
                watchdog.start()

            def stop(_signum, _frame):
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, stop)
            print(f"LabTX http://127.0.0.1:{server.server_port}/ | 日志 {log_root}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                closing.set()
                if watchdog is not None:
                    watchdog.join()
                if not args.preview:
                    with server.tx_lock:
                        for child, cleanup in web.managed_transmitters(server):
                            if child is not None:
                                web.cleanup_transmitter(server, cleanup)
                        if server.last_mute and not server.last_mute.get("ok"):
                            print(f"LabTX 静音未确认: {server.last_mute}", file=sys.stderr)
        return 0
    except (OSError, ImportError, KeyError, TypeError, ValueError, RuntimeError, SubprocessError) as exc:
        print(f"LabTX: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

