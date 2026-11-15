import json
import re
from urllib.parse import unquote

STAMP = re.compile(r"^\s*\[?(?:(\d+):)?(\d{1,3}):([0-5]\d(?:\.\d+)?)\]?\s*[-–|,]?\s*")
RULES = [
    ("sample", r"\b(?:sample|analysis|composition|chemistry)\b|성분|분석"),
    ("charge", r"\b(?:charge|basket)\b|바스켓|장입"),
    ("addition", r"\b(?:add|addition|inject)\w*\b|첨가|투입|취입"),
    ("transfer", r"\b(?:move|transfer|arrive)\w*\b|\bgo to:|이동"),
    ("tap", r"\btap\w*\b|출강"),
    ("power", r"\bpower\b"),
    ("oxygen", r"\boxygen\b"),
    ("simulation_rate", r"\bsimulation rate\b"),
    ("settings", r"^selected (?:user level|steel grade)\b"),
    ("temperature", r"°\s*C|온도"),
    ("cost", r"\bcost\b|원가"),
]
ELEMENT = re.compile(r"\b([A-Z][a-z]?)\s*[:=]\s*(\d+(?:\.\d+)?)\s*%")
AMOUNT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(kg|t|kWh|MW|Nm[3³]|°\s*C)(?!\w)", re.I)


def parse(raw):
    if raw.lstrip().startswith("["):
        try:
            entries = json.loads(raw)
        except ValueError:
            entries = None
        if isinstance(entries, list) and all(isinstance(item, str) for item in entries):
            return parse("\n".join(entries))
    events = []
    for line, original in enumerate(raw.splitlines(), 1):
        clean = unquote(original.strip().rstrip(",").strip('"'))
        if not clean or clean in {"[", "]"}:
            continue
        stamp = STAMP.match(clean)
        seconds = None
        message = clean
        if stamp:
            hours, minutes, sec = stamp.groups()
            if hours is None or int(minutes) < 60:
                seconds = 3600 * int(hours or 0) + 60 * int(minutes) + float(sec)
                message = clean[stamp.end():]
        kind = next((name for name, pattern in RULES if re.search(pattern, message, re.I)), "unknown")
        events.append({
            "line": line, "seconds": seconds, "kind": kind, "raw": original,
            "payload": {"message": message,
                        "composition": {k: float(v) for k, v in ELEMENT.findall(message)},
                        "amounts": [{"value": float(v), "unit": u} for v, u in AMOUNT.findall(message)]},
        })
    return events

