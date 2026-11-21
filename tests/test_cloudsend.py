"""Sending to the cloud: validated archives only, no duplicates, no blind
replacement, and the outcome read back rather than trusted."""
import contextlib
import copy
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from remarksync import cloud
from remarksync.mdcompile import build_rmdoc

FOLDER = "aaaaaaaa-1111-4222-8333-444444444444"


def entry_for(identifier, files: dict[str, bytes], name, parent="", kind="DocumentType"):
    return {"DocumentID": identifier, "Hash": hashlib.sha256(b"".join(sorted(files.values()))).hexdigest(),
            "Subfiles": len(files), "Metadata": {"type": kind, "visibleName": name, "parent": parent},
            "Content": {}, "Files": [{"DocumentID": n, "Hash": hashlib.sha256(d).hexdigest(), "Size": len(d)}
                                     for n, d in files.items()]}


def unzip(path) -> tuple[str, dict[str, bytes]]:
    with zipfile.ZipFile(path) as bundle:
        files = {n: bundle.read(n) for n in bundle.namelist()}
    return next(n for n in files if n.endswith(".content"))[:-len(".content")], files


class FakeCloud(cloud.CloudClient):
    """Stands in for the pinned client: ``put`` names the document after the
    file, rewrites its metadata, refuses a taken name, and with --force deletes
    the same-named document before uploading."""

    def __init__(self, tmp_path: Path):
        self.work = tmp_path / "session"
        self.work.mkdir()
        self.documents = {FOLDER: entry_for(FOLDER, {FOLDER + ".metadata": b"{}"}, "Projects", kind="CollectionType")}
        self.trash: set[str] = set()
        self.puts: list[tuple[str, str, bool]] = []
        self.fail_upload = self.silent = self.blind = self.names_by_path = False

    @contextlib.contextmanager
    def sender(self):
        work = self.work / str(len(list(self.work.iterdir())))  # a real session is a fresh directory
        work.mkdir()

        def put(filename, path, force):
            assert Path(filename).name == filename  # a bare name: the client would take a path as the name
            archive = work / filename
            self.puts.append((filename, path, force))
            name = str(archive)[:-len(".rmdoc")] if self.names_by_path else archive.stem
            parent = FOLDER if path == "/Projects" else ""
            assert path in ("/", "/Projects")
            twins = [k for k, e in self.documents.items()
                     if e["Metadata"]["parent"] == parent and e["Metadata"]["visibleName"] == name]
            if twins and not force:
                return False
            for twin in twins:
                del self.documents[twin]
            if self.fail_upload:
                return False
            if self.silent:
                return True
            identifier, files = unzip(archive)
            files[identifier + ".metadata"] = json.dumps({"visibleName": name, "parent": parent}).encode()
            self.documents[identifier] = entry_for(identifier, files, name, parent)
            return True

        def relist():
            if self.blind:
                raise cloud.CloudError("listing failed")
            return copy.deepcopy(self.documents), set(self.documents) | self.trash

        yield copy.deepcopy(self.documents), set(self.documents) | self.trash, "token", work, put, relist

    def download(self, entry, token, destination):
        Path(destination).write_bytes(b"previous revision " + entry["Hash"].encode())


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / "note.rmdoc"
    path.write_bytes(build_rmdoc("Note", [[("PLAIN", "hello")]]))
    return path


def hashes(path) -> dict[str, str]:
    return {n: hashlib.sha256(d).hexdigest() for n, d in unzip(path)[1].items()}


def seed(client, path, name="Note", parent=""):
    identifier, files = unzip(path)
    client.documents[identifier] = entry_for(identifier, files, name, parent)
    return identifier


def test_new_document_is_uploaded_once_and_a_retry_is_recognised(tmp_path, archive):
    client = FakeCloud(tmp_path)
    result = cloud.send(client, archive, "Meeting notes", folder=FOLDER)
    assert result["status"] == "sent" and not result["replaced"]
    assert client.puts == [("Meeting notes.rmdoc", "/Projects", False)]
    assert client.documents[result["document"]]["Metadata"]["visibleName"] == "Meeting notes"
    again = cloud.send(client, archive, "Meeting notes", folder=FOLDER)
    assert again["status"] == "already-sent" and len(client.puts) == 1


