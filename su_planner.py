"""정확한 엔진(su_engine)으로 2차정련 계획을 계산합니다.

계획 = 출강(합성 슬래그) → 설비 구간들(이동 → 켬 → 첨가·산소·출력 → 끔) → 주조기.
시간 단위는 시뮬레이터 '초 단계'(속도 24에서 잰 이동·켜기 시간 표 TIMING).
"""
from __future__ import annotations

import copy
import math
import random

import numpy as np
from scipy.optimize import linprog

from su_engine import Engine, ADDITIVES, GRADES, COMP, TUNDISH, CASTING_VARIANCE

UNIT = {"stir": "SS", "tank": "TD", "cas": "CAS", "furnace": "LF", "degasser": "RH"}
CASTER = {"bloom": "bloomCaster", "slab": "slabCaster", "billet": "billetCaster"}
# 속도 24에서 잰 값(사본, ±2초). 이동은 래들 위치·크레인 위치에 따라 조금 다름
TIMING = {
    "tap": 343, "tap_drop": 342,
    "on": {"stir": 26, "furnace": 64, "degasser": 52, "cas": 52, "tank": 10},
    "off": {"stir": 26, "furnace": 26, "degasser": 1, "cas": 1, "tank": 10},
    "move": {("BOF", "stir"): 25, ("stir", "furnace"): 223, ("furnace", "degasser"): 187, ("degasser", "cas"): 171,
             ("cas", "tank"): 113, ("tank", "stir"): 178, ("stir", "degasser"): 182, ("degasser", "furnace"): 187,
             ("furnace", "cas"): 182, ("cas", "stir"): 171, ("stir", "tank"): 109, ("tank", "degasser"): 185,
             ("degasser", "tank"): 115, ("tank", "furnace"): 166, ("furnace", "tank"): 97, ("tank", "cas"): 183,
             ("cas", "degasser"): 171, ("degasser", "stir"): 182, ("stir", "cas"): 171, ("cas", "furnace"): 183,
             ("furnace", "stir"): 171,
             ("BOF", "cas"): 235, ("BOF", "degasser"): 247, ("BOF", "furnace"): 235, ("BOF", "tank"): 174,
             ("cas", "billetCaster"): 207, ("cas", "bloomCaster"): 193, ("cas", "slabCaster"): 170,
             ("degasser", "billetCaster"): 199, ("degasser", "bloomCaster"): 194, ("degasser", "slabCaster"): 182,
             ("furnace", "billetCaster"): 176, ("furnace", "bloomCaster"): 200, ("furnace", "slabCaster"): 212,
             ("stir", "billetCaster"): 214, ("stir", "bloomCaster"): 190, ("stir", "slabCaster"): 214,
             ("tank", "billetCaster"): 328, ("tank", "bloomCaster"): 340, ("tank", "slabCaster"): 339},
    "move_default": 200,
}
TAP_AT = 3                       # 시작 후 출강 버튼을 누르는 초


def move_steps(a, b):
    return TIMING["move"].get((a, b), TIMING["move_default"])


def apply_timing(learned):
    """이 PC에서 잰 시간표(매크로가 실행 기록으로 학습, 키 → 최근 측정값 목록)를 반영. 중앙값 사용."""
    import statistics
    for key, vals in (learned or {}).items():
        if not vals:
            continue
        v = int(round(statistics.median(vals)))
        kind, _, rest = key.partition(":")
        if kind == "move":
            a, b = rest.split(">")
            TIMING["move"][(a, b)] = v
        elif kind in ("on", "off"):
            TIMING[kind][rest] = v
        elif kind == "tap":
            TIMING["tap_drop"] = v
            TIMING["tap"] = v + 1


def spec_of(grade, overrides=None):
    """강종 규격 {원소: (lo, aim, hi)} + 온도·시간·개재물·주조기. overrides로 대회 규격을 덮어씀."""
    g = GRADES[grade]
    el = {name: (lo, aim, hi) for name, _tap, aim, lo, hi in g["spec"]}
    sp = {"elements": el, "T": (g["T"][1], g["T"][2]), "incl": g["incl"], "caster": g["caster"]}
    for k, v in (overrides or {}).items():
        if k == "elements":
            sp["elements"].update(v)
        else:
            sp[k] = v
    return sp


