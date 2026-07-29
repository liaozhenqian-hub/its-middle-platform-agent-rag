from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def postgres_saver_url(url: str, *, schema: str) -> str:
    if not _IDENTIFIER.fullmatch(schema):
        raise ValueError("invalid PostgreSQL schema")
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={schema},public"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )
