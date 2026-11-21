"""SteelUniversity 2차정련(Secondary Steelmaking, MAN0004) 시뮬레이터 계산을 그대로 옮긴 엔진.

원본: 시뮬레이터 페이지 스크립트(main.min.9b238b86.js). 시뮬레이터는 '초가 바뀔 때마다' 등록된 순서대로 계산을 한 번씩 합니다.
  1) ladle.calc: 탈탄(kC) → 산소 흡수(RH) → 탈수소(kH) → 탈황(슬래그가 있을 때)
  2) thermo: 자연 냉각 0.5 ℃/분
  3) 탈산 평형(시작 때 등록): Si → Mn → Al(개재물 생성·부상) → C
  4) 켜진 설비 계산(켠 순서): 교반(SS) / 탱크 탈가스(TD) / CAS-OB / LF / RH
  5) 넣은 첨가제 녹이기(넣은 순서)
무작위: 시작 질량 ±5 %, 온도 ±5 ℃, 성분 ±5 %, 목표 시각, 첨가 회수율 배수(교반 스테이션·출강 중 0.65~0.75, 그 밖 0.99~1.01).
"""
from __future__ import annotations

import math
import random

# 성분: 이름 → (원자량, 회수율 %, 액상선 계수)
COMP = {"C": (12, 95, None), "Mn": (55, 95, -4), "Si": (28, 98, -14), "S": (32, 80, -45), "P": (31, 98, -30),
        "Cr": (52, 99, -1.5), "Al": (27, 90, -2.5), "B": (10.8, 100, None), "Ni": (58.7, 100, -3.5), "Cu": (63.5, 100, None),
        "Sn": (118.7, 100, None), "Nb": (93, 100, None), "Ti": (47.8, 90, None), "V": (51, 100, -4), "Mo": (96, 100, -5),
        "As": (75, 100, None), "Ca": (40, 15, None), "O": (16, 100, None), "N": (14, 40, None), "H": (1, 100, None), "Fe": (56, 100, None)}

