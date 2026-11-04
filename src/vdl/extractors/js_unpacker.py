"""Unpacker for Dean Edwards' JavaScript Packer (`eval(function(p,a,c,k,e,d)...)`)."""

from __future__ import annotations

import re

ALPHABET_62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
ALPHABET_95 = (
    " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
)

# Matches the arguments block at the end of eval(function(p,a,c,k,e,d|r){...}('payload', radix, count, 'keywords'.split('|')...))
PACKED_ARGS_REGEX = re.compile(
    r"}\(\s*(?P<quote>['\"])(?P<payload>(?:\\.|(?!(?P=quote)).)*)(?P=quote)\s*,\s*"
    r"(?P<radix>\d+)\s*,\s*"
    r"(?P<count>\d+)\s*,\s*"
    r"(?P<kquote>['\"])(?P<keywords>(?:\\.|(?!(?P=kquote)).)*)(?P=kquote)\.split\(\s*['\"]\|['\"]\s*\)",
    re.DOTALL,
)

WORD_REGEX = re.compile(r"\b\w+\b")


def _encode_base(num: int, radix: int) -> str:
    """Encodes integer `num` into base `radix` string using standard packer alphabet."""
    if radix <= 10:
        return str(num)
    alphabet = ALPHABET_95 if radix > 62 else ALPHABET_62
    if num == 0:
        return alphabet[0]
    digits: list[str] = []
    while num > 0:
        num, rem = divmod(num, radix)
        digits.append(alphabet[rem])
    return "".join(reversed(digits))


def unpack_single(payload: str, radix: int, count: int, keywords: list[str]) -> str:
    """Unpacks a single (p, a, c, k) packed JavaScript payload."""
    if radix <= 0:
        return payload

    # Unescape single/double quotes in payload
    cleaned_payload = payload.replace(r"\'", "'").replace(r"\"", '"')

    # Build lookup dictionary mapping encoded base-radix token -> keyword
    symtab: dict[str, str] = {}
    for i in range(count - 1, -1, -1):
        token = _encode_base(i, radix)
        replacement = keywords[i] if i < len(keywords) and keywords[i] else token
        symtab[token] = replacement

    def replace_token(match: re.Match[str]) -> str:
        word = match.group(0)
        return symtab.get(word, word)

    return WORD_REGEX.sub(replace_token, cleaned_payload)


def unpack_all_from_html(html_text: str) -> list[str]:
    """Finds and unpacks all packed JavaScript blocks within an HTML/JS string."""
    unpacked_blocks: list[str] = []
    for match in PACKED_ARGS_REGEX.finditer(html_text):
        try:
            payload = match.group("payload")
            radix = int(match.group("radix"))
            count = int(match.group("count"))
            keywords = match.group("keywords").split("|")
            unpacked = unpack_single(payload, radix, count, keywords)
            if unpacked:
                unpacked_blocks.append(unpacked)
        except Exception:
            continue
    return unpacked_blocks

