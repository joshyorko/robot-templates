"""Render existing digital text and replace detected sensitive glyph regions."""

import hashlib
import json
import math
import re
from pathlib import Path


PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"\b[0-9]{3}-[0-9]{3}-[0-9]{4}\b"),
    "ssn": re.compile(r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b"),
}


def spans(text):
    return sorted((match.start(), match.end(), kind) for kind, pattern in PATTERNS.items() for match in pattern.finditer(text))


def redact_text(text):
    result = text
    for start, end, kind in reversed(spans(text)):
        result = result[:start] + f"[REDACTED:{kind}]" + result[end:]
    return "\n".join(result.splitlines()) + "\n"


def redact_document(corpus, output, identity):
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    corpus, output = Path(corpus), Path(output)
    manifest = json.loads((corpus / "corpus.json").read_text())
    matches = [document for document in manifest["documents"] if document["id"] == identity]
    if len(matches) != 1 or not re.fullmatch(r"doc-[0-9]{4}", identity):
        raise ValueError("unknown document")
    document = matches[0]
    path = (corpus / document["pdf"]).resolve()
    if path.parent != corpus.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]:
        raise ValueError("corpus document integrity mismatch")
    output.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(path)
    try:
        if len(pdf) != 1:
            raise ValueError("showcase accepts one-page digital PDFs")
        page = pdf[0]
        textpage = page.get_textpage()
        try:
            text = textpage.get_text_range()
            detected = spans(text)
            bitmap = page.render(scale=2)
            try:
                image = bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
            draw = ImageDraw.Draw(image)
            masks = []
            for start, end, kind in detected:
                boxes = [textpage.get_charbox(index) for index in range(start, end)]
                left = max(0, math.floor(min(box[0] for box in boxes) * 2) - 2)
                right = min(image.width - 1, math.ceil(max(box[2] for box in boxes) * 2) + 2)
                top = max(0, math.floor((page.get_height() - max(box[3] for box in boxes)) * 2) - 2)
                bottom = min(image.height - 1, math.ceil((page.get_height() - min(box[1] for box in boxes)) * 2) + 2)
                rectangle = [left, top, right, bottom]
                draw.rectangle(rectangle, fill="black")
                masks.append({"kind": kind, "rectangle": rectangle})
            png = output / f"{identity}.png"
            image.save(png, format="PNG", optimize=False, compress_level=9)
            image.close()
            redacted = redact_text(text)
            text_path = output / f"{identity}.txt"
            text_path.write_text(redacted, encoding="utf-8")
            evidence = {"id": identity, "mode": "digital-pdf-not-ocr", "redacted_path": png.name, "text_path": text_path.name, "sha256": hashlib.sha256(png.read_bytes()).hexdigest(), "text_sha256": hashlib.sha256(text_path.read_bytes()).hexdigest(), "redactions": masks, "text": redacted}
            (output / f"{identity}.json").write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
            return evidence
        finally:
            textpage.close()
            page.close()
    finally:
        pdf.close()