# 강종: 래들 질량(kg), 출강온도, 목표 온도(aim,min,max), 목표 시간(min,max 분), 개재물 한도, 주조기, 성분 [이름, tap, aim, min, max]
TUNDISH = {"slab": ("SlabCaster", 250000.0), "bloom": ("BloomCaster", 100000.0), "billet": ("BilletCaster", 100000.0)}
GRADES = {
    "con": dict(caster="bloom", tap_T=1650, T=(1535, 1530, 1540), time=(60, 80), incl=40, spec=[
        ("C", .05, .145, .13, .16), ("Si", 0, .2, .15, .25), ("Mn", .12, 1.4, 1.3, 1.5), ("P", .017, None, None, .025),
        ("S", .015, None, None, .02), ("Cr", .01, None, None, .1), ("Al", 0, .035, .025, .045), ("B", 1e-4, None, None, 5e-4),
        ("Ni", .01, None, None, .15), ("Nb", 0, .042, .035, .05), ("Ti", 0, None, None, .01), ("V", 0, None, None, .01),
        ("Mo", .002, None, None, .04), ("Ca", 0, None, None, None), ("N", .003, None, None, .005), ("H", 3e-4, None, None, 5e-4),
        ("O", .04, None, None, .001)]),
    "ulc": dict(caster="slab", tap_T=1685, T=(1560, 1555, 1565), time=(70, 90), incl=10, spec=[
        ("C", .03, .003, .002, .004), ("Si", 0, .21, .15, .25), ("Mn", .1, .75, .65, .85), ("P", .008, .065, .055, .075),
        ("S", .01, None, None, .012), ("Cr", .01, None, None, .05), ("Al", 0, .045, .03, .055), ("B", 1e-4, .003, .001, .005),
        ("Ni", .01, None, None, .08), ("Nb", 0, .02, .01, .03), ("Ti", 0, .03, .02, .035), ("V", 0, None, None, None),
        ("Mo", .002, None, None, .01), ("Ca", 0, None, None, None), ("N", .002, None, None, .004), ("H", 3e-4, None, None, 5e-4),
        ("O", .06, None, None, 5e-4)]),
    "lps": dict(caster="slab", tap_T=1675, T=(1540, 1527, 1552), time=(80, 120), incl=10, spec=[
        ("C", .05, .07, .06, .08), ("Si", 0, .18, .13, .23), ("Mn", .12, 1.05, 1, 1.1), ("P", .007, None, None, .008),
        ("S", .008, None, None, .003), ("Cr", .01, None, None, .06), ("Al", 0, .03, .025, .035), ("B", 1e-4, None, None, .005),
        ("Ni", .01, None, None, .05), ("Nb", 0, .015, .012, .018), ("Ti", 0, None, None, .01), ("V", 0, None, None, .01),
        ("Mo", .002, None, None, .01), ("Ca", 0, None, .001, .005), ("N", .003, None, None, .0045), ("H", 4e-4, None, None, 2e-4),
        ("O", .04, None, None, 7e-4)]),
    "eng": dict(caster="billet", tap_T=1675, T=(1568, 1558, 1578), time=(70, 120), incl=20, spec=[
        ("C", .13, None, .38, .45), ("Si", .006, None, None, .4), ("Mn", .12, None, .6, .9), ("P", .007, None, None, .035),
        ("S", .008, None, None, .035), ("Cr", .01, None, .9, 1.2), ("Al", 0, None, .015, .03), ("B", 1e-4, None, None, .005),
        ("Ni", .1, None, None, .3), ("Nb", 0, None, None, None), ("Ti", 0, None, None, None), ("V", 0, None, None, .01),
        ("Mo", .002, None, .15, .3), ("Ca", 0, None, None, None), ("N", .003, None, None, .005), ("H", 4e-4, None, None, 25e-5),
        ("O", .04, None, None, 5e-4)]),
}
CASTING_VARIANCE = {"student": 5, "worker": 3}
INCLUSION = [(10, "Very low"), (20, "Low"), (40, "Moderate"), (70, "High"), (100, "Very high")]

# 첨가제: 코드 → (화면 순서 0~20, 형태 반응속도, 온도 변화 ℃/t, 단가 $/t(화면값 = 1.4×기본), 성분 [(원소, wt%)], 한 번 최대 kg)
POWDER, BEADS, WIRE, PEBBLES = 1600, 32, 21, 5
_ADD = [
    ("C 98", POWDER, 0, 200, [("C", 98)]), ("HCFeMn", PEBBLES, -6.7, 350, [("Mn", 76.5), ("C", 6.7), ("Si", 1), ("S", .03), ("P", .3)]),
    ("LCFeMn", PEBBLES, -6, 600, [("Mn", 81.5), ("C", .85), ("Si", .5), ("S", .1), ("P", .25)]), ("FeMn HP", PEBBLES, -6, 1300, [("Mn", 49)]),
    ("SiMn", PEBBLES, 1.08, 400, [("Mn", 60), ("Si", 30), ("C", .5), ("P", .08), ("S", .08)]),
    ("FeSi75", PEBBLES, 1.08, 550, [("Si", 75), ("Al", 1.5), ("C", .15), ("Mn", .5), ("Ca", .2)]),
    ("FeSi75 HP", PEBBLES, 1.08, 600, [("Si", 75), ("Al", .06), ("Mn", .2), ("C", .02)]),
    ("FeSi45", PEBBLES, 1.08, 450, [("Si", 45), ("Al", 2), ("C", .2), ("Mn", 1), ("Cr", .5)]),
    ("HCFeCr", PEBBLES, -5.8, 900, [("Cr", 66.5), ("C", 6.4)]),
    ("Al wire", WIRE, 0, 1500, [("Al", 98)]), ("Al pebbles", PEBBLES, 0, 1000, [("Al", 98)]),
    ("FeB", PEBBLES, 0, 2700, [("B", 20), ("Si", 3), ("P", .2)]), ("FeMo", PEBBLES, 0, 12000, [("Mo", 70)]),
    ("FeNb", PEBBLES, 0, 7000, [("Nb", 63), ("Al", 2), ("Si", 2), ("Ti", 2), ("C", .2), ("S", .2), ("P", .2)]),
    ("FeV", PEBBLES, 0, 6000, [("V", 50)]), ("FeP", PEBBLES, 0, 450, [("P", 26), ("Si", 1.5)]),
    ("Ni", PEBBLES, 0, 5000, [("Ni", 99)]), ("Ti", PEBBLES, 0, 2000, [("Ti", 99)]),
    ("CaSi powder", POWDER, 0, 870, [("Si", 50), ("Ca", 50)]), ("CaSi wire", WIRE, 0, 1100, [("Si", 50), ("Ca", 50)]),
    ("Ca beads", BEADS, 0, 4000, [("Ca", 100)]),
]
ADDITIVES = {}
for _i, (_code, _rate, _dT, _cost, _cmp) in enumerate(_ADD):
    _fe = 100 - sum(w for _, w in _cmp)
    ADDITIVES[_code] = dict(index=_i, rate=_rate, dT=_dT, unit_cost=1.4 * _cost, comp=_cmp + ([("Fe", _fe)] if _fe > 0 else []),
                            max_kg=5000 if _i < 9 else 500)

