"""Shared data models.

The current GUI still passes jobs as dictionaries so saved queues stay simple.
These dataclasses document the intended shape for future typed refactors.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class DownloadJob:
    identifier: str
    source_url: str
    file_index: int
    file_name: str
    expected_size: Optional[int]
    md5: Optional[str]
    sha1: Optional[str]
    local_path: str
    row_id: str
    size_text: str
    config_file: Optional[str]
    dest: str
