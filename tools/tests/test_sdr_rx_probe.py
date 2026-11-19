import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rx"))
from sdr_rx_probe import measure_iq

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, "NumPy is required for RF measurement tests")
class RFProbeTest(unittest.TestCase):
    def test_known_gfsk_idle_tone_frequency_and_power(self):
        fs = 2_000_000
        count = 32768
        offsets = np.arange(count) / fs
        # At the red RX LO, an info-wave zero-bit idle tone is +600k - 20k Hz.
        tone = 0.1 * np.exp(2j * np.pi * 580_000 * offsets)
        result = measure_iq(tone, fs, 600_000, 200_000)
        self.assertAlmostEqual(result["iq_power_dbfs"], -20, places=4)
        self.assertAlmostEqual(result["target_power_dbfs"], -20, places=3)
        self.assertLess(abs(result["target_peak_offset_hz"] - 580_000), fs / count)
        self.assertLess(abs(result["strongest_offset_hz"] - 580_000), fs / count)
        self.assertEqual(result["clipped_percent"], 0)

    def test_silent_input_has_finite_measurements(self):
        result = measure_iq(np.zeros(1024), 2_000_000, 600_000, 200_000)
        self.assertTrue(all(math.isfinite(value) for value in result.values()))
        self.assertEqual(result["target_power_dbfs"], -200)
        self.assertEqual(result["peak_sample"], 0)

    def test_clipping_and_invalid_bands(self):
        result = measure_iq(np.ones(1024, dtype=np.complex64), 2_000_000, 600_000, 200_000)
        self.assertEqual(result["clipped_percent"], 100)
        with self.assertRaises(ValueError):
            measure_iq(np.ones(32), 2_000_000, 950_000, 200_000)
        with self.assertRaises(ValueError):
            measure_iq([], 2_000_000, 600_000, 200_000)


if __name__ == "__main__":
    unittest.main()
