import argparse
from contextlib import closing, contextmanager
from http.client import HTTPConnection, RemoteDisconnected
import json
from pathlib import Path
from selectors import DefaultSelector, EVENT_READ
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from urllib.parse import urlencode

from rehearsal.desks import make_token
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


# ---------------------------------------------------------------------------
# 双台换谱联合演练

def _has_x(sheet):
    return "X" in sheet.get("cues", {})


def _open_sheet(run, desk, draft, publisher, a_base, b_base, a_issue, b_issue):
    return {"run": run, "desk": desk, "draft": draft, "publisher": publisher,
            "desks": {"A": {"url": a_base, "voice": "soprano", "initial_issue": a_issue},
                      "B": {"url": b_base, "voice": "alto", "initial_issue": b_issue}},
            "cues": {"X": "同吸"}}


def _seed_cue_versions(pub):
    issue_args, plain, marks = seed_score(pub)
    soprano_x = {"text": "同吸", "mark_id": marks["soprano"][0]["mark_id"]}
    alto_x = {"text": "同吸", "mark_id": marks["alto"][0]["mark_id"]}
    versions = {
        "both": pub.ok("/issue", {**issue_args, "cues": {"soprano": {"X": soprano_x}, "alto": {"X": alto_x}}}),
        "only_a": pub.ok("/issue", {**issue_args, "cues": {"soprano": {"X": soprano_x}}}),
        "neither": pub.ok("/issue", {**issue_args, "cues": {}}),
    }
    return issue_args, plain, marks, versions


def _check_issuance(pub, issue_args, plain, marks, versions):
    both, only_a, neither = versions["both"], versions["only_a"], versions["neither"]
    # 按固定谱读回检查绑定的是指定原号
    for issue, voice, mark in ((both, "soprano", marks["soprano"][0]), (both, "alto", marks["alto"][0]),
                               (only_a, "soprano", marks["soprano"][0])):
        sheet = pub.ok("/read", issue=issue["issue"], voice=voice)
        expected = {"X": {"text": "同吸", "mark_id": mark["mark_id"]}}
        if sheet.get("cues") != expected:
            raise AssertionError(("frozen cues", voice, sheet.get("cues"), expected))
    # 未绑定的声部返回空对象；发行响应的各个 sheets 元素各带自己的 cues
    if pub.ok("/read", issue=only_a["issue"], voice="alto").get("cues") != {}:
        raise AssertionError("unbound voice must read back an empty cue object")
    if pub.ok("/read", issue=neither["issue"], voice="soprano").get("cues") != {}:
        raise AssertionError("unbound issue must read back an empty cue object")
    sheets = {sheet["voice"]: sheet for sheet in both["sheets"]}
    if sheets["soprano"].get("cues", {}).get("X", {}).get("mark_id") != marks["soprano"][0]["mark_id"]:
        raise AssertionError(("issue response sheets", both["sheets"]))
    # 未传 cues 的发行保持原形状
    if "cues" in pub.ok("/read", issue=plain["issue"], voice="soprano"):
        raise AssertionError("plain issue must keep the original shape")
    # 后几份仍可有普通「同吸」标记
    if len(pub.ok("/read", issue=neither["issue"], voice="soprano")["phrases"][0]["marks"]) != 2:
        raise AssertionError("ordinary 同吸 marks must remain ordinary marks")
    # 错绑对台标记号、正文不符、错声部都整次退回
    bad_mark = {"text": "同吸", "mark_id": marks["alto"][0]["mark_id"]}
    status, _ = pub.call("/issue", {**issue_args, "cues": {"soprano": {"X": bad_mark}}})
    if status != 404:
        raise AssertionError(("binding the peer's mark must be rejected", status))
    bad_text = {"text": "换气", "mark_id": marks["soprano"][0]["mark_id"]}
    status, _ = pub.call("/issue", {**issue_args, "cues": {"soprano": {"X": bad_text}}})
    if status != 409:
        raise AssertionError(("text mismatch must be rejected", status))
    status, _ = pub.call("/issue", {**issue_args, "cues": {"tenor": {"X": bad_text}}})
    if status != 404:
        raise AssertionError(("unknown voice must be rejected", status))
    return {"both": both["issue"], "only_a": only_a["issue"], "neither": neither["issue"],
            "bound_marks": {"soprano": marks["soprano"][0]["mark_id"], "alto": marks["alto"][0]["mark_id"]}}


