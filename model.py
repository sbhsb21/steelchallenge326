import math
import re

import numpy as np
from scipy.optimize import linprog


def number(value, label, minimum=0, maximum=math.inf):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{label}: 숫자를 입력하세요.")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label}: 허용 범위는 {minimum}~{maximum}입니다.")
    return float(value)


def chemistry(values, maximum=100):
    if not isinstance(values, dict):
        raise ValueError("조성은 원소별 JSON 객체여야 합니다.")
    for key, value in values.items():
        if not re.fullmatch(r"[A-Z][a-z]?", key):
            raise ValueError(f"원소 기호를 확인하세요: {key}")
        if value is not None:
            number(value, key, maximum=maximum)
    if maximum == 100 and sum(v for v in values.values() if v is not None) > 100 + 1e-8:
        raise ValueError("조성 합계가 100 wt%를 초과합니다.")
    return values


def volume(items):
    missing = [m["slot"] for m, kg in items if kg and m["density_t_m3"] is None]
    if missing:
        raise ValueError(f"벌크밀도 미입력: {', '.join(missing)}")
    return sum(kg / (1000 * m["density_t_m3"]) for m, kg in items if kg)


def blend(items, elements, compositions=None):
    mass = sum(kg for _, kg in items)
    if mass <= 0:
        raise ValueError("장입량을 입력하세요.")
    compositions = compositions or {}
    result = {}
    for element in elements:
        values = [(compositions.get(m["slot"], m["composition"]).get(element), kg)
                  for m, kg in items if kg]
        result[element] = None if any(v is None for v, _ in values) else (
            sum(v * kg for v, kg in values) / mass)
    return result


def costs(items, operations, steel_kg):
    number(steel_kg, "완성 용강 kg", 1e-9)
    totals = {"raw": 0.0, "eaf": 0.0, "secondary": 0.0, "operations": 0.0}
    for material, kg in items:
        if kg and material["price_per_t"] is None:
            raise ValueError(f"{material['slot']}: 가격 미입력")
        if kg:
            totals[material["stage"]] += kg * material["price_per_t"] / 1000
    totals["operations"] = sum(number(v, key) for key, v in operations.items())
    totals["total"] = sum(totals.values())
    totals["per_t"] = totals["total"] / (steel_kg / 1000)
    return totals


def alloy(steel_kg, base, additions, recovery, facility):
    number(steel_kg, "초기 용강 kg", 1e-9)
    total_kg = steel_kg
    amounts = {key: steel_kg * value / 100 if value is not None else None
               for key, value in base.items()}
    for material, kg in additions:
        if not kg:
            continue
        total_kg += kg * recovered_fraction(material, recovery, facility)
        for element in amounts:
            fraction = contribution(material, element, recovery, facility)
            if fraction is None or amounts[element] is None:
                amounts[element] = None
            else:
                amounts[element] += kg * fraction
    return total_kg, {key: v / total_kg * 100 if v is not None else None
                      for key, v in amounts.items()}


def contribution(material, element, recovery, facility):
    value = material["composition"].get(element)
    if value is None or value == 0:
        return value
    fraction = recovery.get((material["slot"], facility, element))
    return None if fraction is None else value / 100 * fraction


def recovered_fraction(material, recovery, facility):
    key = (material["slot"], facility, "bulk")
    if key not in recovery:
        raise ValueError(f"{material['slot']} / {facility}: bulk 회수율 미입력")
    bulk = recovery[key]
    known = [contribution(material, element, recovery, facility)
             for element in material["composition"]]
    if sum(value for value in known if value is not None) > bulk + 1e-8:
        raise ValueError(f"{material['slot']} / {facility}: 원소 회수 질량이 bulk 회수량을 초과합니다.")
    return bulk


def required_alloy(steel_kg, current_pct, target_pct, material, element, recovery, facility):
    number(steel_kg, "초기 용강 kg", 1e-9)
    current = number(current_pct, "현재 wt%", maximum=100) / 100
    target = number(target_pct, "목표 wt%", maximum=100) / 100
    if current == target:
        return 0.0
    bulk = recovered_fraction(material, recovery, facility)
    gained = contribution(material, element, recovery, facility)
    if gained is None:
        raise ValueError(f"{element}: 합금 조성 또는 회수율 미입력")
    slope = gained - bulk * target
    if slope == 0 or (target - current) / slope < 0:
        raise ValueError("선택한 첨가물로 목표 농도에 도달할 수 없습니다.")
    return steel_kg * (target - current) / slope


def validate_limits(limits, maximum):
    for key, bounds in limits.items():
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError(f"{key}: [Min, Max] 형식이어야 합니다.")
        lo, hi = bounds
        if lo is not None:
            number(lo, key, maximum=maximum)
        if hi is not None:
            number(hi, key, maximum=maximum)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"{key}: Min이 Max보다 큽니다.")


