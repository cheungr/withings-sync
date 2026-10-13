import struct
from datetime import datetime, timezone

from withings_sync.fit import FitEncoderWeight, _crc


def test_weight_scale_file_has_valid_header_crc_and_composition_fields():
    encoder = FitEncoderWeight()
    measured_at = datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc)
    encoder.write_file_info(time_created=measured_at)
    encoder.write_file_creator()
    encoder.write_device_info(measured_at)
    encoder.write_weight_scale(
        measured_at,
        weight=72.35,
        percent_fat=21.2,
        percent_hydration=54.1,
        bone_mass=3.1,
        muscle_mass=33.4,
        bmi=23.8,
    )
    encoder.finish()
    data = encoder.getvalue()

    assert data[8:12] == b".FIT"
    assert struct.unpack_from("<I", data, 4)[0] == len(data) - 14
    assert struct.unpack_from("<H", data, len(data) - 2)[0] == _crc(data[:-2])

    offset = 12
    definitions = {}
    weight_fields = None
    while offset < len(data) - 2:
        header = data[offset]
        offset += 1
        local = header & 0x0F
        if header & 0x40:
            architecture = data[offset + 1]
            global_message = struct.unpack_from(
                "<H" if architecture == 0 else ">H", data, offset + 2
            )[0]
            count = data[offset + 4]
            offset += 5
            fields = []
            for _ in range(count):
                number, size, base_type = struct.unpack_from("<BBB", data, offset)
                fields.append((number, size, base_type))
                offset += 3
            definitions[local] = (global_message, fields)
            if global_message == 30:
                weight_fields = fields
        else:
            offset += sum(size for _, size, _ in definitions[local][1])

    assert weight_fields is not None
    assert {number for number, _, _ in weight_fields} >= {0, 1, 2, 4, 5, 13, 253}