def _open_round(pub_base, a, b, run, issue_args, a_issue, b_issue):
    sheet = lambda desk: _open_sheet(run, desk, issue_args["draft"], pub_base, a.base, b.base,
                                     a_issue, b_issue)
    return a.ok("/desk/open", sheet("A")), b.ok("/desk/open", sheet("B"))


def _drill_ordered(pub, a, b, issue_args, versions):
    both, neither = versions["both"], versions["neither"]
    outcomes = {}
    for run, first, second in (("r1-a-first", "A", "B"), ("r1-b-first", "B", "A")):
        clients = {"A": a, "B": b}
        _open_round(pub.base, a, b, run, issue_args, both["issue"], both["issue"])
        for client in (a, b):
            client.ok("/desk/stage", {"run": run, "issue": neither["issue"]})
        switched = clients[first].ok("/desk/switch", {"run": run, "request": f"{first}-off-{run}",
                                                      "expected_issue": both["issue"], "issue": neither["issue"]})
        if _has_x(switched["sheet"]):
            raise AssertionError(("first switch must drop X", first, switched))
        status, body = clients[second].call("/desk/switch", {"run": run, "request": f"{second}-off-{run}",
                                                             "expected_issue": both["issue"],
                                                             "issue": neither["issue"]})
        if status != 409 or body.get("error") != "cue_last_copy":
            raise AssertionError(("second switch must be rejected", run, status, body))
        currents = {desk: clients[desk].ok("/desk/current", run=run) for desk in ("A", "B")}
        if _has_x(currents[first]["sheet"]) or not _has_x(currents[second]["sheet"]):
            raise AssertionError(("coverage lost", run, currents))
        outcomes[run] = {"first": first, "first_handout": switched["handout_id"],
                         "second_rejected": body["error"],
                         "currents": {desk: {"issue": currents[desk]["issue"], "has_x": _has_x(currents[desk]["sheet"])}
                                      for desk in ("A", "B")}}
    return outcomes


