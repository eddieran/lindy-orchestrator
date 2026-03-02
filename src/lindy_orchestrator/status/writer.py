"""Write/update STATUS.md sections.

Operates on specific sections without corrupting the rest of the file.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


def update_meta_timestamp(status_path: Path) -> None:
    """Update the last_updated value in the Meta section."""
    text = status_path.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    text = re.sub(
        r"(\|\s*last_updated\s*\|\s*)([^|]+)(\|)",
        rf"\g<1>{now} \3",
        text,
        count=1,
    )
    status_path.write_text(text, encoding="utf-8")


def update_root_status(root_status_path: Path, content: str) -> None:
    """Replace root STATUS.md with new content.

    Safety: validates content looks like a STATUS.md before writing.
    """
    if not content.strip().startswith("#"):
        raise ValueError("Content does not look like a STATUS.md file (must start with #)")
    root_status_path.write_text(content, encoding="utf-8")
