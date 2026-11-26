import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from rehearsal.server import RehearsalHTTPServer
from rehearsal.service import RehearsalService


def _send(request):
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def _play(base, service, draft):
    voice = 'soprano'
    run = 'morning'

    def post_request(path, payload):
        return Request(base + path, data=json.dumps(payload).encode('utf-8'),
                       headers={'Content-Type': 'application/json'})

    def post(path, payload):
        return _send(post_request(path, payload))

    def read(**query):
        return _send(Request(base + '/read?' + urlencode({'voice': voice, **query})))

    loaded = post('/load', draft)
    service.prepare_run(run, draft['draft'])
    initial = post('/issue', {'draft': draft['draft'], 'revision': loaded['revision']})
    whole = read(issue=initial['issue'])['phrases'][0]['phrase']
    split = post('/edit', {'draft': draft['draft'], 'parent': loaded['revision'], 'kind': 'split',
                           'phrase': whole, 'position': 2})
    trial = post('/issue', {'draft': draft['draft'], 'revision': split['revision']})
    post('/handout', {'run': run, 'voice': voice, 'issue': trial['issue']})
    left, right = [line['phrase'] for line in read(issue=trial['issue'])['phrases']]

    common = {'issue': trial['issue'], 'voice': voice}
    a = {**common, 'request': 'A', 'phrase': left, 'position': 2, 'text': '换气'}
    b = {**common, 'request': 'B', 'phrase': right, 'position': 0, 'text': '吸气'}
    c = {**b, 'request': 'C'}
    pending = post_request('/mark', c)
    receipts = [post('/mark', a), post('/mark', b)]
    marked_trial = post('/issue', {'draft': draft['draft'], 'revision': split['revision']})
    frozen_before = read(issue=marked_trial['issue'])
    restored = post('/edit', {'draft': draft['draft'], 'parent': split['revision'], 'kind': 'undo',
                              'operation': split['operation']})
    receipts.append(_send(pending))
    working = read(draft=draft['draft'])
    final = post('/issue', {'draft': draft['draft'], 'revision': restored['revision']})
    post('/handout', {'run': run, 'voice': voice, 'issue': final['issue']})
    closeout = post('/close', {'run': run})

    labels = {receipt['mark_id']: receipt['request'] for receipt in receipts}
    actual = {
        'lyrics': [[note['lyric'] for note in line['notes']] for line in working['phrases']],
        'placements': sorted([labels[mark['mark_id']], mark['position']]
                             for line in working['phrases'] for mark in line['marks']),
    }
    expected = {'lyrics': [['风', '过', '长', '桥']], 'placements': [['A', 2], ['B', 2], ['C', 2]]}
    frozen_after = read(issue=marked_trial['issue'])
    final_sheet = read(issue=final['issue'])
    downstream_expected = {
        'frozen_lines': [['A'], ['B']], 'issued_placements': expected['placements'],
        'handouts': [trial['issue'], final['issue']],
    }
    downstream_actual = {
        'frozen_lines': [[labels[mark['mark_id']] for mark in line['marks']] for line in frozen_after['phrases']],
        'issued_placements': sorted([labels[mark['mark_id']], mark['position']]
                                    for line in final_sheet['phrases'] for mark in line['marks']),
        'handouts': [item['issue'] for item in closeout['handouts']],
    }
    if actual != expected or downstream_actual != downstream_expected or frozen_after != frozen_before:
        raise AssertionError((actual, downstream_actual, frozen_after))
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v6.late_submission',
        'input': {'draft': draft, 'split_position': 2, 'marks': [a, b, c], 'after_undo': ['C']},
        'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': ['structure/undo.py', 'annotations/positions.py', 'annotations/current.py',
                       'publication/editions.py', 'rehearsal/closeout.py'],
        'oracle': '四音句的过与长之间为音隙 2。拆后左尾和右首各指向该音隙。只记录两次实际领用。',
    }, ensure_ascii=False))


def main():
    draft = json.loads(Path(__file__).with_name('draft.json').read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='bridge-demo-') as directory:
        service = RehearsalService(Path(directory, 'rehearsal.sqlite3'))
        service.initialize()
        with RehearsalHTTPServer(service, 0) as server:
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
            thread.start()
            try:
                _play(f'http://127.0.0.1:{server.server_port}', service, draft)
            finally:
                server.shutdown()
                thread.join()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
