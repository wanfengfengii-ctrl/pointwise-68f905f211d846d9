from annotations.positions import resolve_original_position
from annotations.requests import parse_mark_request, request_arguments
from rehearsal.errors import ConflictError


def submit_mark(payload, editions, receipts):
    request = parse_mark_request(payload)
    existing = receipts.get_by_request(request.request)
    if existing is not None:
        mark, receipt = existing
        if request_arguments(mark.request) != request_arguments(request):
            raise ConflictError('request_arguments_changed')
        return receipt

    edition = editions.get(request.issue)
    sheet = editions.get_voice_sheet(edition.issue, request.voice)
    original = resolve_original_position(edition, sheet, request)
    return receipts.save_acceptance(request, original)
