"""ISO 639 language code conversion utilities using the babelfish library."""

from babelfish import Language

from converter.avcodecs import BaseCodec


def getAlpha3TCode(code, default=None):
    """Convert a language code to ISO 639-2/T (terminological) alpha-3 form.

    Accepts 2-letter (ISO 639-1) or 3-letter (ISO 639-2/B or 639-2/T) codes
    and tries multiple babelfish lookup strategies before falling back to the
    default.

    Args:
        code: Input language code string (e.g. ``"en"``, ``"eng"``, ``"fra"``).
        default: Value to return when conversion fails. Defaults to
            ``BaseCodec.UNDEFINED`` (``"und"``).

    Returns:
        ISO 639-2/T alpha-3 code string, or ``default`` on failure.
    """
    lang = default or BaseCodec.UNDEFINED
    if not code or code == BaseCodec.UNDEFINED:
        return lang

    code = code.strip().lower().replace(".", "")

    if len(code) == 3:
        try:
            lang = Language(code).alpha3t
        except:
            try:
                lang = Language.fromalpha3b(code).alpha3t
            except:
                try:
                    lang = Language.fromalpha3t(code).alpha3t
                except:
                    pass
    elif len(code) == 2:
        try:
            lang = Language.fromalpha2(code).alpha3t
        except:
            pass
    return lang


def getAlpha2BCode(code, default=None):
    """Convert a language code to ISO 639-1 alpha-2 form.

    Accepts 2-letter (ISO 639-1) or 3-letter (ISO 639-2/B or 639-2/T) codes
    and tries multiple babelfish lookup strategies before falling back to the
    default.

    Args:
        code: Input language code string (e.g. ``"en"``, ``"eng"``, ``"fra"``).
        default: Value to return when conversion fails. Defaults to
            ``BaseCodec.UNDEFINED`` (``"und"``).

    Returns:
        ISO 639-1 alpha-2 code string, or ``default`` on failure.
    """
    lang = default or BaseCodec.UNDEFINED
    if not code or code == BaseCodec.UNDEFINED:
        return lang

    code = code.strip().lower().replace(".", "")

    if len(code) == 3:
        try:
            lang = Language(code).alpha2
        except:
            try:
                lang = Language.fromalpha3b(code).alpha2
            except:
                try:
                    lang = Language.fromalpha3t(code).alpha2
                except:
                    pass
    elif len(code) == 2:
        try:
            lang = Language.fromalpha2(code).alpha2
        except:
            pass
    return lang
