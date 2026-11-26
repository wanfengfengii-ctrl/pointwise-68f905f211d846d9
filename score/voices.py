from dataclasses import dataclass

from rehearsal.errors import require_fields, require_text
from score.notes import NoteEvent, NoteStore, parse_note_events


@dataclass(frozen=True)
class Voice:
    voice: str
    notes: tuple[NoteEvent, ...]


def parse_voices(items):
    if not isinstance(items, list) or not items:
        raise ValueError('invalid_voices')
    voices = []
    seen = set()
    for item in items:
        require_fields(item, {'voice', 'notes'})
        voice = require_text(item['voice'], 'voice')
        if voice in seen:
            raise ValueError('duplicate_voice')
        seen.add(voice)
        voices.append(Voice(voice, parse_note_events(item['notes'])))
    return tuple(voices)


class VoiceStore:
    def __init__(self, db):
        self.db = db
        self.notes = NoteStore(db)

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS voices (
            draft TEXT NOT NULL REFERENCES drafts(draft),
            voice TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            PRIMARY KEY (draft, voice),
            UNIQUE (draft, ordinal)
        )''')
        self.notes.initialize()

    def save(self, draft, voices):
        for ordinal, voice in enumerate(voices):
            self.db.execute(
                'INSERT INTO voices(draft, voice, ordinal) VALUES (?, ?, ?)',
                (draft, voice.voice, ordinal),
            )
            self.notes.save(draft, voice.voice, voice.notes)

    def list_for_draft(self, draft):
        rows = self.db.execute('SELECT voice FROM voices WHERE draft = ? ORDER BY ordinal', (draft,))
        return tuple(Voice(voice, self.notes.list_for_voice(draft, voice)) for (voice,) in rows)