SLAG_COST, EXCHANGE, AR_UNIT = 210.0, 1.42, 0.6
TAP_DROP = 60.0
ANALYSIS_COST, ANALYSIS_LAG_S = 40.0, 180


class Engine:
    """한 래들의 상태와 초 단위 계산. step()이 시뮬레이터의 '초 바뀜' 한 번."""

    def __init__(self, grade: str, *, mass=None, T=None, comp=None, op_time_min=None, level="student", rng=None):
        self.rng = rng or random.Random()
        g = GRADES[grade]
        self.grade, self.g, self.level = grade, g, level
        ladle_mass = TUNDISH[g["caster"]][1]
        r = self.rng.random
        self.mass = float(mass) if mass is not None else ladle_mass * (.95 + .1 * r())
        self.T = float(T) if T is not None else g["tap_T"] + (2 * r() * 5 - 5)
        self.m = {}                                        # 성분 질량 kg
        fe_kg = self.mass
        for name, tap, *_ in g["spec"]:
            if comp is not None and name in comp:
                pct = comp[name]
            else:
                var = tap * .05
                pct = tap + (2 * var * r() - var)
            self.m[name] = pct * self.mass / 100
            fe_kg -= self.mass * tap / 100                 # 원본: Fe는 공칭 tap으로 계산(무작위 반영 안 함)
        self.m["Fe"] = (comp or {}).get("Fe", 100 * fe_kg / self.mass) * self.mass / 100
        self.S_init = self.m["S"]
        if T is None:
            self.T -= .5 / 60                              # init 때 thermo.refreshDisplay 한 번(T를 읽어 넣으면 이미 반영된 값)
        self.op_time = op_time_min if op_time_min is not None else round(g["time"][0] + r() * (g["time"][1] - g["time"][0]))
        self.slag, self.slag_cao = 0.0, 44.0
        self.height, self.ar, self.pressure = 3.0, 0.0, 1.0
        self.kC = self.kH = self.O_pick = 0.0
        self.where = "BOF"                                 # 첨가 회수율(교반·BOF 0.65~0.75)
        self.t = 0                                         # 시뮬레이터 초
        self.cost = {"slag": 0.0, "additions": 0.0, "analysis": 0.0, "SS": 0.0, "TD": 0.0, "CASOB": 0.0, "LF": 0.0, "RD": 0.0}
        self.units = {}                                    # 켜진 설비
        self.handlers = []                                 # 탈산 뒤에 등록 순서대로: ("unit", 이름) / ("kin", 첨가 상태)
        self.unit_set = {"SS": {"ar": 0.0}, "TD": {"ar": 0.0, "aim": 1.0, "p": 1.0}, "CAS": {"ar": 0.0, "o2": False},
                         "LF": {"ar": 0.0, "mw": 0.0}, "RH": {"o2": False}}
        self.kinetics = []                                 # 녹는 중인 첨가제
        self.deox = [self._deox("Si", 1, 2, 3e4, -11.5, 0), self._deox("Mn", 1, 1, 12440, -5.33, 0),
                     self._deox("Al", 2, 3, 62780, -20.54, 12), self._deox("C", 1, 1, 1168, 2.07, 0)]
        self.al_k, self.al_l = 0.0, self.height            # 개재물: 생성 Al2O3, 부상 누적
        self.log = []
        self.frozen = False

    # ---------------------------------------------------------------- 보조
    def pct(self, el):
        return 100 * self.m[el] / self.mass

    def add_mass(self, el, kg):
        self.m[el] += kg
        self.mass += kg

    def stir_power(self, flow, pressure):
        e = (self.mass + self.slag) / 1e3
        return flow * (self.T + 273) / e * 14.23 * math.log10((1 + self.height) / (1.48 * pressure))

    def _deox(self, el, x, y, A, B, heat):
        o = {"el": el, "x": x, "y": y, "A": A, "B": B, "heat": heat}
        o["m"] = self.pct(el)
        o["p"] = self._eq_o(o, o["m"])
        return o

    def _eq_o(self, o, mp):
        if mp == 0:
            return math.inf
        K = 10 ** (o["A"] / (self.T + 273) + o["B"])
        return (1 / (mp ** o["x"] * K)) ** (1 / o["y"])

    # ---------------------------------------------------------------- 조작
    def set_slag(self, kg=None, cao=None):
        """합성 슬래그 슬라이더(원본: 움직이는 즉시 탈황 계산에 쓰임, 비용은 출강 때)."""
        if kg is not None:
            self.slag = float(kg)
        if cao is not None:
            self.slag_cao = float(cao)

    def tap(self, slag_kg=None, cao=None):
        """출강 시작: 합성 슬래그 비용. 온도 강하(−60 ℃)는 출강 애니메이션이 끝날 때 tap_done()."""
        self.set_slag(slag_kg, cao)
        self.cost["slag"] += SLAG_COST * self.slag / 1e3
        self.initial_mass = self.mass

    def tap_done(self):
        self.T -= TAP_DROP

    def goto(self, where):
        """이동 시작 순간 위치가 바뀜(원본 gotoStation). where: BOF/stir/tank/cas/furnace/degasser/caster."""
        self.where = where

    def add(self, code, kg, mult=None):
        a = ADDITIVES[code]
        if mult is None:
            lo, hi = (.65, .75) if self.where in ("stir", "BOF") else (.99, 1.01)
            mult = lo + self.rng.random() * (hi - lo)
        if any(el == "Al" and w > 50 for el, w in a["comp"]):
            self.al_k, self.al_l = 0.0, 0.0
        kn = {"a": a, "kg": float(kg), "done": 0.0, "mult": mult, "code": code}
        self.kinetics.append(kn)
        self.handlers.append(("kin", kn))
        self.cost["additions"] += a["unit_cost"] * kg / 1e3

    def analysis(self):
        self.cost["analysis"] += ANALYSIS_COST
        return {"t_ready": self.t + ANALYSIS_LAG_S, "comp": {el: self.pct(el) for el in self.m}, "T": self.T}

    def set_unit(self, unit, **kw):
        self.unit_set[unit].update(kw)

    def unit_on(self, unit):
        """설비 계산 시작(원본: 켜는 애니메이션이 끝난 뒤 start()). unit: SS/TD/CAS/LF/RH."""
        if unit in self.units:
            return
        self.units[unit] = True
        self.handlers.append(("unit", unit))
        if unit == "TD":
            self.kC = .1
        if unit == "RH":
            self.pressure, self.kH = .0015, .13

    def unit_off(self, unit):
        if self.units.pop(unit, None) is None:
            return
        self.handlers = [h for h in self.handlers if h != ("unit", unit)]
        if unit == "SS":
            self.ar = 0.0
        elif unit == "TD":
            self.kC = self.kH = 0.0
            self.pressure = 1.0
            self.unit_set["TD"]["p"] = 1.0
            self.ar = 0.0
        elif unit == "LF":
            self.ar = 0.0
        elif unit == "RH":
            self.pressure, self.kC, self.kH, self.O_pick = 1.0, 0.0, 0.0, 0.0
        # CAS: 원본은 끌 때 Ar 유량을 0으로 되돌리지 않음(다음 설비가 바꿀 때까지 교반 유지)

    # ---------------------------------------------------------------- 1초
    def step(self):
        self.t += 1
        a = 1 / 60
        # 1) ladle.calc
        e = .0015 * self.mass / 100
        f = self.m["O"] / self.m["C"] if self.m["C"] else math.inf
        d = min(1.0, .5 * f)
        g = self.m["C"]
        b = (e + (g - e) * math.exp(-self.kC * a) - g) * d
        self.add_mass("C", b)
        self.add_mass("O", b * 16 / 12)
        self.add_mass("O", self.O_pick / 100 * a * self.mass)
        Tk = self.T + 273
        heq = 1e-6 * 10 ** (-1900 / Tk + 2.423) * math.sqrt(self.pressure) * self.mass
        hg = self.m["H"]
        heq = min(heq, hg)
        self.add_mass("H", heq + (hg - heq) * math.exp(-self.kH * a) - hg)
        if self.slag > 0:
            dd = 163.5 * (self.slag_cao - 40) / 15 + 6.5
            ee = .005 + (self.pct("Al") - .005) / 2.2
            ff = 3.2 - ((self.T - 1500) / 125 + 1)
            gg = dd * ee * ff / .005
            j = self.mass / self.slag / gg
            k = self.S_init * j / (1 + j)
            l = self.stir_power(self.ar, self.pressure)
            kS = 0.0 if l == 0 else max(.013 * l ** .25, 8e-6 * l ** 2.1)
            o = self.m["S"]
            p = k + (o - k) * math.exp(-kS * a * (1 + j))
            q = p - o
            r = max(q * 54 / 96, -self.m["Al"])
            q = r * 96 / 54
            self.add_mass("S", q)
            self.add_mass("Al", r)
        # 2) thermo: 자연 냉각(+ 액상선 확인)
        liq = sum(100 * self.m[el] / self.mass * (COMP[el][2] or 0) for el in self.m)
        cp = 100 * self.m["C"] / self.mass
        tl = (1537 - 73.1 * cp if cp < .5 else 1531 - 61.5 * cp) + liq
        if tl >= self.T:
            self.frozen = True
        self.T -= .5 / 60
        # 3) 탈산 평형 Si → Mn → Al → C
        for o in self.deox:
            o["p"] = self._eq_o(o, o["m"])
            o["m"] = self.pct(o["el"])
            n = self.pct("O")
            if o["m"] > 0 and n > o["p"]:
                bb = (o["p"] - n) / 5
                at = COMP[o["el"]][0]
                ee = bb * o["x"] * at / (o["y"] * 16)
                if -ee > o["m"]:
                    ee = -o["m"]
                    bb = ee * (o["y"] * 16) / (o["x"] * at)
                fo, hel = bb * self.mass / 100, ee * self.mass / 100
                self.add_mass("O", fo)
                self.add_mass(o["el"], hel)
                self.T += o["heat"] * -hel / 100
                if o["el"] == "Al":
                    self.al_k += -(fo + hel)
            if o["el"] == "Al":
                h = 0.0 if self.al_k == 0 else 15 * self.al_k ** (1 / 3) * 1e-6
                v = 384180 * h * h * (1 + 8 * self.stir_power(self.ar, self.pressure) / 160)
                self.al_l += v
        # 4·5) 켜진 설비와 녹는 첨가제 — 원본처럼 등록(켬·투입) 순서대로
        for kind, obj in list(self.handlers):
            if kind == "kin":
                self._dissolve(obj)
                continue
            u = obj
            s = self.unit_set[u]
            if u == "SS":
                self.ar = s["ar"]
                self.T -= 1.5 * a * s["ar"]
                self.cost["SS"] += (4 * EXCHANGE + AR_UNIT * s["ar"]) * a
            elif u == "TD":
                self.ar = s["ar"]
                pr, aim = s["p"], s["aim"]
                pr = aim + (pr - aim) * math.exp(-.8 * a) if pr > aim else aim - (aim - pr) * math.exp(-.8 * a)
                s["p"] = self.pressure = pr
                self.kH = .0576 * s["ar"] + .02
                self.T -= 1.03 * a * s["ar"]
                self.cost["TD"] += a * (10 + .6 * s["ar"])
            elif u == "CAS":
                if s["o2"]:
                    self.add_mass("O", 10 * a)
                self.ar = s["ar"]
                self.T -= 1.5 * a * s["ar"]
                self.cost["CASOB"] += a * (AR_UNIT * s["ar"] + 30)
            elif u == "LF":
                self.ar = s["ar"]
                self.T += .55 * s["mw"] * 1e3 * a / 60 / .22 / (self.mass / 1e3) - 1.5 * a * s["ar"]
                self.cost["LF"] += a * (1e3 * (s["mw"] / 20) / 60 + 5.87 * (s["mw"] / 20) + AR_UNIT * s["ar"])
            elif u == "RH":
                vol = self.mass / 7200
                ee = 6e4 / 7200
                self.kC = ee / vol * (18 / (ee + 18))
                self.O_pick = .003 if s["o2"] else 8e-4
                self.T -= a * (1.03 - .5)
                self.cost["RD"] += 163.75 * EXCHANGE / 15 * a

    def _dissolve(self, kn):
        if kn["done"] >= kn["kg"]:
            self.kinetics.remove(kn)
            self.handlers = [h for h in self.handlers if not (h[0] == "kin" and h[1] is kn)]
            return
        mfac = 1 + 8 * self.stir_power(self.ar, self.pressure) / 160
        q = min(.8 * mfac * kn["a"]["rate"], kn["kg"] - kn["done"])
        kn["done"] += q
        for el, w in kn["a"]["comp"]:
            self.add_mass(el, q * (w / 100) * (kn["mult"] * COMP[el][1] / 100))
        self.T += kn["a"]["dT"] * q / 1e3

    # ---------------------------------------------------------------- 결과
    def inclusion_level(self):
        return 100 * (1 - min(self.al_l / self.height, 1.0))

    def total_cost(self):
        return sum(self.cost.values())

    def cost_per_t(self):
        return 1e3 * self.total_cost() / self.mass

    def result(self, caster=None, end_t=None):
        t = self.t if end_t is None else end_t
        g, var = self.g, CASTING_VARIANCE[self.level]
        comp = {el: self.pct(el) for el in self.m}
        checks = {}
        for name, _tap, _aim, lo, hi in g["spec"]:
            v = comp[name]
            checks[name] = not ((hi is not None and v > hi) or (lo is not None and v < lo))
        Tr = round(self.T)
        incl = self.inclusion_level()
        return {"time_min": t / 60, "time_ok": self.op_time - var <= t / 60 <= self.op_time + var,
                "T": self.T, "T_ok": g["T"][1] <= Tr <= g["T"][2], "inclusion": incl, "inclusion_ok": incl <= g["incl"],
                "caster_ok": (caster or g["caster"]) == g["caster"], "comp": comp, "comp_ok": all(checks.values()),
                "comp_checks": checks, "cost_per_t": self.cost_per_t(), "mass": self.mass, "cost": dict(self.cost),
                "frozen": self.frozen}
