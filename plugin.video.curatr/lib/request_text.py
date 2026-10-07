"""Protect quoted names and titles while parsing surrounding instructions."""

import re


_QUOTES = re.compile(r'"((?:\\.|[^"\\])*)"|“([^”]*)”|(?<!\w)\'([^\'\n]+)\'(?!\w)|‘([^’]*)’')
_LITERAL = re.compile("\ue000[0-9]+\ue001")


def mask_literals(value):
    literals = {}
    def replace(match):
        text = next(group for group in match.groups() if group is not None)
        text = re.sub(r'\\(["\\])', r'\1', text)
        token = "\ue000%d\ue001" % len(literals)
        literals[token] = " ".join(text.split())
        return token
    return _QUOTES.sub(replace, str(value or "")), literals


def restore_literals(value, literals):
    return _LITERAL.sub(lambda match: literals.get(match.group(0), match.group(0)), str(value or ""))
