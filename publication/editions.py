from dataclasses import asdict, replace

from annotations.current import list_working_marks
from publication.voice_sheets import build_voice_sheet, voice_sheet_to_record
from rehearsal.errors import ConflictError, require_integer, require_text


def _working_sheet(voice, revision, receipts):
    return build_voice_sheet(
        voice, revision.phrases_for(voice.voice), list_working_marks(receipts, revision, voice.voice)
    )


def _bind_cues(sheets, cues):
    if not isinstance(cues, dict):
        raise ValueError('invalid_cues')
    by_voice = {sheet.voice: sheet for sheet in sheets}
    bindings = {voice: {} for voice in by_voice}
    for voice, entries in cues.items():
        require_text(voice, 'voice')
        if voice not in by_voice:
            raise KeyError('unknown_voice')
        if not isinstance(entries, dict):
            raise ValueError('invalid_cues')
        marks = {mark.mark_id: mark for line in by_voice[voice].phrases for mark in line.marks}
        for cue, binding in entries.items():
            require_text(cue, 'cue')
            if not isinstance(binding, dict) or set(binding) != {'text', 'mark_id'}:
                raise ValueError('invalid_cue_binding')
            text = require_text(binding['text'], 'text')
            mark_id = require_integer(binding['mark_id'], 'mark_id', 1)
            mark = marks.get(mark_id)
            if mark is None:
                raise KeyError('unknown_mark')
            if mark.text != text:
                raise ConflictError('cue_text_mismatch')
            bindings[voice][cue] = {'text': text, 'mark_id': mark_id}
    return tuple(
        replace(sheet, cues=tuple(sorted((cue, bindings[sheet.voice][cue]) for cue in bindings[sheet.voice])))
        for sheet in sheets
    )


def issue_score(score, revision, receipts, editions, cues=None):
    sheets = tuple(_working_sheet(voice, revision, receipts) for voice in score.voices)
    if cues is not None:
        sheets = _bind_cues(sheets, cues)
    edition = editions.save(score.draft, revision.revision, sheets)
    return {**asdict(edition), 'sheets': [voice_sheet_to_record(sheet) for sheet in sheets]}


def read_score(scores, revisions, receipts, editions, *, voice, draft=None, issue=None):
    if issue is not None:
        edition = editions.get(issue)
        sheet = editions.get_voice_sheet(issue, voice)
        return {**asdict(edition), **voice_sheet_to_record(sheet)}

    score = scores.get(draft)
    revision = revisions.get_head(draft)
    sheet = _working_sheet(score.get_voice(voice), revision, receipts)
    return {'draft': draft, 'revision': revision.revision, 'issue': None, **voice_sheet_to_record(sheet)}
