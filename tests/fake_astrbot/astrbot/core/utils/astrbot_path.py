"""旧版本 AstrBot 的路径工具（4.30 之前）。"""

import os
import tempfile
from pathlib import Path


def get_astrbot_data_path() -> str:
    return os.environ.get(
        "FAKE_ASTRBOT_DATA", str(Path(tempfile.gettempdir()) / "fake_astrbot_data")
    )
