from dataclasses import asdict

from annotations.current import list_working_marks
from publication.voice_sheets import build_voice_sheet, voice_sheet_to_record


def _working_sheet(voice, revision, receipts):
    return build_voice_sheet(
        voice, revision.phrases_for(voice.voice), list_working_marks(receipts, revision, voice.voice)
    )


def issue_score(score, revision, receipts, editions):
    sheets = tuple(_working_sheet(voice, revision, receipts) for voice in score.voices)
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
