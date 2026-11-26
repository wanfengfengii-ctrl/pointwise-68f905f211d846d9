import json

from rehearsal.errors import ConflictError
from tests.fixtures import WorkflowCase, lyrics, sample


class PrepareTests(WorkflowCase):
    def test_voice_handout(self):
        payload = sample()
        loaded = self.service.load(payload)
        issued = self.issue(loaded['revision'])
        self.service.prepare_run('morning', 'bridge')
        records = [self.handout(issued, voice) for voice in ('soprano', 'alto')]
        sheets = {record['voice']: self.fixed(issued, record['voice']) for record in records}
        for sheet in sheets.values():
            self.assertEqual(issued['issue'], sheet['issue'])
            self.assertEqual(loaded['revision'], sheet['revision'])
            self.assertEqual([], sheet['phrases'][0]['marks'])
        expected = {'soprano': [['风', '过', '长', '桥']], 'alto': [['河', '岸']]}
        actual = {voice: lyrics(sheet) for voice, sheet in sheets.items()}
        self.assertEqual(expected, actual)
        notes = sheets['soprano']['phrases'][0]['notes']
        self.assertEqual([1, '3/2', 2, 3], [note['beat'] for note in notes])
        self.assertEqual([1, 2, 3, 4], [note['number'] for note in notes])
        downstream_expected = [['soprano', issued['issue']], ['alto', issued['issue']]]
        downstream_actual = [[item['voice'], item['issue']]
                             for item in self.service.close({'run': 'morning'})['handouts']]
        self.assertEqual(downstream_expected, downstream_actual)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v1', 'input': payload, 'expected': expected, 'actual': actual,
            'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
            'code_paths': ['score/notes.py', 'publication/layout.py', 'publication/history.py',
                           'rehearsal/handouts.py', 'rehearsal/closeout.py'],
            'oracle': '按样例中的声部和出现次序手工列出歌词，按实际两次领用的调用顺序核对收场。',
        }, ensure_ascii=False))

    def test_repeated_events(self):
        payload = sample()
        payload['voices'][0]['notes'][2:] = [{'beat': '4/2', 'lyric': '长'}, {'beat': 2, 'lyric': '长'}]
        edition = self.issue(self.service.load(payload)['revision'])
        self.service.prepare_run('morning', 'bridge')
        self.handout(edition)
        soprano = self.fixed(edition)
        self.assertEqual([['风', '过', '长', '长']], lyrics(soprano))
        self.assertEqual([(3, 2), (4, 2)], [(n['number'], n['beat']) for n in soprano['phrases'][0]['notes'][2:]])
        self.assertEqual([['河', '岸']], lyrics(self.fixed(edition, 'alto')))

    def test_bad_beats(self):
        payload = sample()
        payload['voices'][1]['notes'].reverse()
        before = self.count_state()
        with self.assertRaisesRegex(ValueError, 'descending_beats'):
            self.service.load(payload)
        self.assertEqual(before, self.count_state())
        with self.assertRaisesRegex(KeyError, 'unknown_draft'):
            self.working()
        loaded = self.load()
        self.assertEqual(loaded['revision'], self.working()['revision'])

    def test_exact_fraction(self):
        for beat_values in (('3/2', '4/3'), ('9007199254740993/9007199254740992', 1),
                            (1, None), (1, '1/0'), (1, True), (1, 1.5), (1, '1/-2')):
            with self.subTest(beats=beat_values):
                payload = sample()
                for event, beat in zip(payload['voices'][1]['notes'], beat_values):
                    event['beat'] = beat
                with self.assertRaises(ValueError):
                    self.service.load(payload)
                self.assertEqual(0, self.count_state()['drafts'])
        payload = sample()
        payload['voices'][1]['notes'][0]['beat'] = '4/3'
        payload['voices'][1]['notes'][1]['beat'] = '3/2'
        edition = self.issue(self.service.load(payload)['revision'])
        actual = [n['beat'] for n in self.fixed(edition, 'alto')['phrases'][0]['notes']]
        self.assertEqual(['4/3', '3/2'], actual)

    def test_invalid_voice_and_duplicate_draft(self):
        payload = sample()
        payload['voices'][1]['voice'] = 'soprano'
        with self.assertRaisesRegex(ValueError, 'duplicate_voice'):
            self.service.load(payload)
        self.assertEqual(0, self.count_state()['revisions'])
        loaded = self.load()
        old = self.working()
        payload = sample()
        payload['voices'][0]['notes'][0]['lyric'] = '改'
        with self.assertRaisesRegex(ConflictError, 'draft_exists'):
            self.service.load(payload)
        self.assertEqual(loaded['revision'], self.working()['revision'])
        self.assertEqual(old, self.working())

    def test_missing_revision(self):
        other = self.load('other')
        loaded = self.load()
        for revision in (other['revision'], 900):
            with self.assertRaisesRegex(KeyError, 'unknown_revision'):
                self.issue(revision)
        self.assertEqual(0, self.count_state()['editions'])
        self.assertEqual(loaded['revision'], self.issue(loaded['revision'])['revision'])

    def test_voice_isolation(self):
        issued = self.issue(self.load()['revision'])
        self.assertEqual(['soprano', 'alto'], [item['voice'] for item in issued['sheets']])
        self.assertEqual(self.fixed(issued)['issue'], self.fixed(issued, 'alto')['issue'])
        self.assertEqual([['河', '岸']], lyrics(self.fixed(issued, 'alto')))
        with self.assertRaisesRegex(KeyError, 'unknown_voice'):
            self.fixed(issued, 'tenor')

    def test_run_setup(self):
        with self.assertRaisesRegex(KeyError, 'unknown_draft'):
            self.service.prepare_run('morning', 'bridge')
        self.assertEqual(0, self.count_state()['runs'])
        self.load()
        run = self.service.prepare_run('morning', 'bridge')
        self.assertFalse(run['closed'])
        self.assertEqual([('soprano', None), ('alto', None)],
                         [(part['voice'], part['current_issue']) for part in run['parts']])
        self.assertEqual(run, self.service.prepare_run('morning', 'bridge'))

    def test_reopen_run(self):
        edition = self.issue(self.load()['revision'])
        self.service.prepare_run('morning', 'bridge')
        self.handout(edition)
        expected = self.service.prepare_run('morning', 'bridge')
        self.reopen()
        self.assertEqual(expected, self.service.prepare_run('morning', 'bridge'))
        self.assertEqual(edition['issue'], expected['parts'][0]['current_issue'])
        self.load('other')
        with self.assertRaisesRegex(ConflictError, 'run_draft_changed'):
            self.service.prepare_run('morning', 'other')
        self.assertEqual(expected, self.service.prepare_run('morning', 'bridge'))

    def test_wrong_draft_handout(self):
        own = self.issue(self.load()['revision'])
        other = self.issue(self.load('other')['revision'], 'other')
        self.service.prepare_run('morning', 'bridge')
        first = self.handout(own)
        with self.assertRaisesRegex(ConflictError, 'edition_for_other_draft'):
            self.handout(other)
        with self.assertRaisesRegex(KeyError, 'unknown_run_voice'):
            self.handout(own, 'tenor')
        parts = self.service.prepare_run('morning', 'bridge')['parts']
        self.assertEqual([own['issue'], None], [part['current_issue'] for part in parts])
        self.assertEqual([first], self.service.close({'run': 'morning'})['handouts'])
