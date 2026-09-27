from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Room:
    key: str
    name: str
    area_m2: float
    height_m: float = 2.5
    temperature_c: float = 20.0
    valve_percent: float = 0.0
    heat_power_w: float = 0.0

    @property
    def volume_m3(self) -> float:
        return self.area_m2 * self.height_m


class ThermalModel:
    """Modèle RC simple, paramétré pour isolation et inertie faibles."""

    HEATER_LEVELS = {1: 3000.0, 2: 5250.0, 3: 7500.0, 4: 9750.0, 5: 12000.0}

    def __init__(self, initial_temperature: float = 20.0) -> None:
        self.rooms = [
            Room("chambre_rdc", "Chambre RDC", 16, temperature_c=initial_temperature),
            Room("chambre_etage", "Chambre étage", 10, temperature_c=initial_temperature),
            Room("sdb", "SDB", 5, temperature_c=initial_temperature),
            Room("salon", "Salon", 80, temperature_c=initial_temperature),
        ]
        self.outside_temperature_c = 5.0
        self.heater_on = False
        self.heater_level = 0
        self.heater_nominal_w = 0.0
        self.heater_actual_w = 0.0
        self.heater_ramp_enabled = True
        self.heater_ramp_minutes = 10.0
        self.heater_status = "OFF"
        self.aux_heater_on = False
        self.aux_heater_power_w = 9000.0

    def set_aux_heater(self, on: bool) -> None:
        self.aux_heater_on = bool(on)

    def configure_heater(self, ramp_enabled: bool, ramp_minutes: float) -> None:
        self.heater_ramp_enabled = bool(ramp_enabled)
        self.heater_ramp_minutes = max(0.1, min(240.0, float(ramp_minutes)))
        if not self.heater_ramp_enabled:
            self.heater_actual_w = self.heater_nominal_w

    def set_valve(self, index: int, percent: float) -> None:
        self.rooms[index].valve_percent = max(0.0, min(100.0, percent))

    def set_heater(self, on: bool, level: int = 1) -> None:
        if not on:
            self.heater_on = False
            self.heater_level = 0
            self.heater_nominal_w = 0.0
            self.heater_status = "OFF"
            return
        self.heater_on = True
        # An ignition always starts at maximum power and owns the HEATER
        # level until the warm-up ramp has completed.
        self.heater_level = 5
        self.heater_nominal_w = self.HEATER_LEVELS[5]
        if self.heater_ramp_enabled and self.heater_actual_w < self.heater_nominal_w:
            self.heater_status = "WARMUP"
        else:
            self.heater_actual_w = self.heater_nominal_w
            self.heater_status = "ON"

    def set_power(self, level: int) -> bool:
        """Apply a master POWER command, except while the heater warms up."""
        if self.heater_status == "WARMUP":
            return False
        self.heater_on = True
        self.heater_level = max(1, min(5, int(level)))
        self.heater_nominal_w = self.HEATER_LEVELS[self.heater_level]
        self.heater_status = "ON"
        if not self.heater_ramp_enabled:
            self.heater_actual_w = self.heater_nominal_w
        return True

    def step(self, outside_temperature_c: float, dt_seconds: float = 60.0) -> None:
        self.outside_temperature_c = outside_temperature_c
        if self.heater_ramp_enabled:
            ramp_seconds = self.heater_ramp_minutes * 60.0
            maximum_change = 12000.0 * dt_seconds / ramp_seconds
            delta = self.heater_nominal_w - self.heater_actual_w
            self.heater_actual_w += max(-maximum_change, min(maximum_change, delta))
        else:
            self.heater_actual_w = self.heater_nominal_w
        if self.heater_status == "WARMUP" and self.heater_actual_w >= self.heater_nominal_w - 0.1:
            self.heater_actual_w = self.heater_nominal_w
            self.heater_status = "ON"
        # Les vannes sont des coefficients de répartition, pas des limiteurs
        # de puissance. Deux vannes à 100 % se partagent donc 12 kW à 6 kW
        # chacune ; aucune vanne n'est plafonnée à 3 kW.
        requests = [max(0.0, room.valve_percent) for room in self.rooms]
        requested_total = sum(requests)
        powers = [self.heater_actual_w * request / requested_total for request in requests] if requested_total else [0.0] * len(requests)
        if self.aux_heater_on:
            powers[3] += self.aux_heater_power_w
        previous = [room.temperature_c for room in self.rooms]

        for index, room in enumerate(self.rooms):
            # Isolation faible : pertes par surface + renouvellement d'air.
            loss_w_per_k = room.area_m2 * 3.0 + room.volume_m3 * 0.25
            # Inertie faible : air + masse effective légère.
            capacity_j_per_k = room.volume_m3 * 12000.0
            exchange_w_per_k = 0.0
            if index == 1:  # étage au-dessus de la chambre RDC et de la SDB
                exchange_w_per_k += 18.0 * (previous[0] - previous[1])
                exchange_w_per_k += 10.0 * (previous[2] - previous[1])
            elif index == 0:
                exchange_w_per_k += 18.0 * (previous[1] - previous[0])
            elif index == 2:
                exchange_w_per_k += 10.0 * (previous[1] - previous[2])
            net_w = loss_w_per_k * (outside_temperature_c - previous[index]) + exchange_w_per_k + powers[index]
            room.temperature_c = round(previous[index] + dt_seconds * net_w / capacity_j_per_k, 3)
            room.heat_power_w = round(powers[index], 1)

    def snapshot(self) -> dict:
        return {
            "outside_temperature_c": round(self.outside_temperature_c, 2),
            "heater_on": self.heater_on,
            "heater_level": self.heater_level,
            "heater_nominal_w": self.heater_nominal_w,
            "heater_actual_w": round(self.heater_actual_w, 1),
            "heater_ramp_enabled": self.heater_ramp_enabled,
            "heater_ramp_minutes": self.heater_ramp_minutes,
            "heater_status": self.heater_status,
            "aux_heater_on": self.aux_heater_on,
            "aux_heater_power_w": self.aux_heater_power_w if self.aux_heater_on else 0.0,
            "rooms": [
                {"key": room.key, "name": room.name, "temperature_c": room.temperature_c,
                 "valve_percent": room.valve_percent, "heat_power_w": room.heat_power_w,
                 "area_m2": room.area_m2, "volume_m3": room.volume_m3}
                for room in self.rooms
            ],
        }
