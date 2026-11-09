"""Detect attempts to override system policy. Detection does not grant permission."""

import re

_INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above) instructions"
    r"|skip (the )?approval"
    r"|bypass (the )?(approval|policy|safety)"
    r"|disregard (the )?(policy|approval|instructions)"
    r"|you are now"
    r"|reveal (the )?system prompt",
    re.IGNORECASE,
)


def injection_attempt(text: str) -> bool:
    return _INJECTION.search(text) is not None
