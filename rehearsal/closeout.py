import json
from dataclasses import asdict, dataclass

from rehearsal.handouts import Handout


@dataclass(frozen=True)
class Closeout:
    run: str
    draft: str
    handouts: tuple[Handout, ...]

    def to_record(self):
        return {'run': self.run, 'draft': self.draft, 'handouts': [asdict(item) for item in self.handouts]}


class CloseoutStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS closeouts (
            run TEXT PRIMARY KEY NOT NULL REFERENCES runs(run),
            record TEXT NOT NULL
        )''')

    def save(self, closeout):
        self.db.execute('INSERT INTO closeouts(run, record) VALUES (?, ?)',
                        (closeout.run, json.dumps(closeout.to_record(), ensure_ascii=False)))

    def get(self, run):
        row = self.db.execute('SELECT record FROM closeouts WHERE run = ?', (run,)).fetchone()
        if row is None:
            raise RuntimeError('Closed rehearsal missing its saved handouts')
        record = json.loads(row[0])
        return Closeout(record['run'], record['draft'], tuple(Handout(**item) for item in record['handouts']))


def close_run(runs, handouts, closeouts, run):
    rehearsal = runs.get(run)
    if rehearsal.closed:
        return closeouts.get(run)
    closeout = Closeout(run, rehearsal.draft, handouts.list_for_run(run))
    closeouts.save(closeout)
    runs.mark_closed(run)
    return closeout
