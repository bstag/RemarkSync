"""Compare a cloud trial with a read-only USB backup; save evidence alongside it."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import fitz


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    trial = json.loads(args.report.read_text(encoding="utf-8"))
    root, identifier = args.report.parent, trial["document"]
    differences, equal, absent = [], [], []
    with zipfile.ZipFile(root / "document.rmdoc") as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
        for name, data in files.items():
            path = args.backup / name
            if not path.is_file():
                absent.append(name)
            elif digest(path.read_bytes()) == digest(data):
                equal.append(name)
            else:
                differences.append(name)
    original_bytes = files[identifier + ".pdf"]
    output_bytes = (root / "annotated.pdf").read_bytes()
    assert digest((root / "document.rmdoc").read_bytes()) == trial["checksums"]["document.rmdoc"]
    assert digest(output_bytes) == trial["checksums"]["annotated.pdf"]
    with fitz.open(stream=output_bytes, filetype="pdf") as pdf, fitz.open(stream=original_bytes, filetype="pdf") as original:
        manifest = json.loads(pdf.embfile_get("remarksync-manifest.json"))
        assert pdf.embfile_get("original.pdf") == original_bytes
        scenes = [name for name in pdf.embfile_names() if name.endswith(".rm")]
        cloud_scenes = {digest(data) for name, data in files.items() if name.endswith(".rm")}
        assert all(digest(pdf.embfile_get(name)) in cloud_scenes for name in scenes)
        for index, page in enumerate(original):
            # Original words and their boxes precede any appended overlay text.
            words = page.get_text("words")
            assert pdf[index].get_text("words")[:len(words)] == words
        evidence = {"document": identifier, "revision": trial["revision"],
                    "cloud_files": len(files), "usb_identical_files": len(equal),
                    "usb_different_files": differences, "absent_from_usb": absent,
                    "original_pdf_identical_to_usb": identifier + ".pdf" in equal,
                    "raw_scene_files": sum(name.endswith(".rm") for name in files),
                    "embedded_active_scenes": len(scenes), "preserved_highlights": sum(len(p["highlights"]) for p in manifest["pages"]),
                    "output_pages": len(pdf), "original_words_and_boxes_retained": True,
                    "original_attachment_exact": True, "embedded_scenes_verified": True,
                    "warnings": trial["warnings"]}
    target = root / "verification.json"
    target.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
