import os
import unittest
from unittest.mock import patch

from ftdx1200.audio import RxAudio
from ftdx1200.cat import parse_ag, parse_fa, parse_if, parse_md, parse_mhz, parse_rg, parse_rm, parse_sm
from ftdx1200.radio import RadioSession, _open_port_error, _win_com_path


class CatTests(unittest.TestCase):
    def test_parse_mhz(self) -> None:
        self.assertEqual(parse_mhz("14.250"), 14_250_000)
        self.assertEqual(parse_mhz("14250000"), 14_250_000)
        self.assertEqual(parse_mhz("7.030 mhz"), 7_030_000)

    def test_parse_if(self) -> None:
        info = parse_if("IF00014250000+000000200000")
        self.assertIsNotNone(info)
        self.assertEqual(info["hz"], 14_250_000)
        self.assertEqual(info["mode"], "USB")
        self.assertEqual(parse_md("MD01"), "LSB")
        self.assertEqual(parse_md("MD02"), "USB")

    def test_parse_fa_sm(self) -> None:
        self.assertEqual(parse_fa("FA14250000"), 14_250_000)
        self.assertEqual(parse_fa("FA014250000"), 14_250_000)
        self.assertIsNone(parse_fa("SM0080"))
        self.assertIsNone(parse_fa("FA12"))
        self.assertEqual(parse_sm("SM0080"), 80)
        self.assertEqual(parse_ag("AG0128"), 128)
        self.assertEqual(parse_rg("RG0255"), 255)
        self.assertEqual(parse_rg("RG0000"), 0)
        self.assertEqual(parse_rm("RM5120"), ("5", 120))
        self.assertEqual(parse_rm("RM6008"), ("6", 8))

    def test_win_com_path(self) -> None:
        if os.name == "nt":
            self.assertEqual(_win_com_path("COM4"), "\\\\.\\COM4")
        self.assertEqual(_win_com_path("demo"), "demo")

    def test_open_port_error_busy(self) -> None:
        err = _open_port_error("COM4", OSError("could not open port 'COM4': Access is denied."))
        self.assertIn("Another program", str(err))

    def test_preferred_radio_port_skips_tuner(self) -> None:
        from ftdx1200.radio import preferred_radio_port

        ports = [
            {"device": "COM3", "description": "SOL"},
            {"device": "COM4", "description": "USB"},
            {"device": "COM5", "description": "USB"},
        ]
        with patch("ftdx1200.radio.os.name", "nt"), patch("ftdx1200.radio._tuner_com", return_value="COM5"):
            self.assertEqual(preferred_radio_port(ports, "COM5"), "COM4")
            self.assertEqual(preferred_radio_port(ports, "COM4"), "COM4")

    def test_preferred_radio_port_linux_usb(self) -> None:
        from ftdx1200.radio import preferred_radio_port

        ports = [
            {"device": "/dev/ttyUSB1", "description": "USB Serial"},
            {"device": "/dev/serial/by-id/usb-FTDI_Yaesu-if00", "description": "Yaesu"},
        ]
        with patch("ftdx1200.radio.os.name", "posix"), patch("ftdx1200.radio._tuner_com", return_value=""):
            self.assertEqual(
                preferred_radio_port(ports, "COM4"),
                "/dev/serial/by-id/usb-FTDI_Yaesu-if00",
            )

    def test_rx_audio_volume_and_mute(self) -> None:
        with patch("ftdx1200.audio.load_config", return_value={}), patch(
            "ftdx1200.audio.save_config"
        ):
            audio = RxAudio()
            snap = audio.configure({"volume": 0.4, "mute": True, "monitor": False})
            self.assertEqual(snap["volume"], 0.4)
            self.assertTrue(snap["mute"])
            self.assertFalse(snap["monitor"])
            audio.set_tx(True)
            self.assertTrue(audio._tx)

    def test_af_restored_on_connect(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        import ftdx1200.radio as radio
        from ftdx1200.sim import SimulatedRadioPort

        class Port(SimulatedRadioPort):
            def __init__(self) -> None:
                super().__init__()
                self.dtr = False
                self.rts = False
                self.baudrate = 4800

        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            with patch.object(radio, "CONFIG_PATH", cfg), patch("serial.Serial", return_value=Port()):
                first = radio.RadioSession()
                first.connect("COM9")
                first.set_af(90)
                first.disconnect()
                second = radio.RadioSession()
                second.connect("COM9")
                self.assertEqual(second.snapshot["af"], 90)
                second.disconnect()

    def test_utune_on_while_connected(self) -> None:
        from unittest.mock import patch
        import ftdx1200.radio as radio
        from ftdx1200.sim import SimulatedRadioPort

        class Port(SimulatedRadioPort):
            def __init__(self) -> None:
                super().__init__()
                self.sent: list[str] = []
                self.dtr = False
                self.rts = False
                self.baudrate = 4800

            def write(self, data: bytes) -> int:
                self.sent.append(data.decode("ascii", errors="ignore"))
                return super().write(data)

        port = Port()
        with patch("serial.Serial", return_value=port):
            session = radio.RadioSession()
            session.connect("COM9")
            self.assertTrue(any("VF01+0;" in item for item in port.sent))
            self.assertEqual(port.utune, "1")
            session.disconnect()
            self.assertTrue(any("VF00+0;" in item for item in port.sent))
            self.assertEqual(port.utune, "0")

    def test_rf_gain_full_on_connect(self) -> None:
        from unittest.mock import patch
        import ftdx1200.radio as radio
        from ftdx1200.sim import SimulatedRadioPort

        class Port(SimulatedRadioPort):
            def __init__(self) -> None:
                super().__init__()
                self.rf_gain = 40
                self.dtr = False
                self.rts = False
                self.baudrate = 4800

        with patch("serial.Serial", return_value=Port()):
            session = radio.RadioSession()
            session.connect("COM9")
            self.assertEqual(session.snapshot["rf_gain"], 255)
            session.disconnect()

    def test_demo_connect_and_mode(self) -> None:
        session = RadioSession()
        session.connect("demo")
        self.assertTrue(session.snapshot["connected"])
        self.assertEqual(session.snapshot["af"], 128)
        self.assertEqual(session.snapshot["mode"], "USB")
        session.set_mode("LSB")
        self.assertEqual(session.snapshot["mode"], "LSB")
        session.set_frequency("7.030")
        self.assertEqual(session.snapshot["hz"], 7_030_000)
        session.set_mode("AM")
        session.set_hz(5_050_000)
        self.assertEqual(session.snapshot["hz"], 5_050_000)
        self.assertEqual(session.snapshot["mode"], "AM")
        session.disconnect()
        self.assertFalse(session.snapshot["connected"])

    def test_demo_tx_po_swr_meters(self) -> None:
        session = RadioSession()
        session.connect("demo")
        session.set_tx(True)
        po = session.query("RM5;")
        swr = session.query("RM6;")
        self.assertEqual(parse_rm(po)[0], "5")
        self.assertGreater(parse_rm(po)[1], 0)
        self.assertEqual(parse_rm(swr), ("6", 40))
        session.set_tx(False)
        session.disconnect()

    def test_power_off_prompt_only_when_live_radio_is_on(self) -> None:
        from ftdx1200.server import radio_should_prompt_power_off

        self.assertTrue(radio_should_prompt_power_off({
            "connected": True, "radio_on": True, "demo": False,
        }))
        self.assertFalse(radio_should_prompt_power_off({
            "connected": True, "radio_on": False, "demo": False,
        }))
        self.assertFalse(radio_should_prompt_power_off({
            "connected": True, "radio_on": True, "demo": True,
        }))
        self.assertFalse(radio_should_prompt_power_off({
            "connected": False, "radio_on": True, "demo": False,
        }))

    def test_demo_power_off(self) -> None:
        session = RadioSession()
        session.connect("demo")
        port = session._port
        snap = session.power_off()
        self.assertTrue(snap["connected"])
        self.assertFalse(snap["radio_on"])
        self.assertFalse(port.powered)
        on = session.power_on()
        self.assertTrue(on["radio_on"])
        self.assertTrue(port.powered)
        self.assertEqual(on["af"], 128)
        self.assertEqual(on["rf_gain"], 255)
        turned = session.set_rf_gain(64)
        self.assertEqual(turned["rf_gain"], 64)
        self.assertEqual(session.poll()["rf_gain"], 64)
        session.disconnect()

    def test_favorites_add_and_delete(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import ftdx1200.radio as radio

        with TemporaryDirectory() as tmp:
            fav = Path(tmp) / "favorites.json"
            cfg = Path(tmp) / "config.json"
            with patch.object(radio, "FAVORITES_PATH", fav), patch.object(radio, "CONFIG_PATH", cfg):
                items = radio.add_favorite("POTA", "14.250", "USB")
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["name"], "POTA")
                self.assertEqual(items[0]["hz"], 14_250_000)
                self.assertEqual(items[0]["mode"], "USB")
                leftover = radio.delete_favorite(items[0]["id"])
                self.assertEqual(leftover, [])
                self.assertEqual(radio.list_favorites(), [])

    def test_favorites_survive_config_rewrite(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import ftdx1200.radio as radio

        with TemporaryDirectory() as tmp:
            fav = Path(tmp) / "favorites.json"
            cfg = Path(tmp) / "config.json"
            with patch.object(radio, "FAVORITES_PATH", fav), patch.object(radio, "CONFIG_PATH", cfg):
                radio.add_favorite("POTA", "14.250", "USB")
                radio.save_config({"port": "COM4", "favorites": []})
                items = radio.list_favorites()
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["name"], "POTA")
                self.assertTrue(fav.exists())
                self.assertNotIn("favorites", radio.load_config())

    def test_station_id_parsers(self) -> None:
        from datetime import datetime, timezone
        from ftdx1200.stationid import parse_dx_csv, parse_shortwave_html, window_contains_now

        html = """
        <tbody><tr id="row0"><td>9870</td><td>BBC</td><td>00:00</td><td>23:59</td>
        <td>1234567</td><td>English</td><td>250</td><td>ND</td>
        <td>GBR Woofferton</td><td></td></tr></tbody>
        """
        sw = parse_shortwave_html(html, 9870)
        self.assertEqual(sw[0]["station"], "BBC")
        self.assertTrue(window_contains_now("00:00", "23:59", "1234567", now=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)))
        csv = "K1ABC^14250.0^W1AW^CQ TEST^2026-10-02 12:00:00^Y^N^NA^20m^United States^291\n"
        dx = parse_dx_csv(csv, 14250.0)
        self.assertEqual(dx[0]["call"], "K1ABC")
        self.assertEqual(dx[0]["country"], "United States")

    def test_qrz_secrets_survive_config_rewrite(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import ftdx1200.radio as radio

        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            qrz = Path(tmp) / "qrz.json"
            with patch.object(radio, "CONFIG_PATH", cfg), patch.object(radio, "QRZ_PATH", qrz):
                radio.save_qrz_secrets({
                    "qrz_user": "kc4eel",
                    "qrz_pass": "secret-pass",
                    "qrz_log_key": "log-api-key",
                })
                radio.save_config({"port": "COM4", "qrz_pass": "", "qrz_log_key": ""})
                loaded = radio.load_config()
                self.assertEqual(loaded.get("qrz_user"), "kc4eel")
                self.assertEqual(loaded.get("qrz_pass"), "secret-pass")
                self.assertEqual(loaded.get("qrz_log_key"), "log-api-key")
                self.assertNotIn("qrz_pass", radio._read_json_file(cfg) or {})
                self.assertTrue(qrz.exists())

    def test_qrz_secrets_migrate_and_skip_corrupt(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import ftdx1200.radio as radio

        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            qrz = Path(tmp) / "qrz.json"
            with patch.object(radio, "CONFIG_PATH", cfg), patch.object(radio, "QRZ_PATH", qrz):
                cfg.write_text(
                    '{"port": "COM4", "qrz_user": "kc4eel", "qrz_pass": "pw", "qrz_log_key": "k1"}',
                    encoding="utf-8",
                )
                radio.ensure_qrz_secrets()
                main = radio._read_json_file(cfg)
                self.assertNotIn("qrz_pass", main or {})
                self.assertEqual(radio.load_config().get("qrz_log_key"), "k1")
                qrz.write_text("{not json", encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    radio.save_qrz_secrets({"qrz_pass": "new"})
                self.assertEqual(qrz.read_text(encoding="utf-8"), "{not json")

    def test_panel_layout_roundtrip(self) -> None:
        store: dict = {}

        def load() -> dict:
            return dict(store)

        def save(update: dict) -> None:
            store.update(update)

        with patch("ftdx1200.radio.load_config", load), patch("ftdx1200.radio.save_config", save):
            from ftdx1200.radio import load_panel_layout, save_panel_layout

            saved = save_panel_layout({
                "vfoPanel": {"x": 12, "y": 40, "floated": True},
                "bad": "skip",
            })
            self.assertEqual(saved["vfoPanel"]["x"], 12)
            self.assertNotIn("bad", saved)
            self.assertEqual(load_panel_layout()["vfoPanel"]["y"], 40)
            from ftdx1200.radio import load_window_geom, save_window_geom
            win = save_window_geom(1280, 800, 40, 20)
            self.assertEqual(win["width"], 1280)
            self.assertEqual(load_window_geom()["height"], 800)

    def test_pota_normalize_and_mode(self) -> None:
        from ftdx1200.pota import band_for_hz, cat_mode_for, khz_to_hz, normalize_spot

        self.assertEqual(khz_to_hz("14250"), 14_250_000)
        self.assertEqual(khz_to_hz("7074.0"), 7_074_000)
        self.assertEqual(cat_mode_for("SSB", 14_250_000), "USB")
        self.assertEqual(cat_mode_for("SSB", 7_074_000), "LSB")
        self.assertEqual(cat_mode_for("FT8", 14_074_000), "DATA-U")
        spot = normalize_spot({
            "spotId": 1,
            "activator": "N0CALL",
            "frequency": "14280",
            "mode": "SSB",
            "reference": "US-0001",
            "name": "Example Park",
            "locationDesc": "US-CO",
            "grid6": "EM59aa",
            "expire": 1200,
            "invalid": None,
        })
        self.assertEqual(spot["mhz"], "14.280000")
        self.assertEqual(spot["cat_mode"], "USB")
        self.assertEqual(spot["band"], "20m")
        from ftdx1200.pota import park_page_url
        self.assertEqual(park_page_url("us-0001"), "https://pota.app/#/park/US-0001")
        self.assertEqual(spot["grid"], "EM59aa")
        self.assertAlmostEqual(spot["lat"], 39.020833, places=4)
        self.assertAlmostEqual(spot["lon"], -89.958333, places=4)
        self.assertEqual(band_for_hz(7_074_000), "40m")
        self.assertEqual(band_for_hz(14_074_000), "20m")
        self.assertEqual(band_for_hz(144_460_000), "2m")
    def test_qrz_call_and_name(self) -> None:
        from ftdx1200.qrz import normalize_call, operator_name, _xml_map
        import xml.etree.ElementTree as ET

        self.assertEqual(normalize_call("n0abc/p"), "N0ABC")
        self.assertEqual(operator_name("JANE", "DOE"), "Jane Doe")
        from ftdx1200.qrz import format_qth, record_from_info
        self.assertEqual(format_qth({"addr2": "Springfield", "state": "IL", "country": "United States"}), "Springfield, IL")
        rec = record_from_info({"fname": "Jane", "name": "DOE", "addr2": "Springfield", "state": "IL", "country": "United States", "grid": "EM59", "lat": "39.8", "lon": "-89.6", "call": "N0ABC", "image": "https://example.com/n0abc.jpg"})
        self.assertEqual(rec["qth"], "Springfield, IL")
        self.assertEqual(rec["grid"], "EM59")
        self.assertEqual(rec["lat"], "39.8")
        self.assertEqual(rec["lon"], "-89.6")
        self.assertEqual(rec["call"], "N0ABC")
        self.assertTrue(rec["image"].endswith("n0abc.jpg"))
        from ftdx1200.qrz import qrz_page_url
        self.assertEqual(qrz_page_url("n0abc/p"), "https://www.qrz.com/db/N0ABC")
        root = ET.fromstring(
            "<QRZDatabase><Callsign><fname>Jane</fname><name>DOE</name></Callsign>"
            "<Session><Key>abc</Key></Session></QRZDatabase>"
        )
        data = _xml_map(root)
        self.assertEqual(data["Callsign"]["fname"], "Jane")
        self.assertEqual(data["Session"]["Key"], "abc")

    def test_log_adif_and_hrd_command(self) -> None:
        from ftdx1200.logqso import adif_modes, build_adif, hrd_add_command, hrd_fields, parse_qrz_kv, qso_call

        self.assertEqual(qso_call("k1abc/p"), "K1ABC/P")
        self.assertEqual(adif_modes("USB", 14_250_000), ("SSB", "USB"))
        adif = build_adif({
            "call": "K1ABC/P",
            "station_callsign": "N0CALL",
            "qso_date": "20260919",
            "time_on": "130501",
            "hz": 14_250_000,
            "mhz": "14.250000",
            "mode": "USB",
            "band": "20m",
            "rst_sent": "59",
            "rst_rcvd": "57",
            "sig": "POTA",
            "sig_info": "US-1234",
            "qth": "Springfield, IL",
            "city": "Springfield",
            "state": "IL",
            "country": "United States",
        })
        self.assertIn("<call:7>K1ABC/P", adif)
        self.assertIn("<mode:3>SSB", adif)
        self.assertIn("<submode:3>USB", adif)
        self.assertIn("<sig_info:7>US-1234", adif)
        self.assertIn("<qth:15>Springfield, IL", adif)
        self.assertTrue(adif.endswith("<eor>"))
        cmd = hrd_add_command("My Logbook", hrd_fields({
            "call": "K1ABC/P",
            "station_callsign": "N0CALL",
            "qso_date": "20260919",
            "time_on": "130501",
            "hz": 14_250_000,
            "mode": "USB",
            "band": "20m",
            "rst_sent": "59",
            "rst_rcvd": "57",
            "qth": "Springfield, IL",
            "city": "Springfield",
            "state": "IL",
            "country": "United States",
        }))
        self.assertTrue(cmd.startswith('db add "My Logbook"'))
        self.assertIn('CALL="K1ABC/P"', cmd)
        self.assertIn('MODE="USB"', cmd)
        self.assertIn('FREQ="14250000"', cmd)
        self.assertIn('QTH="Springfield, IL"', cmd)
        self.assertIn('CITY="Springfield"', cmd)
        self.assertIn('STATE="IL"', cmd)
        parsed = parse_qrz_kv("RESULT=OK&LOGID=99&COUNT=1")
        self.assertEqual(parsed["RESULT"], "OK")
        self.assertEqual(parsed["LOGID"], "99")

    def test_worked_calls_persist(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import ftdx1200.logqso as logqso

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "worked.json"
            with patch.object(logqso, "WORKED_PATH", path):
                logqso._worked_cache = None
                logqso._worked_dates = None
                logqso.remember_worked_calls(["k1abc/p"], {"k1abc/p": "20240115"})
                calls = logqso.worked_calls_list()
                self.assertIn("K1ABC", calls)
                self.assertEqual(logqso.load_worked_dates().get("K1ABC"), "20240115")
                logqso._worked_cache = None
                logqso._worked_dates = None
                again = logqso.load_worked_calls()
                self.assertIn("K1ABC", again)
                self.assertEqual(logqso.load_worked_dates().get("K1ABC"), "20240115")
                logqso._worked_cache = None
                logqso._worked_dates = None
        from ftdx1200.logqso import log_match_key
        self.assertEqual(log_match_key("k1abc/p", "us-1234"), "K1ABC|US-1234")
        self.assertEqual(log_match_key("N0CALL", ""), "N0CALL")
        self.assertEqual(log_match_key("W4/G4ABC", ""), "G4ABC")
        self.assertEqual(log_match_key("ve3/w1aw/p", "ca-1234"), "W1AW|CA-1234")
        from ftdx1200.logqso import parse_adif_calls
        self.assertEqual(parse_adif_calls("<CALL:6>KC4EEL <CALL:5>W9AV/P"), ["KC4EEL", "W9AV"])
        self.assertEqual(parse_adif_calls("<CALLSIGN:5>K1DLA "), ["K1DLA"])
        from ftdx1200.logqso import _qrz_adif_from_body
        adif = _qrz_adif_from_body("RESULT=OK&COUNT=1&ADIF=<CALL:5>K1DLA <COMMENT:7>a & b")
        self.assertIn("K1DLA", parse_adif_calls(adif))
        escaped = _qrz_adif_from_body("COUNT=1&RESULT=OK&ADIF=&lt;call:5&gt;K1DLA &lt;eor&gt;")
        self.assertIn("K1DLA", parse_adif_calls(escaped))
        from ftdx1200.logqso import parse_adif_worked
        latest = parse_adif_worked(
            "<CALL:5>K1DLA <QSO_DATE:8>20240102 <eor><CALL:5>K1DLA <QSO_DATE:8>20240312 <eor>"
        )
        self.assertEqual(latest.get("K1DLA"), "20240312")
        from ftdx1200.logqso import n1mm_band_mhz, n1mm_contact_xml
        self.assertEqual(n1mm_band_mhz("20m", 14_250_000), "14")
        xml = n1mm_contact_xml({
            "call": "K1ABC/P",
            "station_callsign": "N0CALL",
            "qso_date": "20260919",
            "time_on": "130501",
            "hz": 14_250_000,
            "mode": "USB",
            "band": "20m",
            "rst_sent": "59",
            "rst_rcvd": "57",
            "qth": "Springfield, IL",
        })
        self.assertIn("<call>K1ABC/P</call>", xml)
        self.assertIn("<qth>Springfield, IL</qth>", xml)
        self.assertIn("<rxfreq>1425000</rxfreq>", xml)
        self.assertIn("<timestamp>2026-09-19 13:05:01</timestamp>", xml)

    def test_pota_recent_window(self) -> None:
        from datetime import datetime, timedelta, timezone

        from ftdx1200.pota import is_recent_spot

        now = datetime(2026, 9, 19, 1, 30, tzinfo=timezone.utc)
        fresh = {"spot_time": (now - timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%S")}
        stale = {"spot_time": (now - timedelta(minutes=9)).strftime("%Y-%m-%dT%H:%M:%S")}
        self.assertTrue(is_recent_spot(fresh, now=now))
        self.assertFalse(is_recent_spot(stale, now=now))
        self.assertFalse(is_recent_spot({"spot_time": ""}, now=now))
        older = {"spot_time": (now - timedelta(minutes=12)).strftime("%Y-%m-%dT%H:%M:%S")}
        self.assertTrue(is_recent_spot(older, now=now, max_age_sec=15 * 60))
        self.assertFalse(is_recent_spot(older, now=now, max_age_sec=8 * 60))
        from ftdx1200.pota import spot_age_sec_from_minutes
        self.assertEqual(spot_age_sec_from_minutes(None), 8 * 60)
        self.assertEqual(spot_age_sec_from_minutes("15"), 15 * 60)
        self.assertEqual(spot_age_sec_from_minutes("0"), 60)
        self.assertEqual(spot_age_sec_from_minutes("90"), 60 * 60)
        self.assertEqual(spot_age_sec_from_minutes("nope"), 8 * 60)

    def test_pota_usa_only(self) -> None:
        from ftdx1200.pota import is_usa_spot

        self.assertTrue(is_usa_spot({"reference": "US-0001", "location": "US-CO"}))
        self.assertTrue(is_usa_spot({"reference": "US-1234", "location": ""}))
        self.assertTrue(is_usa_spot({"reference": "", "location": "US-TX,US-NM"}))
        self.assertFalse(is_usa_spot({"reference": "CA-0001", "location": "CA-ON"}))
        self.assertFalse(is_usa_spot({"reference": "AU-0001", "location": "AU-VIC"}))
        self.assertFalse(is_usa_spot({"reference": "", "location": ""}))

    def test_maidenhead_center(self) -> None:
        from ftdx1200.geo import maidenhead_to_latlon

        lat, lon = maidenhead_to_latlon("EM59")
        self.assertAlmostEqual(lat, 39.5, places=4)
        self.assertAlmostEqual(lon, -89.0, places=4)
        self.assertIsNone(maidenhead_to_latlon(""))
        self.assertIsNone(maidenhead_to_latlon("ZZ"))
        from ftdx1200.geo import parse_coord
        self.assertIsNone(parse_coord(None, lo=-90.0, hi=90.0))
        self.assertIsNone(parse_coord("", lo=-90.0, hi=90.0))
        self.assertEqual(parse_coord("39.8", lo=-90.0, hi=90.0), 39.8)

    def test_solar_banner_cache(self) -> None:
        import ftdx1200.solar as solar

        gif = b"GIF89a" + b"\x00" * 20
        solar._cache.update({"at": 0.0, "body": None, "ctype": "image/gif"})

        class _Resp:
            headers = {"Content-Type": "image/gif"}

            def read(self):
                return gif

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("ftdx1200.solar.urllib.request.urlopen", return_value=_Resp()) as mocked:
            body, ctype = solar.fetch_solar_banner(force=True)
            self.assertEqual(body, gif)
            self.assertEqual(ctype, "image/gif")
            solar.fetch_solar_banner()
            self.assertEqual(mocked.call_count, 1)

    def test_hidden_hrd_windows_stay_hidden(self) -> None:
        from ftdx1200.logqso import _should_minimize_hrd_window

        self.assertFalse(_should_minimize_hrd_window("HRDLogBook", visible=False, iconic=False))
        self.assertTrue(_should_minimize_hrd_window("HRD Logbook - My Logbook", visible=True, iconic=False))
        self.assertFalse(_should_minimize_hrd_window("HRD Logbook", visible=True, iconic=True))
        self.assertFalse(_should_minimize_hrd_window("Radio", visible=True, iconic=False))

    def test_ipcli_autostart_xml(self) -> None:
        from ftdx1200.logqso import apply_ipcli_autostart

        xml = "<IPCli>0</IPCli>\n<IPCliPort>1234</IPCliPort>\n<ListenerStart>1</ListenerStart>"
        out = apply_ipcli_autostart(xml, 7826)
        self.assertIn("<IPCli>1</IPCli>", out)
        self.assertIn("<IPCliPort>7826</IPCliPort>", out)
        self.assertNotIn("<IPCli>0</IPCli>", out)

    def test_hrd_tcp_ready_closed(self) -> None:
        from ftdx1200.logqso import hrd_tcp_ready, stop_hrd_logbook

        self.assertFalse(hrd_tcp_ready(1, timeout=0.2))
        stop_hrd_logbook()
        import ftdx1200.logqso as logqso
        logqso._HRD_SHUTDOWN = False

    def test_ensure_hrd_does_not_relaunch_if_running(self) -> None:
        from unittest.mock import patch
        from ftdx1200 import logqso

        with patch.object(logqso, "hrd_tcp_ready", return_value=False), \
             patch.object(logqso, "_hrd_logbook_pids", return_value=[1234]), \
             patch.object(logqso, "_launch_hrd_logbook") as launch, \
             patch.object(logqso, "_start_ip_client_menu"), \
             patch.object(logqso, "_minimize_hrd_logbook"), \
             patch.object(logqso, "_wait_hrd_tcp", return_value=0):
            logqso._HRD_SHUTDOWN = False
            logqso._HRD_LAUNCHED = False
            logqso._HRD_STARTED_BY_US = False
            result = logqso.ensure_hrd_logbook(wait_s=0)
            self.assertFalse(result.get("started"))
            self.assertTrue(logqso._HRD_STARTED_BY_US)
            launch.assert_not_called()
            logqso._HRD_LAUNCHED = False
            logqso._HRD_IPCLI_NUDGED = False
            logqso._HRD_STARTED_BY_US = False
            logqso._HRD_SHUTDOWN = False

    def test_stop_hrd_force_kills_logbook(self) -> None:
        from unittest.mock import patch
        from ftdx1200 import logqso

        clock = {"t": 0.0}

        def mono() -> float:
            clock["t"] += 5.0
            return clock["t"]

        logqso._HRD_SHUTDOWN = False
        logqso._HRD_STARTED_BY_US = True
        try:
            with patch.object(logqso, "_hrd_logbook_pids", return_value=[4242]), \
                 patch.object(logqso, "_post_logbook_close") as close, \
                 patch.object(logqso.time, "monotonic", side_effect=mono), \
                 patch.object(logqso.time, "sleep"), \
                 patch.object(logqso.subprocess, "run") as run:
                logqso.stop_hrd_logbook()
            close.assert_called_once_with([4242])
            self.assertFalse(logqso._HRD_STARTED_BY_US)
            cmds = [call.args[0] for call in run.call_args_list]
            self.assertTrue(any("/F" in cmd and "4242" in cmd for cmd in cmds))
            self.assertTrue(any(cmd[:4] == ["taskkill", "/F", "/T", "/IM"] and "HRDLogBook.exe" in cmd for cmd in cmds))
        finally:
            logqso._HRD_STARTED_BY_US = False
            logqso._HRD_LAUNCHED = False
            logqso._HRD_IPCLI_NUDGED = False
            logqso._HRD_SHUTDOWN = False

    def test_worked_history_skips_without_key(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        from ftdx1200 import logqso

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "worked.json"
            with patch.object(logqso, "WORKED_PATH", path), \
                 patch.object(logqso, "load_config", return_value={}), \
                 patch.object(logqso, "_qrz_log_fetch") as fetch:
                logqso._worked_cache = None
                logqso._worked_dates = None
                logqso._qrz_worked_at = 0.0
                self.assertEqual(logqso.fetch_qrz_worked_history(force=True), [])
                fetch.assert_not_called()
                logqso._worked_cache = None
                logqso._worked_dates = None

    def test_worked_history_pages_old_call(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        from ftdx1200 import logqso

        def fake_fetch(option: str) -> str:
            if "AFTERLOGID:0" in option:
                return "<CALL:5>K1DLA <QSO_DATE:8>20240312 <APP_QRZLOG_LOGID:1>1<eor>"
            return ""

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "worked.json"
            with patch.object(logqso, "WORKED_PATH", path), \
                 patch.object(logqso, "load_config", return_value={"qrz_log_key": "test-key"}), \
                 patch.object(logqso, "_qrz_log_fetch", side_effect=fake_fetch), \
                 patch.object(logqso.time, "sleep"):
                logqso._worked_cache = None
                logqso._worked_dates = None
                logqso._qrz_worked_at = 0.0
                calls = logqso.fetch_qrz_worked_history(force=True)
                self.assertIn("K1DLA", calls)
                self.assertEqual(logqso.load_worked_dates().get("K1DLA"), "20240312")
                logqso._worked_cache = None
                logqso._worked_dates = None
                logqso._qrz_worked_at = 0.0


if __name__ == "__main__":
    unittest.main()
