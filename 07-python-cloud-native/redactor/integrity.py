"""Independent source identity for the code carried outside the .rcca."""

import hashlib
import os
from pathlib import Path


def source_digest(root=None):
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    paths = []

    def collect(directory):
        if directory.is_symlink():
            raise ValueError("source tree contains a symlink: " + str(directory))
        if not directory.exists():
            return
        if not directory.is_dir():
            raise ValueError("source package is not a directory: " + str(directory))
        for path in directory.iterdir():
            if path.is_symlink():
                raise ValueError("source tree contains a symlink: " + str(path))
            if path.is_dir():
                if path.name != "__pycache__":
                    collect(path)
            elif path.is_file():
                if path.suffix != ".pyc":
                    paths.append(path)
            else:
                raise ValueError("source tree contains a non-regular file: " + str(path))

    for package in ("redactor", "cleanroom"):
        collect(root / package)
    for name in ("clean-room-robot.yaml", "clean-room-environment.yaml"):
        path = root / name
        if path.is_symlink():
            raise ValueError("source configuration contains a symlink: " + str(path))
        if path.exists():
            if not path.is_file():
                raise ValueError("source configuration is not a regular file: " + str(path))
            paths.append(path)

    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def verify_source():
    expected = os.environ.get("CLEAN_ROOM_SOURCE_DIGEST")
    actual = source_digest()
    if not expected or expected != actual:
        raise RuntimeError("verified source digest is absent or does not match")
    return actual


if __name__ == "__main__":
    print(source_digest())
