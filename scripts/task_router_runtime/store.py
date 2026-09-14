"""Private, durable task records. No provider credentials are stored here."""

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class StateError(RuntimeError):
    pass


def process_identity(pid):
    """Linux start time distinguishes a recorded process from a reused PID."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        start = stat[stat.rfind(")") + 2:].split()[19]
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return f"{boot}:{start}"
    except (OSError, IndexError):
        return None


def process_present(pid, identity=None):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    observed = process_identity(pid)
    return not (identity and observed and identity != observed)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().absolute()
        if self.directory.is_symlink():
            raise StateError("state directory must not be a symlink")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.stat().st_mode & 0o077:
            raise StateError("state directory must be private (mode 0700)")
        self.directory = self.directory.resolve()
        self.path = self.directory / "tasks.sqlite3"
        if self.path.is_symlink():
            raise StateError("state database must not be a symlink")
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise StateError("unsupported task database version")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE, fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                updated REAL NOT NULL, deadline REAL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                result TEXT, message TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS attempts (
                task_id TEXT NOT NULL REFERENCES tasks(id), number INTEGER NOT NULL,
                model TEXT NOT NULL, effort TEXT NOT NULL, status TEXT NOT NULL,
                started REAL NOT NULL, finished REAL, pid INTEGER, pgid INTEGER,
                identity TEXT, thread_id TEXT, turn_id TEXT, before_files TEXT,
                after_files TEXT, outcome TEXT, PRIMARY KEY (task_id, number)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                number INTEGER, created REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL
            );
            PRAGMA user_version=1;
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def execution_lock(self):
        import fcntl
        descriptor = os.open(self.directory / "execution.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StateError("another controller is executing a task in this state directory") from exc
            yield
        finally:
            os.close(descriptor)

    def submit(self, payload, request_key=None):
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        task_id = str(uuid.uuid4())
        now = time.time()
        try:
            with self.db:
                self.db.execute("INSERT INTO tasks(id,request_key,fingerprint,payload,status,created,updated) VALUES(?,?,?,?,?,?,?)",
                                (task_id, request_key, fingerprint, encoded, "queued", now, now))
        except sqlite3.IntegrityError:
            row = self.db.execute("SELECT id,fingerprint FROM tasks WHERE request_key=?", (request_key,)).fetchone()
            if row is None or row["fingerprint"] != fingerprint:
                raise StateError("request key already belongs to different task inputs")
            return row["id"], False
        return task_id, True

    def task(self, task_id):
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise StateError("task not found")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def attempts(self, task_id):
        result = []
        for row in self.db.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY number", (task_id,)):
            item = dict(row)
            for key in ("before_files", "after_files", "outcome"):
                item[key] = json.loads(item[key]) if item[key] else None
            result.append(item)
        return result

    def list_tasks(self):
        return [dict(row) for row in self.db.execute(
            "SELECT id,status,created,updated,message FROM tasks ORDER BY created DESC LIMIT 100")]

    def set_status(self, task_id, status, message="", result=None):
        with self.db:
            self.db.execute("UPDATE tasks SET status=?,message=?,result=?,updated=? WHERE id=?",
                            (status, message, json.dumps(result) if result is not None else None, time.time(), task_id))

    def start_attempt(self, task_id, model, effort, before_files):
        with self.db:
            task = self.task(task_id)
            number = len(self.attempts(task_id)) + 1
            if number > task["payload"]["policy"]["max_attempts"]:
                raise StateError("attempt budget exhausted")
            now = time.time()
            deadline = task["deadline"] or now + task["payload"]["timeout"]
            if now >= deadline:
                raise StateError("task deadline exhausted")
            self.db.execute("INSERT INTO attempts(task_id,number,model,effort,status,started,before_files) VALUES(?,?,?,?,?,?,?)",
                            (task_id, number, model, effort, "starting", now, json.dumps(before_files)))
            self.db.execute("UPDATE tasks SET status='running',updated=?,deadline=?,message='' WHERE id=?",
                            (now, deadline, task_id))
        return number, deadline

    def event(self, task_id, number, event):
        kind = event.get("kind", "observation")
        allowed = {key: event[key] for key in ("kind", "pid", "pgid", "thread_id", "turn_id", "type", "status") if key in event}
        with self.db:
            if kind == "process":
                self.db.execute("UPDATE attempts SET pid=?,pgid=?,identity=?,status='running' WHERE task_id=? AND number=?",
                                (event["pid"], event.get("pgid"), process_identity(event["pid"]), task_id, number))
            elif kind == "thread":
                self.db.execute("UPDATE attempts SET thread_id=? WHERE task_id=? AND number=?", (event["thread_id"], task_id, number))
            elif kind == "turn":
                self.db.execute("UPDATE attempts SET turn_id=? WHERE task_id=? AND number=?", (event["turn_id"], task_id, number))
            count = self.db.execute("SELECT count(*) FROM events WHERE task_id=?", (task_id,)).fetchone()[0]
            if count < 1000:
                self.db.execute("INSERT INTO events(task_id,number,created,kind,data) VALUES(?,?,?,?,?)",
                                (task_id, number, time.time(), str(kind)[:100], json.dumps(allowed)))

    def finish_attempt(self, task_id, number, outcome, after_files):
        if not outcome.get("quiescent"):
            status = "unknown"
        elif outcome["status"] == "failed" and outcome.get("retryable"):
            status = "retrying"
        else:
            status = outcome["status"]
        with self.db:
            self.db.execute("UPDATE attempts SET status=?,finished=?,after_files=?,outcome=? WHERE task_id=? AND number=?",
                            (outcome["status"], time.time(), json.dumps(after_files), json.dumps(outcome), task_id, number))
            self.db.execute("UPDATE tasks SET status=?,updated=?,message=?,result=? WHERE id=?",
                            (status, time.time(), outcome.get("message", ""), json.dumps(outcome), task_id))

    def cancel(self, task_id):
        with self.db:
            task = self.task(task_id)
            if task["status"] in {"succeeded", "failed", "cancelled"}:
                return
            status = "cancelled" if task["status"] in {"queued", "retrying"} else task["status"]
            self.db.execute("UPDATE tasks SET cancel_requested=1,status=?,updated=? WHERE id=?", (status, time.time(), task_id))

    def blocks_workspace(self, cwd, except_task):
        cwd = Path(cwd)
        for row in self.db.execute("SELECT id,payload FROM tasks WHERE status IN ('running','unknown','retrying') AND id!=?", (except_task,)):
            other = Path(json.loads(row["payload"])["cwd"])
            if cwd == other or cwd in other.parents or other in cwd.parents:
                return row["id"]
        return None
