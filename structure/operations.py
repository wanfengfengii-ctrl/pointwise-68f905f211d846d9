from dataclasses import dataclass


@dataclass(frozen=True)
class SplitOperation:
    operation: int
    draft: str
    before: int
    after: int
    original: str
    left: str
    right: str


@dataclass(frozen=True)
class UndoOperation:
    operation: int
    draft: str
    before: int
    after: int
    split: int


class OperationStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS operations (
            operation INTEGER PRIMARY KEY,
            draft TEXT NOT NULL REFERENCES drafts(draft),
            before_revision INTEGER NOT NULL REFERENCES revisions(revision),
            after_revision INTEGER NOT NULL UNIQUE REFERENCES revisions(revision),
            original TEXT,
            left_phrase TEXT,
            right_phrase TEXT,
            undoes INTEGER UNIQUE REFERENCES operations(operation),
            CHECK ((undoes IS NULL AND original IS NOT NULL AND left_phrase IS NOT NULL AND right_phrase IS NOT NULL)
                OR (undoes IS NOT NULL AND original IS NULL AND left_phrase IS NULL AND right_phrase IS NULL))
        )''')

    def record_split(self, before, after, original, left, right):
        cursor = self.db.execute(
            '''INSERT INTO operations(draft, before_revision, after_revision, original, left_phrase, right_phrase)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (before.draft, before.revision, after.revision, original.phrase, left.phrase, right.phrase),
        )
        return SplitOperation(cursor.lastrowid, before.draft, before.revision, after.revision,
                              original.phrase, left.phrase, right.phrase)

    def get_split(self, draft, operation):
        row = self.db.execute(
            '''SELECT operation, draft, before_revision, after_revision, original, left_phrase, right_phrase
               FROM operations WHERE draft = ? AND operation = ? AND undoes IS NULL''',
            (draft, operation),
        ).fetchone()
        if row is None:
            raise KeyError('unknown_split')
        return SplitOperation(*row)

    def get_undo_for_split(self, operation):
        row = self.db.execute(
            '''SELECT operation, draft, before_revision, after_revision, undoes
               FROM operations WHERE undoes = ?''', (operation,),
        ).fetchone()
        return None if row is None else UndoOperation(*row)

    def record_undo(self, before, after, split):
        cursor = self.db.execute(
            '''INSERT INTO operations(draft, before_revision, after_revision, undoes)
               VALUES (?, ?, ?, ?)''',
            (before.draft, before.revision, after.revision, split.operation),
        )
        return UndoOperation(cursor.lastrowid, before.draft, before.revision, after.revision, split.operation)
