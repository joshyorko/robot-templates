import logging
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from robocorp.tasks import get_output_dir, task

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
DEFAULT_MODEL = "gpt-4.1"


def _load_local_env() -> bool:
    return load_dotenv(ENV_PATH)


def _artifact_path(filename: str) -> Path:
    output_dir = Path(get_output_dir())
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / filename


@task
def smoke_local():
    """Verify imports and local config boundaries without calling OpenAI."""
    dotenv_loaded = _load_local_env()
    artifact = {
        "template": "04-python-assistant-ai",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dotenv_path": str(ENV_PATH),
        "dotenv_loaded": dotenv_loaded,
        "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
        "openai_model": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        "calls_openai_api": False,
        "message": "Local smoke completed without sending a prompt or using credentials.",
    }
    output_file = _artifact_path("local-smoke.json")
    output_file.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    logger.info("Local smoke completed without calling OpenAI.")
    logger.info("Wrote %s", output_file)


@task
def check_openai_credentials():
    """Make one minimal OpenAI request to verify the local API key."""
    _load_local_env()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Export it locally or create "
            "04-python-assistant-ai/.env; do not commit secrets."
        )

    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input="Reply with exactly: OpenAI credential check passed.",
    )

    output_file = _artifact_path("openai-credential-check.txt")
    output_file.write_text(
        f"model={model}\nresponse={response.output_text.strip()}\n",
        encoding="utf-8",
    )
    logger.info("OpenAI credential check completed with model %s.", model)
    logger.info("Wrote %s", output_file)
