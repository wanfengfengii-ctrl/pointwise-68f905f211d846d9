from dataclasses import dataclass


@dataclass(frozen=True)
class RehearsalPart:
    run: str
    voice: str
    current_issue: int | None


class PartStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS rehearsal_parts (
            run TEXT NOT NULL REFERENCES runs(run),
            voice TEXT NOT NULL,
            current_issue INTEGER REFERENCES editions(issue),
            PRIMARY KEY (run, voice)
        )''')

    def register(self, run, voices):
        self.db.executemany('INSERT INTO rehearsal_parts(run, voice) VALUES (?, ?)',
                            [(run, voice.voice) for voice in voices])

    def get(self, run, voice):
        row = self.db.execute('SELECT current_issue FROM rehearsal_parts WHERE run = ? AND voice = ?',
                              (run, voice)).fetchone()
        if row is None:
            raise KeyError('unknown_run_voice')
        return RehearsalPart(run, voice, row[0])

    def set_current_issue(self, run, voice, issue):
        self.db.execute('UPDATE rehearsal_parts SET current_issue = ? WHERE run = ? AND voice = ?',
                        (issue, run, voice))
