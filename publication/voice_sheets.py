from dataclasses import asdict, dataclass

from publication.layout import PhraseLine, PrintedMark, layout_phrases, place_breath_marks
from score.beats import parse_beat, render_beat
from score.notes import NoteEvent


@dataclass(frozen=True)
class VoiceSheet:
    voice: str
    phrases: tuple[PhraseLine, ...]


def build_voice_sheet(voice, phrases, placements):
    lines = layout_phrases(voice, phrases)
    return VoiceSheet(voice.voice, place_breath_marks(lines, placements))


def find_phrase_on_sheet(sheet, phrase):
    offset = 0
    for line in sheet.phrases:
        if line.phrase == phrase:
            return offset, line
        offset += len(line.notes)
    return offset, None


def voice_sheet_to_record(sheet):
    return {
        'voice': sheet.voice,
        'phrases': [
            {
                'phrase': line.phrase,
                'notes': [
                    {'number': note.number, 'beat': render_beat(note.beat), 'lyric': note.lyric}
                    for note in line.notes
                ],
                'marks': [asdict(mark) for mark in line.marks],
            }
            for line in sheet.phrases
        ],
    }


def voice_sheet_from_record(record):
    phrases = []
    for line in record['phrases']:
        notes = tuple(NoteEvent(note['number'], parse_beat(note['beat']), note['lyric']) for note in line['notes'])
        marks = tuple(PrintedMark(**mark) for mark in line['marks'])
        phrases.append(PhraseLine(line['phrase'], notes, marks))
    return VoiceSheet(record['voice'], tuple(phrases))
