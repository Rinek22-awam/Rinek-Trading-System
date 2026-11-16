"""Read complete UTF-8 log lines as they are appended."""
import os

LOG_READ_SIZE = 64 * 1024


class LogTail:
    def __init__(self, path):
        self.path = path
        self.skip_existing()

    def skip_existing(self):
        self.identity = None
        self.offset = 0
        self.partial = b""
        self.skip_line = False
        self.skip_pending = False
        try:
            with self.path.open("rb") as file:
                stat = os.fstat(file.fileno())
                self.identity = stat.st_dev, stat.st_ino
                self.offset = stat.st_size
                if self.offset:
                    file.seek(self.offset - 1)
                    self.skip_line = file.read(1) != b"\n"
        except FileNotFoundError:
            pass
        except OSError:
            self.skip_pending = True

    def read(self):
        if self.skip_pending:
            self.skip_existing()
            return []
        try:
            with self.path.open("rb") as file:
                stat = os.fstat(file.fileno())
                identity = stat.st_dev, stat.st_ino
                if identity != self.identity or stat.st_size < self.offset:
                    self.identity = identity
                    self.offset = max(0, stat.st_size - LOG_READ_SIZE)
                    self.skip_line = self.offset > 0
                    self.partial = b""
                file.seek(self.offset)
                chunk = file.read(LOG_READ_SIZE)
                self.offset = file.tell()
        except OSError:
            return []
        if self.skip_line:
            _, separator, chunk = chunk.partition(b"\n")
            self.skip_line = not separator
        lines = (self.partial + chunk).split(b"\n")
        self.partial = lines.pop()[-LOG_READ_SIZE:]
        return [line.rstrip(b"\r").decode("utf-8", errors="replace") for line in lines]
