from itertools import pairwise

from rehearsal.errors import ConflictError


def undo_split(score, revisions, operations, parent, split_id):
    head = revisions.require_head(score.draft, parent)
    split = operations.get_split(score.draft, split_id)
    if operations.get_undo_for_split(split.operation) is not None:
        raise ConflictError('split_already_undone')

    original = revisions.get(score.draft, split.before).find_phrase(split.original)
    for index, (left, right) in enumerate(pairwise(head.phrases)):
        if (left.phrase, right.phrase) != (split.left, split.right):
            continue
        if (left.voice, right.voice, left.start, left.stop, right.stop) != (
            original.voice, original.voice, original.start, right.start, original.stop
        ):
            raise ConflictError('split_no_longer_applicable')
        restored = head.phrases[:index] + (original,) + head.phrases[index + 2:]
        revision = revisions.append(score, parent, restored)
        operation = operations.record_undo(head, revision, split)
        return revision, operation
    raise ConflictError('split_no_longer_applicable')
