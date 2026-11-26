from rehearsal.errors import ConflictError
from tests.fixtures import WorkflowCase, mark_request, placements


class MarkTests(WorkflowCase):
    def _issued_mark(self):
        loaded = self.load()
        edition = self.issue(loaded['revision'])
        phrase = self.fixed(edition)['phrases'][0]['phrase']
        return loaded, edition, mark_request(edition, phrase, 1)

    def test_issue_with_mark(self):
        loaded, first, request = self._issued_mark()
        frozen = self.fixed(first)
        receipt = self.service.mark(request)
        expected = [(request['phrase'], 1, receipt['mark_id'])]
        self.assertEqual(expected, placements(self.working()))
        updated = self.issue(loaded['revision'])
        self.assertEqual(expected, placements(self.fixed(updated)))
        self.assertEqual(frozen, self.fixed(first))
        self.assertEqual([], placements(self.fixed(first)))
        self.assertEqual([], placements(self.fixed(updated, 'alto')))

    def test_request_reuse(self):
        _, _, request = self._issued_mark()
        first = self.service.mark(request)
        self.assertEqual(first, self.service.mark(dict(request)))
        self.assertEqual(request, {key: first[key] for key in request})
        with self.assertRaisesRegex(ConflictError, 'request_arguments_changed'):
            self.service.mark({**request, 'text': '轻吸'})
        self.assertEqual([(request['phrase'], 1, first['mark_id'])], placements(self.working()))
        self.assertEqual(1, self.count_state()['mark_receipts'])

    def test_reopen_replay(self):
        loaded, _, request = self._issued_mark()
        first = self.service.mark(request)
        another = self.issue(loaded['revision'])
        self.reopen()
        self.assertEqual(first, self.service.mark(request))
        for change in ({'position': 2}, {'issue': another['issue']}):
            with self.subTest(change=change):
                with self.assertRaisesRegex(ConflictError, 'request_arguments_changed'):
                    self.service.mark({**request, **change})
        self.assertEqual(first, self.service.mark(request))
        self.assertEqual(1, len(placements(self.working())))
        self.assertEqual(1, self.count_state()['mark_receipts'])

    def test_distinct_requests(self):
        loaded, _, request = self._issued_mark()
        first = self.service.mark(request)
        second = self.service.mark({**request, 'request': 'B'})
        self.assertNotEqual(first['mark_id'], second['mark_id'])
        edition = self.issue(loaded['revision'])
        marks = self.fixed(edition)['phrases'][0]['marks']
        self.assertEqual([(first['mark_id'], 1, '换气'), (second['mark_id'], 1, '换气')],
                         [(mark['mark_id'], mark['position'], mark['text']) for mark in marks])

    def test_original_address(self):
        _, _, request = self._issued_mark()
        for change, exception, message in (
            ({'issue': 900}, KeyError, 'unknown_issue'),
            ({'voice': 'tenor'}, KeyError, 'unknown_voice'),
            ({'voice': 'alto'}, ValueError, 'invalid_original_position'),
            ({'phrase': 'missing'}, ValueError, 'invalid_original_position'),
            ({'position': 5}, ValueError, 'invalid_original_position'),
            ({'position': -1}, ValueError, 'invalid_position'),
            ({'position': None}, ValueError, 'invalid_position'),
            ({'position': True}, ValueError, 'invalid_position'),
        ):
            with self.subTest(change=change):
                with self.assertRaisesRegex(exception, message):
                    self.service.mark({**request, **change})
        self.assertEqual([], placements(self.working()))
        self.assertEqual(0, self.count_state()['mark_receipts'])
        start = self.service.mark({**request, 'position': 0})
        end = self.service.mark({**request, 'position': 4, 'request': 'end'})
        self.assertEqual([(request['phrase'], 0, start['mark_id']), (request['phrase'], 4, end['mark_id'])],
                         placements(self.working()))

    def test_updated_handout(self):
        loaded, first, request = self._issued_mark()
        self.service.prepare_run('morning', 'bridge')
        old = self.handout(first)
        accepted = self.service.mark(request)
        updated = self.issue(loaded['revision'])
        current = self.service.prepare_run('morning', 'bridge')['parts'][0]
        self.assertEqual(first['issue'], current['current_issue'])
        new = self.handout(updated)
        current = self.service.prepare_run('morning', 'bridge')['parts'][0]
        self.assertEqual(updated['issue'], current['current_issue'])
        self.assertEqual([(request['phrase'], 1, accepted['mark_id'])], placements(self.fixed(updated)))
        self.assertEqual([old, new], self.service.close({'run': 'morning'})['handouts'])
