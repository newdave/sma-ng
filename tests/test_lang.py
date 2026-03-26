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

    def test_undefined_returns_und(self):
        result = getAlpha2BCode('und')
        assert result == 'und'

    def test_empty_string(self):
        assert getAlpha2BCode('', 'en') == 'en'

    def test_invalid_no_default(self):
        assert getAlpha2BCode('zzz') == 'und'


class TestGetAlpha3TCodeFallbacks:
    """Test fromalpha3b/fromalpha3t fallback paths for 3-letter codes."""

    def test_alpha3b_code_fre(self):
        # 'fre' is the alpha3b code for French; alpha3t is 'fra'
        assert getAlpha3TCode('fre') == 'fra'

    def test_alpha3b_code_ger(self):
        # 'ger' is alpha3b for German; alpha3t is 'deu'
        assert getAlpha3TCode('ger') == 'deu'

    def test_alpha3b_code_chi(self):
        # 'chi' is alpha3b for Chinese; alpha3t is 'zho'
        assert getAlpha3TCode('chi') == 'zho'

    def test_invalid_3letter_returns_default(self):
        assert getAlpha3TCode('zzz', 'und') == 'und'

    def test_invalid_3letter_no_default(self):
        assert getAlpha3TCode('zzz') == 'und'

    def test_code_with_dots_stripped(self):
        # Dots should be stripped before lookup
        assert getAlpha3TCode('en.') == 'eng'

    def test_code_with_whitespace_stripped(self):
        assert getAlpha3TCode(' en ') == 'eng'

    def test_uppercase_normalized(self):
        assert getAlpha3TCode('ENG') == 'eng'


class TestGetAlpha2BCodeFallbacks:
    """Test fromalpha3b/fromalpha3t fallback paths."""

    def test_alpha3b_code_fre(self):
        assert getAlpha2BCode('fre') == 'fr'

    def test_alpha3b_code_ger(self):
        assert getAlpha2BCode('ger') == 'de'

    def test_alpha3t_code_fra(self):
        assert getAlpha2BCode('fra') == 'fr'

    def test_invalid_2letter_returns_default(self):
        assert getAlpha2BCode('zz', 'en') == 'en'

    def test_code_with_dots(self):
        assert getAlpha2BCode('en.') == 'en'

    def test_uppercase(self):
        assert getAlpha2BCode('ENG') == 'en'
