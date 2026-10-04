from datetime import datetime, timezone

from withings_sync.withings import (
    WithingsClient,
    parse_measurement_groups,
)


def test_parse_weight_composition_and_bmi():
    groups = [
        {
            "grpid": 1234,
            "date": 1786530600,
            "measures": [
                {"type": 1, "value": 72350, "unit": -3},
                {"type": 6, "value": 2120, "unit": -2},
                {"type": 77, "value": 39000, "unit": -3},
                {"type": 76, "value": 32800, "unit": -3},
                {"type": 88, "value": 3100, "unit": -3},
                {"type": 170, "value": 8, "unit": 0},
            ],
        },
        {"grpid": 1235, "date": 1786530700, "measures": [{"type": 6, "value": 20}]},
    ]

    measurements = parse_measurement_groups(groups, height_m=1.75)
    item = measurements[0]

    assert item.source_id == "1234"
    assert item.timestamp == datetime.fromtimestamp(1786530600, timezone.utc)
    assert item.weight == 72.35
    assert item.percent_fat == 21.2
    assert item.percent_hydration == 53.9
    assert item.muscle_mass == 32.8
    assert item.bone_mass == 3.1
    assert item.visceral_fat == 8
    assert item.bmi == 23.6
    assert len(measurements) == 1


def test_extract_code_accepts_code_or_validated_redirect_url():
    assert WithingsClient.extract_code("  auth-code  ") == "auth-code"
    assert (
        WithingsClient.extract_code(
            "https://example.org/callback?code=abc&state=s1", "s1"
        )
        == "abc"
    )
