import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from rehearsal.errors import Accepted, ConflictError, UpstreamError
from rehearsal.service import RehearsalService


_POST_ROUTES = {'/load': 'load', '/edit': 'edit', '/issue': 'issue', '/mark': 'mark',
                '/handout': 'handout', '/close': 'close', '/desk/open': 'desk_open',
                '/desk/stage': 'desk_stage', '/desk/switch': 'desk_switch',
                '/desk/hold': 'desk_hold', '/desk/release': 'desk_release'}
_GET_ROUTES = {'/read': 'read', '/desk/current': 'desk_current'}
_MAX_BODY_BYTES = 1_048_576


def _unique_fields(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError('duplicate_json_field')
        fields[key] = value
    return fields


class RehearsalRequestHandler(BaseHTTPRequestHandler):
    def _send(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self):
        if self.headers.get_content_type() != 'application/json':
            raise ValueError('expected_json_content_type')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
            raise ValueError('invalid_content_length')
        length = int(lengths[0])
        if not 0 < length <= _MAX_BODY_BYTES:
            raise ValueError('invalid_content_length')
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'), object_pairs_hook=_unique_fields)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('invalid_json') from None

    def _read_query(self, query_string):
        values = parse_qs(query_string, strict_parsing=True, keep_blank_values=True,
                          errors='strict', max_num_fields=8)
        if any(len(items) != 1 for items in values.values()):
            raise ValueError('duplicate_query_field')
        query = {key: items[0] for key, items in values.items()}
        if 'issue' in query:
            value = query['issue']
            if not value.isascii() or not value.isdecimal():
                raise ValueError('invalid_issue')
            query['issue'] = int(value)
        return query

    def _handle(self, method):
        try:
            url = urlsplit(self.path)
            if method == 'GET':
                route = _GET_ROUTES.get(url.path)
                if route is None:
                    raise KeyError('unknown_route')
                result = getattr(self.server.service, route)(self._read_query(url.query))
            else:
                route = _POST_ROUTES.get(url.path)
                if route is None:
                    raise KeyError('unknown_route')
                if url.query:
                    raise ValueError('unexpected_query')
                handler = getattr(self.server.service, route)
                result = handler(self._body())
        except Accepted as accepted:
            self._send(202, accepted.body)
        except ValueError as error:
            self._send(400, {'error': str(error)})
        except KeyError as error:
            self._send(404, {'error': error.args[0]})
        except ConflictError as error:
            self._send(409, {'error': str(error)})
        except UpstreamError as error:
            self._send(502, {'error': str(error)})
        except sqlite3.Error:
            self._send(500, {'error': 'storage_failure'})
        else:
            self._send(200, result)

    def do_GET(self):
        self._handle('GET')

    def do_POST(self):
        self._handle('POST')


class RehearsalHTTPServer(ThreadingHTTPServer):
    def __init__(self, service, port):
        self.service = service
        super().__init__(('127.0.0.1', port), RehearsalRequestHandler)


def main(argv=None):
    parser = argparse.ArgumentParser(description='本机合唱排练谱服务')
    parser.add_argument('--db', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--port', type=int, default=8000)
    prepare = commands.add_parser('prepare-run')
    prepare.add_argument('--run', required=True)
    prepare.add_argument('--draft', required=True)
    args = parser.parse_args(argv)

    try:
        service = RehearsalService(args.db)
        service.initialize()
        if args.command == 'prepare-run':
            record = service.prepare_run(args.run, args.draft)
            print(json.dumps(record, ensure_ascii=False))
        else:
            with RehearsalHTTPServer(service, args.port) as server:
                print(f'http://127.0.0.1:{server.server_port}', flush=True)
                server.serve_forever()
    except (ConflictError, KeyError, ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, f'{error}\n')
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
