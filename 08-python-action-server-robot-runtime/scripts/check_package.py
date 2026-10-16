"""Check real RCC wrap contents without extracting or executing the package."""

from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_package(archive):
    from invoice_robot.validator import SOURCE_PATHS

    required = set(SOURCE_PATHS) | {
        "README.md", ".gitignore", "scripts/compare.py", "scripts/check_package.py",
        "scripts/action_server.py", "scripts/setup_action_server.py",
        "scripts/artifact_runtime.py", "scripts/setup_rcc.py",
        "rcc-lock.json", "runtime-lock.json",
    }
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate package entries")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError(f"unsafe package path: {name}")
            if any(part in {"output", ".runtime", "__pycache__", ".git", ".env"}
                   for part in path.parts) or path.suffix in {".key", ".pem", ".rcca", ".pyc"}:
                raise ValueError(f"generated or private state in package: {name}")
            if re.search(br"(?m)^-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----", bundle.read(name)):
                raise ValueError(f"private key in package: {name}")
        missing = required - set(names)
        if missing:
            raise ValueError(f"required files missing: {sorted(missing)}")
        for name in SOURCE_PATHS:
            if bundle.read(name) != (ROOT / name).read_bytes():
                raise ValueError(f"packaged source differs: {name}")
    return {"entries": len(names), "required_files": len(required), "source_bytes_match": True}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 scripts/check_package.py robot.zip")
    print(check_package(Path(sys.argv[1])))
