from __future__ import annotations

import json
import os
import urllib.parse
import urllib.error
import urllib.request


class InfluxWriter:
    def __init__(self) -> None:
        self.url = os.getenv("INFLUXDB_URL", "").rstrip("/")
        self.token = os.getenv("INFLUXDB_TOKEN", "")
        self.org = os.getenv("INFLUXDB_ORG", "")
        self.bucket = os.getenv("INFLUXDB_BUCKET", "")
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token and self.org and self.bucket)

    def write(self, snapshot: dict, timestamp: str) -> bool:
        if not self.enabled:
            return False
        lines = [
            f"thermal_environment,device=SIM temperature_c={snapshot['outside_temperature_c']} {timestamp}",
            f"thermal_master,device=SIM heater_on={str(snapshot['heater_on']).lower()},heater_level={snapshot['heater_level']}i,heater_nominal_w={snapshot['heater_nominal_w']} {timestamp}",
        ]
        for room in snapshot["rooms"]:
            tags = f"device=SIM,room={room['key']}"
            lines.append(f"thermal_room,{tags} temperature_c={room['temperature_c']},valve_percent={room['valve_percent']},heat_power_w={room['heat_power_w']} {timestamp}")
        endpoint = f"{self.url}/api/v2/write?org={urllib.parse.quote(self.org)}&bucket={urllib.parse.quote(self.bucket)}&precision=ms"
        request = urllib.request.Request(endpoint, data=("\n".join(lines) + "\n").encode(), method="POST")
        request.add_header("Authorization", f"Token {self.token}")
        request.add_header("Content-Type", "text/plain; charset=utf-8")
        try:
            with urllib.request.urlopen(request, timeout=5):
                self.last_error = ""
                return True
        except (urllib.error.URLError, OSError) as error:
            self.last_error = str(error)
            return False
