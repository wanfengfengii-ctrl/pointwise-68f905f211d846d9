import sqlite3
from contextlib import contextmanager
from dataclasses import asdict

from annotations.intake import submit_mark
from annotations.receipts import ReceiptStore
from publication.editions import issue_score, read_score
from publication.history import EditionStore
from rehearsal.closeout import CloseoutStore, close_run
from rehearsal.errors import require_fields, require_integer, require_text
from rehearsal.handouts import HandoutStore, record_handout
from rehearsal.parts import PartStore
from rehearsal.sessions import RunStore
from score.scores import ScoreStore
from structure.operations import OperationStore
from structure.revisions import RevisionStore
from structure.splits import apply_split
from structure.undo import undo_split


class RehearsalService:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def _transaction(self, *, write=False):
        db = sqlite3.connect(self.database, isolation_level=None)
        try:
            db.execute('PRAGMA foreign_keys = ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        with self._transaction(write=True) as db:
            for store in (ScoreStore, RevisionStore, OperationStore, EditionStore, ReceiptStore,
                          RunStore, PartStore, HandoutStore, CloseoutStore):
                store(db).initialize()

    def prepare_run(self, run, draft):
        run = require_text(run, 'run')
        draft = require_text(draft, 'draft')
        with self._transaction(write=True) as db:
            score = ScoreStore(db).get(draft)
            session, created = RunStore(db).open_or_get(run, draft)
            parts = PartStore(db)
            if created:
                parts.register(run, score.voices)
            return {**asdict(session), 'parts': [asdict(parts.get(run, voice.voice)) for voice in score.voices]}

    def load(self, payload):
        with self._transaction(write=True) as db:
            score = ScoreStore(db).load(payload)
            revision = RevisionStore(db).create_initial(score)
            return {'draft': score.draft, 'revision': revision.revision, 'voices': [v.voice for v in score.voices]}

    def edit(self, payload):
        require_fields(payload, {'draft', 'parent', 'kind'}, {'phrase', 'position', 'operation'})
        draft = require_text(payload['draft'], 'draft')
        parent = require_integer(payload['parent'], 'parent', 1)
        kind = payload['kind']
        if kind == 'split':
            require_fields(payload, {'draft', 'parent', 'kind', 'phrase', 'position'})
            action = apply_split
            arguments = (require_text(payload['phrase'], 'phrase'), require_integer(payload['position'], 'position', 0))
        elif kind == 'undo':
            require_fields(payload, {'draft', 'parent', 'kind', 'operation'})
            action = undo_split
            arguments = (require_integer(payload['operation'], 'operation', 1),)
        else:
            raise ValueError('invalid_edit_kind')
        with self._transaction(write=True) as db:
            score = ScoreStore(db).get(draft)
            revision, operation = action(score, RevisionStore(db), OperationStore(db), parent, *arguments)
            return {'revision': revision.revision, 'operation': operation.operation}

    def issue(self, payload):
        require_fields(payload, {'draft', 'revision'})
        draft = require_text(payload['draft'], 'draft')
        revision = require_integer(payload['revision'], 'revision', 1)
        with self._transaction(write=True) as db:
            score = ScoreStore(db).get(draft)
            selected = RevisionStore(db).get(draft, revision)
            return issue_score(score, selected, ReceiptStore(db), EditionStore(db))

    def read(self, query):
        require_fields(query, {'voice'}, {'draft', 'issue'})
        voice = require_text(query['voice'], 'voice')
        if ('draft' in query) == ('issue' in query):
            raise ValueError('choose_draft_or_issue')
        draft = require_text(query['draft'], 'draft') if 'draft' in query else None
        issue = require_integer(query['issue'], 'issue', 1) if 'issue' in query else None
        with self._transaction() as db:
            return read_score(ScoreStore(db), RevisionStore(db), ReceiptStore(db), EditionStore(db),
                              voice=voice, draft=draft, issue=issue)

    def mark(self, payload):
        with self._transaction(write=True) as db:
            return submit_mark(payload, EditionStore(db), ReceiptStore(db)).record

    def handout(self, payload):
        require_fields(payload, {'run', 'voice', 'issue'})
        run = require_text(payload['run'], 'run')
        voice = require_text(payload['voice'], 'voice')
        issue = require_integer(payload['issue'], 'issue', 1)
        with self._transaction(write=True) as db:
            record = record_handout(RunStore(db), PartStore(db), EditionStore(db), HandoutStore(db),
                                    run=run, voice=voice, issue=issue)
            return asdict(record)

    def close(self, payload):
        require_fields(payload, {'run'})
        run = require_text(payload['run'], 'run')
        with self._transaction(write=True) as db:
            return close_run(RunStore(db), HandoutStore(db), CloseoutStore(db), run).to_record()
