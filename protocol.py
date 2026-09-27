"""Subset autonome du protocole de trame FreakyDomos utilisé par NET."""
from __future__ import annotations

from dataclasses import dataclass

HEADER = b"@#&:"


def crc16(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte << 8
        for _ in range(8):
            value = ((value << 1) ^ (0x8001 if value & 0x8000 else 0)) & 0xFFFF
    return value


@dataclass(frozen=True)
class Frame:
    crc: int
    counter: int
    to: str
    source: str
    payload: tuple[str, ...]

    def encode(self, checked: bool = True) -> bytes:
        body = ":".join((str(self.counter), self.to, self.source, *self.payload)).encode() + b";"
        checksum = crc16(body) if checked else 0
        return HEADER + str(checksum).encode() + b":" + body

    @classmethod
    def decode(cls, data: bytes) -> "Frame":
        data = data.rstrip(b"\r\n")
        if not data.startswith(HEADER) or not data.endswith(b";"):
            raise ValueError("invalid frame")
        text_crc, body = data[len(HEADER):].split(b":", 1)
        received = int(text_crc)
        if received and crc16(body) != received:
            raise ValueError("invalid crc")
        fields = body[:-1].decode("ascii").split(":")
        if len(fields) < 4:
            raise ValueError("incomplete frame")
        return cls(received, int(fields[0]), fields[1], fields[2], tuple(fields[3:]))


class Parser:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self.buffer.extend(data)
        result = []
        while True:
            start = self.buffer.find(HEADER)
            if start < 0:
                self.buffer[:] = self.buffer[-3:]
                break
            del self.buffer[:start]
            end = self.buffer.find(b";")
            if end < 0:
                break
            raw = bytes(self.buffer[:end + 1])
            del self.buffer[:end + 1]
            try:
                result.append(Frame.decode(raw))
            except (ValueError, UnicodeError):
                continue
        return result
