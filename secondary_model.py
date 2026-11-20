"""Transparent secondary refining estimates using editable manual parameters."""

import math


FACILITIES = ['이동·대기', 'Ar Stir Station', 'CAS-OB', 'Ladle Furnace',
              'Tank Degasser', 'Recirculating Degasser']
ELEMENTS = ['C', 'Si', 'Mn', 'P', 'S', 'Cr', 'Mo', 'Ni', 'Nb', 'Ti',
            'Al', 'V', 'B', 'Ca', 'Cu', 'Sn', 'O', 'H', 'N']


def numeric(value, label, minimum=0, maximum=math.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label}에 숫자를 입력하세요.')
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{label} 허용 범위 {minimum}–{maximum}')
    return float(value)


def clock_minutes(value):
    parts = str(value).strip().split(':')
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError('시간은 HH:MM 또는 HH:MM:SS로 입력하세요.')
    h, m = map(int, parts[:2])
    s = int(parts[2]) if len(parts) == 3 else 0
    if m >= 60 or s >= 60:
        raise ValueError('분과 초는 0–59입니다.')
    return h * 60 + m + s / 60


def liquidus(chemistry):
    required = ['C', 'Mn', 'Si', 'S', 'P', 'Cr', 'Al', 'Ni', 'V', 'Mo']
    if any(chemistry.get(e) is None for e in required):
        return None
    c = chemistry['C']
    base, coefficient = (1537, 73.1) if c < 0.5 else (1531, 61.5)
    weights = dict(Mn=4, Si=14, S=45, P=30, Cr=1.5, Al=2.5, Ni=3.5, V=4, Mo=5)
    return base - coefficient * c - sum(weights[e] * chemistry[e] for e in weights)


def deoxidize(mass, aluminum, oxygen, temperature, heat_per_kg):
    """Forward Al2O3 formation, unit oxide activity and dilute wt% activities.

    Manual equations 9-4–9-6. Solve stoichiometry, mass loss and reaction heat
    together. Slag dissolution/reoxidation and finite reaction kinetics need data.
    """
    def residual(reacted):
        remaining_mass = mass - reacted * (1 + 48 / 54)
        if remaining_mass <= 0:
            return -math.inf
        al_pct = max(0, aluminum - reacted) / remaining_mass * 100
        o_pct = max(0, oxygen - reacted * 48 / 54) / remaining_mass * 100
        kelvin = temperature + heat_per_kg * reacted + 273.15
        if kelvin <= 0:
            raise ValueError('계산 온도가 절대영도 아래입니다. 처리시간과 냉각률을 확인하세요.')
        solubility = 10 ** (20.5 - 62780 / kelvin)
        return al_pct ** 2 * o_pct ** 3 - solubility

    if residual(0) <= 0:
        return 0.0
    lower, upper = 0.0, min(aluminum, oxygen * 54 / 48)
    for _ in range(60):
        middle = (lower + upper) / 2
        if residual(middle) > 0:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2


