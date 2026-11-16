"""Workspace-local stop requests, scoped to one run of a bot."""
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import subprocess
import sys
import uuid

from .single import BOT_PORT, BYBIT_BOT_PORT, READER_PORT

WORKER_PROGRAMS = (
    ("Reader", READER_PORT, "pembaca.log", ("reader.main", "--non-interaktif")),
    ("Eksekutor MT5", BOT_PORT, "bot.log", ("copier.main",)),
    ("Eksekutor Bybit", BYBIT_BOT_PORT, "bot-bybit.log", ("copier.main_bybit",)),
)


def _session_file(port):
    return Path("data") / f"process-{port}.session"


def _stop_file(port, session):
    return _session_file(port).with_name(f"process-{port}-{session}.stop")


def running_session(port):
    try:
        value = _session_file(port).read_text(encoding="ascii").strip()
        return value if uuid.UUID(hex=value).hex == value else None
    except (OSError, ValueError):
        return None


def request_stop(port, session):
    _stop_file(port, session).touch()


def launch_background(command, *, environment=None):
    return subprocess.Popen(
        [sys.executable, "-m", *command],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        start_new_session=os.name != "nt",
    )


def _ready_file(port):
    return _session_file(port).with_suffix(".ready")


def is_ready(port, session):
    try:
        return bool(session) and _ready_file(port).read_text(encoding="ascii") == session
    except (OSError, ValueError):
        return False


@contextmanager
def managed(port, log):
    """Publish a session while its caller holds the existing single-instance lock."""
    path = _session_file(port)
    path.parent.mkdir(exist_ok=True)
    previous = running_session(port)
    if previous:
        _stop_file(port, previous).unlink(missing_ok=True)
    session = uuid.uuid4().hex
    stop = _stop_file(port, session)
    path.write_text(session, encoding="ascii")
    try:
        yield stop.exists
    except SystemExit as exc:
        if isinstance(exc.code, str):
            log.error("%s", exc.code)
        raise
    except Exception:
        log.exception("Proses berhenti karena error")
        raise
    finally:
        _ready_file(port).unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        stop.unlink(missing_ok=True)


def ready(log, message, *, port=None):
    if port is not None and (session := running_session(port)):
        _ready_file(port).write_text(session, encoding="ascii")
    log.info(message, extra={"process_ready": True})


class ProcessLogFormatter(logging.Formatter):
    def format(self, record):
        line = super().format(record)
        if getattr(record, "process_ready", False):
            return line.replace(" INFO ", " AKTIF ", 1)
        return line


def log_file(path):
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(ProcessLogFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    return handler
