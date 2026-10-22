"""Interpretador mínimo de robots.txt (RFC 9309): grupos por User-agent, `*` e `$`, regra mais longa vence.

Escrito à mão porque `urllib.robotparser` só entende curingas a partir do Python 3.13.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

MAX_LINES = 5000


class Robots:
    def __init__(self, text: str, agent: str):
        agent = agent.lower()
        groups: list[tuple[list[str], list[tuple[bool, re.Pattern, int]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, re.Pattern, int]] = []
        in_rules = False
        for raw in text.splitlines()[:MAX_LINES]:
            line = raw.split("#", 1)[0].strip()
            key, sep, value = line.partition(":")
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if in_rules:
                    groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if value:                                  # "Disallow:" vazio = permite tudo
                    rules.append((key == "allow", _pattern(value), len(value)))
        if agents:
            groups.append((agents, rules))
        specific = [g for g in groups if any(a != "*" and a in agent for a in g[0])]
        wildcard = [g for g in groups if "*" in g[0]]
        chosen = specific or wildcard
        self._rules = [rule for _, group_rules in chosen for rule in group_rules]

    def can_fetch(self, url: str) -> bool:
        parts = urlsplit(url)
        path = unquote(parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, pattern, length in self._rules:
            if pattern.match(path) and (best is None or length > best[0] or (length == best[0] and allow)):
                best = (length, allow)
        return True if best is None else best[1]


def _pattern(value: str) -> re.Pattern:
    anchored = value.endswith("$")
    body = value[:-1] if anchored else value
    regex = ".*".join(re.escape(piece) for piece in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""))
