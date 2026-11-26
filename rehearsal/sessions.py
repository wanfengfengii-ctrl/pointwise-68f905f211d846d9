from dataclasses import dataclass

from rehearsal.errors import ConflictError


@dataclass(frozen=True)
class RehearsalRun:
    run: str
    draft: str
    closed: bool


class RunStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS runs (
            run TEXT PRIMARY KEY NOT NULL,
            draft TEXT NOT NULL REFERENCES drafts(draft),
            closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1))
        )''')

    def open_or_get(self, run, draft):
        cursor = self.db.execute('INSERT OR IGNORE INTO runs(run, draft) VALUES (?, ?)', (run, draft))
        existing = self.get(run)
        if existing.draft != draft:
            raise ConflictError('run_draft_changed')
        return existing, cursor.rowcount == 1

    def get(self, run):
        row = self.db.execute('SELECT draft, closed FROM runs WHERE run = ?', (run,)).fetchone()
        if row is None:
            raise KeyError('unknown_run')
        return RehearsalRun(run, row[0], bool(row[1]))

    def mark_closed(self, run):
        self.db.execute('UPDATE runs SET closed = 1 WHERE run = ?', (run,))