# ---------------------------------------------------------------- 계획 실행(엔진)
def simulate(start, plan, rng=None, jitter=0, trace=False):
    """start: {"grade","mass","T","comp","op_time","level"}. 반환: (Engine, 일정 기록)."""
    rng = rng or random.Random(0)
    e = Engine(start["grade"], mass=start["mass"], T=start["T"], comp=start["comp"], op_time_min=start["op_time"],
               level=start.get("level", "student"), rng=rng)
    acts = []                                           # (step, 함수) — step 직전에 적용
    sched = []

    def at(step, fn, label=None):
        if label:
            def fn2(fn=fn, label=label, step=int(step)):
                fn()
                sched.append((step, label, e.T))
            acts.append((int(step), len(acts), fn2))
        else:
            acts.append((int(step), len(acts), fn))

    j = (lambda: rng.randint(-jitter, jitter)) if jitter else (lambda: 0)
    at(0, lambda: e.set_slag(plan.get("slag_kg", 0), plan.get("cao", 45)))
    t = start.get("tap_at", TAP_AT)                   # 실제 출강 버튼을 누를 초(매크로가 계획 후 바로 누름)
    for code, kg in plan.get("tap_adds", []):
        at(t, lambda c=code, k=kg: e.add(c, k))
    at(t, e.tap, "출강")
    at(t + TIMING["tap_drop"], e.tap_done)
    t += TIMING["tap"]
    where = "BOF"
    for seg in plan["segments"]:
        st = seg["st"]
        at(t, lambda s=st: e.goto(s), f"이동 → {st}")
        t += move_steps(where, st) + j()
        where = st
        u = UNIT[st]
        t_arr = t
        for k_, v in seg.get("set", {}).items():
            at(t, lambda k_=k_, v=v, u=u: e.set_unit(u, **{k_: v}))
        for off, code, kg in seg.get("pre_adds", []):       # 켜기 전 첨가(탱크는 뚜껑 닫히면 못 넣음)
            at(t_arr + off, lambda c=code, k=kg: e.add(c, k), f"{st} 첨가 {code} {kg:.0f} kg")
        t0 = t_arr + max([o for o, _, _ in seg.get("pre_adds", [])] + [0])
        if seg.get("on", True):
            t0 = t0 + TIMING["on"][st] + j()
            at(t0, lambda u=u: e.unit_on(u), f"{st} 켬")
        for off, code, kg in seg.get("adds", []):
            at(t0 + off, lambda c=code, k=kg: e.add(c, k), f"{st} 첨가 {code} {kg:.0f} kg")
        for off, key, val in seg.get("changes", []):          # (초, 키, 값): 출력·산소·Ar 변경
            at(t0 + off, lambda k_=key, v=val, u=u: e.set_unit(u, **{k_: v}), f"{st} {key}={val}")
        t_end = t0 + seg["dur"]
        if seg.get("on", True):
            at(t_end, lambda u=u: e.unit_off(u), f"{st} 끔")
            t = t_end + TIMING["off"][st]
        else:
            t = t_end
        for off, code, kg in seg.get("post_adds", []):
            at(t + off, lambda c=code, k=kg: e.add(c, k), f"{st} 첨가 {code} {kg:.0f} kg")
        t += max([o for o, _, _ in seg.get("post_adds", [])] + [0])
    caster = plan["caster"]
    at(t, lambda: e.goto(caster), f"이동 → {caster}")
    t_end = t + move_steps(where, caster) + j()
    acts.sort()
    i = 0
    traj = []
    for s in range(1, t_end + 1):
        while i < len(acts) and acts[i][0] <= s - 1:
            acts[i][2]()
            i += 1
        e.step()
        if trace and s % 30 == 0:
            traj.append((s, e.T))
    return e, {"end": t_end, "sched": sorted(sched, key=lambda x: x[0]), "traj": traj, "T_end": e.T}


