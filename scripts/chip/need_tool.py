"""Rule 19 / Gap 4: missing allowlisted tools stop with NEED_TOOL:<name>."""

from __future__ import annotations


class NeedToolError(Exception):
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"NEED_TOOL:{tool_name}")

    def token(self) -> str:
        return f"NEED_TOOL:{self.tool_name}"


def require_allowlisted(tool_name: str, *, allowed: bool) -> None:
    if not allowed:
        raise NeedToolError(tool_name)
