"""The desktop log: somewhere for errors to go when there is no console."""
import logging

from remarksync import applog


def test_warnings_reach_the_log_file_and_uncaught_errors_are_reported(tmp_path, monkeypatch):
    import sys

    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    try:
        path = applog.configure("test", folder=tmp_path / "logs")
        logging.getLogger("remarksync.test").warning("page 3 could not be decoded")
        logging.getLogger("remarksync.test").info("not worth keeping")
        reported = []
        monkeypatch.setattr(sys, "excepthook", lambda *a: None)
        applog.log_uncaught(reported.append)
        try:
            raise ValueError("broken slot")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        for handler in root.handlers:
            handler.flush()
        text = path.read_text(encoding="utf-8")
        assert "page 3 could not be decoded" in text and "not worth keeping" not in text
        assert "uncaught error" in text and "ValueError: broken slot" in text
        assert reported == ["ValueError: broken slot"]
    finally:
        for handler in [h for h in root.handlers if h not in before]:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(level)


def test_worker_failures_are_logged_by_place_not_by_message():
    def secretive():
        raise RuntimeError("token=abc123 refused")

    try:
        secretive()
    except RuntimeError as exc:
        where = applog.where_it_failed(exc)
    assert where.startswith("RuntimeError at test_applog.py:") and "secretive" in where and "abc123" not in where