def judge(e, start, spec, t_end):
    var = start.get("time_tolerance", CASTING_VARIANCE[start.get("level", "student")])
    res = {}
    for el, (lo, _aim, hi) in spec["elements"].items():
        v = e.pct(el)
        res[el] = not ((hi is not None and v > hi) or (lo is not None and v < lo))
    Tr = round(e.T)
    res["T"] = spec["T"][0] <= Tr <= spec["T"][1]
    res["time"] = start["op_time"] - var <= t_end / 60 <= start["op_time"] + var
    res["incl"] = e.inclusion_level() <= spec["incl"]
    res["liquid"] = not e.frozen
    res["caster"] = e.where == CASTER[spec["caster"]]
    return all(res.values()), res


# ---------------------------------------------------------------- 첨가량(LP + 엔진 보정)
LP_ADDS = [c for c in ADDITIVES]


def _targets(spec, cur, tilt=0.0):
    """원소별 (목표, 하한, 상한) — 두쪽 규격은 가운데(aim 있으면 aim), 한쪽은 여유를 둔 경계.
    tilt > 0(비용 우선, 계산기): 합금으로 올리는 원소는 가운데 대신 하한 쪽(하한 + 폭 × (0.5 − 0.5·tilt))을 겨냥 — 합금을 덜 씀.
    하한이 0인 규격(예: Ti 0~0.05)은 '하한 없음'으로 봄(가운데를 맞추려고 Ti 등을 일부러 넣지 않음). Al은 탈산 때문에 그대로."""
    out = {}
    for el, (lo, aim, hi) in spec["elements"].items():
        if el in ("O", "H", "N", "Fe"):
            continue
        if tilt > 0 and lo is not None and lo <= 0 and hi is not None and el != "Al":
            lo = None
        if lo is not None and hi is not None:
            mid = aim if (aim is not None and tilt <= 0) else lo + (hi - lo) * (.5 - .5 * tilt)
            band = (hi - lo) * (.3 if tilt <= 0 else .15)
            out[el] = (mid, max(lo, mid - band), min(hi, mid + band))
        elif lo is not None:
            out[el] = (lo * 1.15 + 1e-4, lo * 1.08, None)
        elif hi is not None:
            out[el] = (None, None, hi * .85)
    return out


def solve_adds(deficit, mass, spec, cur_after, allow=None, removable=(), tilt=0.0):
    """deficit {원소: 더할 wt%(목표 − 지금)} → 첨가 kg {코드: kg}. 비용 최소, 한쪽 상한 원소는 넘지 않게."""
    tg = _targets(spec, cur_after, tilt)
    codes = [c for c in LP_ADDS if (allow is None or c in allow)]
    els = sorted(tg)
    n = len(codes)
    A = np.zeros((len(els), n))
    for j, c in enumerate(codes):
        for el, w in ADDITIVES[c]["comp"]:
            if el in els:
                A[els.index(el), j] = w / 100 * COMP[el][1] / 100 * 100 / mass
    cost = np.array([ADDITIVES[c]["unit_cost"] / 1e3 for c in codes])
    A_ub, b_ub, A_eq, b_eq = [], [], [], []
    # 목표가 있는 원소는 목표에 맞추는 등식 대신 ±여유 안(부등식) + 목표 이탈 벌점(보조 변수)
    m_slack = 0
    rows = []
    for i, el in enumerate(els):
        aim, lo, hi = tg[el]
        base = cur_after.get(el, 0.0)
        real_hi = spec["elements"][el][2]
        if hi is not None and el not in removable:
            # 여유 상한(규격의 85 %)을 이미 넘었으면 남은 여유(규격 상한까지)의 절반까지만 허용
            room = max(hi - base, 0.5 * (real_hi - base) if real_hi is not None else 0.0)
            A_ub.append(np.r_[A[i], np.zeros(0)]); b_ub.append(max(room, 0.0))
        if lo is not None:
            A_ub.append(np.r_[-A[i], np.zeros(0)]); b_ub.append(-(lo - base))
        if aim is not None:
            rows.append((i, aim - base))
    # 목표 이탈 |A x − d| ≤ s, 비용 + 큰 벌점 × s
    nv = n + len(rows)
    A_ub2 = [np.r_[r, np.zeros(len(rows))] for r in A_ub]
    b_ub2 = list(b_ub)
    for k, (i, d) in enumerate(rows):
        e_k = np.zeros(len(rows)); e_k[k] = 1
        A_ub2.append(np.r_[A[i], -e_k]); b_ub2.append(d)
        A_ub2.append(np.r_[-A[i], -e_k]); b_ub2.append(-d)
    c_vec = np.r_[cost, np.full(len(rows), 1e5)]
    bounds = [(0, None)] * n + [(0, None)] * len(rows)
    r = linprog(c_vec, A_ub=np.array(A_ub2) if A_ub2 else None, b_ub=np.array(b_ub2) if b_ub2 else None, bounds=bounds, method="highs")
    if not r.success:
        return None
    x = r.x[:n]
    return {c: float(v) for c, v in zip(codes, x) if v > 1e-6}


