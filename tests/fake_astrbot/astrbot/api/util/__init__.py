import os
import tempfile
from pathlib import Path


def get_astrbot_data_path() -> str:
    return os.environ.get(
        "FAKE_ASTRBOT_DATA", str(Path(tempfile.gettempdir()) / "fake_astrbot_data")
    )
