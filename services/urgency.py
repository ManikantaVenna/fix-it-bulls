"""Local urgency screening for the email workflow."""

import re


URGENT_PATTERN = re.compile(
    r"\b(fire|smoke|smoking|burning|sparks?|sparking|gas (leak|smell)|smell(s|ing)? (of )?gas|"
    r"carbon monoxide|live wire|electrocut\w*|flood(ed|ing)?|trapped|stuck in (the |an )?elevator|"
    r"injur\w*|bleeding|unconscious|weapon|gun|assault\w*)\b",
    re.IGNORECASE,
)


def has_urgent_text(*texts: str) -> bool:
    return any(URGENT_PATTERN.search(text) for text in texts)
