"""Regression tests for LabTX ownership, preview and startup rollback."""

from contextlib import ExitStack
import io
import os
from pathlib import Path
from threading import RLock
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from labtx import __main__ as lab

from apps.lab_tx import radio_tx_dashboard as web

INFO_URI = "ip:192.168.1.101"
JAM_URI = "ip:192.168.1.102"
RX_URI = "ip:192.168.1.11"
DEVICES = dict(zip(lab.ROLES, (INFO_URI, JAM_URI)))


class VerifyDevicesTest(unittest.TestCase):
    def test_same_board_different_address_is_refused(self):
        probe = Mock(side_effect=lambda uri, **_: "serial:duplicate")
        with self.assertRaisesRegex(RuntimeError, "独立序列号"):
            lab.verify_devices(DEVICES, DEVICES, RX_URI, probe=probe)

    def test_second_wave_cannot_alias_already_running_wave(self):
        with self.assertRaisesRegex(ValueError, "相同的物理设备"):
            lab.verify_devices(
                DEVICES, {"interference_tx": JAM_URI}, RX_URI,
                probe=Mock(return_value="serial:existing"),
                known_roles={"broadcast_tx": "serial:existing"},
            )

    def test_exact_and_alias_rx_addresses_are_rejected(self):
        configured = {**DEVICES, "broadcast_tx": RX_URI}
        probe = Mock(return_value="serial:receiver")
        with self.assertRaisesRegex(ValueError, "接收地址"):
            lab.verify_devices(configured, {"broadcast_tx": RX_URI}, RX_URI, probe=probe)
        probe.assert_not_called()
        with self.assertRaisesRegex(ValueError, "同一台硬件"):
            lab.verify_devices(DEVICES, {"broadcast_tx": INFO_URI}, RX_URI, probe=probe)

    def test_selected_uris_are_pinned_to_roles(self):
        probe = Mock()
        with self.assertRaisesRegex(ValueError, "配置不一致"):
            lab.verify_devices(DEVICES, {"broadcast_tx": JAM_URI}, RX_URI, probe=probe)
        probe.assert_not_called()

    def test_distinct_devices_can_be_selected(self):
        probe = Mock(side_effect=lambda uri, **_: f"serial:{uri}")
        self.assertEqual(
            lab.verify_devices(DEVICES, DEVICES, RX_URI, probe=probe),
            {"broadcast_tx": f"serial:{INFO_URI}", "interference_tx": f"serial:{JAM_URI}"},
        )


class PreviewTest(unittest.TestCase):
    def test_preview_blocks_decoded_paths_with_queries(self):
        with TemporaryDirectory(dir=lab.PACKAGE.parent) as directory:
            config_path = Path(directory) / "lab_tx.yaml"
            config_path.write_text(
                (lab.PACKAGE / "config.yaml").read_text(encoding="utf-8").replace(
                    "~/.local/state/rm-sdr/labtx", Path(directory).as_posix()
                ),
                encoding="utf-8",
            )
            delegated_get = Mock()
            delegated_post = Mock()
            acquire_server = MagicMock()
            hardware = Mock()
            transmit = MagicMock()

            def fake_server(_address, handler_cls):
                def run():
                    for method, path in (
                        ("GET", "/api/%63heck-sdr?detail=1"),
                        ("GET", "/api/check-four-sdr?detail=1"),
                        ("GET", "/api/tx-hardware-%73tatus?force=yes"),
                        ("POST", "/api/start-dual-tx?force=yes"),
                        ("POST", "/api/%63heck-sdr?force=yes"),
                        ("POST", "/api/tx-wave-stop?wave=broadcast"),
                    ):
                        with self.subTest(method=method, path=path):
                            handler = object.__new__(handler_cls)
                            handler.path = path
                            handler._send_json = Mock()
                            getattr(handler, "do_" + method)()
                            handler._send_json.assert_called_once()

                acquire_server.__enter__.return_value.serve_forever.side_effect = run
                return acquire_server

            with ExitStack() as stack:
                for target, name, replacement in (
                    (web, "RadioTxHttpServer", fake_server),
                    (web.RadioTxHandler, "do_GET", delegated_get),
                    (web.RadioTxHandler, "do_POST", delegated_post),
                    (web, "GENERATED_ROOT", web.GENERATED_ROOT),
                    (lab, "probe_device", hardware),
                    (lab, "TxLeases", transmit),
                    (lab.signal, "signal", Mock()),
                    (lab.sys, "stdout", io.StringIO()),
                ):
                    stack.enter_context(patch.object(target, name, replacement))
                stack.enter_context(patch.dict(os.environ, {}, clear=False))
                self.assertEqual(lab.main(["--preview", "--port", "0", "--config", str(config_path)]), 0)
            self.assertTrue(acquire_server.__enter__.return_value.serve_forever.called)
            delegated_get.assert_not_called()
            delegated_post.assert_not_called()
            hardware.assert_not_called()
            transmit.return_value.__enter__.return_value.acquire.assert_not_called()


class DualStartTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(TemporaryDirectory(dir=lab.PACKAGE.parent))
        self.stack.enter_context(patch.dict(os.environ, {
            "BROADCAST_TX_URI": INFO_URI, "INTERFERENCE_TX_URI": JAM_URI,
            "BROADCAST_TX_ATTENUATION_DB": "61", "INTERFERENCE_TX_ATTEND_GR": "63",
        }))
        self.stack.enter_context(patch.object(web, "LOG_ROOT", Path(directory)))
        self.stack.enter_context(patch.object(web, "_assert_tx_start_allowed"))
        self.state = web.default_state()
        self.state["tx"]["lab_confirm"] = True
        self.server = SimpleNamespace(
            tx_lock=RLock(),
            dual_tx_process=None,
            authorize_devices=Mock(return_value=(42, 43)),
            lab_tx_effective_confirm=Mock(return_value=True),
        )

    def test_partial_preinitialization_mutes_both_devices(self):
        cleanup = Mock(return_value={"requested_pids": []})
        mute = Mock(return_value={"ok": False, "required": True})
        with (
            patch.object(web, "_preinit_role_tx", side_effect=[{}, RuntimeError("device offline")]),
            patch.object(web, "_cleanup_failed_tx_start", cleanup),
            patch.object(web, "_mute_and_verify_tx_hardware", mute),
        ):
            with self.assertRaises(web.TxSafetyError) as caught:
                web.start_dual_tx(self.server, self.state)
        self.server.authorize_devices.assert_called_once_with(DEVICES)
        cleanup.assert_called_once_with(None, [])
        mute.assert_called_once_with([INFO_URI, JAM_URI])
        self.assertFalse(caught.exception.safety["hardware_mute"]["ok"])
        self.assertIsNone(self.server.dual_tx_process)

    def test_pair_uses_independent_settings_and_inherited_leases(self):
        proc = Mock(pid=100)
        with (
            patch.object(web, "_preinit_role_tx", return_value={}) as prepare,
            patch.object(web, "_verify_dual_managed_process_start") as verify,
            patch.object(web, "_require_tx_node_started", return_value={}),
            patch.object(web.subprocess, "Popen", return_value=proc) as launch,
        ):
            result = web.start_dual_tx(self.server, self.state)
        self.assertTrue(result["已启动"])
        self.assertEqual(verify.call_count, 2)
        self.assertIs(self.server.dual_tx_process, proc)
        self.assertEqual(launch.call_args.kwargs["pass_fds"], (42, 43))
        self.assertEqual(
            [(call.args[1]["uri"], call.args[1]["attenuation_db"]) for call in prepare.call_args_list],
            [(INFO_URI, 61.0), (JAM_URI, 63.0)],
        )


class WorkerExitTest(unittest.TestCase):
    def test_late_dual_exit_invokes_cleanup_and_reports_unverified_mute(self):
        server = SimpleNamespace(
            dual_tx_process=Mock(), tx_process=None,
            wave_processes={"broadcast": None, "interference": None},
        )
        server.dual_tx_process.poll.return_value = 3
        mute = {"ok": False, "required": True}
        with patch.object(web, "stop_dual_tx", return_value={"hardware_mute": mute}) as stop:
            web.reap_exited_transmitters(server)
        stop.assert_called_once_with(server)
        self.assertEqual(server.last_mute, mute)


if __name__ == "__main__":
    unittest.main()

