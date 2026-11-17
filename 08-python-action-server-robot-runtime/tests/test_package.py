from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.check_package import check_package


class PackageTests(unittest.TestCase):
    def test_generated_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "robot.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("output/home/private.key", "not a real key")
            with self.assertRaisesRegex(ValueError, "generated|private"):
                check_package(archive)


if __name__ == "__main__":
    unittest.main()
