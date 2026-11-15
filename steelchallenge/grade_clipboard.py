"""Read a SteelUniversity composition table copied as plain text."""

import re

import model


def parse_composition(text):
    lines = [(i, line) for i, line in enumerate(text.lstrip("\ufeff").splitlines(), 1)
             if line.strip()]
    if not lines:
        raise ValueError("조성표를 붙여넣으세요.")
    header = lines[0][1].strip()
    tabular = "\t" in header

    def split(line):
        return [cell.strip() for cell in line.split("\t")] if tabular else line.split()

    columns = [cell.casefold() for cell in split(lines[0][1])]
    required = ("element", "result", "min", "max")
    if any(columns.count(name) != 1 for name in required):
        raise ValueError("첫 줄에 Element / Result / Min / Max 머리글이 있어야 합니다.")
    element, low, high = (columns.index(name) for name in ("element", "min", "max"))
    limits = {}
    for line_number, line in lines[1:]:
        cells = split(line)
        try:
            if len(cells) != len(columns):
                raise ValueError("열 개수가 머리글과 다릅니다. 이 행을 다시 복사하세요.")
            key = cells[element]
            model.chemistry({key: None})
            if key in limits:
                raise ValueError(f"{key} 항목이 중복되었습니다.")
            bounds = [None if cells[i] in {"", "-", "–", "—", "−"} else float(cells[i])
                      for i in (low, high)]
            model.validate_limits({key: bounds}, 100)
            limits[key] = bounds
        except ValueError as error:
            raise ValueError(f"{line_number}행 · {error}") from error
    if not limits or all(bounds == [None, None] for bounds in limits.values()):
        raise ValueError("Min 또는 Max 값이 있는 원소가 한 개 이상 필요합니다.")
    return limits


def parse_process(text):
    lines = [line.strip() for line in text.lstrip("\ufeff").splitlines() if line.strip()]
    limits, grade = {}, None
    fields = {
        "temperature required": ("temp_c", r"°\s*C"),
        "mass required": ("tapping_mass_t", r"tonnes?|t"),
        "maximum time": ("time_min", None),
        "co2emissionsrequired": ("co2_kg", r"kg\s*CO[2₂]"),
        "basicity": ("basicity", ""),
        "mgo": ("MgO", "%?"),
        "feo": ("FeO", "%?"),
    }
    numeric = r"\d+(?:\.\d+)?"
    for i, line in enumerate(lines):
        label, sep, value = line.partition(":")
        label = label.strip().lstrip("%").strip().casefold()
        value = value.strip() if sep and value.strip() else (lines[i + 1] if i + 1 < len(lines) else "")
        if label == "steel grade":
            grade = value or None
        if label not in fields:
            continue
        key, unit = fields[label]
        if key in limits:
            raise ValueError(f"{label}: 항목이 중복되었습니다.")
        if key == "time_min":
            match = re.fullmatch(r"(?:(\d+)\s*h\s*)?(?:(\d+)\s*m(?:in)?)?", value, re.I)
            if not match or not any(match.groups()):
                raise ValueError(f"{label}: 시간을 읽을 수 없습니다 ({value}).")
            hours, minutes = (int(v or 0) for v in match.groups())
            limits[key] = [None, hours * 60 + minutes]
        elif key == "co2_kg":
            match = re.fullmatch(rf"({numeric})\s*{unit}", value, re.I)
            if not match:
                raise ValueError(f"{label}: 배출량을 읽을 수 없습니다 ({value}).")
            limits[key] = [None, float(match[1])]
        else:
            match = re.fullmatch(rf"({numeric})\s*[-–—−]\s*({numeric})\s*(?:{unit})", value, re.I)
            if not match:
                raise ValueError(f"{label}: 범위 또는 단위를 읽을 수 없습니다 ({value}).")
            limits[key] = [float(match[1]), float(match[2])]
    if not limits:
        raise ValueError("온도, 질량, 시간, CO₂ 또는 슬래그 조건이 있는 표를 붙여넣으세요.")
    model.validate_limits(limits, float("inf"))
    return grade, limits
