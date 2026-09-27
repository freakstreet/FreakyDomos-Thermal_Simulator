from __future__ import annotations

import os
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone

from protocol import Frame, Parser
from thermal import ThermalModel


class NetDevice:
    """Client TCP NET autonome compatible avec la découverte du master."""

    SENSOR_NAMES = ["TEMP Chambre RDC", "TEMP Chambre étage", "TEMP SDB", "TEMP Salon"]
    OUTSIDE_SENSOR_NAME = "TEMP Extérieure"
    VALVE_NAMES = ["Heat chmb RDC", "Heat chmb Mezz", "Heat SDB", "Heat Saloon"]
    MIN_FRAME_INTERVAL = 0.015

    def __init__(self, model: ThermalModel) -> None:
        self.model = model
        self.host = os.getenv("NET_MASTER_HOST", "")
        self.port = int(os.getenv("NET_MASTER_PORT", "8082"))
        self.token = os.getenv("NET_MASTER_TOKEN", "")
        self.identity = os.getenv("NET_DEVICE_ID", "SIM")[:5]
        self.counter = 0
        self.sock: socket.socket | None = None
        self.connected = False
        self.last_error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="net-device", daemon=True)
        self.valve_targets = [0.0] * 4
        self.valve_status = ["NOM_IDLE"] * 4
        self._valve_thread = threading.Thread(target=self._valve_loop, name="net-valves", daemon=True)
        self._send_lock = threading.Lock()
        self._last_send_at = 0.0
        self.command_log: deque[str] = deque(maxlen=200)
        self._log_lock = threading.Lock()

    def start(self) -> None:
        self._valve_thread.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()

    def _valve_loop(self) -> None:
        """Simule le mouvement d'une vanne positionnelle à 32 %/s."""
        while not self._stop.wait(1.0):
            changed = False
            for index, room in enumerate(self.model.rooms):
                target = self.valve_targets[index]
                actual = room.valve_percent
                if abs(target - actual) < 0.1:
                    room.valve_percent = target
                    self.valve_status[index] = "NOM_IDLE"
                    continue
                step = min(32.0, abs(target - actual))
                room.valve_percent = actual + (step if target > actual else -step)
                self.valve_status[index] = "NOM_OPENING" if target > actual else "NOM_CLOSING"
                changed = True
            if changed:
                self.publish_values()

    def send(self, to: str, *payload: str, checked: bool = True) -> None:
        with self._send_lock:
            elapsed = time.monotonic() - self._last_send_at
            if elapsed < self.MIN_FRAME_INTERVAL:
                time.sleep(self.MIN_FRAME_INTERVAL - elapsed)
            sock = self.sock
            if not sock or not self.connected:
                return
            self.counter = self.counter % 65535 + 1
            wire = Frame(0, self.counter, to, self.identity, tuple(payload)).encode(checked)
            try:
                sock.sendall(wire)
                self._last_send_at = time.monotonic()
            except OSError as error:
                self.last_error = str(error)

    def log_command(self, frame: Frame) -> None:
        if frame.source != "MST" or not frame.payload or frame.payload[0] == "ACK":
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        line = f"[{stamp}] RX MST → {frame.to} : {':'.join(frame.payload)}"
        with self._log_lock:
            self.command_log.append(line)

    def command_log_snapshot(self) -> list[str]:
        with self._log_lock:
            return list(self.command_log)

    def publish_discovery(self) -> None:
        self.send("MST", "SETTINGS", "NAME", "NET Thermal Simulator")
        for index, name in enumerate(self.SENSOR_NAMES):
            self.send("MST", "SETTINGS", "SENSOR", f"SIM_TEMP_{index}", name, "TEMP", "20", "4", "60", "0", "0", "0")
        self.send("MST", "SETTINGS", "SENSOR", "SIM_TEMP_EXT", self.OUTSIDE_SENSOR_NAME, "TEMP", str(self.model.outside_temperature_c), "4", "60", "0", "0", "0")
        for index, name in enumerate(self.VALVE_NAMES):
            self.send("MST", "SETTINGS", "IO", f"SIM_VALVE_{index}", name, "AIR POS VALVE", "0", "4", "0", "0", "0", "0")
        self.send("MST", "SETTINGS", "IO", "SIM_HEATER", "HEATER", "HEATER", "0", "4", "3000", "12000", "1", "5", self.model.heater_status)
        self.send("MST", "DISCOVER", "DONE")

    def publish_values(self) -> None:
        for index, room in enumerate(self.model.rooms):
            self.send("MST", "SENSOR", self.SENSOR_NAMES[index], "TEMP", f"{room.temperature_c:.2f}")
            self.send("MST", "STATUS", "IO", f"SIM_VALVE_{index}", self.VALVE_NAMES[index], f"{room.valve_percent:.0f}", self.valve_status[index])
        self.send("MST", "SENSOR", self.OUTSIDE_SENSOR_NAME, "TEMP", f"{self.model.outside_temperature_c:.2f}")
        self.send("MST", "STATUS", "IO", "SIM_HEATER", "HEATER", str(self.model.heater_level), self.model.heater_status)

    def _handle(self, frame: Frame) -> None:
        self.log_command(frame)
        # Toute trame applicative FreakyDomos portant un CRC non nul doit être
        # acquittée. Les ACK eux-mêmes ont un CRC nul et ne déclenchent jamais
        # un ACK en retour.
        if frame.crc and (not frame.payload or frame.payload[0] != "ACK"):
            self.send(frame.source, "ACK", str(frame.counter), checked=False)
        if frame.to not in (self.identity, "BCST") or not frame.payload:
            return
        payload = frame.payload
        if payload[0] == "DISCOVER" and len(payload) > 1 and payload[1] == "FULL":
            self.publish_discovery()
            self.publish_values()
        elif payload[0] == "ACTION" and len(payload) >= 3:
            name, action = payload[1], payload[2]
            value = payload[3] if len(payload) > 3 else None
            if name.startswith("Heat "):
                index = self.VALVE_NAMES.index(name) if name in self.VALVE_NAMES else -1
                if index >= 0:
                    if action == "OPEN": self.valve_targets[index] = 100
                    elif action == "CLOSE": self.valve_targets[index] = 0
                    elif action == "STOP": self.valve_targets[index] = self.model.rooms[index].valve_percent
                    elif action == "VALUE" and value is not None: self.valve_targets[index] = max(0, min(100, float(value)))
            elif name == "HEATER":
                if action == "OFF": self.model.set_heater(False)
                elif action == "ON": self.model.set_heater(True)
                elif action == "POWER" and value is not None: self.model.set_power(int(value))
            self.publish_values()

    def _run(self) -> None:
        parser = Parser()
        while not self._stop.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=5) as sock:
                    self.sock = sock
                    sock.sendall(f"AUTH {self.token}\n".encode())
                    sock.settimeout(1)
                    self.connected = True
                    self.last_error = ""
                    self.send("MST", "DISCOVER", "PRESENT")
                    while not self._stop.is_set():
                        try:
                            data = sock.recv(4096)
                            if not data: raise OSError("connexion fermée par le master")
                            for frame in parser.feed(data): self._handle(frame)
                        except socket.timeout:
                            continue
            except (OSError, ValueError) as error:
                self.connected = False
                self.last_error = str(error)
                time.sleep(3)
            finally:
                self.connected = False
                self.sock = None
