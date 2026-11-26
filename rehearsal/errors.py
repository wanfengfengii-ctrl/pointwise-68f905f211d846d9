SQLITE_MAX_INTEGER = (1 << 63) - 1


class ConflictError(Exception):
    pass


def require_fields(data, required, optional=()):
    required = set(required)
    if not isinstance(data, dict):
        raise ValueError('expected_object')
    keys = set(data)
    if not required <= keys or keys - required - set(optional):
        raise ValueError('invalid_fields')


def require_text(value, field):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'invalid_{field}')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError(f'invalid_{field}') from None
    return value


def require_integer(value, field, minimum):
    if type(value) is not int or not minimum <= value <= SQLITE_MAX_INTEGER:
        raise ValueError(f'invalid_{field}')
    return value
