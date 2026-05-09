"""
GigsFill In-Memory Log Ring Buffer
====================================
Captures all Python logging output into a fixed-size deque so the admin
panel can read recent log lines without needing log files on disk.

Usage
-----
Import and call ``install()`` once at application startup (main.py):

    from backend.log_buffer import install
    install()

The handler installs itself on the root logger and captures every record
from every named logger in the process.
"""

import logging
import threading
from collections import deque
from datetime import datetime, timezone

_BUFFER_SIZE = 2000          # max lines kept in memory
_LOCK = threading.Lock()

# Public ring-buffer accessed by the admin routes
class _LogBuffer:
    def __init__(self, maxlen: int):
        self._buf: deque[str] = deque(maxlen=maxlen)

    def append(self, line: str):
        with _LOCK:
            self._buf.append(line)

    def get_all(self):
        with _LOCK:
            return list(self._buf)

    def clear(self):
        with _LOCK:
            self._buf.clear()


log_buffer = _LogBuffer(_BUFFER_SIZE)


class _RingBufferHandler(logging.Handler):
    """Logging handler that writes formatted records into log_buffer."""

    # Colour-free level tag so the admin UI can filter by text
    LEVEL_TAG = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO",
        logging.WARNING:  "WARNING",
        logging.ERROR:    "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def emit(self, record: logging.LogRecord):
        try:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            lvl = self.LEVEL_TAG.get(record.levelno, record.levelname)
            line = f"{ts}  {lvl:<8}  {record.name}  —  {self.format(record)}"
            log_buffer.append(line)
        except Exception:
            pass   # never let logging itself crash the app


_installed = False

def install():
    """Attach the ring-buffer handler to the root logger (idempotent)."""
    global _installed
    if _installed:
        return
    _installed = True

    handler = _RingBufferHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.addHandler(handler)

    # Make sure the root logger passes DEBUG+ so we see everything
    if root.level == logging.NOTSET or root.level > logging.DEBUG:
        root.setLevel(logging.DEBUG)
