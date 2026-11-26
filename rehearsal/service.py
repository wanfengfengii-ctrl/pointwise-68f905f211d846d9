import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from threading import Lock

from annotations.intake import submit_mark
from annotations.receipts import ReceiptStore
from publication.editions import issue_score, read_score
from publication.history import EditionStore
from rehearsal.closeout import CloseoutStore, close_run
from rehearsal.desks import (
    DeskRound,
    DeskStore,
    call_json,
    check_round_cue_texts,
    fetch_sheet,
    make_token,
    parse_token_desk,
    parse_token_request,
    removed_cues,
    sheet_covers,
    validate_open_sheet,
)
from rehearsal.errors import (
    Accepted,
    ConflictError,
    UpstreamError,
    require_fields,
    require_integer,
    require_text,
)
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
        self._switch_lock = Lock()

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
                          RunStore, PartStore, HandoutStore, CloseoutStore, DeskStore):
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
        require_fields(payload, {'draft', 'revision'}, {'cues'})
        draft = require_text(payload['draft'], 'draft')
        revision = require_integer(payload['revision'], 'revision', 1)
        with self._transaction(write=True) as db:
            score = ScoreStore(db).get(draft)
            selected = RevisionStore(db).get(draft, revision)
            return issue_score(score, selected, ReceiptStore(db), EditionStore(db), cues=payload.get('cues'))

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

    # ------------------------------------------------------------------
    # 双台轮次

    @staticmethod
    def _desk_receipt_body(desk_round, receipt, request=None):
        body = {'run': desk_round.run, 'desk': desk_round.desk, 'issue': receipt['issue'],
                'handout_id': receipt['handout_id'], 'sheet': receipt['sheet']}
        if request is not None:
            body['request'] = request
        return body

    def desk_open(self, payload):
        run, desk, draft, publisher, desks, cues = validate_open_sheet(payload)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._transaction() as db:
            store = DeskStore(db)
            existing = store.find_round(run)
            if existing is not None:
                if existing.payload != canonical:
                    raise ConflictError('open_arguments_changed')
                receipt = store.get_receipt(existing.open_handout_id)
                return self._desk_receipt_body(existing, {**receipt, 'handout_id': existing.open_handout_id})
        own = desks[desk]
        peer = desks[next(name for name in desks if name != desk)]
        own_sheet = fetch_sheet(publisher, own['initial_issue'], own['voice'])
        peer_sheet = fetch_sheet(publisher, peer['initial_issue'], peer['voice'])
        for sheet in (own_sheet, peer_sheet):
            if sheet.get('draft') != draft:
                raise ConflictError('edition_for_other_draft')
            check_round_cue_texts(sheet, cues)
        for cue in cues:
            if not sheet_covers(own_sheet, cue, cues) and not sheet_covers(peer_sheet, cue, cues):
                raise ConflictError('cues_uncovered')
        with self._transaction(write=True) as db:
            store = DeskStore(db)
            existing = store.find_round(run)
            if existing is not None:
                if existing.payload != canonical:
                    raise ConflictError('open_arguments_changed')
                receipt = store.get_receipt(existing.open_handout_id)
                return self._desk_receipt_body(existing, {**receipt, 'handout_id': existing.open_handout_id})
            handout_id = store.append_receipt(run, own['initial_issue'], own_sheet)
            store.save_round(DeskRound(run, desk, draft, publisher, desks, cues, canonical, handout_id))
            store.set_current(run, handout_id)
            store.save_staged(run, own['initial_issue'], own_sheet)
            return self._desk_receipt_body(
                DeskRound(run, desk, draft, publisher, desks, cues, canonical, handout_id),
                {'issue': own['initial_issue'], 'handout_id': handout_id, 'sheet': own_sheet})

    def desk_stage(self, payload):
        require_fields(payload, {'run', 'issue'})
        run = require_text(payload['run'], 'run')
        issue = require_integer(payload['issue'], 'issue', 1)
        with self._transaction() as db:
            desk_round = DeskStore(db).get_round(run)
        sheet = fetch_sheet(desk_round.publisher, issue, desk_round.desks[desk_round.desk]['voice'])
        if sheet.get('draft') != desk_round.draft:
            raise ConflictError('edition_for_other_draft')
        check_round_cue_texts(sheet, desk_round.cues)
        with self._transaction(write=True) as db:
            DeskStore(db).save_staged(run, issue, sheet)
        return {'run': run, 'desk': desk_round.desk, 'issue': issue, 'sheet': sheet}

    def desk_current(self, query):
        require_fields(query, {'run'})
        run = require_text(query['run'], 'run')
        with self._transaction() as db:
            store = DeskStore(db)
            desk_round = store.get_round(run)
            current = store.get_current(run)
        return self._desk_receipt_body(desk_round, current)

    def desk_switch(self, payload):
        require_fields(payload, {'run', 'request', 'expected_issue', 'issue'})
        run = require_text(payload['run'], 'run')
        request = require_text(payload['request'], 'request')
        expected_issue = require_integer(payload['expected_issue'], 'expected_issue', 1)
        issue = require_integer(payload['issue'], 'issue', 1)
        with self._switch_lock:
            with self._transaction() as db:
                store = DeskStore(db)
                desk_round = store.get_round(run)
                record = store.find_request(run, request)
            if record is not None:
                if record['expected_issue'] != expected_issue or record['issue'] != issue:
                    raise ConflictError('request_arguments_changed')
                if record['status'] == 'switched':
                    return self._desk_reclaim(desk_round, record)
                if record['status'] == 'rejected':
                    raise ConflictError(record['error'])
                return self._desk_attempt(desk_round, record)
            with self._transaction(write=True) as db:
                store = DeskStore(db)
                current = store.get_current(run)
                if current['issue'] != expected_issue:
                    raise ConflictError('expected_issue_stale')
                target = store.get_staged(run, issue)
                removed = removed_cues(current['sheet'], target, desk_round.cues)
                if not removed:
                    handout_id = store.append_receipt(run, issue, target)
                    store.set_current(run, handout_id)
                    store.save_request(run, request, expected_issue, issue, 'switched', [],
                                       handout_id=handout_id)
                    return self._desk_receipt_body(
                        desk_round, {'issue': issue, 'handout_id': handout_id, 'sheet': target}, request)
                store.save_request(run, request, expected_issue, issue, 'pending', list(removed))
            record = {'run': run, 'request': request, 'expected_issue': expected_issue, 'issue': issue,
                      'status': 'pending', 'removed': list(removed), 'handout_id': None, 'error': None}
            return self._desk_attempt(desk_round, record)

    def _desk_reclaim(self, desk_round, record):
        with self._transaction() as db:
            receipt = DeskStore(db).get_receipt(record['handout_id'])
        return self._desk_receipt_body(
            desk_round, {**receipt, 'handout_id': record['handout_id']}, record['request'])

    def _desk_pending_body(self, desk_round, record):
        with self._transaction() as db:
            current = DeskStore(db).get_current(desk_round.run)
        return self._desk_receipt_body(desk_round, current, record['request'])

    def _desk_attempt(self, desk_round, record):
        """办理或续办一笔待核换谱。成功返回新实领；待核抛 Accepted，冲突抛 ConflictError。"""
        run = desk_round.run
        request = record['request']
        removed = record['removed']
        for cue in removed:
            with self._transaction() as db:
                basis = [hold for hold in DeskStore(db).outstanding_holds(run, cue, 'taken')
                         if hold['request'] == request]
            if basis:
                continue
            outcome = self._request_hold(desk_round, request, cue)
            if outcome == 'reject':
                self._desk_reject(desk_round, record, 'cue_last_copy')
            if outcome != 'ok':
                raise Accepted(self._desk_pending_body(desk_round, record))
        for cue in removed:
            while True:
                with self._transaction() as db:
                    given = DeskStore(db).outstanding_holds(run, cue, 'given')
                if not given:
                    break
                if self._request_release(desk_round, request, cue, given[0]['token']) != 'ok':
                    raise Accepted(self._desk_pending_body(desk_round, record))
        with self._transaction(write=True) as db:
            store = DeskStore(db)
            current = store.get_current(run)
            outcome = None
            if current['issue'] != record['expected_issue']:
                store.settle_request(run, request, 'rejected', error='expected_issue_stale')
                outcome = ('reject', 'expected_issue_stale')
            else:
                uncovered = [cue for cue in removed
                             if not any(hold['request'] == request
                                        for hold in store.outstanding_holds(run, cue, 'taken'))]
                blocked = [cue for cue in removed if store.outstanding_holds(run, cue, 'given')]
                if uncovered or blocked:
                    outcome = ('pending', None)
                else:
                    staged = store.get_staged(run, record['issue'])
                    handout_id = store.append_receipt(run, record['issue'], staged)
                    store.set_current(run, handout_id)
                    store.settle_request(run, request, 'switched', handout_id=handout_id)
                    outcome = ('switched', handout_id, staged)
        if outcome[0] == 'reject':
            raise ConflictError(outcome[1])
        if outcome[0] == 'pending':
            raise Accepted(self._desk_pending_body(desk_round, record))
        return self._desk_receipt_body(
            desk_round, {'issue': record['issue'], 'handout_id': outcome[1], 'sheet': outcome[2]}, request)

    def _desk_reject(self, desk_round, record, error):
        run = desk_round.run
        with self._transaction(write=True) as db:
            store = DeskStore(db)
            store.settle_request(run, record['request'], 'rejected', error=error)
            released = []
            for cue in record['removed']:
                for hold in store.outstanding_holds(run, cue, 'taken'):
                    if hold['request'] == record['request']:
                        store.set_hold_status(hold['token'], 'released')
                        released.append((cue, hold['token']))
        for cue, token in released:
            self._request_release(desk_round, record['request'], cue, token)
        raise ConflictError(error)

    def _request_hold(self, desk_round, request, cue):
        """向对台申请某提示的覆盖保障，返回 'ok'、'reject' 或 'pending'。"""
        run = desk_round.run
        peer = desk_round.peer
        peer_url = desk_round.desks[peer]['url']
        with self._transaction() as db:
            generation = DeskStore(db).count_taken_holds(run, request, cue)
        for _ in range(3):
            token = make_token(desk_round.desk, request, cue, generation)
            try:
                status, body = call_json('POST', peer_url, '/desk/hold',
                                         {'run': run, 'desk': desk_round.desk, 'request': request,
                                          'cue': cue, 'token': token})
            except UpstreamError:
                return 'pending'
            error = body.get('error') if isinstance(body, dict) else None
            if status == 200:
                released = body.get('released', []) if isinstance(body, dict) else []
                with self._transaction(write=True) as db:
                    store = DeskStore(db)
                    store.save_hold(token, run, cue, 'taken', request, peer, 'outstanding')
                    for old in released:
                        store.set_hold_status(old, 'released')
                return 'ok'
            if status == 409 and error == 'cue_not_covered':
                return 'reject'
            if status == 409 and error == 'hold_released':
                with self._transaction(write=True) as db:
                    store = DeskStore(db)
                    store.save_hold(token, run, cue, 'taken', request, peer, 'released')
                    store.set_hold_status(token, 'released')
                generation += 1
                continue
            return 'pending'
        return 'pending'

    def _request_release(self, desk_round, request, cue, token):
        """请对台解除本台给出的某条保障，返回 'ok' 或 'pending'。"""
        run = desk_round.run
        peer = desk_round.peer
        try:
            status, _body = call_json('POST', desk_round.desks[peer]['url'], '/desk/release',
                                      {'run': run, 'desk': desk_round.desk, 'request': request,
                                       'cue': cue, 'token': token})
        except UpstreamError:
            return 'pending'
        if status == 200:
            with self._transaction(write=True) as db:
                DeskStore(db).set_hold_status(token, 'released')
            return 'ok'
        return 'pending'

    # ------------------------------------------------------------------
    # 台间内部交接

    def desk_hold(self, payload):
        require_fields(payload, {'run', 'desk', 'request', 'cue', 'token'})
        run = require_text(payload['run'], 'run')
        desk = require_text(payload['desk'], 'desk')
        request = require_text(payload['request'], 'request')
        cue = require_text(payload['cue'], 'cue')
        token = require_text(payload['token'], 'token')
        with self._transaction(write=True) as db:
            store = DeskStore(db)
            desk_round = store.get_round(run)
            if desk != desk_round.peer:
                raise ValueError('invalid_desk')
            if cue not in desk_round.cues:
                raise ValueError('invalid_cue')
            existing = store.find_hold(token)
            if existing is not None:
                if (existing['direction'] != 'given' or existing['request'] != request
                        or existing['cue'] != cue or existing['run'] != run):
                    raise ConflictError('hold_arguments_changed')
                if existing['status'] == 'released':
                    raise ConflictError('hold_released')
                return self._grant_body(store, desk_round, cue, token)
            current = store.get_current(run)
            if not sheet_covers(current['sheet'], cue, desk_round.cues):
                raise ConflictError('cue_not_covered')
            for hold in store.outstanding_holds(run, cue, 'taken'):
                tied = store.find_request(run, hold['request'])
                if tied is not None and tied['status'] == 'pending':
                    raise ConflictError('hold_contended')
            store.save_hold(token, run, cue, 'given', request, desk, 'outstanding')
            return self._grant_body(store, desk_round, cue, token)

    def _grant_body(self, store, desk_round, cue, token):
        run = desk_round.run
        released = []
        for hold in store.outstanding_holds(run, cue, 'taken'):
            tied = store.find_request(run, hold['request'])
            if tied is None or tied['status'] != 'pending':
                store.set_hold_status(hold['token'], 'released')
                released.append(hold['token'])
        current = store.get_current(run)
        return {'run': run, 'desk': desk_round.desk, 'cue': cue, 'token': token,
                'issue': current['issue'], 'released': released}

    def desk_release(self, payload):
        require_fields(payload, {'run', 'desk', 'request', 'cue', 'token'})
        run = require_text(payload['run'], 'run')
        desk = require_text(payload['desk'], 'desk')
        request = require_text(payload['request'], 'request')
        cue = require_text(payload['cue'], 'cue')
        token = require_text(payload['token'], 'token')
        with self._transaction(write=True) as db:
            store = DeskStore(db)
            desk_round = store.get_round(run)
            if desk != desk_round.peer:
                raise ValueError('invalid_desk')
            if cue not in desk_round.cues:
                raise ValueError('invalid_cue')
            hold = store.find_hold(token)
            if hold is not None and hold['direction'] == 'given':
                # 持保方主动解除本台给出的保障。
                store.set_hold_status(token, 'released')
                return {'run': run, 'cue': cue, 'token': token, 'released': True}
            if parse_token_desk(token) != desk_round.desk:
                # 本台是保障给出方但未登记，无需处理。
                return {'run': run, 'cue': cue, 'token': token, 'released': True}
            if hold is None:
                # 本台尚未登记该保障：立碑挡下迟到的旧保障，再视为已解除。
                store.save_hold(token, run, cue, 'taken', parse_token_request(token), desk, 'released')
                return {'run': run, 'cue': cue, 'token': token, 'released': True}
            if hold['status'] == 'released':
                return {'run': run, 'cue': cue, 'token': token, 'released': True}
            tied = store.find_request(run, hold['request'])
            if tied is not None and tied['status'] == 'pending':
                # 本台待核申请仍指望这条保障，按（申请号、谱台）次序裁定。
                if (request, desk) < (hold['request'], desk_round.desk):
                    store.set_hold_status(token, 'released')
                    return {'run': run, 'cue': cue, 'token': token, 'released': True}
                raise ConflictError('release_contended')
            current = store.get_current(run)
            if tied is not None and tied['status'] == 'switched' and not sheet_covers(
                    current['sheet'], cue, desk_round.cues):
                raise ConflictError('release_contended')
            store.set_hold_status(token, 'released')
            return {'run': run, 'cue': cue, 'token': token, 'released': True}
