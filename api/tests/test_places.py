from __future__ import annotations

import pytest

from api.reference import places


@pytest.fixture(autouse=True)
def restore_map():
    original = places._city_by_code
    yield
    places._reset_for_tests(original)


class TestSamePlace:
    def test_airport_matches_its_city(self):
        """The case that broke DEL-LHR: every row came back stamped LON."""
        places._reset_for_tests({"LHR": "LON", "LGW": "LON", "DXB": "DXB"})

        assert places.same_place("LON", "LHR")
        assert places.same_place("LHR", "LGW")

    def test_neighbouring_city_does_not_match(self):
        """A DXB search also returns Sharjah fares, which are a different city."""
        places._reset_for_tests({"DXB": "DXB", "SHJ": "SHJ"})

        assert not places.same_place("SHJ", "DXB")

    def test_identical_codes_match_without_the_map(self):
        places._reset_for_tests(None)

        assert places.same_place("DXB", "DXB")

    def test_permissive_when_the_map_is_unavailable(self):
        """A failed download must not silently empty every search."""
        places._reset_for_tests(None)

        assert places.same_place("LON", "LHR")

    def test_unknown_codes_fall_back_to_themselves(self):
        places._reset_for_tests({"LHR": "LON"})

        assert places.city_code("ZZZ") == "ZZZ"
        assert not places.same_place("ZZZ", "LHR")
