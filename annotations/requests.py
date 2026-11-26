from dataclasses import dataclass

from rehearsal.errors import require_fields, require_integer, require_text


@dataclass(frozen=True)
class MarkRequest:
    request: str
    issue: int
    voice: str
    phrase: str
    position: int
    text: str


def parse_mark_request(payload):
    require_fields(payload, {'request', 'issue', 'voice', 'phrase', 'position', 'text'})
    return MarkRequest(
        require_text(payload['request'], 'request'),
        require_integer(payload['issue'], 'issue', 1),
        require_text(payload['voice'], 'voice'),
        require_text(payload['phrase'], 'phrase'),
        require_integer(payload['position'], 'position', 0),
        require_text(payload['text'], 'text'),
    )


def request_arguments(request):
    return {
        'request': request.request,
        'issue': request.issue,
        'voice': request.voice,
        'phrase': request.phrase,
        'position': request.position,
        'text': request.text,
    }