def round_adds(adds):
    out = {}
    for c, kg in adds.items():
        k = round(kg / 10) * 10
        if k >= 10:
            out[c] = float(k)
    return out


def split_max(code, kg):
    m = ADDITIVES[code]["max_kg"]
    parts = []
    while kg > 0:
        parts.append(min(kg, m))
        kg -= parts[-1]
    return parts


# ---------------------------------------------------------------- 계획 만들기
def needs(start, spec):
    c0 = start["comp"]
    el = spec["elements"]
    n = {}
    lo, aim, hi = el["C"]
    tgt_c = aim if aim is not None else (hi if hi is not None else None)
    n["decarb"] = hi is not None and c0["C"] > hi * .9
    lo, aim, hi = el.get("H", (None, None, None))
    n["degas"] = hi is not None and c0["H"] > hi * .8
    lo, aim, hi = el.get("S", (None, None, None))
    n["desulf"] = hi is not None and c0["S"] > hi * .75
    return n


def base_plan(start, spec, route, *, lf_ar=.5, slag=0, cao=55, rh=None):
    grade = start["grade"]
    plan = {"grade": grade, "slag_kg": slag, "cao": cao, "tap_adds": [], "segments": [], "caster": CASTER[spec["caster"]]}
    for st in route:
        if st == "degasser":
            r = rh or {"dur": 600, "o2": 0}
            seg = {"st": "degasser", "on": True, "dur": r["dur"], "changes": []}
            if r.get("o2"):
                seg["changes"] = [(0, "o2", True), (r["o2"], "o2", False)]
            plan["segments"].append(seg)
        elif st == "tank":
            plan["segments"].append({"st": "tank", "on": True, "dur": 600, "set": {"aim": .001, "ar": 1.0}})
        elif st == "furnace":
            plan["segments"].append({"st": "furnace", "on": True, "dur": 900, "set": {"ar": lf_ar, "mw": 0}, "adds": [], "changes": []})
        elif st == "cas":
            # CAS-OB 산소 취입: 녹은 산소를 올려 두면 다음 RH(진공)에서 탈탄이 빨라짐(시작 C가 규격보다 훨씬 높을 때, v0.19.22 계산기)
            plan["segments"].append({"st": "cas", "on": True, "dur": 600, "set": {"ar": 0.3}, "changes": [(0, "o2", True), (595, "o2", False)]})
    return plan


def _lf(plan):
    return next(s for s in plan["segments"] if s["st"] == "furnace")


def set_lf_adds(plan, adds, al_codes=("Al pebbles", "Al wire")):
    """합금 투입: 탱크가 있으면 탱크 도착 직후(뚜껑 닫기 전, 탈황에 Al 필요), 없으면 LF 켬 직후.
    Al(탈산) 먼저 → 60초 뒤 나머지 합금(한 번 최대량 넘으면 나눠서)."""
    tank = next((s_ for s_ in plan["segments"] if s_["st"] == "tank"), None)
    seg = tank or _lf(plan)
    key = "pre_adds" if tank else "adds"
    for s_ in plan["segments"]:
        s_.pop("pre_adds", None) if s_["st"] == "tank" else None
    _lf(plan)["adds"] = []
    seg[key] = []
    for c, kg in sorted(adds.items()):
        off = 0 if c in al_codes else 60
        for part in split_max(c, kg):
            seg[key].append((off, c, part))


def set_lf_heat(plan, heat_s, mw=20):
    seg = _lf(plan)
    seg["changes"] = [ch for ch in seg.get("changes", []) if ch[1] != "mw"]
    seg.setdefault("set", {})["mw"] = 0
    if heat_s > 0:
        seg["changes"] = [(0, "mw", mw), (int(heat_s), "mw", 0)] + seg["changes"]


