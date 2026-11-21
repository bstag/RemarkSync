"""Run one of the package's worker modules as a child process.

Network, MuPDF export and cloud work stay out of the GUI process. Every
worker speaks the same protocol: one JSON request on stdin, JSON lines on
stdout. A line with an ``"event"`` key is progress; the last other object is
the result. This module is the one place that launches such a worker and
reads that protocol, so the window and the dialogs only say what to run and
what to do with the answer.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer


class Reply:
    """A worker's stdout, fed in whatever chunks the pipe delivers."""

    def __init__(self, on_event: Callable[[dict], None] | None = None):
        self._on_event = on_event
        self._buffer = b""
        self.result: dict | None = None

    def feed(self, data: bytes, *, last: bool = False) -> None:
        """Take more output. ``last`` says nothing follows, so a final line
        without a newline is read too."""
        self._buffer += data
        lines = self._buffer.split(b"\n")
        self._buffer = b"" if last else lines.pop()
        for line in lines:
            try:
                message = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeError):
                continue
            if not isinstance(message, dict):
                continue
            if "event" in message:
                if self._on_event is not None:
                    self._on_event(message)
            else:
                self.result = message


def start_json_worker(parent, module: str, request: dict, on_done: Callable[[QProcess, dict | None, int], None],
                      on_event: Callable[[dict], None] | None = None) -> QProcess:
    """Start ``python -m module``, send it ``request`` and return the process.

    ``on_done(process, result, exit_code)`` is called exactly once, from the
    event loop and never from inside this call: ``result`` is the worker's
    result object, or None when it produced none (it crashed, or never
    started; then ``exit_code`` is non-zero). The process deletes itself
    afterwards. ``on_event`` receives progress objects as they arrive.
    """
    process = QProcess(parent)
    environment = QProcessEnvironment.systemEnvironment()
    # Also works from an editable/source checkout, independent of cwd.
    environment.insert("PYTHONPATH", os.pathsep.join(str(path) for path in sys.path if path))
    process.setProcessEnvironment(environment)
    reply = Reply(on_event)
    reported = False

    def finish(code: int) -> None:
        nonlocal reported
        if reported:
            return
        reported = True
        reply.feed(bytes(process.readAllStandardOutput()), last=True)
        on_done(process, reply.result, code)
        process.deleteLater()

    def failed(error) -> None:
        if error == QProcess.FailedToStart:
            # start() can report this before it returns; the caller has not
            # stored the process yet, so answer from the event loop instead
            QTimer.singleShot(0, process, lambda: finish(1))

    process.readyReadStandardOutput.connect(lambda: reply.feed(bytes(process.readAllStandardOutput())))
    process.finished.connect(lambda code, _status: finish(code))
    process.errorOccurred.connect(failed)
    process.start(sys.executable, ["-m", module])
    process.write(json.dumps(request).encode("utf-8"))
    process.closeWriteChannel()
    return process
