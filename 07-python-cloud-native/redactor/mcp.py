"""Newline-delimited MCP JSON-RPC stdio server; protocol output only on stdout."""

import json
import os
import sys

from redactor.engine import redact_document
from redactor.integrity import verify_source


VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
TOOL = {"name": "redact_document", "description": "Redact synthetic sensitive fields in a digital PDF, rendering a PNG. No OCR.", "inputSchema": {"type": "object", "properties": {"document": {"type": "string", "pattern": "^doc-[0-9]{4}$"}}, "required": ["document"], "additionalProperties": False}}


def serve(reader, writer, corpus, output, digest):
    phase = "new"

    def send(message):
        writer.write(json.dumps(message, separators=(",", ":")) + "\n")
        writer.flush()

    for line in reader:
        try:
            request = json.loads(line)
        except (ValueError, UnicodeError):
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        valid_identity = isinstance(request, dict) and (
            "id" not in request
            or request["id"] is None
            or (isinstance(request["id"], (str, int)) and not isinstance(request["id"], bool))
        )
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str) or not valid_identity:
            send({"jsonrpc": "2.0", "id": request.get("id") if valid_identity else None, "error": {"code": -32600, "message": "Invalid Request"}})
            continue
        if "id" not in request:
            if request["method"] == "notifications/initialized" and phase == "initializing" and isinstance(request.get("params", {}), dict):
                phase = "ready"
            continue
        identity, method = request["id"], request["method"]
        params = request.get("params", {})
        result, error = None, None
        if not isinstance(params, dict):
            error = (-32602, "Invalid params")
        elif method == "initialize":
            version = params.get("protocolVersion")
            client = params.get("clientInfo")
            if phase != "new":
                error = (-32600, "Session already initialized")
            elif not isinstance(version, str) or not isinstance(params.get("capabilities"), dict) or not isinstance(client, dict) or not all(isinstance(client.get(key), str) for key in ("name", "version")):
                error = (-32602, "Invalid initialization params")
            else:
                phase = "initializing"
                result = {"protocolVersion": version if version in VERSIONS else VERSIONS[-1], "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "clean-room-redactor", "version": "1.0.0"}, "instructions": "Synthetic digital PDFs only; no OCR. Verified source " + digest}
        elif method == "ping":
            result = {}
        elif phase != "ready":
            error = (-32000, "Complete MCP initialization first")
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            arguments = params.get("arguments", {})
            if params.get("name") != TOOL["name"]:
                error = (-32602, "Unknown tool")
            elif not isinstance(arguments, dict) or set(arguments) != {"document"} or not isinstance(arguments.get("document"), str):
                error = (-32602, "Expected document string only")
            else:
                try:
                    evidence = redact_document(corpus, output, arguments["document"])
                    result = {"content": [{"type": "text", "text": json.dumps(evidence, sort_keys=True)}], "isError": False}
                except (ValueError, OSError, RuntimeError) as exc:
                    print(f"redaction failed: {type(exc).__name__}", file=sys.stderr, flush=True)
                    result = {"content": [{"type": "text", "text": "Redaction failed; inspect local stderr."}], "isError": True}
        else:
            error = (-32601, "Method not found")
        response = {"jsonrpc": "2.0", "id": identity}
        response["error" if error else "result"] = {"code": error[0], "message": error[1]} if error else result
        send(response)


def main():
    try:
        digest = verify_source()
        corpus, output = os.environ["CLEAN_ROOM_CORPUS"], os.environ["CLEAN_ROOM_OUTPUT"]
    except (RuntimeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    serve(sys.stdin, sys.stdout, corpus, output, digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
