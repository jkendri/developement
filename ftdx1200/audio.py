"""PC-side RX audio monitor (radio USB/line in → speakers)."""

from __future__ import annotations

from typing import Any

from .radio import load_config, save_config


class RxAudio:
    def __init__(self) -> None:
        cfg = load_config()
        self.input_id = cfg.get("rx_input")
        self.output_id = cfg.get("rx_output")
        try:
            self.volume = max(0.0, min(1.5, float(cfg.get("rx_volume", 0.8))))
        except (TypeError, ValueError):
            self.volume = 0.8
        self.mute = bool(cfg.get("rx_mute"))
        self.running = False
        self.level = 0.0
        self.error = ""
        self._tx = False
        self._stream = None

    def snapshot(self, devices: bool = True) -> dict[str, Any]:
        data = {
            "input": self.input_id,
            "output": self.output_id,
            "volume": round(self.volume, 3),
            "mute": self.mute,
            "monitor": self.running,
            "level": round(self.level, 3),
            "error": self.error,
            "available": self._sounddevice() is not None,
        }
        if devices:
            data["inputs"] = self.list_devices("input")
            data["outputs"] = self.list_devices("output")
        return data

    def set_tx(self, on: bool) -> None:
        self._tx = bool(on)

    def configure(self, body: dict[str, Any]) -> dict[str, Any]:
        if "input" in body:
            self.input_id = None if body["input"] in ("", None) else int(body["input"])
        if "output" in body:
            self.output_id = None if body["output"] in ("", None) else int(body["output"])
        if "volume" in body:
            self.volume = max(0.0, min(1.5, float(body["volume"])))
        if "mute" in body:
            self.mute = bool(body["mute"])
        save_config({
            "rx_input": self.input_id,
            "rx_output": self.output_id,
            "rx_volume": self.volume,
            "rx_mute": self.mute,
        })
        if "monitor" in body:
            if body["monitor"]:
                self.start()
            else:
                self.stop()
        elif self.running and ("input" in body or "output" in body):
            self.start()
        return self.snapshot(devices=True)

    def list_devices(self, kind: str) -> list[dict[str, Any]]:
        sd = self._sounddevice()
        if sd is None:
            return []
        rows = []
        try:
            host = sd.query_hostapis()
        except Exception:
            host = []
        try:
            devices = list(sd.query_devices())
        except Exception:
            return []
        for index, dev in enumerate(devices):
            channels = int(dev.get("max_input_channels" if kind == "input" else "max_output_channels") or 0)
            if channels < 1:
                continue
            api = ""
            try:
                api = str(host[int(dev.get("hostapi") or 0)].get("name") or "")
            except Exception:
                pass
            rows.append({
                "id": index,
                "name": str(dev.get("name") or f"Device {index}"),
                "hostapi": api,
            })
        return rows

    def start(self) -> None:
        sd = self._sounddevice()
        if sd is None:
            self.error = "Install sounddevice (pip install sounddevice numpy)"
            self.running = False
            return
        self.stop()
        try:
            stream = sd.Stream(
                device=(self.input_id, self.output_id),
                samplerate=48000,
                blocksize=512,
                dtype="float32",
                channels=1,
                callback=self._callback,
            )
            stream.start()
        except Exception as exc:
            self.error = str(exc)
            self.running = False
            self._stream = None
            return
        self._stream = stream
        self.running = True
        self.error = ""

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        self.running = False
        self.level = 0.0
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    def _callback(self, indata, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        import numpy as np

        peak = float(np.max(np.abs(indata))) if indata.size else 0.0
        self.level = peak if peak > self.level * 0.7 else self.level * 0.85
        if self.mute or self._tx or not self.running:
            outdata.fill(0)
            return
        outdata[:] = indata * self.volume

    @staticmethod
    def _sounddevice():
        try:
            import sounddevice as sd
        except ImportError:
            return None
        return sd
