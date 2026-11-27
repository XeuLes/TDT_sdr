"""Dashboard-owned GNU Radio and ROS 2 receive processes."""

import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Lock
import time


PROJECT = Path(__file__).resolve().parents[1]
RADIO_RECEIVE_COMMAND = ("ros2", "run", "radio_recive", "radio_recive_node")


class RadioReceiveProcess:
    def __init__(self):
        self.child = None
        self.error = None

    def __enter__(self):
        command = list(RADIO_RECEIVE_COMMAND)
        overlay = PROJECT / "install" / "setup.bash"
        if os.name == "posix" and overlay.is_file():
            shell = 'source "$1" || exit $?\nshift\nexec "$@"'
            command = ["bash", "-c", shell, "rm-sdr-radio-receive", str(overlay), *command]
        try:
            self.child = subprocess.Popen(command, cwd=PROJECT, start_new_session=(os.name == "posix"))
        except OSError as exc:
            self.error = f"radio_recive_node 启动失败：{exc}"
            print(self.error, file=sys.stderr, flush=True)
        return self

    def snapshot(self):
        child = self.child
        if child is None:
            return {"status": "error" if self.error else "stopped", "error": self.error}
        code = child.poll()
        return {"status": "running" if code is None else "error", "pid": child.pid,
                "error": None if code is None else f"radio_recive_node 已退出（{code}），请查看启动终端"}

    def __exit__(self, *exc):
        child = self.child
        if child is None or child.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(child.pid, signal.SIGINT)
            else:
                child.terminate()
            child.wait(timeout=4)
        except ProcessLookupError:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                child.kill()
            child.wait(timeout=2)


class ReceiverController:
    def __init__(self, state, python=None):
        self.state = state
        self.command = [python or sys.executable, "-u", "-m", "dashboard.flowgraph"]
        self.lock = Lock()
        self.child = None
        self.side = None
        self.closed = False

    def __enter__(self):
        self.state.set_receiver(None, "idle")
        return self

    def __exit__(self, *exc):
        with self.lock:
            self.closed = True
            self._stop()

    def _stop(self):
        if self.child is None:
            return
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=2)
        self.child = None

    def _start(self, side):
        self.child = subprocess.Popen([*self.command, side], cwd=PROJECT, start_new_session=True)
        self.side = side
        self.state.set_receiver(side, "starting", self.child.pid, reset=True)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            code = self.child.poll()
            if code is not None:
                raise RuntimeError(f"接收流图退出（{code}），请查看启动终端")
            if self.state.has_live_iq(self.child.pid, side):
                self.state.set_receiver(side, "running", self.child.pid)
                return
            time.sleep(0.05)
        raise RuntimeError("接收流图未送出宽带 IQ，请检查 E200 连接和启动终端")

    def switch(self, side):
        if side not in ("red", "blue"):
            raise ValueError("side must be red or blue")
        with self.lock:
            if self.closed:
                raise RuntimeError("接收台正在关闭")
            previous = self.side if self.child and self.child.poll() is None else None
            if previous == side and self.state.has_live_iq(self.child.pid, side):
                return side
            self.state.set_receiver(side, "starting", reset=True)
            try:
                self._stop()
                self._start(side)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                error = str(exc)
                restored = False
                try:
                    self._stop()
                    if previous and previous != side:
                        self._start(previous)
                        restored = True
                except (OSError, RuntimeError, subprocess.SubprocessError) as restore_error:
                    error += f" · 清理或恢复失败 · {restore_error}"
                    try:
                        self._stop()
                    except (OSError, subprocess.SubprocessError) as stop_error:
                        error += f" · 停止接收失败 · {stop_error}"
                if restored:
                    self.state.set_receiver(previous, "running", self.child.pid,
                                            error=f"切换失败，已恢复原阵营 · {error}")
                else:
                    self.state.set_receiver(side, "error", error=error, reset=True)
                raise RuntimeError(error) from exc
            return side
