from structure.phrases import split_phrase


def apply_split(score, revisions, operations, parent, phrase_id, position):
    head = revisions.require_head(score.draft, parent)
    original = head.find_phrase(phrase_id)
    left, right = split_phrase(original, position, f'split:{head.revision}:L', f'split:{head.revision}:R')
    index = head.phrases.index(original)
    updated = head.phrases[:index] + (left, right) + head.phrases[index + 1:]
    revision = revisions.append(score, parent, updated)
    operation = operations.record_split(head, revision, original, left, right)
    return revision, operation
