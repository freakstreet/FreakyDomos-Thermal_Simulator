from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from influx import InfluxWriter
from net_device import NetDevice
from thermal import ThermalModel
from weather import WeatherSeries

ROOT = Path(__file__).parent


class State:
    def __init__(self) -> None:
        initial = float(os.getenv("SIMULATION_INITIAL_TEMPERATURE", "20"))
        self.model = ThermalModel(initial)
        configured_weather = os.getenv("WEATHER_CSV", "").strip()
        bundled_weather = ROOT / "data" / "weather_2024_luz_saint_sauveur.csv"
        weather_path = configured_weather if configured_weather and Path(configured_weather).exists() else str(bundled_weather)
        self.weather = WeatherSeries(weather_path)
        self.influx = InfluxWriter()
        self.device = NetDevice(self.model)
        self.step_seconds = max(1.0, float(os.getenv("SIMULATION_STEP_SECONDS", "10")))
        self.lock = threading.RLock()
        self.running = False
        self.selected_time = self.weather.points[0][0] if self.weather.points else dt.datetime.now(dt.timezone.utc)
        self.started_at: str | None = None
        self.last_step: str | None = None
        self.last_influx_ok = False
        self.history: list[dict] = []
        self.scenario_elapsed_seconds = 0.0
        self.heater_on_seconds = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="thermal-clock", daemon=True)

    def start(self) -> None:
        self.device.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.device.stop()

    def set_running(self, running: bool) -> None:
        with self.lock:
            if running and not self.running:
                self.scenario_elapsed_seconds = 0.0
                self.heater_on_seconds = 0.0
            self.running = running
            if running:
                self.started_at = dt.datetime.now(dt.timezone.utc).isoformat()

    def _loop(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            next_tick += self.step_seconds
            delay = max(0, next_tick - time.monotonic())
            self._stop.wait(delay)
            if self._stop.is_set(): break
            with self.lock:
                if not self.running: continue
                self.selected_time += dt.timedelta(seconds=self.step_seconds)
                outside = self.weather.value_at(self.selected_time)
                self.model.step(outside, dt_seconds=self.step_seconds)
                self.scenario_elapsed_seconds += self.step_seconds
                if self.model.heater_on:
                    self.heater_on_seconds += self.step_seconds
                self.history.append(self.history_point())
                stamp = int(time.time() * 1000)
                self.last_influx_ok = self.influx.write(self.model.snapshot(), str(stamp)) if self.influx.enabled else False
                self.last_step = dt.datetime.now(dt.timezone.utc).isoformat()
            self.device.publish_values()

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "selected_time": self.selected_time.isoformat(),
                "started_at": self.started_at,
                "last_step": self.last_step,
                "weather_source": self.weather.source,
                "weather_loaded": bool(self.weather.points),
                "simulation_step_seconds": self.step_seconds,
                "heating_stats": {
                    "scenario_elapsed_seconds": self.scenario_elapsed_seconds,
                    "heater_on_seconds": self.heater_on_seconds,
                    "heater_on_percent": round(100.0 * self.heater_on_seconds / self.scenario_elapsed_seconds, 1) if self.scenario_elapsed_seconds else 0.0,
                },
                "influx": {"enabled": self.influx.enabled, "last_write_ok": self.last_influx_ok, "error": self.influx.last_error},
                "net": {"connected": self.device.connected, "device_id": self.device.identity, "error": self.device.last_error, "command_log": self.device.command_log_snapshot()},
                "thermal": self.model.snapshot(),
                "heater_config": {"ramp_enabled": self.model.heater_ramp_enabled, "ramp_minutes": self.model.heater_ramp_minutes},
            }

    def history_point(self) -> dict:
        thermal = self.model.snapshot()
        return {
            "timestamp": self.selected_time.isoformat(),
            "outside_temperature_c": thermal["outside_temperature_c"],
            "heater_actual_w": thermal["heater_actual_w"],
            "aux_heater_power_w": thermal["aux_heater_power_w"],
            "rooms": [
                {"temperature_c": room["temperature_c"], "valve_percent": room["valve_percent"],
                 "heat_power_w": room["heat_power_w"]}
                for room in thermal["rooms"]
            ],
        }


state = State()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def json(self, value: object, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state": return self.json(state.snapshot())
        if path == "/api/history":
            try:
                after = max(0, int(urlparse(self.path).query.removeprefix("after="))) if "after=" in self.path else 0
            except ValueError:
                after = 0
            with state.lock:
                return self.json({"points": state.history[after:], "next": len(state.history)})
        if path == "/api/weather": return self.json({"source": state.weather.source, "points": state.weather.graph()})
        if path == "/":
            data = (ROOT / "static" / "index.html").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in ("/api/start", "/api/stop", "/api/outside", "/api/weather/select", "/api/heater/config", "/api/aux-heater"):
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if path == "/api/start": state.set_running(True)
        elif path == "/api/stop": state.set_running(False)
        elif path == "/api/heater/config":
            with state.lock:
                state.model.configure_heater(bool(body.get("ramp_enabled", True)), float(body.get("ramp_minutes", 10)))
        elif path == "/api/aux-heater":
            with state.lock:
                state.model.set_aux_heater(bool(body.get("on", False)))
        elif path == "/api/weather/select":
            selected = dt.datetime.fromisoformat(str(body["timestamp"]))
            with state.lock:
                state.selected_time = selected
        else:
            with state.lock: state.model.outside_temperature_c = float(body["temperature_c"])
        self.json(state.snapshot())


def main() -> None:
    port = int(os.getenv("WEB_PORT", "8090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    state.start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: state.stop(); server.server_close()


if __name__ == "__main__": main()
