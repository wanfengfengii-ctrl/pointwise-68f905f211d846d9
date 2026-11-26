from dataclasses import dataclass, replace

from score.notes import NoteEvent


@dataclass(frozen=True)
class PrintedMark:
    mark_id: int
    position: int
    text: str


@dataclass(frozen=True)
class PhraseLine:
    phrase: str
    notes: tuple[NoteEvent, ...]
    marks: tuple[PrintedMark, ...] = ()


def layout_phrases(voice, phrases):
    return tuple(PhraseLine(phrase.phrase, voice.notes[phrase.start:phrase.stop]) for phrase in phrases)


def place_breath_marks(lines, placements):
    lines_by_id = {line.phrase: line for line in lines}
    marks = {line.phrase: [] for line in lines}
    for placement in placements:
        line = lines_by_id[placement.phrase]
        if not 0 <= placement.position <= len(line.notes):
            raise RuntimeError('Placement outside the printed phrase')
        marks[line.phrase].append(PrintedMark(placement.mark_id, placement.position, placement.text))
    return tuple(
        replace(line, marks=tuple(sorted(marks[line.phrase], key=lambda mark: (mark.position, mark.mark_id))))
        for line in lines
    )
