"""Two-cell CJK print grid and optional locally installed Song typeface."""
from functools import lru_cache
from itertools import groupby
import os
from pathlib import Path
import sys

CELL = 5.84
SIZE = CELL * 2
FAMILY = 'SimSun, "Songti SC", STSong, "Noto Serif CJK SC", "Times New Roman", serif'


def runs(text):
    """Place each word at its original character column, including leading blanks."""
    # A long dotted rule must not stretch its two framing stars.
    def category(ch):
        if ch in '*#':
            return ch
        return (ch.isspace(), ch.isascii())

    for _, group in groupby(text, key=category):
        word = ''.join(group)
        yield word, CELL * sum(1 if ord(c) < 128 else 2 for c in word)


@lru_cache(maxsize=4)
def _register(path, index, stamp):
    from hashlib import sha256
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont, TTFError
    name = 'ClassicSong-' + sha256(f'{path}-{index}-{stamp}'.encode()).hexdigest()[:16]
    try:
        face = TTFont(name, str(path), subfontIndex=index)
        pdfmetrics.registerFont(face)
    except (OSError, TTFError):
        return None
    return name, face.face.charToGlyph


def song_font():
    """Prefer the original Windows typeface, then a Song equivalent with printable embedding."""
    custom = os.environ.get('DCSJ_CLASSIC_FONT')
    if custom:
        path, index = Path(custom), 0
    elif sys.platform == 'win32':
        path, index = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / 'simsun.ttc', 0
    elif sys.platform == 'darwin':
        path, index = Path('/System/Library/Fonts/Supplemental/Songti.ttc'), 6
    else:
        return None
    try:
        stat = path.stat()
        return _register(path, index, (stat.st_mtime_ns, stat.st_size))
    except OSError:
        return None
