import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from redactor.corpus import generate
from redactor.engine import redact_document, redact_text
from redactor.integrity import source_digest


HAS_PDF = importlib.util.find_spec("pypdfium2") is not None and importlib.util.find_spec("PIL") is not None


class CorpusTest(unittest.TestCase):
    def test_corpus_reproducible_and_synthetic(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            manifest = generate(first, 3)
            generate(second, 3)
            self.assertTrue(manifest["synthetic"])
            self.assertEqual(sorted(path.name for path in Path(first).iterdir()), sorted(path.name for path in Path(second).iterdir()))
            for path in Path(first).iterdir():
                self.assertEqual(path.read_bytes(), (Path(second) / path.name).read_bytes())
            for document in manifest["documents"]:
                text = "\n".join(document["safe_text"]) + "\n" + "\n".join(f"{label['kind'].title()}: {label['value']}" for label in document["truth"])
                self.assertEqual(redact_text(text), document["expected_text"])

    def test_source_identity_changes_on_code_and_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "redactor"
            package.mkdir()
            code = package / "a.py"
            code.write_text("a = 1\n")
            first = source_digest(root)
            code.write_text("a = 2\n")
            self.assertNotEqual(first, source_digest(root))
            second = source_digest(root)
            code.rename(package / "b.py")
            self.assertNotEqual(second, source_digest(root))

    def test_source_identity_covers_ui_cleanroom_and_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "redactor").mkdir()
            (root / "redactor" / "__init__.py").write_text("")
            (root / "cleanroom").mkdir()
            included = [
                root / "cleanroom" / "conductor.py",
                root / "cleanroom" / "ui.html",
                root / "clean-room-robot.yaml",
                root / "clean-room-environment.yaml",
            ]
            for path in included:
                path.write_text("original\n")
            for path in included:
                with self.subTest(path=path.name):
                    before = source_digest(root)
                    path.write_text("changed\n")
                    self.assertNotEqual(before, source_digest(root))
                    path.write_text("original\n")

    def test_source_identity_ignores_only_bytecode_and_other_top_level_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "redactor").mkdir()
            (root / "redactor" / "module.py").write_text("a = 1\n")
            before = source_digest(root)
            cache = root / "redactor" / "__pycache__"
            cache.mkdir()
            (cache / "module.pyc").write_bytes(b"cache")
            (root / "redactor" / "other.pyc").write_bytes(b"cache")
            (root / "tasks.py").write_text("baseline task\n")
            self.assertEqual(before, source_digest(root))

    def test_source_identity_rejects_symlink_files_and_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "redactor").mkdir()
            (root / "outside.txt").write_text("outside\n")
            (root / "outside-directory").mkdir()
            for name, target in [
                ("module.py", root / "outside.txt"),
                ("linked-directory", root / "outside-directory"),
                ("__pycache__", root / "outside-directory"),
                ("module.pyc", root / "outside.txt"),
            ]:
                link = root / "redactor" / name
                with self.subTest(name=name):
                    link.symlink_to(target, target_is_directory=target.is_dir())
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        source_digest(root)
                    link.unlink()
            (root / "cleanroom").symlink_to(root / "outside-directory", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                source_digest(root)
            (root / "cleanroom").unlink()
            (root / "clean-room-environment.yaml").symlink_to(root / "outside.txt")
            with self.assertRaisesRegex(ValueError, "symlink"):
                source_digest(root)

    @unittest.skipUnless(HAS_PDF, "requires pinned PDFium/Pillow RCC environment")
    def test_pdf_redaction_truth_pixels_and_determinism(self):
        import pypdfium2 as pdfium
        from PIL import Image, ImageChops, ImageDraw

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            corpus = root / "corpus"
            manifest = generate(corpus, 2)
            for document in manifest["documents"]:
                first = redact_document(corpus, root / "first", document["id"])
                second = redact_document(corpus, root / "second", document["id"])
                self.assertEqual(first, second)
                self.assertEqual(first["text"], document["expected_text"])
                for label in document["truth"]:
                    self.assertNotIn(label["value"], json.dumps(first))
                for safe in document["safe_text"]:
                    self.assertIn(safe, first["text"])
                with pdfium.PdfDocument(corpus / document["pdf"]) as source:
                    page = source[0]
                    bitmap = page.render(scale=2)
                    original = bitmap.to_pil().convert("RGB")
                    bitmap.close()
                    page.close()
                with Image.open(root / "first" / first["redacted_path"]) as result:
                    self.assertEqual(len(first["redactions"]), len(document["truth"]))
                    for mask in first["redactions"]:
                        left, top, right, bottom = mask["rectangle"]
                        crop = result.crop((left, top, right + 1, bottom + 1))
                        self.assertEqual(crop.getextrema(), ((0, 0), (0, 0), (0, 0)))
                        crop.close()
                    expected = original.copy()
                    draw = ImageDraw.Draw(expected)
                    for mask in first["redactions"]:
                        draw.rectangle(mask["rectangle"], fill="black")
                    self.assertIsNone(ImageChops.difference(expected, result).getbbox())
                    expected.close()
                original.close()
                self.assertEqual(hashlib.sha256((root / "first" / first["redacted_path"]).read_bytes()).hexdigest(), first["sha256"])

    @unittest.skipUnless(HAS_PDF, "requires pinned PDFium/Pillow RCC environment")
    def test_modified_input_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            generate(root / "corpus", 1)
            (root / "corpus" / "doc-0001.pdf").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "integrity"):
                redact_document(root / "corpus", root / "output", "doc-0001")


if __name__ == "__main__":
    unittest.main()
