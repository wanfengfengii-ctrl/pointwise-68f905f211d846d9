import sqlite3
from contextlib import closing

from rehearsal.errors import ConflictError
from tests.fixtures import WorkflowCase, lyrics


class RehearsalTests(WorkflowCase):
    def test_close_handouts(self):
        first = self.issue(self.load()['revision'])
        self.service.prepare_run('morning', 'bridge')
        handout = self.handout(first)
        result = self.service.close({'run': 'morning'})
        self.assertEqual({'run': 'morning', 'draft': 'bridge', 'handouts': [handout]}, result)
        with self.assertRaisesRegex(ConflictError, 'run_closed'):
            self.handout(first, 'alto')
        self.assertEqual(result, self.service.close({'run': 'morning'}))
        self.assertEqual([first['issue'], None],
                         [part['current_issue'] for part in self.service.prepare_run('morning', 'bridge')['parts']])

    def test_reissued_handouts(self):
        _, split, trial, _, _ = self.split_trial()
        self.service.prepare_run('morning', 'bridge')
        old = self.handout(trial)
        restored = self.undo(split['revision'], split['operation'])
        merged = self.issue(restored['revision'])
        new = self.handout(merged)
        unclaimed = self.issue(restored['revision'])
        summary = self.service.close({'run': 'morning'})
        self.assertEqual([old, new], summary['handouts'])
        self.assertEqual([trial['issue'], merged['issue']], [item['issue'] for item in summary['handouts']])
        self.assertEqual([['风', '过'], ['长', '桥']], lyrics(self.fixed(trial)))
        self.assertEqual([['风', '过', '长', '桥']], lyrics(self.fixed(merged)))
        before = self.count_state()
        with self.assertRaisesRegex(ConflictError, 'run_closed'):
            self.handout(unclaimed)
        self.assertEqual(before, self.count_state())
        self.reopen()
        run = self.service.prepare_run('morning', 'bridge')
        self.assertTrue(run['closed'])
        self.assertEqual(merged['issue'], run['parts'][0]['current_issue'])
        self.assertEqual(summary, self.service.close({'run': 'morning'}))

    def test_empty_and_unknown_run(self):
        with self.assertRaisesRegex(KeyError, 'unknown_run'):
            self.service.close({'run': 'morning'})
        self.assertEqual(0, self.count_state()['closeouts'])
        self.load()
        self.service.prepare_run('morning', 'bridge')
        self.assertEqual([], self.service.close({'run': 'morning'})['handouts'])
        self.reopen()
        self.assertEqual([], self.service.close({'run': 'morning'})['handouts'])

    def test_close_write_failure_is_atomic(self):
        edition = self.issue(self.load()['revision'])
        self.service.prepare_run('morning', 'bridge')
        original = self.handout(edition)
        before = self.count_state()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('''CREATE TRIGGER reject_close BEFORE UPDATE OF closed ON runs
                          BEGIN SELECT RAISE(ABORT, 'close write rejected'); END''')
            db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'close write rejected'):
            self.service.close({'run': 'morning'})
        self.assertEqual(before, self.count_state())
        self.assertFalse(self.service.prepare_run('morning', 'bridge')['closed'])
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('DROP TRIGGER reject_close')
            db.commit()
        replacement = self.handout(edition)
        result = self.service.close({'run': 'morning'})
        self.assertEqual([original, replacement], result['handouts'])
        self.reopen()
        self.assertEqual(result, self.service.close({'run': 'morning'}))