def fit_time(plan, start, target_end):
    """LF 시간으로 도착 시각을 target_end(초)에 맞춤."""
    seg = _lf(plan)
    _, info = simulate(start, plan)
    seg["dur"] = max(int(seg["dur"] + (target_end - info["end"])), 300)
    return seg["dur"]


def plan_grade(start, spec=None, *, route=None, lf_ar=.5, slag=0, cao=55, verbose=False, tilt=0.0):
    """시작 상태·규격 → (계획, 엔진 결과, 판정). 경로가 없으면 필요 처리로 정함."""
    spec = spec or spec_of(start["grade"])
    nd = needs(start, spec)
    big_decarb = False
    if route is None:
        route = []
        lo_c, aim_c, hi_c = spec["elements"]["C"]
        if nd["decarb"] and hi_c is not None and start["comp"]["C"] - hi_c > .05:
            route.append("cas")                         # RH 산소만으로는 분당 약 0.002 %p — 먼저 CAS-OB로 산소를 올림
            big_decarb = True
        if nd["decarb"]:
            route.append("degasser")
        if nd["desulf"]:
            route.append("tank")                       # 진공 + Ar 교반: 탈황(Al 필요 → 뚜껑 닫기 전에 첨가)·탈수소
        elif nd["degas"] and "degasser" not in route:
            route.append("degasser")
        route.append("furnace")
    rh = None
    if "degasser" in route:
        rh = {"dur": 600, "o2": 0}
    plan = base_plan(start, spec, route, lf_ar=lf_ar, slag=slag if not nd["desulf"] else max(slag, 2000), cao=cao, rh=rh)
    var = CASTING_VARIANCE[start.get("level", "student")]
    target_end = int(start["op_time"] * 60)
    # 1) RH: 탈탄·탈수소가 될 때까지(산소가 모자라면 산소 취입)
    if rh:
        seg = next(s_ for s_ in plan["segments"] if s_["st"] == "degasser")
        lo, aim, hi = spec["elements"]["C"]
        c_t = (aim if aim is not None else hi * .7) if nd["decarb"] else None
        cas = next((s_ for s_ in plan["segments"] if s_["st"] == "cas"), None)
        if cas is not None:
            # 큰 탈탄: 목표 = 규격 가운데보다 조금 아래(합금이 넣는 C 몫), CAS 산소 시간을 2분씩 늘리며 'CAS + RH 30분' 뒤 C로 판단
            c_goal = ((lo + hi) / 2 - .25 * (hi - lo) + .2 * tilt * (hi - lo)) if (lo is not None and hi is not None) else c_t * .8
            c_t = c_goal / .8
            seg["dur"] = 1800
            for m in range(4, 61, 2):
                cas["dur"] = m * 60
                cas["changes"] = [(0, "o2", True), (m * 60 - 5, "o2", False)]
                e, info = simulate(start, dict(plan, segments=[cas, seg]))
                if e.pct("C") <= c_goal:
                    break
        h_hi = spec["elements"].get("H", (None, None, None))[2]
        o2_s = 0
        for dur in range(240, 2400, 60):
            seg["dur"] = dur
            only = dict(plan, segments=([cas] if cas is not None else []) + [seg])   # RH가 끝난 상태로 판단(뒤 공정의 탈탄에 기대지 않음)
            e, info = simulate(start, only)
            ok_c = c_t is None or e.pct("C") <= c_t * .8   # 합금이 넣는 C 몫을 남겨 둠
            ok_h = h_hi is None or e.pct("H") <= h_hi * .7
            if ok_c and ok_h:
                break
            if nd["decarb"] and not ok_c and e.pct("O") < e.pct("C") * 1.5:
                # 산소가 모자라 탈탄이 멈춤 → 산소 취입을 늘림(처음 3분, 그다음 1분씩, RH 시간 − 1분까지).
                # 시작 C가 규격보다 훨씬 높으면(예: 0.44 → 0.18~0.23) 3분으로는 모자랐음(v0.19.22 계산기)
                o2_s = 180 if o2_s == 0 else min(o2_s + 60, dur - 60)
                seg["changes"] = [(0, "o2", True), (o2_s, "o2", False)]
    # 2) 도착 시각 맞추기(LF 시간)
    fit_time(plan, start, target_end)
    tank = next((s_ for s_ in plan["segments"] if s_["st"] == "tank"), None)
    removable = {"S"} if "tank" in route else set()
    # 3) 첨가량: 엔진으로 보정하며 LP 반복
    e0, info = simulate(start, plan)
    cur = {el: e0.pct(el) for el in e0.m}
    adds = {}
    for it in range(6):
        set_lf_adds(plan, round_adds(adds))
        fit_time(plan, start, target_end)
        e, info = simulate(start, plan)
        now = {el: e.pct(el) for el in e.m}
        # 지금 첨가로 얻은 기여 = now − 첨가 없을 때; 다음 LP는 '첨가 없을 때' 기준으로 다시 풀되, 탈산 손실은 now에 반영
        base = {el: now[el] - _contrib(adds, el, e.mass) for el in now}
        new = solve_adds({}, e.mass, spec, base, removable=removable, tilt=tilt)
        if new is None:
            break
        if all(abs(new.get(c, 0) - adds.get(c, 0)) < 5 for c in set(new) | set(adds)):
            adds = new
            break
        adds = new
    set_lf_adds(plan, round_adds(adds))
    if tank:   # 탱크: 탈황·탈수소가 될 때까지(합금·Al을 넣은 뒤)
        s_hi = spec["elements"].get("S", (None, None, None))[2]
        h_hi = spec["elements"].get("H", (None, None, None))[2]
        for dur in range(180, 2400, 60):
            tank["dur"] = dur
            fit_time(plan, start, target_end)
            e, info = simulate(start, plan)
            if (s_hi is None or e.pct("S") <= s_hi * .7) and (h_hi is None or e.pct("H") <= h_hi * .7):
                break
        # 탈황이 Al을 먹으므로 첨가량 한 번 더 맞춤
        for it in range(3):
            e, info = simulate(start, plan)
            now = {el: e.pct(el) for el in e.m}
            base = {el: now[el] - _contrib(adds, el, e.mass) for el in now}
            new = solve_adds({}, e.mass, spec, base, removable=removable, tilt=tilt)
            if new is None:
                break
            adds = new
            set_lf_adds(plan, round_adds(adds))
    # 4) 온도: LF 가열 시간(이분법)으로 목표 온도 가운데
    t_aim = (spec["T"][0] + spec["T"][1]) / 2
    lo_s, hi_s = 0, 1800
    set_lf_heat(plan, 0)
    fit_time(plan, start, target_end)
    e, info = simulate(start, plan)
    if e.T < t_aim:
        for _ in range(16):
            mid = (lo_s + hi_s) / 2
            set_lf_heat(plan, mid)
            e, info = simulate(start, plan)
            if e.T < t_aim:
                lo_s = mid
            else:
                hi_s = mid
        set_lf_heat(plan, round(hi_s))
    else:
        # 너무 뜨거우면 Ar을 늘려 식힘(LF Ar 냉각 1.5 ℃/분 × 유량)
        seg = _lf(plan)
        for ar in [round(x, 2) for x in np.arange(seg["set"]["ar"], 1.001, .05)]:
            seg["set"]["ar"] = ar
            e, info = simulate(start, plan)
            if e.T <= t_aim:
                break
    # 5) 마무리: 시간·온도가 정해진 뒤 첨가량을 다시 맞추고(탈황·탈산 손실 반영) 온도를 다시 맞추기를 두 번
    for cyc in range(2):
        for it in range(5):
            e, info = simulate(start, plan)
            now = {el: e.pct(el) for el in e.m}
            base = {el: now[el] - _contrib(adds, el, e.mass) for el in now}
            new = solve_adds({}, e.mass, spec, base, removable=removable, tilt=tilt)
            if new is None:
                break
            done = all(abs(new.get(c, 0) - adds.get(c, 0)) < 5 for c in set(new) | set(adds))
            adds = new
            set_lf_adds(plan, round_adds(adds))
            if done:
                break
        _fit_heat(plan, start, spec, target_end)
    e, info = simulate(start, plan, trace=True)
    ok, res = judge(e, start, spec, info["end"])
    return plan, e, info, ok, res


