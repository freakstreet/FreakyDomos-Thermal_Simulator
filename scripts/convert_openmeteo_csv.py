#!/usr/bin/env python3
"""Convertit la réponse JSON Open-Meteo en CSV attendu par le simulateur."""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: convert_openmeteo_csv.py input.json output.csv")
    source, target = map(Path, sys.argv[1:])
    payload = json.loads(source.read_text(encoding="utf-8"))
    times = payload["hourly"]["time"]
    temperatures = payload["hourly"]["temperature_2m"]
    if len(times) != len(temperatures):
        raise SystemExit("hourly time and temperature arrays have different lengths")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "temp_c"))
        for timestamp, temperature in zip(times, temperatures):
            local = dt.datetime.fromisoformat(timestamp).replace(tzinfo=ZoneInfo("Europe/Paris"))
            writer.writerow((local.isoformat(), f"{temperature:.2f}"))


if __name__ == "__main__":
    main()
