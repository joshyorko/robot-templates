import argparse
import sys
from pathlib import Path

from .validator import validate_batch


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a synthetic invoice batch")
    parser.add_argument("--input", required=True, type=Path, help="Absolute JSON input path")
    parser.add_argument("--output", required=True, type=Path, help="Absolute artifact directory")
    parser.add_argument("--fail", action="store_true", help="Write deliberate failure evidence and fail")
    args = parser.parse_args()
    if not args.input.is_absolute() or not args.output.is_absolute():
        parser.error("--input and --output must be absolute paths")
    try:
        validate_batch(args.input, args.output, force_failure=args.fail)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"invoice batch failed: {error}", file=sys.stderr)
        return 2
    print(f"invoice batch validated; artifacts: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
