"""Parse copied secondary-steelmaking tables and setup summaries."""

import html
import re

import model
from secondary_model import clock_minutes


def parse_caster_time(value):
    pieces = re.split(r'\s*(?:±|\+\s*/\s*-)\s*', value.strip().rstrip('.'))
    if len(pieces) > 2:
        raise ValueError('주조 요구시간과 허용오차를 확인하세요.')
    duration = pieces[0].strip()
    if ':' in duration:
        minutes = clock_minutes(duration)
    else:
        match = re.fullmatch(r'(?:(\d+)\s*(?:hours?|hrs?|h)\s*)?'
                             r'(?:(\d+)\s*(?:minutes?|mins?|m))?', duration, re.I)
        if not match or not any(match.groups()):
            raise ValueError('주조 요구시간을 1 hr 28 mins ±5 mins. 형식으로 입력하세요.')
        minutes = int(match[1] or 0) * 60 + int(match[2] or 0)
    seconds = round(minutes * 60)
    result = {'required_at': f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'}
    if len(pieces) == 2:
        match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m)', pieces[1], re.I)
        if not match:
            raise ValueError('주조 시간 허용오차를 분 단위로 입력하세요.')
        result['time_tolerance'] = float(match[1])
    return result


def format_caster_time(required_at, tolerance):
    minutes = clock_minutes(required_at)
    if minutes != int(minutes):
        return f'{required_at} ±{tolerance:g} mins.'
    return f'{int(minutes) // 60} hr {int(minutes) % 60} mins ±{tolerance:g} mins.'


def parse_setup(text):
    text = html.unescape(text).lstrip('\ufeff').replace('**', '').replace('\u00a0', ' ')
    text = text.replace('\u200b', '').replace('\ufeff', '')
    lines = [re.sub(r'^\s*[-•]\s+', '', line).strip().rstrip('\\').rstrip()
             for line in text.splitlines() if line.strip()]
    result = {}
    chemistry, aims, limits = {}, {}, {}
    header = None
    delimiter = None

    def cells(line, separator):
        if separator == '|':
            return [c.strip() for c in line.strip().strip('|').split('|')]
        if separator == '\t':
            return [c.strip() for c in line.split('\t')]
        return line.split()

    def number(token):
        if token in ('', '-', '–', '—', '−'):
            return None
        value = float(token.replace(',', '').rstrip('%'))
        return model.number(value, '조성 wt%', maximum=100)

    for i, line in enumerate(lines):
        separator = '|' if '|' in line else '\t' if '\t' in line else None
        parts = cells(line, separator)
        names = [p.casefold() for p in parts]
        if all(name in names for name in ('current', 'aim', 'min', 'max')):
            header = {name: names.index(name) for name in ('current', 'aim', 'min', 'max')}
            # Whitespace copies often omit the empty header above element names.
            if header['current'] == 0:
                header = {name: index + 1 for name, index in header.items()}
            delimiter = separator
            continue
        row = cells(line, delimiter) if header else []
        if header and row and re.fullmatch(r'[A-Z][a-z]?', row[0]):
            element = row[0]
            try:
                if element in chemistry:
                    raise ValueError(f'{element} 행이 중복되었습니다.')
                values = {key: number(row[index]) for key, index in header.items()}
                bounds = [values['min'], values['max']]
                model.validate_limits({element: bounds}, 100)
                if values['aim'] is not None:
                    if bounds[0] is not None and values['aim'] < bounds[0]:
                        raise ValueError('Aim이 Min보다 작습니다.')
                    if bounds[1] is not None and values['aim'] > bounds[1]:
                        raise ValueError('Aim이 Max보다 큽니다.')
                chemistry[element], aims[element], limits[element] = values['current'], values['aim'], bounds
            except (ValueError, IndexError) as error:
                raise ValueError(f'{i + 1}행 {element} · 조성표를 확인하세요. {error}') from error

    if chemistry:
        model.chemistry(chemistry)
        result.update(chemistry=chemistry, aims=aims, limits=limits)

    def field(label):
        pattern = re.compile(r'^' + re.escape(label) + r'\s*:?\s*(.*)$', re.I)
        for i, line in enumerate(lines):
            match = pattern.fullmatch(line)
            if match:
                value = match[1].strip()
                if value:
                    return value
                if i + 1 < len(lines):
                    return lines[i + 1]
                raise ValueError(f'{label} 값이 없습니다.')
        return None

    grade = field('Chosen grade')
    if grade:
        result['grade'] = grade
    level = field('User Level')
    if level:
        levels = ['University Student', 'Steel Industry Works Technical']
        result['user_level'] = next((v for v in levels if v.casefold() == level.casefold()), None)
        if result['user_level'] is None:
            raise ValueError(f'User Level을 확인하세요 ({level}).')
    mass = field('Ladle steel bath mass')
    if mass:
        match = re.fullmatch(r'([\d,.]+)\s*(kg|t|tonnes?)', mass, re.I)
        if not match:
            raise ValueError('Ladle steel bath mass에 숫자와 kg 또는 t 단위가 필요합니다.')
        result['mass_kg'] = model.number(float(match[1].replace(',', '')),
                                         'Ladle steel bath mass', minimum=1) * (1 if match[2].lower() == 'kg' else 1000)
    inclusion = field('Required inclusion level')
    if inclusion:
        levels = ['Very low', 'Low', 'Moderate']
        result['inclusion'] = next((v for v in levels if inclusion.casefold() == v.casefold()), None)
        if result['inclusion'] is None:
            raise ValueError('Required inclusion level을 확인하세요.')
    required = field('The ladle is required at')
    if required:
        match = re.fullmatch(r'(Slab|Bloom|Billet)\s+Caster', required, re.I)
        if not match:
            raise ValueError('The ladle is required at의 Caster 이름을 확인하세요.')
        result['caster'] = match[1].capitalize() + ' Caster'
        result['destination'] = result['caster']
    time = field('Time until ladle required at caster')
    if time:
        result.update(parse_caster_time(time))
    tap = field('Tap temperature')
    if tap:
        match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*°?\s*C', tap, re.I)
        if not match:
            raise ValueError('Tap temperature를 확인하세요.')
        result['tap_temperature'] = model.number(float(match[1]), 'Tap temperature', 1, 2500)
    temperature = field('Aim temperature at caster')
    if temperature:
        match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:[-–—−~]\s*(\d+(?:\.\d+)?))?\s*°?\s*C', temperature, re.I)
        if not match:
            raise ValueError('Aim temperature at caster를 확인하세요.')
        lo, hi = float(match[1]), float(match[2] or match[1])
        model.number(lo, '주조 최소온도', 1, 2500)
        model.number(hi, '주조 최대온도', lo, 2500)
        result['aim_temperature'] = (lo + hi) / 2
        result['temperature_tolerance'] = (hi - lo) / 2
    if not result:
        raise ValueError('Current / Aim / Min / Max 조성표 또는 시작 조건을 붙여넣으세요.')
    return result
