import json
from dataclasses import dataclass

from annotations.positions import OriginalPosition
from annotations.requests import MarkRequest, request_arguments


@dataclass(frozen=True)
class BreathMark:
    mark_id: int
    request: MarkRequest
    original: OriginalPosition


@dataclass(frozen=True)
class MarkReceipt:
    record: dict


_MARK_COLUMNS = 'm.mark_id, m.request, m.issue, m.voice, m.phrase, m.position, m.text, m.draft, m.revision, m.gap'


class ReceiptStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS breath_marks (
            mark_id INTEGER PRIMARY KEY,
            request TEXT NOT NULL UNIQUE,
            issue INTEGER NOT NULL REFERENCES editions(issue),
            voice TEXT NOT NULL,
            phrase TEXT NOT NULL,
            position INTEGER NOT NULL,
            text TEXT NOT NULL,
            draft TEXT NOT NULL REFERENCES drafts(draft),
            revision INTEGER NOT NULL REFERENCES revisions(revision),
            gap INTEGER NOT NULL,
            CHECK (position >= 0 AND gap >= position),
            FOREIGN KEY (draft, voice) REFERENCES voices(draft, voice)
        )''')
        self.db.execute('CREATE INDEX IF NOT EXISTS marks_by_voice ON breath_marks(draft, voice, mark_id)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS mark_receipts (
            mark_id INTEGER PRIMARY KEY REFERENCES breath_marks(mark_id),
            record TEXT NOT NULL
        )''')

    @staticmethod
    def _read_mark(row):
        return BreathMark(row[0], MarkRequest(*row[1:7]), OriginalPosition(*row[7:10]))

    def get_by_request(self, request):
        row = self.db.execute(
            f'''SELECT {_MARK_COLUMNS}, r.record FROM breath_marks AS m
                JOIN mark_receipts AS r ON r.mark_id = m.mark_id WHERE m.request = ?''',
            (request,),
        ).fetchone()
        if row is None:
            return None
        return self._read_mark(row), MarkReceipt(json.loads(row[10]))

    def save_acceptance(self, request, original):
        cursor = self.db.execute(
            '''INSERT INTO breath_marks(request, issue, voice, phrase, position, text, draft, revision, gap)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (request.request, request.issue, request.voice, request.phrase, request.position, request.text,
             original.draft, original.revision, original.gap),
        )
        record = {'mark_id': cursor.lastrowid, **request_arguments(request)}
        self.db.execute('INSERT INTO mark_receipts(mark_id, record) VALUES (?, ?)',
                        (cursor.lastrowid, json.dumps(record, ensure_ascii=False)))
        return MarkReceipt(record)

    def list_for_voice(self, draft, voice):
        rows = self.db.execute(
            f'SELECT {_MARK_COLUMNS} FROM breath_marks AS m WHERE m.draft = ? AND m.voice = ? ORDER BY m.mark_id',
            (draft, voice),
        )
        return tuple(self._read_mark(row) for row in rows)
