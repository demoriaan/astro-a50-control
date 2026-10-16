"""Device model and worker tests against the simulated base station."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from fake_base import FakeBase  # noqa: E402

from a50control import protocol as p  # noqa: E402
from a50control.device import A50  # noqa: E402


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.base = FakeBase()
        self.dev = A50(self.base)
        self.state = self.dev.read_all()

    def test_read_all(self):
        s = self.state
        self.assertEqual((s.base.version, s.base_minor, s.headset_version), (40372, 43, "39964.43"))
        self.assertTrue(s.headset_on)
        self.assertFalse(s.docked)
        self.assertEqual(s.active_preset, 2)
        self.assertEqual(s.live.presets[2].name, "STUDIO+")
        self.assertEqual(s.live.presets[0].bands[1].bandwidth, 2.0)
        self.assertEqual(s.live.sliders[p.Slider.STREAM_CHAT], 81)
        self.assertEqual((s.live.noise_gate, s.live.mic_eq, s.live.auto_off), (1, 2, 1))
        self.assertFalse(s.dirty)

    def test_edit_save_cycle(self):
        self.dev.set_slider(p.Slider.SIDETONE, 10)
        self.dev.set_gains(0, [1, 2, 3, 4, 5])
        self.dev.set_noise_gate(p.NoiseGate.TOURNAMENT)
        self.dev.set_brightness(40)
        self.assertTrue(self.dev.state.dirty)
        self.dev.save()
        self.assertFalse(self.dev.state.dirty)
        fresh = A50(self.base).read_all()
        self.assertEqual(fresh.saved.sliders[p.Slider.SIDETONE], 10)
        self.assertEqual([b.gain for b in fresh.saved.presets[0].bands], [1, 2, 3, 4, 5])
        self.assertEqual(fresh.saved.brightness, 40)

    def test_revert_rewrites_saved_copy(self):
        self.dev.set_slider(p.Slider.SIDETONE, 10)
        self.dev.set_band(1, 2, 1234, 0.7)
        self.dev.set_name(2, "TEST")
        self.dev.set_auto_off(4)
        # simulate a write that reached the device but whose answer got lost
        self.base.live["balance"] = 3
        self.dev.revert()
        self.assertEqual(self.base.live, self.base.saved)
        self.assertFalse(self.dev.state.dirty)

    def test_name_clipping(self):
        name = "Ä" * 30
        self.dev.set_name(0, name)
        self.assertEqual(self.dev.state.live.presets[0].name, "Ä" * 24)
        self.assertEqual(self.base.live["names"][1], "Ä" * 24)

    def test_shelf_bandwidth_forced_to_zero(self):
        self.dev.set_band(0, 0, 100, 2.0)
        self.assertEqual(self.base.live["bands"][1][0], [0, 100])


class WorkerTests(unittest.TestCase):
    def test_replaced_job_moves_to_end(self):
        from a50control.worker import Worker
        worker = Worker()
        worker.device = FakeBase()
        gate = threading.Event()
        order = []
        worker.submit("block", lambda d: gate.wait(2))
        worker.submit(("gains", 0), lambda d: order.append("gains-old"))
        worker.submit(("apply", 0), lambda d: order.append("apply"))
        worker.submit(("gains", 0), lambda d: order.append("gains-new"))
        gate.set()
        worker.shutdown()
        self.assertEqual(order, ["apply", "gains-new"])
        self.assertTrue(worker.device is None)


if __name__ == "__main__":
    unittest.main()
