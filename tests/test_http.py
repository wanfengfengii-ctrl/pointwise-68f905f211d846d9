import json
import subprocess
import sys
from contextlib import contextmanager
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from rehearsal.server import RehearsalHTTPServer
from tests.fixtures import WorkflowCase, lyrics, mark_request, placements, sample


class HttpClient:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'

    def prepare(self, path, payload=None, **query):
        url = self.base + path
        if query:
            url += '?' + urlencode(query)
        if payload is None:
            return Request(url)
        return Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})

    def send(self, request):
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)


class HttpTests(WorkflowCase):
    @contextmanager
    def server(self):
        with RehearsalHTTPServer(self.service, 0) as server:
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
            thread.start()
            try:
                yield HttpClient(server.server_port)
            finally:
                server.shutdown()
                thread.join()

    def ok(self, client, path, body=None, **query):
        status, result = client.send(client.prepare(path, body, **query))
        self.assertEqual(200, status, result)
        return result

    def test_registration_and_handout_chain(self):
        with self.server() as client:
            loaded = self.ok(client, '/load', sample())
            issued = self.ok(client, '/issue', {'draft': 'bridge', 'revision': loaded['revision']})
            args = [sys.executable, '-B', '-m', 'rehearsal.server', '--db', str(self.database),
                    'prepare-run', '--run', 'morning', '--draft', 'bridge']
            response = subprocess.run(args, check=True, capture_output=True, text=True, timeout=10)
            run = json.loads(response.stdout)
            self.assertEqual(['soprano', 'alto'], [item['voice'] for item in run['parts']])
            handout = self.ok(client, '/handout', {'run': 'morning', 'voice': 'alto', 'issue': issued['issue']})
            sheet = self.ok(client, '/read', issue=handout['issue'], voice='alto')
            self.assertEqual([['河', '岸']], lyrics(sheet))
            result = self.ok(client, '/close', {'run': 'morning'})
            self.assertEqual([handout], result['handouts'])
            status, body = client.send(client.prepare('/handout', {'run': 'morning', 'voice': 'alto',
                                                                    'issue': issued['issue']}))
            self.assertEqual((409, {'error': 'run_closed'}), (status, body))
        self.reopen()
        with self.server() as client:
            self.assertEqual(result, self.ok(client, '/close', {'run': 'morning'}))

    def test_marks_across_undo(self):
        observations = []
        for early in (False, True):
            with self.subTest(c_before_undo=early):
                self.database = self.directory / f'order-{int(early)}.sqlite3'
                self.reopen()
                observations.append(self._exercise_order(early))
        self.assertEqual(2, len(observations))
        self.assertEqual(observations[0], observations[1])

    def _exercise_order(self, early):
        with self.server() as client:
            load = self.ok(client, '/load', sample())
            first = self.ok(client, '/issue', {'draft': 'bridge', 'revision': load['revision']})
            original = self.ok(client, '/read', issue=first['issue'], voice='soprano')
            whole = original['phrases'][0]['phrase']
            split = self.ok(client, '/edit', {'draft': 'bridge', 'parent': load['revision'], 'kind': 'split',
                                              'position': 2, 'phrase': whole})
            trial = self.ok(client, '/issue', {'draft': 'bridge', 'revision': split['revision']})
            trial_sheet = self.ok(client, '/read', issue=trial['issue'], voice='soprano')
            left, right = [line['phrase'] for line in trial_sheet['phrases']]
            a = mark_request(trial, left, 2)
            b = mark_request(trial, right, 0, 'B', text='吸气')
            c = {**b, 'request': 'C'}
            pending = client.prepare('/mark', c)
            a_receipt = self.ok(client, '/mark', a)
            b_receipt = self.ok(client, '/mark', b)
            marked = self.ok(client, '/issue', {'draft': 'bridge', 'revision': split['revision']})
            frozen = self.ok(client, '/read', issue=marked['issue'], voice='soprano')
            self.assertEqual([['风', '过'], ['长', '桥']], lyrics(frozen))
            self.assertEqual([(left, 2, a_receipt['mark_id']), (right, 0, b_receipt['mark_id'])], placements(frozen))

            if early:
                status, c_receipt = client.send(pending)
                self.assertEqual(200, status, c_receipt)
            restored = self.ok(client, '/edit', {'draft': 'bridge', 'parent': split['revision'], 'kind': 'undo',
                                                 'operation': split['operation']})
            if not early:
                status, c_receipt = client.send(pending)
                self.assertEqual(200, status, c_receipt)
            merged = self.ok(client, '/read', draft='bridge', voice='soprano')
            self.assertEqual([['风', '过', '长', '桥']], lyrics(merged))
            mapped = {receipt['mark_id']: receipt['request'] for receipt in (a_receipt, b_receipt, c_receipt)}
            observed = sorted((mapped[mark], position) for phrase, position, mark in placements(merged)
                              if phrase == whole)
            self.assertEqual([('A', 2), ('B', 2), ('C', 2)], observed)
            self.assertEqual(3, len(placements(merged)))
            final = self.ok(client, '/issue', {'draft': 'bridge', 'revision': restored['revision']})
            self.assertEqual(placements(merged), placements(self.ok(client, '/read', issue=final['issue'], voice='soprano')))

        self.reopen()
        with self.server() as client:
            self.assertEqual(b_receipt, self.ok(client, '/mark', b))
            for change, status, error in (
                ({**b, 'position': 1}, 409, 'request_arguments_changed'),
                ({**b, 'request': 'bad-edition', 'issue': 999}, 404, 'unknown_issue'),
                ({**b, 'request': 'bad-gap', 'position': 3}, 400, 'invalid_original_position'),
            ):
                self.assertEqual((status, {'error': error}), client.send(client.prepare('/mark', change)))
            self.assertEqual(merged, self.ok(client, '/read', draft='bridge', voice='soprano'))
            self.assertEqual(frozen, self.ok(client, '/read', issue=marked['issue'], voice='soprano'))
            self.assertEqual(trial_sheet, self.ok(client, '/read', issue=trial['issue'], voice='soprano'))
        return observed
