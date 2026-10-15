"""Build byte-for-byte reproducible PDFs with explicitly synthetic truth labels."""

import argparse
import hashlib
import json
from pathlib import Path


def pdf_bytes(lines):
    commands = ["BT", "/F1 12 Tf", "50 742 Td", "18 TL"]
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({escaped}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def generate(output, count=8):
    if not 1 <= count <= 1000:
        raise ValueError("count must be between 1 and 1000")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    documents = []
    for number in range(1, count + 1):
        identity = f"doc-{number:04d}"
        truth = [
            {"kind": "email", "value": f"person{number:04d}@example.invalid"},
            {"kind": "phone", "value": f"202-555-{100 + (number - 1) % 100:04d}"},
            {"kind": "ssn", "value": f"000-00-{number:04d}"},
        ]
        safe = ["SYNTHETIC CLEAN ROOM DOCUMENT", f"Document: {identity}", "Public note: blue lanterns stay visible."]
        lines = safe + [f"{entry['kind'].title()}: {entry['value']}" for entry in truth]
        expected = safe + [f"{entry['kind'].title()}: [REDACTED:{entry['kind']}]" for entry in truth]
        content = pdf_bytes(lines)
        (output / f"{identity}.pdf").write_bytes(content)
        documents.append({"id": identity, "pdf": f"{identity}.pdf", "sha256": hashlib.sha256(content).hexdigest(), "truth": truth, "safe_text": safe, "expected_text": "\n".join(expected) + "\n"})
    manifest = {"schema": 1, "synthetic": True, "mode": "digital-pdf-not-ocr", "documents": documents}
    (output / "corpus.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=8)
    args = parser.parse_args()
    generate(args.output, args.count)
