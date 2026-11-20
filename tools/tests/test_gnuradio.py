"""Optional real GNU Radio/OOT software loopbacks; never opens SDR hardware."""

import contextlib
import importlib.util
import io
from pathlib import Path
import unittest

try:
    from gnuradio import robomaster
    AVAILABLE = hasattr(robomaster, "gfsk_receiver") and hasattr(robomaster, "gfsk_receiver_noise")
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "Linux GNU Radio + gr-robomaster required")
class GNUradioLoopbackTests(unittest.TestCase):
    def test_all_eight_official_side_wave_choices(self):
        path = Path(__file__).resolve().parents[1] / "test" / "loopback.py"
        spec = importlib.util.spec_from_file_location("real_loopback", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for side in ("red", "blue"):
            for wave in ("info", "jam1", "jam2", "jam3"):
                with self.subTest(side=side, wave=wave):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        code = module.main(["--side", side, "--wave", wave])
                    self.assertEqual(code, 0, output.getvalue())


if __name__ == "__main__":
    unittest.main()
