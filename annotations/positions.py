from dataclasses import dataclass

from publication.voice_sheets import find_phrase_on_sheet


@dataclass(frozen=True)
class OriginalPosition:
    draft: str
    revision: int
    gap: int


def resolve_original_position(edition, sheet, request):
    if request.issue != edition.issue or request.voice != sheet.voice:
        raise ValueError('invalid_original_voice')
    offset, line = find_phrase_on_sheet(sheet, request.phrase)
    if line is None or not 0 <= request.position <= len(line.notes):
        raise ValueError('invalid_original_position')
    return OriginalPosition(edition.draft, edition.revision, offset + request.position)
