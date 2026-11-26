from dataclasses import dataclass


@dataclass(frozen=True)
class Phrase:
    phrase: str
    voice: str
    start: int
    stop: int

    @property
    def size(self):
        return self.stop - self.start


def make_initial_phrases(voices):
    return tuple(Phrase(f'initial:{voice.voice}', voice.voice, 0, len(voice.notes)) for voice in voices)


def validate_partition(voices, phrases):
    groups = {voice.voice: [] for voice in voices}
    identifiers = set()
    for phrase in phrases:
        if phrase.voice not in groups or phrase.phrase in identifiers:
            raise ValueError('invalid_phrase_partition')
        identifiers.add(phrase.phrase)
        groups[phrase.voice].append(phrase)
    ordered = tuple(phrase for group in groups.values() for phrase in group)
    if ordered != tuple(phrases):
        raise ValueError('invalid_phrase_order')
    for voice in voices:
        cursor = 0
        for phrase in groups[voice.voice]:
            if phrase.start != cursor or not phrase.start < phrase.stop <= len(voice.notes):
                raise ValueError('invalid_phrase_partition')
            cursor = phrase.stop
        if cursor != len(voice.notes):
            raise ValueError('incomplete_phrase_partition')


def split_phrase(phrase, position, left_id, right_id):
    if type(position) is not int or not 0 < position < phrase.size:
        raise ValueError('invalid_split_gap')
    cut = phrase.start + position
    left = Phrase(left_id, phrase.voice, phrase.start, cut)
    right = Phrase(right_id, phrase.voice, cut, phrase.stop)
    return left, right
