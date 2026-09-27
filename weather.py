from __future__ import annotations

import csv
import datetime as dt
import math
from pathlib import Path


class WeatherSeries:
    def __init__(self, csv_path: str = "") -> None:
        self.points: list[tuple[dt.datetime, float]] = []
        self.source = "manual"
        if csv_path and Path(csv_path).exists():
            self.load_csv(Path(csv_path))

    def load_csv(self, path: Path) -> None:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                timestamp = dt.datetime.fromisoformat(row["timestamp"])
                self.points.append((timestamp, float(row["temp_c"])))
        self.points.sort()
        self.source = str(path)

    def value_at(self, timestamp: dt.datetime, fallback: float = 5.0) -> float:
        if not self.points:
            # Fallback explicitement non historique : variation saisonnière de démonstration.
            day = timestamp.timetuple().tm_yday
            return round(11.0 + 8.0 * math.sin(2 * math.pi * (day - 80) / 365), 2)
        if timestamp <= self.points[0][0]:
            return self.points[0][1]
        for (left_time, left), (right_time, right) in zip(self.points, self.points[1:]):
            if left_time <= timestamp <= right_time:
                fraction = (timestamp - left_time).total_seconds() / (right_time - left_time).total_seconds()
                return left + fraction * (right - left)
        return self.points[-1][1]

    def graph(self) -> list[dict[str, object]]:
        return [{"timestamp": point.isoformat(), "temperature_c": value} for point, value in self.points]