def test_taken_name_or_reused_identity_is_refused_before_any_upload(tmp_path, archive):
    client = FakeCloud(tmp_path)
    other = tmp_path / "other.rmdoc"
    other.write_bytes(build_rmdoc("Other", [[("PLAIN", "different")]]))
    seed(client, other, name="note")  # same name, different case
    with pytest.raises(cloud.CloudError, match="already exists"):
        cloud.send(client, archive, "Note")
    client.trash.add(unzip(archive)[0])  # this identity sits in the cloud's trash
    with pytest.raises(cloud.CloudError, match="identity"):
        cloud.send(client, archive, "Fresh name")
    assert client.puts == []


@pytest.mark.parametrize("name", ["", "a/b", "trailing.", " padded", "CON", 'quo"te', "x" * 181])
def test_names_the_client_cannot_carry_are_refused(tmp_path, archive, name):
    with pytest.raises(cloud.CloudError, match="name"):
        cloud.send(FakeCloud(tmp_path), archive, name)


def test_exit_status_is_not_trusted(tmp_path, archive):
    client = FakeCloud(tmp_path)
    client.silent = True  # the client reports success without writing anything
    with pytest.raises(cloud.CloudError, match="not in the cloud"):
        cloud.send(client, archive, "Note")
    client.silent, client.blind = False, True
    with pytest.raises(cloud.CloudError, match="Send again"):
        cloud.send(client, archive, "Note")
    client.blind = False
    assert cloud.send(client, archive, "Note")["status"] == "already-sent"  # it had arrived


def test_a_document_that_arrives_under_another_name_is_reported(tmp_path, archive):
    client = FakeCloud(tmp_path)
    client.names_by_path = True  # what the real client did with a full Windows path
    with pytest.raises(cloud.CloudError, match="is named"):
        cloud.send(client, archive, "Note")


def edited(tmp_path, original):
    identifier, files = unzip(original)
    page = next(n for n in files if n.endswith(".rm"))
    files[page] = files[page] + b"\x00"  # any change to a page file
    path = tmp_path / "edited.rmdoc"
    with zipfile.ZipFile(path, "w") as bundle:
        for n, d in files.items():
            bundle.writestr(n, d)
    return path


def test_replace_keeps_the_previous_revision_and_swaps_the_document(tmp_path, archive):
    client = FakeCloud(tmp_path)
    identifier = seed(client, archive, parent=FOLDER)
    before = client.documents[identifier]["Hash"]
    result = cloud.send(client, edited(tmp_path, archive), "Note", replace=hashes(archive), keep=tmp_path / "kept")
    assert result["status"] == "sent" and result["replaced"] and result["document"] == identifier
    assert client.puts == [("Note.rmdoc", "/Projects", True)]
    assert Path(result["kept"]) == (tmp_path / "kept" / identifier / (before + ".rmdoc")).resolve()
    assert Path(result["kept"]).read_bytes().endswith(before.encode())
    assert client.documents[identifier]["Hash"] != before and len(client.documents) == 2


def test_replace_refuses_when_the_tablet_changed_the_document(tmp_path, archive):
    client = FakeCloud(tmp_path)
    identifier = seed(client, archive)
    base = hashes(archive)
    client.documents[identifier]["Files"][0]["Hash"] = "f" * 64  # the tablet wrote since the sync
    with pytest.raises(cloud.CloudError, match="changed on the tablet"):
        cloud.send(client, edited(tmp_path, archive), "Note", replace=base, keep=tmp_path / "kept")
    assert client.puts == [] and not (tmp_path / "kept").exists()


def test_replace_refuses_anything_that_could_hit_the_wrong_document(tmp_path, archive):
    client = FakeCloud(tmp_path)
    change = edited(tmp_path, archive)
    with pytest.raises(cloud.CloudError, match="nothing to replace"):
        cloud.send(client, change, "Note", replace=hashes(archive), keep=tmp_path / "kept")
    seed(client, archive)
    with pytest.raises(cloud.CloudError, match="current name"):
        cloud.send(client, change, "Renamed", replace=hashes(archive), keep=tmp_path / "kept")
    twin = tmp_path / "twin.rmdoc"
    twin.write_bytes(build_rmdoc("Twin", [[("PLAIN", "x")]]))
    seed(client, twin, name="NOTE")
    with pytest.raises(cloud.CloudError, match="ambiguous"):
        cloud.send(client, change, "Note", replace=hashes(archive), keep=tmp_path / "kept")
    with pytest.raises(cloud.CloudError, match="somewhere to keep"):
        client.documents.pop(unzip(twin)[0])
        cloud.send(client, change, "Note", replace=hashes(archive))
    assert client.puts == []


