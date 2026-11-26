from dataclasses import dataclass

from rehearsal.errors import ConflictError, require_fields, require_text
from score.voices import Voice, VoiceStore, parse_voices


@dataclass(frozen=True)
class VoiceScore:
    draft: str
    voices: tuple[Voice, ...]

    def get_voice(self, voice):
        for part in self.voices:
            if part.voice == voice:
                return part
        raise KeyError('unknown_voice')


class ScoreStore:
    def __init__(self, db):
        self.db = db
        self.voices = VoiceStore(db)

    def initialize(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS drafts (draft TEXT PRIMARY KEY NOT NULL)')
        self.voices.initialize()

    def load(self, payload):
        require_fields(payload, {'draft', 'voices'})
        draft = require_text(payload['draft'], 'draft')
        voices = parse_voices(payload['voices'])
        inserted = self.db.execute('INSERT OR IGNORE INTO drafts(draft) VALUES (?)', (draft,))
        if inserted.rowcount != 1:
            raise ConflictError('draft_exists')
        self.voices.save(draft, voices)
        return VoiceScore(draft, voices)

    def get(self, draft):
        row = self.db.execute('SELECT draft FROM drafts WHERE draft = ?', (draft,)).fetchone()
        if row is None:
            raise KeyError('unknown_draft')
        return VoiceScore(draft, self.voices.list_for_draft(draft))