def _fit_heat(plan, start, spec, target_end):
    t_aim = (spec["T"][0] + spec["T"][1]) / 2
    seg = _lf(plan)
    set_lf_heat(plan, 0)
    fit_time(plan, start, target_end)
    e, info = simulate(start, plan)
    if e.T < t_aim:
        lo_s, hi_s = 0, max(seg["dur"] - 30, 60)
        for _ in range(16):
            mid = (lo_s + hi_s) / 2
            set_lf_heat(plan, mid)
            e, info = simulate(start, plan)
            if e.T < t_aim:
                lo_s = mid
            else:
                hi_s = mid
        set_lf_heat(plan, round(hi_s))
    else:
        for ar in [round(x, 2) for x in np.arange(seg["set"]["ar"], 1.001, .05)]:
            seg["set"]["ar"] = ar
            e, info = simulate(start, plan)
            if e.T <= t_aim:
                break


def _contrib(adds, el, mass):
    tot = 0.0
    for c, kg in adds.items():
        for e2, w in ADDITIVES[c]["comp"]:
            if e2 == el:
                tot += kg * w / 100 * COMP[el][1] / 100 * 100 / mass
    return tot


def robust(start, plan, spec, n=40, jitter=3, seed=1):
    rng = random.Random(seed)
    oks, fails = 0, {}
    for k in range(n):
        e, info = simulate(start, plan, rng=random.Random(rng.random()), jitter=jitter)
        ok, res = judge(e, start, spec, info["end"])
        oks += ok
        for kk, v in res.items():
            if not v:
                fails[kk] = fails.get(kk, 0) + 1
    return oks / n, fails