def test_failed_replace_says_where_the_previous_revision_is(tmp_path, archive):
    client = FakeCloud(tmp_path)
    identifier = seed(client, archive)
    client.fail_upload = True  # the client deleted the original, then the upload failed
    with pytest.raises(cloud.CloudError, match="previous revision is kept at") as failure:
        cloud.send(client, edited(tmp_path, archive), "Note", replace=hashes(archive), keep=tmp_path / "kept")
    assert identifier not in client.documents and str((tmp_path / "kept").resolve()) in str(failure.value)
    assert next((tmp_path / "kept" / identifier).glob("*.rmdoc")).is_file()


def test_ambiguous_folder_path_is_refused(tmp_path, archive):
    client = FakeCloud(tmp_path)
    twin = "bbbbbbbb-1111-4222-8333-444444444444"
    client.documents[twin] = entry_for(twin, {twin + ".metadata": b"{}"}, "projects", kind="CollectionType")
    with pytest.raises(cloud.CloudError, match="unambiguously"):
        cloud.send(client, archive, "Note", folder=FOLDER)
    with pytest.raises(cloud.CloudError, match="not in the cloud library"):
        cloud.send(client, archive, "Note", folder="cccccccc-1111-4222-8333-444444444444")


def bundle(tmp_path, files: dict[str, bytes]) -> Path:
    path = tmp_path / "bad.rmdoc"
    with zipfile.ZipFile(path, "w") as out:
        for n, d in files.items():
            out.writestr(n, d)
    return path


def test_archives_that_are_not_one_plain_document_are_refused(tmp_path, archive):
    identifier, files = unzip(archive)
    other = "dddddddd-1111-4222-8333-444444444444"
    bad = [
        {**files, other + ".content": b"{}", other + ".metadata": b"{}"},  # two documents
        {**files, other + "/stray.rm": b"x"},  # a file of another document
        {**files, identifier + "/../escape.rm": b"x"},
        {**files, identifier + "/sp ace.rm": b"x"},
        {n: d for n, d in files.items() if not n.endswith(".metadata")},
        {**files, identifier + ".content": b"[]"},
        {"notes.content": b"{}", "notes.metadata": b"{}"},  # identity is not a UUID
    ]
    for case in bad:
        with pytest.raises(cloud.CloudError):
            cloud.archive_files(bundle(tmp_path, case))
    (tmp_path / "junk.rmdoc").write_bytes(b"not a zip")
    with pytest.raises(cloud.CloudError, match="could not be read"):
        cloud.archive_files(tmp_path / "junk.rmdoc")
    assert cloud.archive_files(archive) == (identifier, hashes(archive))


def test_put_runs_only_the_fixed_upload_command(tmp_path, monkeypatch, archive):
    client = cloud.CloudClient(executable=tmp_path / "rmapi.exe", profile=tmp_path / "profile")
    calls = []

    @contextlib.contextmanager
    def session(code=None):
        yield {}, "token", tmp_path, set()

    class Done:
        returncode = 0

    monkeypatch.setattr(client, "_session", session)
    monkeypatch.setattr(cloud.subprocess, "run", lambda command, **kw: calls.append((command, kw)) or Done())
    with client.sender() as (_documents, _identifiers, _token, _work, put, _relist):
        assert put("note.rmdoc", "/Projects", True) and put("note.rmdoc", "/", False)
        for path in (str(archive), "sub/note.rmdoc", "missing.rmdoc"):
            with pytest.raises(cloud.CloudError, match="session directory"):
                put(path, "/", False)
    assert [c[0][1:] for c in calls] == [["-ni", "put", "--force", "note.rmdoc", "/Projects"], ["-ni", "put", "note.rmdoc", "/"]]
    assert all(kw["env"]["RMAPI_TRACE"] == "0" and kw["capture_output"] and kw["cwd"] == tmp_path for _c, kw in calls)
