import json
from dataclasses import dataclass

from publication.voice_sheets import voice_sheet_from_record, voice_sheet_to_record


@dataclass(frozen=True)
class IssuedScore:
    issue: int
    draft: str
    revision: int


class EditionStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS editions (
            issue INTEGER PRIMARY KEY,
            draft TEXT NOT NULL REFERENCES drafts(draft),
            revision INTEGER NOT NULL REFERENCES revisions(revision)
        )''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS issued_sheets (
            issue INTEGER NOT NULL REFERENCES editions(issue),
            voice TEXT NOT NULL,
            record TEXT NOT NULL,
            PRIMARY KEY (issue, voice)
        )''')

    def save(self, draft, revision, sheets):
        cursor = self.db.execute('INSERT INTO editions(draft, revision) VALUES (?, ?)', (draft, revision))
        issue = cursor.lastrowid
        self.db.executemany(
            'INSERT INTO issued_sheets(issue, voice, record) VALUES (?, ?, ?)',
            [(issue, sheet.voice, json.dumps(voice_sheet_to_record(sheet), ensure_ascii=False)) for sheet in sheets],
        )
        return IssuedScore(issue, draft, revision)

    def get(self, issue):
        row = self.db.execute('SELECT issue, draft, revision FROM editions WHERE issue = ?', (issue,)).fetchone()
        if row is None:
            raise KeyError('unknown_issue')
        return IssuedScore(*row)

    def get_voice_sheet(self, issue, voice):
        row = self.db.execute(
            'SELECT record FROM issued_sheets WHERE issue = ? AND voice = ?', (issue, voice)
        ).fetchone()
        if row is None:
            self.get(issue)
            raise KeyError('unknown_voice')
        return voice_sheet_from_record(json.loads(row[0]))
