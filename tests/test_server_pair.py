import importlib.util
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QProcess
from PySide6.QtWidgets import QApplication, QLineEdit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/pair-server.py"


def load(monkeypatch, **attributes):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    spec = importlib.util.spec_from_file_location("pair_server", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, value in attributes.items():
        monkeypatch.setattr(module, name, value)
    return module


def test_server_pairing_is_masked_stdin_and_never_in_ssh_arguments(monkeypatch):
    application = QApplication.instance() or QApplication([])
    module = load(monkeypatch, HOST="deploy@server.example", RELEASE="/srv/remarksync/releases/test",
                  KEY="/keys/deploy")
    dialog = module.PairServer()

    class FakeProcess:
        def start(self, program, arguments):
            self.program, self.arguments = program, arguments
        def write(self, payload):
            self.payload = payload
        def closeWriteChannel(self):
            self.closed = True

    process = FakeProcess()
    dialog.process = process
    assert dialog.code.echoMode() == QLineEdit.EchoMode.Password
    dialog.code.setText("ABCD1234")
    dialog.pair()
    assert process.program == "ssh" and "ABCD1234" not in " ".join(process.arguments)
    assert process.arguments[-2] == "deploy@server.example" and "docker compose" in process.arguments[-1]
    assert "/srv/remarksync/releases/test" in process.arguments[-1] and "/keys/deploy" in process.arguments
    assert json.loads(process.payload) == {"operation": "pair", "code": "ABCD1234"}
    assert process.closed and dialog.code.text() == ""
    assert not dialog.button.isEnabled()
    assert application is not None


@pytest.mark.parametrize("host, release", [("", "/srv/release"), ("deploy@server.example", "")])
def test_pairing_refuses_without_this_installation_s_host_and_release(monkeypatch, host, release):
    """No deployment is built into the script, so an unconfigured run must not start SSH."""
    application = QApplication.instance() or QApplication([])
    module = load(monkeypatch, HOST=host, RELEASE=release)
    dialog = module.PairServer()
    started = []
    monkeypatch.setattr(dialog.process, "start", lambda *args: started.append(args))
    dialog.code.setText("ABCD1234")
    dialog.pair()
    assert started == [] and "REMARKSYNC_SSH_HOST" in dialog.status.text()
    assert dialog.code.text() == "ABCD1234" and dialog.button.isEnabled()
    assert application is not None


@pytest.mark.parametrize("exit_code, response, paired, message", [
    (0, b'{"documents": []}', True, "The server is paired"),
    (1, b'{"error": "Pairing code expired."}', False, "Pairing code expired."),
    (255, b'', False, "Server pairing failed"),
])
def test_pairing_process_completion_updates_window_and_result(monkeypatch, tmp_path, exit_code, response, paired, message):
    application = QApplication.instance() or QApplication([])
    module = load(monkeypatch, ROOT=tmp_path)
    dialog = module.PairServer()
    dialog.status.setText("Pairing the server; this can take a minute…")
    monkeypatch.setattr(dialog.process, "readAllStandardOutput", lambda: QByteArray(response))
    dialog.process.finished.emit(exit_code, QProcess.ExitStatus.NormalExit)
    assert message in dialog.status.text()
    assert json.loads((tmp_path / "tmp/deploy/server-pair-result.json").read_text()) == {"paired": paired}
    assert dialog.button.isEnabled() is not paired
    assert dialog.code.isEnabled() is not paired
    assert application is not None