def simulate(config):
    numeric(config['lf_efficiency'], 'LF 효율', 0, 1)
    numeric(config['electricity_price'], '전력 단가')
    electrode_rate = numeric(config.get('electrode_per_mw_min', 0.295), 'LF 전극 $/MW/분')
    if config['inclusion'] not in ('Moderate', 'Low', 'Very low'):
        raise ValueError('요구 개재물 등급을 확인하세요.')
    if not config['steps']:
        raise ValueError('공정 구간을 입력하세요.')
    for addition in config.get('additions', []):
        step = numeric(addition['step'], '첨가 구간', 1, len(config['steps']))
        if step != int(step):
            raise ValueError('첨가 구간 번호는 정수입니다.')
    mass = numeric(config['mass_kg'], 'Ladle steel bath mass', 1)
    temp = numeric(config['tap_temperature'], 'Tap temperature', 1, 2500)
    initial_temp = temp
    elapsed = 0.0
    total = numeric(config.get('eaf_cost_total', 0), 'EAF 총비용')
    initial_cost = total
    composition = config['chemistry']
    amounts = {e: None if v is None else mass * numeric(v, e, 0, 100) / 100
               for e, v in composition.items()}
    if sum(v for v in composition.values() if v is not None) > 100:
        raise ValueError('Tap 조성 합계가 100 wt%를 초과합니다.')
    limits = config['limits']
    for element, bounds in limits.items():
        lo, hi = bounds
        if lo is not None:
            numeric(lo, f'{element} Min', 0, 100)
        if hi is not None:
            numeric(hi, f'{element} Max', 0, 100)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f'{element} Min이 Max보다 큽니다.')
    parameters = config['parameters']
    warnings = ['매뉴얼 기본값을 사용하는 추정입니다. 현재 UI와 실제 Run으로 보정하세요.',
                'H 평형 추정은 진공 압력을 H₂ 분압으로 근사합니다. 현재 가스 조건으로 보정하세요.',
                'N 제거·재흡수, 슬래그 재산화, 산소 취입의 반응속도는 별도 실측이 필요합니다.']
    if initial_cost == 0:
        warnings.append('EAF 총비용이 0입니다. 표시 비용에는 입력한 2차정련 비용만 반영됩니다.')
    if config.get('user_level') == 'Steel Industry Works Technical':
        warnings.append('설비 고장·분석 지연·주조 일정 변경은 입력한 일정에 반영해야 합니다.')
    calibrated_clean_time = None
    last_addition_time = None
    last_al_time = None
    frozen = False
    trace = []

    def snapshot(label):
        nonlocal frozen
        chem = {e: None if amount is None else amount / mass * 100
                for e, amount in amounts.items()}
        freezing = liquidus(chem)
        if freezing is not None and temp <= freezing:
            frozen = True
        trace.append(dict(구간=label, 분=elapsed, 온도=temp, 용강kg=mass,
                          액상선=freezing, 누적비용=total, **chem))

    snapshot('Tap')
    for index, step in enumerate(config['steps'], 1):
        facility = step['facility']
        if facility not in FACILITIES:
            raise ValueError(f'설비 확인 필요 {facility}')
        duration = numeric(step['minutes'], '구간 시간')
        power = numeric(step.get('power_mw', 0), 'LF 전력 MW', 0, 20)
        argon = numeric(step.get('argon', 0), 'Ar Nm³/min')
        pressure = numeric(step.get('pressure', 1), '절대압 atm', 0.001, 1)
        if power and facility != 'Ladle Furnace':
            raise ValueError('전기 가열은 Ladle Furnace 구간에 입력하세요.')
        if facility not in ('Tank Degasser', 'Recirculating Degasser') and pressure != 1:
            raise ValueError('진공 압력은 진공 설비 구간에 입력하세요.')
        settings = parameters[facility]
        for key in ('cooling', 'per_min', 'argon_price', 'k_c', 'c_eq', 'k_s', 's_eq'):
            numeric(settings[key], f'{facility} {key}')
        if settings['k_c'] == 0 and facility in ('CAS-OB', 'Tank Degasser', 'Recirculating Degasser'):
            warnings.append(f'{facility} 탈탄속도 미입력으로 C/O 반응을 계산하지 않았습니다.')
        al_reaction = 0.0
        added_kg = 0.0
        for addition in [a for a in config.get('additions', []) if int(a['step']) == index]:
            kg = numeric(addition['kg'], '첨가 kg')
            if not kg:
                continue
            last_addition_time = elapsed
            comp = addition['composition']
            if not isinstance(comp, dict) or not comp:
                raise ValueError('첨가물 조성 JSON 객체를 입력하세요.')
            if any(e not in ELEMENTS + ['Fe'] for e in comp):
                raise ValueError('첨가물 원소 기호를 확인하세요.')
            if sum(numeric(v, e, 0, 100) for e, v in comp.items()) > 100 + 1e-8:
                raise ValueError('첨가물 조성 합계가 100%를 초과합니다.')
            rec = addition['recovery']
            bulk = numeric(addition['bulk'], 'bulk recovery', 0, 1)
            gains = {}
            for e, pct in comp.items():
                gains[e] = kg * pct / 100 * numeric(rec[e], f'{e} recovery', 0, 1)
            if sum(gains.values()) > kg * bulk + 1e-8:
                raise ValueError('원소 회수질량이 bulk 회수질량보다 큽니다.')
            mass += kg * bulk
            for e in amounts:
                if amounts[e] is not None:
                    amounts[e] += gains.get(e, 0)
            if gains.get('Al', 0):
                last_al_time = elapsed
                if amounts.get('O') is not None and amounts.get('Al') is not None:
                    reacted = deoxidize(mass, amounts['Al'], amounts['O'], temp, config['al_heat'])
                    removed_o = reacted * 48 / 54
                    amounts['Al'] -= reacted
                    amounts['O'] -= removed_o
                    mass -= reacted + removed_o
                    al_reaction += reacted
                else:
                    warnings.append('O 미입력으로 Al 탈산 소비량·발열·개재물 예측이 불완전합니다.')
            added_kg += kg
            total += kg * numeric(addition['price_per_t'], '첨가물 $/t') / 1000
        temp += al_reaction * numeric(config['al_heat'], 'Al 반응 발열 °C/kg')
        temp -= added_kg * numeric(config['addition_chill'], '첨가 냉각 °C/kg')
        snapshot(f'{index} {facility} 첨가 직후')
        # Substeps couple changing mass, C/O consumption and cooling without hiding endpoints.
        count = max(1, math.ceil(duration / 0.25))
        if count > 40000:
            raise ValueError('구간 시간을 10,000분 이하로 입력하세요.')
        dt = duration / count
        for _ in range(count):
            if facility in ('Tank Degasser', 'Recirculating Degasser'):
                if amounts.get('H') is not None:
                    h_eq = 25.6 * math.sqrt(pressure) / 10000
                    k_h = (0.0576 * argon + 0.02) if facility == 'Tank Degasser' else 0.13
                    h_pct = amounts['H'] / mass * 100
                    amounts['H'] = min(h_pct, h_eq + (h_pct - h_eq) * math.exp(-k_h * dt)) * mass / 100
            if settings['k_c']:
                if amounts.get('C') is None or amounts.get('O') is None:
                    warnings.append('C/O가 없어 탈탄 계산을 생략한 구간이 있습니다.')
                else:
                    current_c = amounts['C'] / mass * 100
                    removed_c = max(0, current_c - settings['c_eq']) * mass / 100 * (1 - math.exp(-settings['k_c'] * dt))
                    removed_c = min(removed_c, amounts['O'] * 12 / 16)
                    removed_o = removed_c * 16 / 12
                    amounts['C'] -= removed_c
                    amounts['O'] -= removed_o
                    mass -= removed_c + removed_o
            if settings['k_s'] and amounts.get('S') is not None:
                current_s = amounts['S'] / mass * 100
                removed_s = max(0, current_s - settings['s_eq']) * mass / 100 * (1 - math.exp(-settings['k_s'] * dt))
                amounts['S'] -= removed_s
                mass -= removed_s
            heating = power * 1000 * config['lf_efficiency'] / (60 * (mass / 1000) * 0.22)
            temp += (heating - settings['cooling']) * dt
            if temp <= -273.15:
                raise ValueError('계산 온도가 절대영도 아래입니다. 처리시간과 냉각률을 확인하세요.')
            if amounts.get('Al') is not None and amounts.get('O') is not None:
                reacted = deoxidize(mass, amounts['Al'], amounts['O'], temp, config['al_heat'])
                amounts['Al'] -= reacted
                amounts['O'] -= reacted * 48 / 54
                mass -= reacted * (1 + 48 / 54)
                temp += reacted * config['al_heat']
            total += (settings['per_min'] + argon * settings['argon_price'] + power * electrode_rate
                      + power * 1000 / 60 * config['electricity_price']) * dt
            elapsed += dt
            snapshot(f'{index} {facility}')
        if step.get('clean_minutes') is not None:
            calibrated_clean_time = numeric(step['clean_minutes'], '개재물 실측 필요시간')
    final = trace[-1]
    checks = []
    for e, (lo, hi) in limits.items():
        if lo is None and hi is None:
            continue
        value = final.get(e)
        ok = None if value is None else (lo is None or value >= lo) and (hi is None or value <= hi)
        checks.append(dict(항목=e, 예측=value, 최소=lo, 최대=hi, 판정='미확인' if ok is None else '충족' if ok else '범위 밖'))
    required_time = clock_minutes(config['required_at'])
    tolerance = numeric(config['time_tolerance'], 'Caster time 허용오차')
    aim = numeric(config['aim_temperature'], '주조 목표 온도', 1, 2500)
    temp_tol = numeric(config['temperature_tolerance'], '온도 허용오차')
    for label, value, lo, hi in [('도착시간(분)', elapsed, required_time-tolerance, required_time+tolerance),
                                 ('주조온도(°C)', temp, aim-temp_tol, aim+temp_tol)]:
        checks.append(dict(항목=label, 예측=value, 최소=lo, 최대=hi, 판정='충족' if lo-1e-8 <= value <= hi+1e-8 else '범위 밖'))
    caster_ok = config['caster'] == config['destination']
    checks.append(dict(항목='Caster', 예측=None, 최소=None, 최대=None, 판정='충족' if caster_ok else '범위 밖'))
    clean_ok = (None if calibrated_clean_time is None or last_al_time is None else elapsed-last_al_time >= calibrated_clean_time)
    checks.append(dict(항목='개재물 · '+config['inclusion'], 예측=None, 최소=None, 최대=None,
                       판정='미확인' if clean_ok is None else '충족' if clean_ok else '범위 밖'))
    mixing = numeric(config['mix_minutes'], '최종 혼합시간')
    if last_addition_time is not None and elapsed-last_addition_time < mixing:
        warnings.append('마지막 첨가 이후 지정 혼합시간이 부족합니다.')
        checks.append(dict(항목='혼합시간', 예측=elapsed-last_addition_time, 최소=mixing, 최대=None, 판정='범위 밖'))
    checks.append(dict(항목='공정 중 액상선', 예측=None, 최소=None, 최대=None,
                       판정='범위 밖' if frozen else '미확인' if any(r['액상선'] is None for r in trace) else '충족'))
    return dict(trace=trace, checks=checks, warnings=list(dict.fromkeys(warnings)),
                chemistry={e: final.get(e) for e in amounts},
                process=dict(time_min=elapsed, temp_c=temp, mass_kg=mass),
                cost=dict(total=total, secondary=total-initial_cost, per_t=total/(mass/1000)),
                temperature_change=temp-initial_temp)
