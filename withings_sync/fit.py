"""Small FIT encoder for Garmin weight-scale/body-composition records."""

from __future__ import annotations

from datetime import datetime, timezone
import struct
from typing import Any

FIT_EPOCH = 631065600
CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)
UINT8 = (2, 1, 0x02, 0xFF, "B")
UINT16 = (4, 2, 0x84, 0xFFFF, "H")
UINT32 = (6, 4, 0x86, 0xFFFFFFFF, "I")
UINT32Z = (12, 4, 0x8C, 0, "I")
ENUM = (0, 1, 0x00, 0xFF, "B")


def _crc_byte(crc: int, byte: int) -> int:
    tmp = CRC_TABLE[crc & 0x0F]
    crc = (crc >> 4) & 0x0FFF
    crc ^= tmp ^ CRC_TABLE[byte & 0x0F]
    tmp = CRC_TABLE[crc & 0x0F]
    crc = (crc >> 4) & 0x0FFF
    crc ^= tmp ^ CRC_TABLE[(byte >> 4) & 0x0F]
    return crc


def _crc(data: bytes) -> int:
    result = 0
    for byte in data:
        result = _crc_byte(result, byte)
    return result


def _fit_timestamp(value: datetime | int | float) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.timestamp()
    result = int(value) - FIT_EPOCH
    if result < 0 or result >= UINT32[3]:
        raise ValueError("Timestamp is outside the FIT uint32 range.")
    return result


class FitEncoderWeight:
    """Encode a single Garmin FIT weight-scale file."""

    def __init__(self) -> None:
        self.data = bytearray()
        self._file_defined = False
        self._creator_defined = False
        self._device_defined = False
        self._weight_defined = False

    @staticmethod
    def _pack_value(base_type: tuple[int, int, int, int, str], value: Any) -> bytes:
        _, _, _, invalid, fmt = base_type
        if value is None:
            value = invalid
        else:
            value = int(round(value))
            if value < 0 or value >= invalid:
                raise ValueError(f"FIT value is outside {fmt} range.")
        return struct.pack("<" + fmt, value)

    def _write_message(
        self,
        local: int,
        fields: list[tuple[int, tuple[int, int, int, int, str], Any]],
        values: list[Any] | None = None,
        definition: bool = True,
    ) -> None:
        if definition:
            global_message = {0: 0, 1: 49, 2: 23, 3: 30}[local]
            self.data.extend(
                struct.pack("<BBBHB", 0x40 | local, 0, 0, global_message, len(fields))
            )
            for number, base_type, _ in fields:
                self.data.extend(struct.pack("<BBB", number, base_type[1], base_type[2]))
        self.data.append(local)
        if values is None:
            values = [field[2] for field in fields]
        for (_, base_type, _), value in zip(fields, values):
            self.data.extend(self._pack_value(base_type, value))

    def write_file_info(
        self,
        serial_number: int | None = None,
        time_created: datetime | None = None,
        manufacturer: int | None = None,
        product: int | None = None,
        number: int | None = None,
    ) -> None:
        created = time_created or datetime.now(timezone.utc)
        values = [
            serial_number,
            _fit_timestamp(created),
            manufacturer,
            product,
            number,
            9,
        ]
        fields = [
            (3, UINT32Z, values[0]), (4, UINT32, values[1]), (1, UINT16, values[2]),
            (2, UINT16, values[3]), (5, UINT16, values[4]), (0, ENUM, values[5]),
        ]
        self._write_message(0, fields, values, definition=not self._file_defined)
        self._file_defined = True

    def write_file_creator(
        self, software_version: int | None = None, hardware_version: int | None = None
    ) -> None:
        fields = [(0, UINT16, software_version), (1, UINT8, hardware_version)]
        self._write_message(1, fields, definition=not self._creator_defined)
        self._creator_defined = True

    def write_device_info(self, timestamp: datetime | int | float) -> None:
        fields: list[tuple[int, tuple[int, int, int, int, str], Any]] = [
            (253, UINT32, None), (3, UINT32Z, None), (7, UINT32, None),
            (8, UINT32, None), (2, UINT16, None), (4, UINT16, None),
            (5, UINT16, None), (10, UINT16, None), (0, UINT8, None),
            (1, UINT8, None), (6, UINT8, None), (11, UINT8, None),
        ]
        values = [_fit_timestamp(timestamp)] + [None] * (len(fields) - 1)
        self._write_message(2, fields, values, definition=not self._device_defined)
        self._device_defined = True

    def write_weight_scale(
        self,
        timestamp: datetime | int | float,
        weight: int | float,
        percent_fat: int | float | None = None,
        percent_hydration: int | float | None = None,
        visceral_fat_mass: int | float | None = None,
        bone_mass: int | float | None = None,
        muscle_mass: int | float | None = None,
        basal_met: int | float | None = None,
        active_met: int | float | None = None,
        physique_rating: int | float | None = None,
        metabolic_age: int | float | None = None,
        visceral_fat_rating: int | float | None = None,
        bmi: int | float | None = None,
    ) -> None:
        fields = [
            (253, UINT32, _fit_timestamp(timestamp)),
            (0, UINT16, _scaled(weight, 100)),
            (1, UINT16, _scaled(percent_fat, 100)),
            (2, UINT16, _scaled(percent_hydration, 100)),
            (3, UINT16, _scaled(visceral_fat_mass, 100)),
            (4, UINT16, _scaled(bone_mass, 100)),
            (5, UINT16, _scaled(muscle_mass, 100)),
            (7, UINT16, _scaled(basal_met, 4)),
            (9, UINT16, _scaled(active_met, 4)),
            (8, UINT8, physique_rating),
            (10, UINT8, metabolic_age),
            (11, UINT8, visceral_fat_rating),
            (13, UINT16, _scaled(bmi, 10)),
        ]
        self._write_message(3, fields, definition=not self._weight_defined)
        self._weight_defined = True

    def finish(self) -> None:
        data_size = len(self.data)
        header = struct.pack("<BBHI4s", 12, 16, 108, data_size, b".FIT")
        content = header + self.data
        self.data = bytearray(content + struct.pack("<H", _crc(content)))

    def getvalue(self) -> bytes:
        return bytes(self.data)


def _scaled(value: int | float | None, factor: int) -> int | float | None:
    return None if value is None else value * factor
