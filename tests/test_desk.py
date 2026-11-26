import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from rehearsal.server import RehearsalHTTPServer
from rehearsal.service import RehearsalService
from tests.fixtures import sample


class HttpClient:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'

    def call(self, path, payload=None, **query):
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        url = self.base + path
        if query:
            url += '?' + urlencode(query)
        request = Request(url)
        if payload is not None:
            request = Request(url, data=json.dumps(payload).encode('utf-8'),
                              headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def ok(self, path, payload=None, **query):
        status, body = self.call(path, payload, **query)
        self.assert200 = status
        if status != 200:
            raise AssertionError((path, status, body))
        return body


@contextmanager
def desk_server(database):
    service = RehearsalService(database)
    service.initialize()
    with RehearsalHTTPServer(service, 0) as server:
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        thread.start()
        try:
            yield HttpClient(server.server_port)
        finally:
            server.shutdown()
            thread.join()


def seed(client):
    loaded = client.ok('/load', sample())
    args = {'draft': 'bridge', 'revision': loaded['revision']}
    plain = client.ok('/issue', args)
    marks = {}
    for voice in loaded['voices']:
        sheet = client.ok('/read', issue=plain['issue'], voice=voice)
        phrase = sheet['phrases'][0]['phrase']
        marks[voice] = [client.ok('/mark', {'request': f'{voice}-{position}', 'issue': plain['issue'],
                                            'voice': voice, 'phrase': phrase, 'position': position,
                                            'text': '同吸'}) for position in (0, 1)]
    return args, plain, marks


def seed_versions(client):
    args, plain, marks = seed(client)
    soprano = {'text': '同吸', 'mark_id': marks['soprano'][0]['mark_id']}
    alto = {'text': '同吸', 'mark_id': marks['alto'][0]['mark_id']}
    both = client.ok('/issue', {**args, 'cues': {'soprano': {'X': soprano}, 'alto': {'X': alto}}})
    neither = client.ok('/issue', {**args, 'cues': {}})
    return args, marks, both, neither


def open_sheet(run, desk, publisher, a_base, b_base, a_issue, b_issue):
    return {'run': run, 'desk': desk, 'draft': 'bridge', 'publisher': publisher,
            'desks': {'A': {'url': a_base, 'voice': 'soprano', 'initial_issue': a_issue},
                      'B': {'url': b_base, 'voice': 'alto', 'initial_issue': b_issue}},
            'cues': {'X': '同吸'}}


def has_x(sheet):
    return 'X' in sheet.get('cues', {})


class IssueCueTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='bridge-desk-tests-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def server(self, name='publisher.sqlite3'):
        return desk_server(self.directory / name)

    def test_issue_binding_and_readback(self):
        with self.server() as pub:
            args, plain, marks = seed(pub)
            bound = {'soprano': {'X': {'text': '同吸', 'mark_id': marks['soprano'][0]['mark_id']}},
                     'alto': {'X': {'text': '同吸', 'mark_id': marks['alto'][0]['mark_id']}}}
            issued = pub.ok('/issue', {**args, 'cues': bound})
            sheets = {sheet['voice']: sheet for sheet in issued['sheets']}
            self.assertEqual(bound['soprano'], sheets['soprano']['cues'])
            self.assertEqual(bound['alto'], sheets['alto']['cues'])
            for voice in ('soprano', 'alto'):
                sheet = pub.ok('/read', issue=issued['issue'], voice=voice)
                self.assertEqual(bound[voice], sheet['cues'])
            # 未绑定的声部返回空对象；未传 cues 的发行保持原形状
            only = pub.ok('/issue', {**args, 'cues': {'soprano': bound['soprano']}})
            self.assertEqual({}, pub.ok('/read', issue=only['issue'], voice='alto')['cues'])
            self.assertNotIn('cues', pub.ok('/read', issue=plain['issue'], voice='soprano'))
            self.assertNotIn('cues', pub.ok('/read', draft='bridge', voice='soprano'))

    def test_issue_binding_rejections(self):
        with self.server() as pub:
            args, plain, marks = seed(pub)
            peer_mark = {'text': '同吸', 'mark_id': marks['alto'][0]['mark_id']}
            status, _ = pub.call('/issue', {**args, 'cues': {'soprano': {'X': peer_mark}}})
            self.assertEqual(404, status)
            bad_text = {'text': '换气', 'mark_id': marks['soprano'][0]['mark_id']}
            status, _ = pub.call('/issue', {**args, 'cues': {'soprano': {'X': bad_text}}})
            self.assertEqual(409, status)
            status, _ = pub.call('/issue', {**args, 'cues': {'tenor': {'X': bad_text}}})
            self.assertEqual(404, status)
            # 发行退回不落库
            status, _ = pub.call('/issue', {**args, 'cues': {'soprano': {'X': {'text': '', 'mark_id': 1}}}})
            self.assertEqual(400, status)


class DeskSwitchTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='bridge-desk-tests-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    @contextmanager
    def desks(self):
        with desk_server(self.directory / 'pub.sqlite3') as pub, \
                desk_server(self.directory / 'a.sqlite3') as a, \
                desk_server(self.directory / 'b.sqlite3') as b:
            yield pub, a, b

    def open_round(self, pub, a, b, run='r', a_issue=None, b_issue=None):
        args, marks, both, neither = seed_versions(pub)
        a_issue = both['issue'] if a_issue is None else a_issue
        b_issue = both['issue'] if b_issue is None else b_issue
        sheet = lambda desk: open_sheet(run, desk, pub.base, a.base, b.base, a_issue, b_issue)
        a.ok('/desk/open', sheet('A'))
        b.ok('/desk/open', sheet('B'))
        return args, both, neither

    def test_open_stage_current(self):
        with self.desks() as (pub, a, b):
            args, both, neither = self.open_round(pub, a, b)
            for client in (a, b):
                staged = client.ok('/desk/stage', {'run': 'r', 'issue': neither['issue']})
                self.assertEqual(neither['issue'], staged['issue'])
                self.assertNotIn('request', staged)
            current = a.ok('/desk/current', run='r')
            self.assertEqual(both['issue'], current['issue'])
            self.assertTrue(has_x(current['sheet']))
            # 开轮幂等
            sheet = open_sheet('r', 'A', pub.base, a.base, b.base, both['issue'], both['issue'])
            self.assertEqual(a.ok('/desk/open', sheet), a.ok('/desk/open', sheet))

    def test_first_delete_then_last_copy_rejected(self):
        with self.desks() as (pub, a, b):
            args, both, neither = self.open_round(pub, a, b)
            for client in (a, b):
                client.ok('/desk/stage', {'run': 'r', 'issue': neither['issue']})
            switched = a.ok('/desk/switch', {'run': 'r', 'request': 'A-off', 'expected_issue': both['issue'],
                                             'issue': neither['issue']})
            self.assertFalse(has_x(switched['sheet']))
            self.assertEqual('A-off', switched['request'])
            status, body = b.call('/desk/switch', {'run': 'r', 'request': 'B-off', 'expected_issue': both['issue'],
                                                   'issue': neither['issue']})
            self.assertEqual((409, 'cue_last_copy'), (status, body['error']))
            self.assertTrue(has_x(b.ok('/desk/current', run='r')['sheet']))
            # 同号同参复领首执；同号改参与预期谱号过期退回
            self.assertEqual(switched, a.ok('/desk/switch', {'run': 'r', 'request': 'A-off',
                                                             'expected_issue': both['issue'],
                                                             'issue': neither['issue']}))
            status, _ = a.call('/desk/switch', {'run': 'r', 'request': 'A-off', 'expected_issue': both['issue'],
                                                'issue': both['issue']})
            self.assertEqual(409, status)
            status, body = a.call('/desk/switch', {'run': 'r', 'request': 'A-stale',
                                                   'expected_issue': both['issue'], 'issue': both['issue']})
            self.assertEqual((409, 'expected_issue_stale'), (status, body['error']))

    def test_add_only_switch_offline(self):
        with self.desks() as (pub, a, b):
            args, marks, both, neither = seed_versions(pub)
            sheet = lambda desk: open_sheet('r', desk, pub.base, a.base, b.base, neither['issue'], both['issue'])
            a.ok('/desk/open', sheet('A'))
            b.ok('/desk/open', sheet('B'))
            a.ok('/desk/stage', {'run': 'r', 'issue': both['issue']})
            pub_port, a_port = pub.base, a.base
        # 谱务与对台停机后，只增加提示的换谱仍可办理
        with desk_server(self.directory / 'a.sqlite3') as a2:
            switched = a2.ok('/desk/switch', {'run': 'r', 'request': 'A-add', 'expected_issue': neither['issue'],
                                              'issue': both['issue']})
            self.assertTrue(has_x(switched['sheet']))
            self.assertTrue(has_x(a2.ok('/desk/current', run='r')['sheet']))


if __name__ == '__main__':
    unittest.main()
