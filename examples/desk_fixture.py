import argparse
from contextlib import closing, contextmanager
from http.client import HTTPConnection, RemoteDisconnected
import json
from pathlib import Path
from selectors import DefaultSelector, EVENT_READ
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.parse import urlencode

from rehearsal.server import RehearsalHTTPServer, RehearsalRequestHandler
from rehearsal.service import RehearsalService


HOST = "127.0.0.1"


def sample():
    return json.loads(Path(__file__).with_name("draft.json").read_text(encoding="utf-8"))


class Client:
    def __init__(self, port):
        self.port = port
        self.base = f"http://{HOST}:{port}"

    def call(self, path, payload=None, **query):
        if query:
            path += "?" + urlencode(query)
        with closing(HTTPConnection(HOST, self.port, timeout=5)) as connection:
            if payload is None:
                connection.request("GET", path)
            else:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                connection.request("POST", path, body, {"Content-Type": "application/json"})
            with connection.getresponse() as response:
                return response.status, json.load(response)

    def ok(self, path, payload=None, **query):
        status, body = self.call(path, payload, **query)
        if status != 200:
            raise AssertionError((self.base, path, status, body))
        return body


def serve(database, port, *, drop_path=None, drop_status=200, seeded=False):
    service = RehearsalService(database)
    service.initialize()
    if seeded:
        service.load(sample())
    drop_lock = Lock()
    dropped = False

    class Handler(RehearsalRequestHandler):
        def do_GET(self):
            if self.path == "/ready":
                self._send(200, {"ready": True})
                return
            super().do_GET()

        def log_message(self, format, *args):
            pass

        def _send(self, status, data):
            nonlocal dropped
            if self.command == "POST" and self.path == drop_path and status == drop_status:
                with drop_lock:
                    if not dropped:
                        dropped = True
                        self.close_connection = True
                        return
            super()._send(status, data)

    with RehearsalHTTPServer(service, port) as server:
        server.RequestHandlerClass = Handler
        print(server.server_port, flush=True)
        server.serve_forever()


@contextmanager
def running_desk(database, port=0, *, drop_path=None, drop_status=200):
    args = [sys.executable, "-B", "-m", "examples.desk_fixture", "serve", str(database), str(port)]
    if drop_path is not None:
        args.extend(["--drop-path", drop_path, "--drop-status", str(drop_status)])
    with subprocess.Popen(args, stdout=subprocess.PIPE, text=True) as process:
        try:
            with DefaultSelector() as ready:
                ready.register(process.stdout, EVENT_READ)
                if not ready.select(timeout=10):
                    raise TimeoutError("score service did not start")
                line = process.stdout.readline().strip()
            if not line.isdecimal():
                raise RuntimeError(f"score service startup output: {line!r}")
            client = Client(int(line))
            if client.ok("/ready") != {"ready": True}:
                raise AssertionError("invalid readiness response")
            yield client
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def seed_score(client):
    draft = sample()
    loaded = client.ok("/load", draft)
    issue_args = {"draft": loaded["draft"], "revision": loaded["revision"]}
    plain = client.ok("/issue", issue_args)
    marks = {}
    for voice in loaded["voices"]:
        sheet = client.ok("/read", issue=plain["issue"], voice=voice)
        phrase = sheet["phrases"][0]["phrase"]
        marks[voice] = [client.ok("/mark", {"request": f"{voice}-{position}", "issue": plain["issue"],
                                          "voice": voice, "phrase": phrase, "position": position,
                                          "text": "同吸"}) for position in (0, 1)]
    return issue_args, plain, marks


def demo():
    with TemporaryDirectory(prefix="bridge-desks-") as directory:
        left_db = Path(directory, "left.sqlite3")
        right_db = Path(directory, "right.sqlite3")
        run = {"run": "practice"}
        with running_desk(left_db, drop_path="/handout") as left, running_desk(right_db) as right:
            right_issue = right.ok("/issue", seed_score(right)[0])
            left_args, plain, marks = seed_score(left)
            left_issue = left.ok("/issue", left_args)
            RehearsalService(left_db).prepare_run(run["run"], left_args["draft"])
            RehearsalService(right_db).prepare_run(run["run"], right_issue["draft"])
            right_receipt = right.ok("/handout", {**run, "voice": "alto", "issue": right_issue["issue"]})
            left_body = {**run, "voice": "soprano", "issue": left_issue["issue"]}
            try:
                left.call("/handout", left_body)
            except RemoteDisconnected:
                pass
            else:
                raise AssertionError("the handout reply was not dropped")
            right_summary = right.ok("/close", run)
            left_port = left.port
        with running_desk(left_db, left_port) as reopened:
            left_summary = reopened.ok("/close", run)
            frozen = reopened.ok("/read", issue=left_issue["issue"], voice="soprano")
            old = reopened.ok("/read", issue=plain["issue"], voice="soprano")
        left_rows = left_summary["handouts"]
        printed = frozen["phrases"][0]["marks"]
        expected = [(mark["mark_id"], mark["position"], "同吸") for mark in marks["soprano"]]
        actual = [(mark["mark_id"], mark["position"], mark["text"]) for mark in printed]
        if (len(left_rows) != 1 or any(left_rows[0][key] != value for key, value in left_body.items())
                or right_summary["handouts"] != [right_receipt] or actual != expected or old["phrases"][0]["marks"]):
            raise AssertionError((left_summary, right_summary, actual, expected, old))
        return {"left_after_restart": left_rows[0], "right_handout": right_receipt, "same_text_marks": printed}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("verify", "demo"):
        commands.add_parser(command)
    child = commands.add_parser("serve")
    child.add_argument("database", type=Path)
    child.add_argument("port", type=int)
    child.add_argument("--drop-path")
    child.add_argument("--drop-status", type=int, default=200)
    start = commands.add_parser("start")
    start.add_argument("--host", choices=[HOST], required=True)
    start.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.database, args.port, drop_path=args.drop_path, drop_status=args.drop_status)
    elif args.command == "start":
        with TemporaryDirectory(prefix="bridge-preview-") as directory:
            serve(Path(directory, "preview.sqlite3"), args.port, seeded=True)
    else:
        if args.command == "verify":
            from check_project import main as check_original

            status = check_original()
            if status:
                return status
        print(json.dumps(demo(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
