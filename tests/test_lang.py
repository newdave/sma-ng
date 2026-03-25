"""Tests for resources/lang.py - language code conversion."""
import pytest
from resources.lang import getAlpha3TCode, getAlpha2BCode


class TestGetAlpha3TCode:
    """Test conversion to ISO 639-3 (alpha3t) codes."""

    def test_english_from_alpha2(self):
        assert getAlpha3TCode('en') == 'eng'

    def test_english_from_alpha3(self):
        assert getAlpha3TCode('eng') == 'eng'

    def test_french(self):
        assert getAlpha3TCode('fr') == 'fra'

    def test_japanese(self):
        assert getAlpha3TCode('ja') == 'jpn'

    def test_german(self):
        assert getAlpha3TCode('de') == 'deu'

    def test_spanish(self):
        assert getAlpha3TCode('es') == 'spa'

    def test_invalid_returns_default(self):
        assert getAlpha3TCode('zzzz', 'und') == 'und'

    def test_invalid_no_default_returns_und(self):
        assert getAlpha3TCode('zzzz') == 'und'

    def test_empty_string(self):
        assert getAlpha3TCode('', 'und') == 'und'

    def test_passthrough_valid_alpha3(self):
        assert getAlpha3TCode('deu') == 'deu'


class TestGetAlpha2BCode:
    """Test conversion to ISO 639-1 (alpha2) codes."""

    def test_english(self):
        assert getAlpha2BCode('eng') == 'en'

    def test_french(self):
        assert getAlpha2BCode('fra') == 'fr'

    def test_from_alpha2(self):
        assert getAlpha2BCode('en') == 'en'

    def test_invalid_returns_default(self):
        assert getAlpha2BCode('zzzz', 'en') == 'en'