def _drill_race(pub, a, b, issue_args, versions):
    both, neither = versions["both"], versions["neither"]
    run = "r1-race"
    _open_round(pub.base, a, b, run, issue_args, both["issue"], both["issue"])
    for client in (a, b):
        client.ok("/desk/stage", {"run": run, "issue": neither["issue"]})
    clients = {"A": a, "B": b}
    results = {}

    def attempt(desk):
        body = {"run": run, "request": f"{desk}-off-{run}", "expected_issue": both["issue"],
                "issue": neither["issue"]}
        for _ in range(12):
            status, payload = clients[desk].call("/desk/switch", body)
            if status != 202:
                results[desk] = (status, payload.get("error") if isinstance(payload, dict) else None)
                return
        results[desk] = (202, None)

    threads = [Thread(target=attempt, args=(desk,)) for desk in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    currents = {desk: clients[desk].ok("/desk/current", run=run) for desk in ("A", "B")}
    # 两台同时申请删同一提示时，不能两笔都换成无该提示的谱
    if not _has_x(currents["A"]["sheet"]) and not _has_x(currents["B"]["sheet"]):
        raise AssertionError(("both desks lost X", results, currents))
    return {"results": results,
            "currents": {desk: {"issue": currents[desk]["issue"], "has_x": _has_x(currents[desk]["sheet"])}
                         for desk in ("A", "B")}}


def _drill_restart(pub, directory, issue_args, versions):
    both, only_a, neither = versions["both"], versions["only_a"], versions["neither"]
    run = "r2-restart"
    a_db = Path(directory, "restart-a.sqlite3")
    with running_desk(Path(directory, "restart-b.sqlite3")) as b:
        with running_desk(a_db, drop_path="/desk/switch") as a:
            sheet = lambda desk: _open_sheet(run, desk, issue_args["draft"], pub.base, a.base, b.base,
                                             only_a["issue"], neither["issue"])
            a.ok("/desk/open", sheet("A"))
            b.ok("/desk/open", sheet("B"))
            b.ok("/desk/stage", {"run": run, "issue": both["issue"]})
            a.ok("/desk/stage", {"run": run, "issue": neither["issue"]})
            b.ok("/desk/stage", {"run": run, "issue": neither["issue"]})
            # 仅 A 实领 X 时，A 仍无权删 X
            status, body = a.call("/desk/switch", {"run": run, "request": "A-early", "expected_issue": only_a["issue"],
                                                   "issue": neither["issue"]})
            if status != 409 or body.get("error") != "cue_last_copy":
                raise AssertionError(("A must not delete the last X", status, body))
            # B 实领 X（只增加提示，离线也可办理）
            received = b.ok("/desk/switch", {"run": run, "request": "B-on", "expected_issue": neither["issue"],
                                             "issue": both["issue"]})
            if not _has_x(received["sheet"]):
                raise AssertionError(("B must receive X", received))
            # A 删 X 办成，但回包被扣留
            body = {"run": run, "request": "A-off", "expected_issue": only_a["issue"], "issue": neither["issue"]}
            try:
                a.call("/desk/switch", body)
            except RemoteDisconnected:
                pass
            else:
                raise AssertionError("the switch reply was not dropped")
            a_port = a.port
        # 重开 A，以原请求复领首执
        with running_desk(a_db, a_port) as reopened:
            first = reopened.ok("/desk/switch", body)
            if _has_x(first["sheet"]) or first["request"] != "A-off":
                raise AssertionError(("reclaim must return the first execution", first))
            current = reopened.ok("/desk/current", run=run)
            if current["issue"] != first["issue"] or _has_x(current["sheet"]):
                raise AssertionError(("current must stay switched", current))
            # 恢复 A 的 X，再让 B 换下 X
            reopened.ok("/desk/stage", {"run": run, "issue": only_a["issue"]})
            restored = reopened.ok("/desk/switch", {"run": run, "request": "A-on", "expected_issue": neither["issue"],
                                                    "issue": only_a["issue"]})
            if not _has_x(restored["sheet"]):
                raise AssertionError(("A must restore X", restored))
            switched_b = b.ok("/desk/switch", {"run": run, "request": "B-off", "expected_issue": both["issue"],
                                               "issue": neither["issue"]})
            if _has_x(switched_b["sheet"]):
                raise AssertionError(("B must drop X", switched_b))
            # 迟到的旧保障不得使 A 再删掉 X
            stale_token = make_token("A", "A-off", "X", 0)
            status, body = b.call("/desk/hold", {"run": run, "desk": "A", "request": "A-off", "cue": "X",
                                                 "token": stale_token})
            if status != 409 or body.get("error") != "hold_released":
                raise AssertionError(("stale basis must be rejected", status, body))
            status, body = reopened.call("/desk/switch", {"run": run, "request": "A-off-again",
                                                          "expected_issue": only_a["issue"],
                                                          "issue": neither["issue"]})
            if status != 409 or body.get("error") != "cue_last_copy":
                raise AssertionError(("A must not delete X again", status, body))
            currents = {"A": reopened.ok("/desk/current", run=run), "B": b.ok("/desk/current", run=run)}
            if not _has_x(currents["A"]["sheet"]) or _has_x(currents["B"]["sheet"]):
                raise AssertionError(("final coverage wrong", currents))
            return {"reclaim": {"issue": first["issue"], "handout_id": first["handout_id"],
                                "request": first["request"]},
                    "stale_basis_rejected": "hold_released",
                    "currents": {desk: {"issue": currents[desk]["issue"],
                                        "has_x": _has_x(currents[desk]["sheet"])} for desk in ("A", "B")}}


def _drill_offline(directory):
    run = "r3-offline"
    pub_ctx = running_desk(Path(directory, "off-pub.sqlite3"))
    peer_ctx = running_desk(Path(directory, "off-b.sqlite3"))
    desk_ctx = running_desk(Path(directory, "off-a.sqlite3"))
    pub = pub_ctx.__enter__()
    try:
        with peer_ctx as b, desk_ctx as a:
            issue_args, plain, marks, versions = _seed_cue_versions(pub)
            both, neither = versions["both"], versions["neither"]
            sheet = lambda desk: _open_sheet(run, desk, issue_args["draft"], pub.base, a.base, b.base,
                                             neither["issue"], both["issue"])
            a.ok("/desk/open", sheet("A"))
            b.ok("/desk/open", sheet("B"))
            a.ok("/desk/stage", {"run": run, "issue": both["issue"]})
            a_port = a.port
    finally:
        pub_ctx.__exit__(None, None, None)
    # 谱务与对台都停机后，只增加提示的换谱仍可办理，当前版面仍读得出
    with running_desk(Path(directory, "off-a.sqlite3"), a_port) as reopened:
        switched = reopened.ok("/desk/switch", {"run": run, "request": "A-add",
                                                "expected_issue": neither["issue"], "issue": both["issue"]})
        if not _has_x(switched["sheet"]):
            raise AssertionError(("add-only switch must work offline", switched))
        current = reopened.ok("/desk/current", run=run)
        if not _has_x(current["sheet"]):
            raise AssertionError(("current must be readable offline", current))
        return {"switched_offline": {"issue": switched["issue"], "handout_id": switched["handout_id"]},
                "current": {"issue": current["issue"], "has_x": _has_x(current["sheet"])}}


def joint_drill():
    with TemporaryDirectory(prefix="bridge-desks-") as directory:
        with running_desk(Path(directory, "publisher.sqlite3")) as pub:
            issue_args, plain, marks, versions = _seed_cue_versions(pub)
            summary = {"issuance": _check_issuance(pub, issue_args, plain, marks, versions)}
            with running_desk(Path(directory, "a1.sqlite3")) as a, running_desk(Path(directory, "b1.sqlite3")) as b:
                summary["ordered"] = _drill_ordered(pub, a, b, issue_args, versions)
                summary["race"] = _drill_race(pub, a, b, issue_args, versions)
            summary["restart"] = _drill_restart(pub, directory, issue_args, versions)
        summary["offline"] = _drill_offline(Path(directory))
        return summary


def desk_demo():
    """演示先换、拒换和失答复领，输出实际当前谱及重开的原号首执。"""
    with TemporaryDirectory(prefix="bridge-desks-") as directory:
        pub_db = Path(directory, "publisher.sqlite3")
        a_db = Path(directory, "a.sqlite3")
        with running_desk(pub_db) as pub, running_desk(Path(directory, "b.sqlite3")) as b:
            issue_args, plain, marks, versions = _seed_cue_versions(pub)
            both, neither = versions["both"], versions["neither"]
            # 先换与拒换：两台都实领 X 后，A 先换下 X 办成，B 再删同一提示被退回
            with running_desk(a_db) as a:
                sheet = lambda desk: _open_sheet("evening", desk, issue_args["draft"], pub.base, a.base,
                                                 b.base, both["issue"], both["issue"])
                a.ok("/desk/open", sheet("A"))
                b.ok("/desk/open", sheet("B"))
                for client in (a, b):
                    client.ok("/desk/stage", {"run": "evening", "issue": neither["issue"]})
                first_switch = a.ok("/desk/switch", {"run": "evening", "request": "A-off",
                                                     "expected_issue": both["issue"], "issue": neither["issue"]})
                status, rejected = b.call("/desk/switch", {"run": "evening", "request": "B-off",
                                                           "expected_issue": both["issue"], "issue": neither["issue"]})
                if status != 409:
                    raise AssertionError(("the second delete must be rejected", status, rejected))
                evening = {"A": a.ok("/desk/current", run="evening"),
                           "B": b.ok("/desk/current", run="evening")}
                a_port = a.port
            # 失答复领：A 删 X 的回包被扣留，重开后以原请求复领首执
            with running_desk(a_db, a_port, drop_path="/desk/switch") as a:
                reclaim_body = {"run": "reclaim", "request": "A-off-lost", "expected_issue": both["issue"],
                                "issue": neither["issue"]}
                sheet = lambda desk: _open_sheet("reclaim", desk, issue_args["draft"], pub.base, a.base,
                                                 b.base, both["issue"], both["issue"])
                a.ok("/desk/open", sheet("A"))
                b.ok("/desk/open", sheet("B"))
                a.ok("/desk/stage", {"run": "reclaim", "issue": neither["issue"]})
                try:
                    a.call("/desk/switch", reclaim_body)
                except RemoteDisconnected:
                    pass
                else:
                    raise AssertionError("the switch reply was not dropped")
            with running_desk(a_db, a_port) as reopened:
                first_execution = reopened.ok("/desk/switch", reclaim_body)
                reclaim_current = reopened.ok("/desk/current", run="reclaim")
            return {"first_switch": first_switch, "rejected_switch": {"status": status, "body": rejected},
                    "currents": {run: {desk: {"issue": evening[desk]["issue"],
                                              "handout_id": evening[desk]["handout_id"],
                                              "has_x": _has_x(evening[desk]["sheet"])}
                                       for desk in evening} for run, evening in (("evening", evening),)},
                    "reclaim": {"first_execution": first_execution, "current": reclaim_current}}


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
            print(json.dumps(joint_drill(), ensure_ascii=False))
            print(json.dumps(demo(), ensure_ascii=False))
        else:
            print(json.dumps({"ordinary_handout": demo(), "desk_switching": desk_demo()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
