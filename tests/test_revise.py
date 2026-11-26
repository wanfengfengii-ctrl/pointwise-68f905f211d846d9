from rehearsal.errors import ConflictError
from tests.fixtures import WorkflowCase, lyrics, mark_request, placements


class ReviseTests(WorkflowCase):
    def test_stale_parent(self):
        loaded = self.load()
        original = self.working()['phrases'][0]['phrase']
        first = self.split(loaded['revision'], original, 1)
        working = self.working()
        before = self.count_state()
        with self.assertRaisesRegex(ConflictError, 'stale_parent'):
            self.split(loaded['revision'], original, 3)
        self.assertEqual(before, self.count_state())
        self.assertEqual(first['revision'], self.working()['revision'])
        self.assertEqual([['风'], ['过', '长', '桥']], lyrics(working))
        self.assertEqual(working, self.working())

    def test_invalid_split_gap(self):
        loaded = self.load()
        old = self.working()
        original = old['phrases'][0]['phrase']
        before = self.count_state()
        for gap in (0, 4, 5, -1, None):
            with self.subTest(gap=gap):
                with self.assertRaises(ValueError):
                    self.split(loaded['revision'], original, gap)
        self.assertEqual(before, self.count_state())
        self.assertEqual(old, self.working())

    def test_working_split(self):
        loaded = self.load()
        original = self.issue(loaded['revision'])
        phrase = self.fixed(original)['phrases'][0]['phrase']
        a = self.service.mark(mark_request(original, phrase, 2))
        b = self.service.mark(mark_request(original, phrase, 3, 'B'))
        trial = self.split(loaded['revision'], phrase)
        working = self.working()
        self.assertEqual([['风', '过'], ['长', '桥']], lyrics(working))
        left, right = [line['phrase'] for line in working['phrases']]
        expected = [(left, 2, a['mark_id']), (right, 1, b['mark_id'])]
        self.assertEqual(expected, placements(working))
        self.assertEqual(expected, placements(self.fixed(self.issue(trial['revision']))))
        self.assertEqual([['风', '过', '长', '桥']], lyrics(self.fixed(original)))
        self.assertEqual([['河', '岸']], lyrics(self.working('alto')))

    def test_undo_joint_boundary(self):
        before, split, edition, left, right = self.split_trial()
        a = self.service.mark(mark_request(edition, left, 2))
        b = self.service.mark(mark_request(edition, right, 0, 'B', text='吸气'))
        self.assertEqual([(left, 2, a['mark_id']), (right, 0, b['mark_id'])], placements(self.working()))
        restored = self.undo(split['revision'], split['operation'])
        sheet = self.working()
        phrase = before['phrases'][0]['phrase']
        self.assertEqual([['风', '过', '长', '桥']], lyrics(sheet))
        self.assertEqual(restored['revision'], sheet['revision'])
        expected = [(phrase, 2, a['mark_id']), (phrase, 2, b['mark_id'])]
        self.assertEqual(expected, placements(sheet))
        self.assertEqual(expected, placements(self.fixed(self.issue(restored['revision']))))

    def test_undo_structure(self):
        before, split, issued, _, _ = self.split_trial()
        older = before['revision']
        with self.assertRaisesRegex(ConflictError, 'stale_parent'):
            self.undo(older, split['operation'])
        with self.assertRaisesRegex(KeyError, 'unknown_split'):
            self.undo(split['revision'], 999)
        restored = self.undo(split['revision'], split['operation'])
        self.assertNotIn(restored['revision'], (older, split['revision']))
        self.assertEqual(before['phrases'], self.working()['phrases'])
        state = self.count_state()
        with self.assertRaisesRegex(ConflictError, 'split_already_undone'):
            self.undo(restored['revision'], split['operation'])
        self.assertEqual(state, self.count_state())
        self.assertEqual([['风', '过'], ['长', '桥']], lyrics(self.fixed(issued)))

    def test_undo_preserves_other_voice_edit(self):
        _, split, _, _, _ = self.split_trial()
        alto_phrase = self.working('alto')['phrases'][0]['phrase']
        next_edit = self.split(split['revision'], alto_phrase, 1)
        self.undo(next_edit['revision'], split['operation'])
        self.assertEqual([['风', '过', '长', '桥']], lyrics(self.working()))
        self.assertEqual([['河'], ['岸']], lyrics(self.working('alto')))

    def test_inapplicable_undo_keeps_revision(self):
        _, split, _, _, right = self.split_trial()
        child = self.split(split['revision'], right, 1)
        state = self.count_state()
        with self.assertRaisesRegex(ConflictError, 'split_no_longer_applicable'):
            self.undo(child['revision'], split['operation'])
        self.assertEqual(state, self.count_state())
        self.assertEqual([['风', '过'], ['长'], ['桥']], lyrics(self.working()))

    def test_original_address(self):
        _, split, edition, _, right = self.split_trial()
        self.undo(split['revision'], split['operation'])
        before = self.count_state()
        with self.assertRaisesRegex(ValueError, 'invalid_original_position'):
            self.service.mark(mark_request(edition, right, 3))
        self.assertEqual(before, self.count_state())
        valid = self.service.mark(mark_request(edition, right, 2))
        self.assertEqual([(self.working()['phrases'][0]['phrase'], 4, valid['mark_id'])], placements(self.working()))

    def test_reopen_prior_issue(self):
        _, split, edition, left, right = self.split_trial()
        a = self.service.mark(mark_request(edition, left, 2))
        b = self.service.mark(mark_request(edition, right, 0, 'B'))
        marked_trial = self.issue(split['revision'])
        original = self.fixed(marked_trial)
        self.assertEqual([['风', '过'], ['长', '桥']], lyrics(original))
        self.assertEqual([(left, 2, a['mark_id']), (right, 0, b['mark_id'])], placements(original))
        restored = self.undo(split['revision'], split['operation'])
        c = self.service.mark(mark_request(edition, right, 0, 'C'))
        combined = self.issue(restored['revision'])
        self.reopen()
        self.assertEqual(original, self.fixed(marked_trial))
        self.assertEqual([], placements(self.fixed(edition)))
        self.assertEqual([['风', '过', '长', '桥']], lyrics(self.fixed(combined)))
        self.assertEqual([(2, item['mark_id']) for item in (a, b, c)],
                         [(position, mark_id) for _, position, mark_id in placements(self.fixed(combined))])

    def test_frozen_projection_when_reissuing_old_revision(self):
        _, split, edition, _, right = self.split_trial()
        self.undo(split['revision'], split['operation'])
        mark = self.service.mark(mark_request(edition, right, 0))
        reissued = self.issue(split['revision'])
        self.assertEqual([], placements(self.fixed(edition)))
        self.assertEqual([['风', '过'], ['长', '桥']], lyrics(self.fixed(reissued)))
        self.assertEqual([(right, 0, mark['mark_id'])], placements(self.fixed(reissued)))
