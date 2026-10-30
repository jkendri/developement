"""AT-1000 panel API uses the demo tuner without a serial port."""

from __future__ import annotations

import time
import unittest

from ftdx1200.server import app, tuner


class TunerApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        tuner.disconnect()

    def test_demo_meters_and_memory_tune(self) -> None:
        client = app.test_client()
        connected = client.post("/api/tuner/connect", json={"demo": True, "port": "demo"})
        self.assertEqual(connected.status_code, 200)
        body = connected.get_json()
        self.assertTrue(body["connected"])
        self.assertTrue(body["demo"])
        self.assertEqual(body["tune_mode"], "manual")
        deadline = time.time() + 2.0
        meter = None
        while time.time() < deadline:
            meter = client.get("/api/tuner/status").get_json().get("meter")
            if meter:
                break
            time.sleep(0.05)
        self.assertIsNotNone(meter)
        self.assertGreater(meter["fwd_w"], 0)
        tuned = client.post("/api/tuner/command", json={"name": "memory_tune"})
        self.assertEqual(tuned.status_code, 200)
        snap = tuned.get_json()
        self.assertEqual(snap["last_tune"]["code"], "T")
        ports = client.get("/api/tuner/ports")
        self.assertEqual(ports.status_code, 200)
        self.assertIn("ports", ports.get_json())
