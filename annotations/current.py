from dataclasses import dataclass


@dataclass(frozen=True)
class MarkPlacement:
    mark_id: int
    voice: str
    phrase: str
    position: int
    text: str


def list_working_marks(receipts, revision, voice):
    placements = []
    for mark in receipts.list_for_voice(revision.draft, voice):
        phrase, position = revision.locate_gap(voice, mark.original.gap, mark.request.phrase)
        placements.append(MarkPlacement(mark.mark_id, voice, phrase, position, mark.request.text))
    return tuple(placements)
