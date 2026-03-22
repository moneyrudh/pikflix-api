import re
from enum import Enum


class ERROR_CODES(Enum):
    DYNAMIC = "[dynamic-access]"


class ERROR_NODES(Enum):
    GETATTR = "getattr"
    SETATTR = "setattr"
    HASATTR = "hasattr"


RE_GIT_DIFF_HEADER = re.compile(r"@@.*\+(\d+)(?:,(\d+))?\s+@@")
RE_MYPY_ERR_FORMAT_HEADER = re.compile(r"^(.+?):(\d+):\s*(error|warning|note):\s*(.+)$")
RE_ERROR_CODE = re.compile(
    r"(\[[\w-]+\])$",
)  # Matches error codes like [assignment], [arg-type]
