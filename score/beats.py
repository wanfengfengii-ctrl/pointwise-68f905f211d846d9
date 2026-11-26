import re
from fractions import Fraction
from itertools import pairwise


_RATIO = re.compile(r'(-?[0-9]+)/([0-9]+)')


def parse_beat(value):
    if type(value) is int:
        return Fraction(value)
    if isinstance(value, str):
        match = _RATIO.fullmatch(value)
        if match:
            numerator, denominator = map(int, match.groups())
            if denominator > 0:
                return Fraction(numerator, denominator)
    raise ValueError('invalid_beat')


def require_nondecreasing_beats(beats):
    for before, after in pairwise(beats):
        if after < before:
            raise ValueError('descending_beats')


def render_beat(beat):
    if beat.denominator == 1:
        return beat.numerator
    return f'{beat.numerator}/{beat.denominator}'