def route_metrics(route):
    metrics = dict(route.get("slag", {}))
    metrics["time_min"] = sum(route[key] for key in ("eaf_min", "move_min", "secondary_min", "mix_min"))
    metrics["temp_c"] = route["tap_c"] - route["move_loss_c"] + route["secondary_delta_c"]
    if route.get("co2_kg") is not None:
        metrics["co2_kg"] = number(route["co2_kg"], "CO₂ kg")
    return metrics


def validate_route(route):
    for key in ("eaf_min", "move_min", "secondary_min", "mix_min", "tap_c", "move_loss_c"):
        number(route[key], key)
    number(route["secondary_delta_c"], "secondary_delta_c", -math.inf)
    number(route["steel_yield"], "steel_yield", 1e-9, 1)
    if not isinstance(route["facility"], str) or not route["facility"].strip():
        raise ValueError("facility를 입력하세요.")
    if not isinstance(route["evidence"], str) or not route["evidence"].strip():
        raise ValueError("공정 경로의 근거를 입력하세요.")
    for key, value in route["operations"].items():
        number(value, key)
    for key, value in route["slag"].items():
        if key in {"time_min", "temp_c"}:
            raise ValueError(f"예약된 슬래그 지표 이름: {key}")
        number(value, key)
    chemistry(route["retention"], maximum=1)
    for stage in ("eaf_kg", "secondary_max_kg"):
        for key, value in route[stage].items():
            number(value, key)


def optimize(materials, recovery, measured, routes, limits, charge_kg, baskets):
    """One continuous cost-per-ton optimum per registered process route."""
    chemistry({element: None for element in limits["chemistry"]})
    number(charge_kg, "장입 kg", 1e-9)
    if not baskets:
        raise ValueError("Basket 용량을 하나 이상 입력하세요.")
    for cap in baskets:
        number(cap, "Basket m³", 1e-9)
    targets = {k: v for k, v in limits["chemistry"].items() if v != [None, None]}
    conditions = {k: v for k, v in limits["process"].items() if v != [None, None]}
    validate_limits(targets, 100)
    validate_limits(conditions, math.inf)
    candidates, rejected = [], {}
    for name, route in routes.items():
        try:
            validate_route(route)
            candidate = solve_route(materials, recovery, measured, route, targets,
                                    conditions, charge_kg, baskets)
            candidates.append(dict(candidate, route=name))
        except (ValueError, KeyError) as error:
            rejected[name] = str(error)
    return sorted(candidates, key=lambda c: c["cost"]["per_t"]), rejected


