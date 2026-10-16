"""Protocol tests built from reports captured on a real base station (FW 40372)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from a50control import eq_math, presets  # noqa: E402
from a50control import protocol as p  # noqa: E402


def report(status: int, seq: int, data: bytes) -> bytes:
    raw = bytes([p.REPORT_ID, status, (seq << 6) | len(data)]) + data
    return raw + bytes(p.REPORT_SIZE - len(raw))


class EncodeTests(unittest.TestCase):
    def test_plain_request(self):
        pkt = p.encode_request(p.Cmd.GET_SLIDER, b"\x04", seq=2)
        self.assertEqual(len(pkt), 64)
        self.assertEqual(pkt[:4], bytes([0x02, 0x68, 0x81, 0x04]))
        self.assertFalse(any(pkt[4:]))

    def test_remote_request_gets_prefix(self):
        pkt = p.encode_request(p.Cmd.RX_DSP_VERSION, b"", seq=1)
        self.assertEqual(pkt[:4], bytes([0x02, 0xD6, 0x41, 0x0A]))

    def test_payload_limit(self):
        with self.assertRaises(ValueError):
            p.encode_request(0x6D, bytes(62), seq=0)


class DecodeTests(unittest.TestCase):
    def test_ok(self):
        data = p.decode_response(0x7C, report(0x02, 0, b"\x4e"))
        self.assertEqual(p.parse_battery(data), (78, False))

    def test_empty_status_is_success(self):
        self.assertEqual(p.decode_response(0x75, report(0x00, 0, b"")), b"")

    def test_error(self):
        msg = bytes.fromhex("01000000") + b"HID_ERROR_UNHANDLED_PACKET\0"
        with self.assertRaises(p.DeviceError) as ctx:
            p.decode_response(0x6B, report(0x01, 0, msg))
        self.assertEqual(ctx.exception.message, "HID_ERROR_UNHANDLED_PACKET")
        self.assertEqual(ctx.exception.code, 1)

    def test_charging_bit(self):
        self.assertEqual(p.parse_battery(b"\xe4"), (100, True))


class ParseTests(unittest.TestCase):
    def test_slider_pair(self):
        self.assertEqual(p.parse_pair(bytes.fromhex("68015151"), 0x68, 1), (81, 81))
        with self.assertRaises(p.ProtocolError):
            p.parse_pair(bytes.fromhex("68025151"), 0x68, 1)

    def test_eq_gains(self):
        data = bytes.fromhex("6901 12070c1313 12070c1313")
        cur, saved = p.parse_eq_gains(data, 0)
        self.assertEqual(cur, [6, -5, 0, 7, 7])
        self.assertEqual(saved, cur)

    def test_eq_band(self):
        cur, saved = p.parse_eq_band(bytes.fromhex("70 02 02 00 08 00 08 69 10 69 10"), 1, 1)
        self.assertEqual((cur.freq, cur.bandwidth), (4201, 0.5))
        self.assertEqual((saved.freq, saved.bandwidth), (4201, 0.5))

    def test_eq_name(self):
        data = bytes.fromhex("6e01") + b"ASTRO+" + bytes(44)
        self.assertEqual(p.parse_eq_name(data, 0), "ASTRO+")

    def test_tx_info(self):
        data = bytes.fromhex("50 42 02 08 86 98 2c 00 e6 07 04 14 09 04 27 00 00 58 42 02 08 b4 9d 00 00"
                             " 00 5c 42 02 08 68 42 02 08 78 42 02 08")
        info = p.parse_tx_info(data)
        self.assertEqual((info.vendor_id, info.product_id, info.version), (0x9886, 0x2C, 40372))
        self.assertEqual(info.build_date, "2022-04-20 09:04:39")

    def test_auto_off_nearest(self):
        self.assertEqual(p.parse_auto_off(b"\x0f"), 1)
        self.assertEqual(p.parse_auto_off(b"\x1e"), 4)

    def test_brightness(self):
        self.assertEqual(p.parse_brightness(bytes([70] * 9)), 70)
        self.assertEqual(p.payload_brightness(99), bytes([70] * 9))


class PayloadTests(unittest.TestCase):
    def test_gains(self):
        self.assertEqual(p.payload_eq_gains(2, [-12, 0, 12, 13, -20]), bytes([3, 0, 12, 24, 24, 0]))

    def test_band(self):
        self.assertEqual(p.payload_eq_band(0, 1, 406, 2.0), bytes.fromhex("0102 0020 9601"))
        self.assertEqual(p.payload_eq_band(0, 0, 10, 0), bytes.fromhex("0101 0000 5000"))

    def test_name(self):
        self.assertEqual(p.payload_eq_name(1, "PRO"), b"\x02\x04PRO\x00")
        with self.assertRaises(ValueError):
            p.payload_eq_name(0, "")


class PresetFileTests(unittest.TestCase):
    def test_roundtrip(self):
        preset = presets.factory_presets()[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.ini")
            presets.save_ini(preset, path)
            self.assertEqual(presets.load_ini(path), preset)

    def test_acc_file(self):
        text = ("[General]\nName=A50 MOD KIT\n\n[Eq_Bands]\nEq_Band_1=-5\nEq_Band_2=-7\nEq_Band_3=5\n"
                "Eq_Band_4=-7\nEq_Band_5=5\nEq_Freq_Band_1=200\nEq_Freq_Band_2=325\nEq_Freq_Band_3=2753\n"
                "Eq_Freq_Band_4=6691\nEq_Freq_Band_5=11002\nEq_Bandwidth_2=1.7\nEq_Bandwidth_3=2.0\n"
                "Eq_Bandwidth_4=0.5\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mod.ini")
            with open(path, "w") as fh:
                fh.write(text)
            preset = presets.load_ini(path)
        self.assertEqual(preset.name, "A50 MOD KIT")
        self.assertEqual([b.gain for b in preset.bands], [-5, -7, 5, -7, 5])
        self.assertEqual([b.bandwidth for b in preset.bands], [0.0, 1.7, 2.0, 0.5, 0.0])


class EqMathTests(unittest.TestCase):
    def test_flat(self):
        bands = [p.Band(0, f, 1.0) for f in (100, 300, 1000, 3000, 8000)]
        self.assertTrue(all(abs(v) < 1e-6 for v in eq_math.response_db(bands, [50, 1000, 10000])))

    def test_peak_hits_gain(self):
        bands = [p.Band(0, 100), p.Band(0, 300, 1.0), p.Band(6, 1000, 1.0), p.Band(0, 3000, 1.0), p.Band(0, 8000)]
        self.assertAlmostEqual(eq_math.response_db(bands, [1000])[0], 6.0, places=1)


if __name__ == "__main__":
    unittest.main()