def random_start(grade, seed=None, level="student"):
    rng = random.Random(seed)
    e = Engine(grade, rng=rng, level=level)
    return {"grade": grade, "mass": e.mass, "T": e.T, "comp": {el: e.pct(el) for el in e.m}, "op_time": e.op_time, "level": level}


def plan_best(start, spec=None, *, robust_n=20):
    """여러 설정(슬래그·LF Ar)으로 계획해 보고, 판정 통과·흔들림에도 통과하는 것 중 가장 싼 계획."""
    spec = spec or spec_of(start["grade"])
    tries = [dict(), dict(slag=3000), dict(lf_ar=.8), dict(slag=3000, lf_ar=.8), dict(lf_ar=.3)]
    best = None
    for kw in tries:
        try:
            plan, e, info, ok, res = plan_grade(start, spec, **kw)
        except Exception:
            continue
        if not ok:
            continue
        pr, _ = robust(start, plan, spec, n=robust_n, jitter=5)
        key = (pr < .95, e.cost_per_t())
        if best is None or key < best[0]:
            best = (key, plan, e, info, ok, res, pr)
        if pr >= .95 and kw == {}:
            pass
    if best is None:
        plan, e, info, ok, res = plan_grade(start, spec)
        return plan, e, info, ok, res, 0.0
    _, plan, e, info, ok, res, pr = best
    return plan, e, info, ok, res, pr


def plan_explore(start, spec=None, *, rng=None, tries=6):
    """데이터를 다양하게 쌓기 위한 계획: 슬래그·CaO·LF Ar을 무작위로 바꿔 계획(규격은 그대로 맞춤).
    판정을 통과하는 계획이 안 나오면 plan_best로 돌아감. 반환은 plan_best와 같고 마지막에 쓴 설정을 붙임."""
    spec = spec or spec_of(start["grade"])
    rng = rng or random.Random()
    for _ in range(tries):
        kw = dict(slag=rng.choice([0, 500, 1000, 2000, 3000]), cao=rng.choice([40, 45, 50, 55]),
                  lf_ar=rng.choice([.2, .3, .5, .7, 1.0]))
        try:
            plan, e, info, ok, res = plan_grade(start, spec, **kw)
        except Exception:
            continue
        if ok:
            pr, _ = robust(start, plan, spec, n=10, jitter=5)
            if pr >= .9:
                plan["explore"] = kw
                return plan, e, info, ok, res, pr
    out = plan_best(start, spec)
    out[0]["explore"] = None
    return out
