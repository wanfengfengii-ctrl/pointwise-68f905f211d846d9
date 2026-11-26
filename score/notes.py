from dataclasses import dataclass
from fractions import Fraction

from rehearsal.errors import require_fields, require_text
from score.beats import parse_beat, require_nondecreasing_beats


@dataclass(frozen=True)
class NoteEvent:
    number: int
    beat: Fraction
    lyric: str


def parse_note_events(items):
    if not isinstance(items, list) or not items:
        raise ValueError('invalid_notes')
    events = []
    for number, item in enumerate(items, 1):
        require_fields(item, {'beat', 'lyric'})
        beat = parse_beat(item['beat'])
        lyric = require_text(item['lyric'], 'lyric')
        events.append(NoteEvent(number, beat, lyric))
    require_nondecreasing_beats(event.beat for event in events)
    return tuple(events)


class NoteStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS notes (
            draft TEXT NOT NULL,
            voice TEXT NOT NULL,
            number INTEGER NOT NULL,
            beat TEXT NOT NULL,
            lyric TEXT NOT NULL,
            PRIMARY KEY (draft, voice, number),
            FOREIGN KEY (draft, voice) REFERENCES voices(draft, voice)
        )''')

    def save(self, draft, voice, notes):
        self.db.executemany(
            'INSERT INTO notes(draft, voice, number, beat, lyric) VALUES (?, ?, ?, ?, ?)',
            [(draft, voice, note.number, str(note.beat), note.lyric) for note in notes],
        )

    def list_for_voice(self, draft, voice):
        rows = self.db.execute(
            'SELECT number, beat, lyric FROM notes WHERE draft = ? AND voice = ? ORDER BY number',
            (draft, voice),
        )
        return tuple(NoteEvent(number, Fraction(beat), lyric) for number, beat, lyric in rows)
