import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from rehearsal.service import RehearsalService


SAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'draft.json'


def sample(draft='bridge'):
    data = json.loads(SAMPLE.read_text(encoding='utf-8'))
    data['draft'] = draft
    return data


def lyrics(sheet):
    return [[note['lyric'] for note in line['notes']] for line in sheet['phrases']]


def placements(sheet):
    return [(line['phrase'], mark['position'], mark['mark_id'])
            for line in sheet['phrases'] for mark in line['marks']]


class WorkflowCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='bridge-tests-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / 'rehearsal.sqlite3'
        self.reopen()

    def reopen(self):
        self.service = RehearsalService(self.database)
        self.service.initialize()

    def load(self, draft='bridge'):
        return self.service.load(sample(draft))

    def working(self, voice='soprano', draft='bridge'):
        return self.service.read({'voice': voice, 'draft': draft})

    def fixed(self, edition, voice='soprano'):
        return self.service.read({'voice': voice, 'issue': edition['issue']})

    def issue(self, revision, draft='bridge'):
        return self.service.issue({'draft': draft, 'revision': revision})

    def split(self, parent, phrase, position=2, draft='bridge'):
        return self.service.edit({'kind': 'split', 'draft': draft, 'parent': parent,
                                  'phrase': phrase, 'position': position})

    def undo(self, parent, operation, draft='bridge'):
        return self.service.edit({'kind': 'undo', 'draft': draft, 'parent': parent, 'operation': operation})

    def handout(self, edition, voice='soprano', run='morning'):
        return self.service.handout({'run': run, 'voice': voice, 'issue': edition['issue']})

    def split_trial(self):
        initial = self.load()
        before = self.working()
        split = self.split(initial['revision'], before['phrases'][0]['phrase'])
        edition = self.issue(split['revision'])
        left, right = [line['phrase'] for line in self.fixed(edition)['phrases']]
        return before, split, edition, left, right

    def count_state(self):
        with closing(sqlite3.connect(self.database)) as db:
            return {
                table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                for table in ('drafts', 'revisions', 'operations', 'editions', 'breath_marks',
                              'mark_receipts', 'runs', 'handouts', 'closeouts')
            }


def mark_request(edition, phrase, position, request='A', voice='soprano', text='换气'):
    return {'request': request, 'issue': edition['issue'], 'voice': voice, 'phrase': phrase,
            'position': position, 'text': text}
