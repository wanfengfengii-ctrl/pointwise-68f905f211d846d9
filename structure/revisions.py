from dataclasses import dataclass

from rehearsal.errors import ConflictError
from structure.phrases import Phrase, make_initial_phrases, validate_partition


@dataclass(frozen=True)
class PhraseRevision:
    revision: int
    draft: str
    parent: int | None
    phrases: tuple[Phrase, ...]

    def phrases_for(self, voice):
        return tuple(phrase for phrase in self.phrases if phrase.voice == voice)

    def find_phrase(self, phrase_id):
        for phrase in self.phrases:
            if phrase.phrase == phrase_id:
                return phrase
        raise KeyError('unknown_phrase')

    def locate_gap(self, voice, gap, source_phrase):
        candidates = [phrase for phrase in self.phrases
                      if phrase.voice == voice and phrase.start <= gap <= phrase.stop]
        if not candidates:
            raise RuntimeError('Gap outside the revision voice')
        landing = next((phrase for phrase in candidates if phrase.phrase == source_phrase), candidates[0])
        return landing.phrase, gap - landing.start


class RevisionStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS revisions (
            revision INTEGER PRIMARY KEY,
            draft TEXT NOT NULL REFERENCES drafts(draft),
            parent INTEGER REFERENCES revisions(revision)
        )''')
        self.db.execute('CREATE INDEX IF NOT EXISTS revisions_by_draft ON revisions(draft, revision)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS revision_phrases (
            revision INTEGER NOT NULL REFERENCES revisions(revision),
            ordinal INTEGER NOT NULL,
            phrase TEXT NOT NULL,
            voice TEXT NOT NULL,
            start INTEGER NOT NULL,
            stop INTEGER NOT NULL,
            PRIMARY KEY (revision, ordinal),
            UNIQUE (revision, phrase),
            CHECK (start >= 0 AND stop > start)
        )''')

    def _insert(self, score, parent, phrases):
        validate_partition(score.voices, phrases)
        cursor = self.db.execute('INSERT INTO revisions(draft, parent) VALUES (?, ?)', (score.draft, parent))
        revision = cursor.lastrowid
        self.db.executemany(
            '''INSERT INTO revision_phrases(revision, ordinal, phrase, voice, start, stop)
               VALUES (?, ?, ?, ?, ?, ?)''',
            [(revision, ordinal, item.phrase, item.voice, item.start, item.stop)
             for ordinal, item in enumerate(phrases)],
        )
        return PhraseRevision(revision, score.draft, parent, tuple(phrases))

    def create_initial(self, score):
        return self._insert(score, None, make_initial_phrases(score.voices))

    def get(self, draft, revision):
        row = self.db.execute(
            'SELECT parent FROM revisions WHERE draft = ? AND revision = ?', (draft, revision)
        ).fetchone()
        if row is None:
            raise KeyError('unknown_revision')
        rows = self.db.execute(
            '''SELECT phrase, voice, start, stop FROM revision_phrases
               WHERE revision = ? ORDER BY ordinal''',
            (revision,),
        )
        return PhraseRevision(revision, draft, row[0], tuple(Phrase(*item) for item in rows))

    def get_head(self, draft):
        row = self.db.execute(
            'SELECT revision FROM revisions WHERE draft = ? ORDER BY revision DESC LIMIT 1', (draft,)
        ).fetchone()
        if row is None:
            raise KeyError('unknown_draft')
        return self.get(draft, row[0])

    def require_head(self, draft, parent):
        head = self.get_head(draft)
        if head.revision != parent:
            raise ConflictError('stale_parent')
        return head

    def append(self, score, parent, phrases):
        self.require_head(score.draft, parent)
        return self._insert(score, parent, phrases)
