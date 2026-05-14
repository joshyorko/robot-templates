import os
import sys
from pathlib import Path

from robocorp.tasks import task


@task
def uv_native_smoke():
    output_dir = Path(os.environ.get("ROBOT_ARTIFACTS", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact = output_dir / "uv-native-smoke.txt"
    artifact.write_text(
        "\n".join(
            [
                "uv-native RCC smoke completed.",
                f"python={sys.version.split()[0]}",
                f"executable={sys.executable}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {artifact}")
