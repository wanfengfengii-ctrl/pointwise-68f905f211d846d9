"""双台轮次的谱台状态与台间交接。

每个谱台持有自己的库。开轮、备谱、查谱和换谱的记录都落在本台库中；
台间通过 HTTP 交接覆盖保障（hold），保障按提示号登记，解除前对台不得
撤掉该提示的覆盖。
"""
import json
from contextlib import closing
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
from urllib.parse import urlencode, urlsplit

from rehearsal.errors import ConflictError, UpstreamError, require_fields, require_integer, require_text


# ---------------------------------------------------------------------------
# 台面记录

@dataclass(frozen=True)
class DeskRound:
    run: str
    desk: str
    draft: str
    publisher: str
    desks: dict
    cues: dict
    payload: str
    open_handout_id: int

    @property
    def peer(self):
        return next(name for name in self.desks if name != self.desk)


class DeskStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS desk_rounds (
            run TEXT PRIMARY KEY NOT NULL,
            desk TEXT NOT NULL,
            draft TEXT NOT NULL,
            publisher TEXT NOT NULL,
            desks TEXT NOT NULL,
            cues TEXT NOT NULL,
            payload TEXT NOT NULL,
            open_handout_id INTEGER
        )''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS desk_receipts (
            handout_id INTEGER PRIMARY KEY,
            run TEXT NOT NULL,
            issue INTEGER NOT NULL,
            sheet TEXT NOT NULL
        )''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS desk_current (
            run TEXT PRIMARY KEY NOT NULL,
            handout_id INTEGER NOT NULL REFERENCES desk_receipts(handout_id)
        )''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS desk_staged (
            run TEXT NOT NULL,
            issue INTEGER NOT NULL,
            sheet TEXT NOT NULL,
            PRIMARY KEY (run, issue)
        )''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS desk_requests (
            run TEXT NOT NULL,
            request TEXT NOT NULL,
            expected_issue INTEGER NOT NULL,
            issue INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'switched', 'rejected')),
            removed TEXT NOT NULL,
            handout_id INTEGER,
            error TEXT,
            PRIMARY KEY (run, request)
        )''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS desk_holds (
            token TEXT PRIMARY KEY NOT NULL,
            run TEXT NOT NULL,
            cue TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('given', 'taken')),
            request TEXT NOT NULL,
            peer TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('outstanding', 'released'))
        )''')
        self.db.execute('CREATE INDEX IF NOT EXISTS desk_holds_by_cue ON desk_holds(run, cue, direction, status)')

    # -- 轮次 --
    def find_round(self, run):
        row = self.db.execute(
            'SELECT desk, draft, publisher, desks, cues, payload, open_handout_id FROM desk_rounds WHERE run = ?',
            (run,),
        ).fetchone()
        if row is None:
            return None
        return DeskRound(run, row[0], row[1], row[2], json.loads(row[3]), json.loads(row[4]), row[5], row[6])

    def get_round(self, run):
        found = self.find_round(run)
        if found is None:
            raise KeyError('unknown_run')
        return found

    def save_round(self, desk_round):
        self.db.execute(
            'INSERT INTO desk_rounds(run, desk, draft, publisher, desks, cues, payload, open_handout_id)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (desk_round.run, desk_round.desk, desk_round.draft, desk_round.publisher,
             json.dumps(desk_round.desks, ensure_ascii=False, sort_keys=True),
             json.dumps(desk_round.cues, ensure_ascii=False, sort_keys=True),
             desk_round.payload, desk_round.open_handout_id),
        )

    def set_open_handout(self, run, handout_id):
        self.db.execute('UPDATE desk_rounds SET open_handout_id = ? WHERE run = ?', (handout_id, run))

    # -- 实领 --
    def append_receipt(self, run, issue, sheet):
        cursor = self.db.execute(
            'INSERT INTO desk_receipts(run, issue, sheet) VALUES (?, ?, ?)',
            (run, issue, json.dumps(sheet, ensure_ascii=False, sort_keys=True)),
        )
        return cursor.lastrowid

    def get_receipt(self, handout_id):
        row = self.db.execute(
            'SELECT run, issue, sheet FROM desk_receipts WHERE handout_id = ?', (handout_id,),
        ).fetchone()
        if row is None:
            raise KeyError('unknown_handout')
        return {'run': row[0], 'issue': row[1], 'sheet': json.loads(row[2])}

    def get_current(self, run):
        row = self.db.execute(
            '''SELECT r.handout_id, r.issue, r.sheet FROM desk_current AS c
               JOIN desk_receipts AS r ON r.handout_id = c.handout_id WHERE c.run = ?''',
            (run,),
        ).fetchone()
        if row is None:
            raise KeyError('unknown_run')
        return {'handout_id': row[0], 'issue': row[1], 'sheet': json.loads(row[2])}

    def set_current(self, run, handout_id):
        self.db.execute(
            'INSERT INTO desk_current(run, handout_id) VALUES (?, ?)'
            ' ON CONFLICT(run) DO UPDATE SET handout_id = excluded.handout_id',
            (run, handout_id),
        )

    # -- 备谱 --
    def find_staged(self, run, issue):
        row = self.db.execute(
            'SELECT sheet FROM desk_staged WHERE run = ? AND issue = ?', (run, issue),
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def get_staged(self, run, issue):
        sheet = self.find_staged(run, issue)
        if sheet is None:
            raise KeyError('unknown_issue')
        return sheet

    def save_staged(self, run, issue, sheet):
        self.db.execute(
            'INSERT INTO desk_staged(run, issue, sheet) VALUES (?, ?, ?)'
            ' ON CONFLICT(run, issue) DO UPDATE SET sheet = excluded.sheet',
            (run, issue, json.dumps(sheet, ensure_ascii=False, sort_keys=True)),
        )

    # -- 换谱申请 --
    def find_request(self, run, request):
        row = self.db.execute(
            'SELECT expected_issue, issue, status, removed, handout_id, error FROM desk_requests'
            ' WHERE run = ? AND request = ?',
            (run, request),
        ).fetchone()
        if row is None:
            return None
        return {'run': run, 'request': request, 'expected_issue': row[0], 'issue': row[1],
                'status': row[2], 'removed': json.loads(row[3]), 'handout_id': row[4], 'error': row[5]}

    def save_request(self, run, request, expected_issue, issue, status, removed,
                     handout_id=None, error=None):
        self.db.execute(
            'INSERT INTO desk_requests(run, request, expected_issue, issue, status, removed, handout_id, error)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (run, request, expected_issue, issue, status,
             json.dumps(removed, ensure_ascii=False), handout_id, error),
        )

    def settle_request(self, run, request, status, handout_id=None, error=None):
        self.db.execute(
            'UPDATE desk_requests SET status = ?, handout_id = ?, error = ? WHERE run = ? AND request = ?',
            (status, handout_id, error, run, request),
        )

    # -- 覆盖保障 --
    def find_hold(self, token):
        row = self.db.execute(
            'SELECT run, cue, direction, request, peer, status FROM desk_holds WHERE token = ?', (token,),
        ).fetchone()
        if row is None:
            return None
        return {'token': token, 'run': row[0], 'cue': row[1], 'direction': row[2],
                'request': row[3], 'peer': row[4], 'status': row[5]}

    def save_hold(self, token, run, cue, direction, request, peer, status):
        self.db.execute(
            'INSERT OR IGNORE INTO desk_holds(token, run, cue, direction, request, peer, status)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?)',
            (token, run, cue, direction, request, peer, status),
        )

    def set_hold_status(self, token, status):
        self.db.execute('UPDATE desk_holds SET status = ? WHERE token = ?', (status, token))

    def outstanding_holds(self, run, cue, direction):
        rows = self.db.execute(
            "SELECT token, request FROM desk_holds WHERE run = ? AND cue = ? AND direction = ?"
            " AND status = 'outstanding'",
            (run, cue, direction),
        )
        return tuple({'token': row[0], 'request': row[1]} for row in rows)

    def count_taken_holds(self, run, request, cue):
        row = self.db.execute(
            "SELECT count(*) FROM desk_holds WHERE run = ? AND direction = 'taken' AND request = ? AND cue = ?",
            (run, request, cue),
        ).fetchone()
        return row[0]


# ---------------------------------------------------------------------------
# 提示覆盖

def sheet_bound_text(sheet, cue):
    """版面按冻结绑定给出的提示正文；未绑定时为 None。"""
    cues = sheet.get('cues')
    if not isinstance(cues, dict):
        return None
    binding = cues.get(cue)
    if not isinstance(binding, dict):
        return None
    return binding.get('text')


def sheet_covers(sheet, cue, round_cues):
    """场内是否按冻结绑定覆盖某个共用提示。历史谱缺少 cues 时不覆盖。"""
    return cue in round_cues and sheet_bound_text(sheet, cue) == round_cues[cue]


def removed_cues(current_sheet, target_sheet, round_cues):
    return tuple(
        cue for cue in round_cues
        if sheet_covers(current_sheet, cue, round_cues) and not sheet_covers(target_sheet, cue, round_cues)
    )


def check_round_cue_texts(sheet, round_cues):
    """版面如含本轮提示号，其绑定正文须与开轮单相符。"""
    cues = sheet.get('cues')
    if not isinstance(cues, dict):
        return
    for cue, binding in cues.items():
        if cue in round_cues and (not isinstance(binding, dict) or binding.get('text') != round_cues[cue]):
            raise ConflictError('cue_text_mismatch')


def make_token(desk, request, cue, generation):
    return json.dumps([desk, request, cue, generation], ensure_ascii=False)


def _parse_token(token):
    try:
        parts = json.loads(token)
    except ValueError:
        return None
    if isinstance(parts, list) and len(parts) == 4 and all(isinstance(part, str) for part in parts[:3]):
        return parts
    return None


def parse_token_desk(token):
    parts = _parse_token(token)
    return '' if parts is None else parts[0]


def parse_token_request(token):
    parts = _parse_token(token)
    return '' if parts is None else parts[1]


# ---------------------------------------------------------------------------
# 本机 HTTP 调用

def call_json(method, base, path, payload=None, query=None, timeout=5):
    parts = urlsplit(base)
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme != 'http' or not parts.hostname or port is None:
        raise ValueError('invalid_url')
    if query:
        path += '?' + urlencode(query)
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    try:
        with closing(HTTPConnection(parts.hostname, port, timeout=timeout)) as connection:
            connection.request(method, path, body, headers)
            with connection.getresponse() as response:
                return response.status, json.load(response)
    except (OSError, HTTPException, json.JSONDecodeError, UnicodeDecodeError):
        raise UpstreamError('upstream_unreachable') from None


def fetch_sheet(publisher, issue, voice):
    """从谱务服务取回固定声部版面；谱务缺失时按原错误码上报。"""
    status, body = call_json('GET', publisher, '/read', query={'issue': issue, 'voice': voice})
    if status == 200:
        return body
    if status == 404 and isinstance(body, dict) and 'error' in body:
        raise KeyError(body['error'])
    raise UpstreamError('upstream_unreachable')


def _valid_url(url):
    try:
        parts = urlsplit(url)
        return parts.scheme == 'http' and bool(parts.hostname) and parts.port is not None
    except ValueError:
        return False


def validate_open_sheet(payload):
    require_fields(payload, {'run', 'desk', 'draft', 'publisher', 'desks', 'cues'})
    run = require_text(payload['run'], 'run')
    desk = require_text(payload['desk'], 'desk')
    draft = require_text(payload['draft'], 'draft')
    publisher = require_text(payload['publisher'], 'publisher')
    if not _valid_url(publisher):
        raise ValueError('invalid_url')
    desks = payload['desks']
    if not isinstance(desks, dict) or len(desks) != 2 or desk not in desks:
        raise ValueError('invalid_desks')
    parsed = {}
    for name, entry in desks.items():
        require_text(name, 'desk')
        if not isinstance(entry, dict) or set(entry) != {'url', 'voice', 'initial_issue'}:
            raise ValueError('invalid_desks')
        parsed[name] = {
            'url': require_text(entry['url'], 'url'),
            'voice': require_text(entry['voice'], 'voice'),
            'initial_issue': require_integer(entry['initial_issue'], 'initial_issue', 1),
        }
        if not _valid_url(parsed[name]['url']):
            raise ValueError('invalid_url')
    voices = [entry['voice'] for entry in parsed.values()]
    if len(set(voices)) != 2:
        raise ValueError('invalid_desks')
    cues = payload['cues']
    if not isinstance(cues, dict):
        raise ValueError('invalid_cues')
    for cue, text in cues.items():
        require_text(cue, 'cue')
        require_text(text, 'text')
    return run, desk, draft, publisher, parsed, dict(cues)
