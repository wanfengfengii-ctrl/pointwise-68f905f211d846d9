from dataclasses import dataclass

from rehearsal.errors import ConflictError


@dataclass(frozen=True)
class Handout:
    handout_id: int
    run: str
    voice: str
    issue: int


class HandoutStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS handouts (
            handout_id INTEGER PRIMARY KEY,
            run TEXT NOT NULL,
            voice TEXT NOT NULL,
            issue INTEGER NOT NULL REFERENCES editions(issue),
            FOREIGN KEY (run, voice) REFERENCES rehearsal_parts(run, voice)
        )''')
        self.db.execute('CREATE INDEX IF NOT EXISTS handouts_by_run ON handouts(run, handout_id)')

    def append(self, run, voice, issue):
        cursor = self.db.execute('INSERT INTO handouts(run, voice, issue) VALUES (?, ?, ?)', (run, voice, issue))
        return Handout(cursor.lastrowid, run, voice, issue)

    def list_for_run(self, run):
        rows = self.db.execute(
            'SELECT handout_id, run, voice, issue FROM handouts WHERE run = ? ORDER BY handout_id', (run,)
        )
        return tuple(Handout(*row) for row in rows)


def record_handout(runs, parts, editions, handouts, *, run, voice, issue):
    rehearsal = runs.get(run)
    if rehearsal.closed:
        raise ConflictError('run_closed')
    parts.get(run, voice)
    edition = editions.get(issue)
    if edition.draft != rehearsal.draft:
        raise ConflictError('edition_for_other_draft')
    editions.get_voice_sheet(issue, voice)
    record = handouts.append(run, voice, issue)
    parts.set_current_issue(run, voice, issue)
    return record
