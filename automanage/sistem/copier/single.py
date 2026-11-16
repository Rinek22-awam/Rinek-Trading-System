"""One copy at a time: each program holds a local port while it runs.

Two bots would read the same Telegram updates and could execute a setup twice; two
readers would fight over the Telegram login file. The port is freed by Windows the
moment the program exits, even after a crash, so there is no stale lock to clean up.
"""
import errno
import socket

BOT_PORT = 47651
READER_PORT = 47652
BYBIT_BOT_PORT = 47653
DASHBOARD_PORT = 47654
SUPERVISOR_PORT = 47655


def _bind(s: socket.socket, port: int):
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind(("127.0.0.1", port))


def claim(port: int):
    """Hold the port for this program's lifetime. None if another copy already has it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _bind(s, port)
        s.listen(1)
    except OSError:
        s.close()
        return None
    return s


def is_running(port: int) -> bool:
    """Probe the lock without filling the listener's unaccepted connection queue."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            _bind(probe, port)
        except OSError as exc:
            if exc.errno not in (errno.EADDRINUSE, errno.EACCES) and getattr(exc, "winerror", None) not in (10048, 10013):
                raise
            return True
    return False
