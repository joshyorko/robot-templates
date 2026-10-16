import hashlib
import tempfile
import unittest
from pathlib import Path

from cleanroom.control import check_binary, archive_info, resource_names, local_endpoint, write_json


class ControlTests(unittest.TestCase):
    def test_runtime_roots_use_rcc_canonical_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roots.json"
            write_json(path, {"z-key": "second", "a-key": "first"}, canonical=True)
            self.assertEqual(path.read_bytes(), b'{"a-key":"first","z-key":"second"}\n')

    def test_remote_engine_endpoints_rejected(self):
        self.assertTrue(local_endpoint("unix:///var/run/docker.sock"))
        for endpoint in ("ssh://other", "tcp://127.0.0.1:2375", "unix://relative", "http://remote", ""):
            self.assertFalse(local_endpoint(endpoint))

    def test_binary_rejects_tampering_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rcc"
            path.write_bytes(b"not a trusted binary")
            with self.assertRaisesRegex(ValueError, "checksum"):
                check_binary(path)

    def test_archive_rejects_oversized_and_unsafe_members(self):
        import zipfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.rcca"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../escape", "x")
            with self.assertRaisesRegex(ValueError, "member"):
                archive_info(path)

    def test_names_reject_untrusted_resource_identifiers(self):
        with self.assertRaises(ValueError):
            resource_names("../../other")
        names = resource_names("cr-0123456789ab")
        self.assertEqual(names["network"], "cr-0123456789ab-net")


if __name__ == "__main__":
    unittest.main()
