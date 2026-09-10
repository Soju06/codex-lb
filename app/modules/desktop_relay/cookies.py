from __future__ import annotations


def loopback_cookie(value: str) -> str:
    """Keep an upstream cookie intact except its official-host Domain attribute."""
    parts: list[str] = []
    start = 0
    quoted = False
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
        elif quoted and character == "\\":
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == ";" and not quoted:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    # Do not repair malformed quoted cookies or interpret their contents as attributes.
    if quoted or parts[0].lstrip().startswith("__Host-"):
        return value
    retained = parts[:1]
    for attribute in parts[1:]:
        name, separator, domain = attribute.partition("=")
        if separator and name.strip().lower() == "domain" and domain.strip().lower().removeprefix(".") == "chatgpt.com":
            continue
        retained.append(attribute)
    return ";".join(retained)