def solve_route(materials, recovery, measured, route, targets, conditions, charge_kg, baskets):
    metrics = route_metrics(route)
    for key, (lo, hi) in conditions.items():
        if key == "tapping_mass_t":
            continue
        v = metrics.get(key)
        if v is None or (lo is not None and v < lo) or (hi is not None and v > hi):
            raise ValueError(f"{key}: 예측값 {v}, 요구 범위 {[lo, hi]}")
    if any(route["retention"].get(e) is None for e in targets):
        raise ValueError("목표 원소의 EAF 잔존율을 입력하세요.")

    # Each column is kg in one basket, or kg of one secondary additive.
    columns, fixed = [], []
    excluded = {}
    for slot, kg in route["eaf_kg"].items():
        material = materials[slot]
        if material["stage"] != "eaf" or (kg and (not material["name"] or material["price_per_t"] is None)):
            raise ValueError(f"{slot}: EAF 첨가물 또는 가격을 확인하세요.")
        if kg:
            fixed.append((material, kg))
    for material in materials.values():
        slot = material["slot"]
        if not material["name"]:
            continue
        if material["stage"] == "raw":
            composition = measured.get(slot, {})
            fractions = {e: composition.get(e, material["composition"].get(e)) for e in targets}
            if (material["price_per_t"] is None or material["density_t_m3"] is None
                    or any(v is None for v in fractions.values())):
                excluded[slot] = "가격, 벌크밀도 또는 목표 원소 조성 미입력"
                continue
            # A melt sample already reflects melt losses. UI values use route retention.
            mass = route["steel_yield"]
            elements = {e: v / 100 * (mass if e in composition else route["retention"][e])
                        for e, v in fractions.items()}
            for basket in range(len(baskets)):
                columns.append((material, basket, None, mass, elements))
        elif route["secondary_max_kg"].get(slot, 0) > 0 and material["stage"] == "secondary":
            facility = route["facility"]
            elements = {e: contribution(material, e, recovery, facility) for e in targets}
            if (material["price_per_t"] is None or (slot, facility, "bulk") not in recovery
                    or any(v is None for v in elements.values())):
                excluded[slot] = "가격, 조성 또는 설비별 회수율 미입력"
                continue
            mass = recovered_fraction(material, recovery, facility)
            columns.append((material, None, route["secondary_max_kg"][slot], mass, elements))
    for slot in route["secondary_max_kg"]:
        if slot not in materials or materials[slot]["stage"] != "secondary" or not materials[slot]["name"]:
            raise ValueError(f"{slot}: 이름이 등록된 2차정련 첨가물을 선택하세요.")
    if not any(c[1] is not None for c in columns):
        raise ValueError(f"계산 가능한 원재료가 없습니다. 제외 사유 {excluded}")

    fixed_mass = 0.0
    fixed_elements = dict.fromkeys(targets, 0.0)
    for material, kg in fixed:
        facility = "EAF"
        fixed_mass += kg * recovered_fraction(material, recovery, facility)
        for element in targets:
            fraction = contribution(material, element, recovery, facility)
            if fraction is None:
                raise ValueError(f"{material['slot']}: EAF {element} 조성 또는 회수율 미입력")
            fixed_elements[element] += kg * fraction
    fixed_cost = sum(m["price_per_t"] * kg / 1000 for m, kg in fixed)
    fixed_cost += sum(route["operations"].values())
    # Charnes–Cooper: y = kg / finished_kg and z = 1 / finished_kg.
    # Normalizing the finished mass makes cost per finished tonne linear.
    prices = [c[0]["price_per_t"] / 1000 for c in columns] + [fixed_cost]
    mass = np.array([c[3] for c in columns] + [fixed_mass])
    raw_mass = [float(c[1] is not None) for c in columns] + [-charge_kg]
    inequalities = []
    lo, hi = conditions.get("tapping_mass_t", (None, None))
    if lo is not None:
        inequalities.append(np.array([0.0] * len(columns) + [lo * 1000]) - mass)
    if hi is not None:
        inequalities.append(mass - np.array([0.0] * len(columns) + [hi * 1000]))
    for basket, cap in enumerate(baskets):
        inequalities.append([
            1 / (1000 * c[0]["density_t_m3"]) if c[1] == basket else 0 for c in columns
        ] + [-cap])
    for i, column in enumerate(columns):
        if column[2] is not None:
            row = np.zeros(len(columns) + 1)
            row[i], row[-1] = 1, -column[2]
            inequalities.append(row)
    for element, (lo, hi) in targets.items():
        vector = np.array([c[4][element] for c in columns] + [fixed_elements[element]])
        if lo is not None:
            inequalities.append(mass * lo / 100 - vector)
        if hi is not None:
            inequalities.append(vector - mass * hi / 100)
    result = linprog(prices, A_ub=inequalities, b_ub=np.zeros(len(inequalities)),
                     A_eq=[raw_mass, mass], b_eq=[0, 1], bounds=(0, None), method="highs")
    if not result.success or result.x[-1] <= 0:
        raise ValueError(f"모델 해를 찾지 못했습니다. {result.message} 제외 품목 {excluded}")
    amounts = result.x[:-1] / result.x[-1]
    steel_kg = 1 / result.x[-1]
    metrics["tapping_mass_t"] = float(steel_kg / 1000)
    charge = [[] for _ in baskets]
    secondary = []
    for (material, basket, *_), kg in zip(columns, amounts):
        if kg > 1e-8:
            (secondary if basket is None else charge[basket]).append((material, float(kg)))
    ingredients = [item for basket in charge for item in basket] + fixed + secondary
    chemistry_result = {}
    for element in targets:
        kg = fixed_elements[element] + sum(c[4][element] * v for c, v in zip(columns, amounts))
        chemistry_result[element] = float(kg / steel_kg * 100)
    return {
        "cost": costs(ingredients, route["operations"], steel_kg),
        "steel_kg": float(steel_kg), "chemistry": chemistry_result, "process": metrics,
        "baskets": [{"volume_m3": volume(items), "capacity_m3": cap,
                     "materials": quantities(items)} for cap, items in zip(baskets, charge)],
        "eaf": quantities(fixed), "secondary": quantities(secondary), "excluded": excluded,
        "evidence": route["evidence"], "event_log": schedule(charge, fixed, secondary, route),
    }


def quantities(items):
    return [{"slot": m["slot"], "name": m["name"], "kg": kg} for m, kg in items]


def schedule(charge, fixed, secondary, route):
    def line(minutes, description):
        seconds = round(minutes * 60)
        hours, remainder = divmod(seconds, 3600)
        mins, secs = divmod(remainder, 60)
        return f"{hours:02}:{mins:02}:{secs:02} {description}"

    def describe(items):
        return ", ".join(f"{m['name']} {kg:.3f} kg" for m, kg in items)

    tap = route["eaf_min"]
    arrival = tap + route["move_min"]
    return "\n".join(
        [line(0, f"Plan basket {i}: {describe(items)}") for i, items in enumerate(charge, 1) if items]
        + ([line(0, f"Plan EAF additions (timing TBD): {describe(fixed)}")] if fixed else [])
        + [line(tap, "Tap EAF"), line(arrival, f"Transfer to {route['facility']}")]
        + ([line(arrival + route["secondary_min"], f"Add {describe(secondary)}")] if secondary else [])
        + [line(route_metrics(route)["time_min"], "Sample final steel")]
    )

