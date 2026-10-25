"""Account-number masking for anything a supervisor reads on a shared screen."""

import re

# A run of five or more digits, or digits in groups of four ("1234 5678 9012"), unless it is
# part of an amount ($1,234,567.89) or a call, member or staff id (CALL-00042, MBR-000584).
_ACCOUNT = re.compile(
    r"(?<![\d$.,])(?<!CALL-)(?<!MBR-)(?<!AGT-)"
    r"(?:\d{4}(?:[ -]\d{4})+|\d{5,})(?![\d,]|\.\d)"
)


def mask_accounts(text: str) -> str:
    """Keep only the last four digits of every account number: 123456789 becomes ****6789."""
    return _ACCOUNT.sub(lambda m: "****" + re.sub(r"\D", "", m.group())[-4:], text)
