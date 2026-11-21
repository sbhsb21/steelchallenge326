"""Offline candidate evaluation and operator instructions for secondary refining."""
import random
import su_planner as P

STATION = {"stir": "교반 스테이션(Stirring Station)", "tank": "탱크 탈가스(Tank Degasser)", "cas": "CAS-OB",
           "furnace": "래들 퍼니스(Ladle Furnace)", "degasser": "RH 탈가스(Recirculating Degasser)",
           "slabCaster": "슬래브 연주기(Slab Caster)", "bloomCaster": "블룸 연주기(Bloom Caster)", "billetCaster": "빌릿 연주기(Billet Caster)"}
GRADE_KO = {"con": "구조용(Construction)", "ulc": "극저탄소(ULC)", "lps": "라인파이프(Linepipe)", "eng": "기계구조용(Engineering, 4140)"}
INCL = {10: "매우 낮음(Very low)", 20: "낮음(Low)", 40: "보통(Moderate)", 70: "높음(High)", 100: "매우 높음(Very high)"}
def hms(s):
    s = int(round(s))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def robust_start(start, plan, spec, n=30, seed=1, uncertain=True):
    """시작값이 요약 화면 값이면 무게 ±5 %, 온도 ±5 ℃, 조성 ±5 %(시뮬레이터와 같은 무작위)와 조작 시각 ±5초로 n번 돌려 통과율."""
    rng = random.Random(seed)
    oks, fails, Ts = 0, {}, []
    for _ in range(n):
        st = dict(start)
        if uncertain:
            st["mass"] = start["mass"] * rng.uniform(.95, 1.05)
            st["T"] = start["T"] + rng.uniform(-5, 5)
            st["comp"] = {el: (v * rng.uniform(.95, 1.05) if el != "Fe" else v) for el, v in start["comp"].items()}
        e, info = P.simulate(st, plan, rng=random.Random(rng.random()), jitter=5)
        ok, res = P.judge(e, st, spec, info["end"])
        oks += ok
        Ts.append(e.T)
        for k, v in res.items():
            if not v:
                fails[k] = fails.get(k, 0) + 1
    return oks / n, fails, (min(Ts), max(Ts))


def timeline(start, plan):
    """su_planner.simulate와 같은 시각 계산으로 조작 목록(초, 설비, 조작, 값, 메모)."""
    T = P.TIMING
    rows = []
    rows.append((0, "BOF", "합성 슬래그 설정(출강 전)", f"{plan.get('slag_kg', 0):.0f} kg, CaO {plan.get('cao', 45)} %", "슬래그 0이면 그대로 둠"))
    t = start.get("tap_at", P.TAP_AT)
    for code, kg in plan.get("tap_adds", []):
        rows.append((t, "BOF", "첨가(출강 중)", f"{code} {kg:.0f} kg", ""))
    rows.append((t, "BOF", "출강 시작(Start tapping)", "", "계획한 게임 경과시각에 누르기"))
    t += T["tap"]
    where = "BOF"
    for seg in plan["segments"]:
        st = seg["st"]
        rows.append((t, STATION[st], "이동(Go to)", "", f"출강·이전 설비가 끝나면 바로"))
        t += P.move_steps(where, st)
        where = st
        t_arr = t
        for k, v in seg.get("set", {}).items():
            name = {"ar": "Ar 유량", "mw": "전력(MW)", "aim": "목표 진공도"}.get(k, k)
            rows.append((t_arr, STATION[st], f"{name} 설정", f"{v}", "도착하면 켜기 전에"))
        for off, code, kg in seg.get("pre_adds", []):
            rows.append((t_arr + off, STATION[st], "첨가(켜기 전, 뚜껑 닫기 전)", f"{code} {kg:.0f} kg", ""))
        t_click = t_arr + max([o for o, _, _ in seg.get("pre_adds", [])] + [0])
        t0 = t_click
        if seg.get("on", True):
            t0 = t_click + T["on"][st]
            rows.append((t_click, STATION[st], "켜기(Start)", "", f"실제 처리 시작 ≈ {hms(t0)}(장치 내려오는 시간)"))
        for off, code, kg in seg.get("adds", []):
            rows.append((t0 + off, STATION[st], "첨가", f"{code} {kg:.0f} kg", ""))
        for off, key, val in seg.get("changes", []):
            if key == "mw":
                rows.append((t0 + off, STATION[st], "전력 변경", f"{val} MW", "heat" if val == 0 else ""))
            elif key == "o2":
                rows.append((t0 + off, STATION[st], "산소 취입 " + ("켬" if val else "끔"), "", ""))
            else:
                rows.append((t0 + off, STATION[st], f"{key} 변경", f"{val}", ""))
        t_end = t0 + seg["dur"]
        if seg.get("on", True):
            rows.append((t_end, STATION[st], "끄기(Stop)", "", "LF는 이 시각이 '목표 도착'에 맞춘 시각" if st == "furnace" and seg is plan["segments"][-1] else ""))
            t = t_end + T["off"][st]
        else:
            t = t_end
    caster = plan["caster"]
    rows.append((t, STATION[caster], "이동(Go to)", "", ""))
    t_end = t + P.move_steps(where, caster)
    rows.append((t_end, STATION[caster], "도착 = 주조(끝)", "", f"목표 {hms(start['op_time'] * 60)}"))
    return rows



def search(start, spec, uncertain, progress=None):
    settings = [
        ("기준", {}), ("슬래그 3 t", {"slag": 3000}),
        ("Ar 0.8", {"lf_ar": .8}), ("슬래그 3 t · Ar 0.8", {"slag": 3000, "lf_ar": .8}),
        ("Ar 0.3", {"lf_ar": .3}), ("Ar 0.3 · 하한 접근 40%", {"lf_ar": .3, "tilt": .4}),
        ("Ar 0.3 · 하한 접근 55%", {"lf_ar": .3, "tilt": .55}),
        ("Ar 0.3 · 하한 접근 70%", {"lf_ar": .3, "tilt": .7}), ("하한 접근 40%", {"tilt": .4}),
    ]
    rows, best, best_key = [], None, None
    for index, (label, options) in enumerate(settings):
        if progress:
            progress(index / len(settings), text=f"{index + 1}/{len(settings)} · {label}")
        try:
            plan, engine, info, ok, checks = P.plan_grade(start, spec, **options)
        except (ArithmeticError, ValueError) as error:
            rows.append({"후보": label, "기본 판정": "계산 실패", "기본 미달": str(error)})
            continue
        for _ in range(start.get("analyses", 0)):
            engine.analysis()
        rate, fails, _ = robust_start(start, plan, spec, uncertain=uncertain)
        passed = round(rate * 30)
        cost = engine.cost_per_t()
        rows.append({"후보": label, "기본 판정": "합격" if ok else "미달", "모의 합격 / 30": passed,
                     "예상 $/t": round(cost, 3), "기본 미달": ", ".join(k for k,v in checks.items() if not v)})
        if not ok:
            continue
        key = (passed < 27, -passed if passed < 27 else 0, cost)
        if best_key is None or key < best_key:
            best_key = key
            best = dict(label=label, passed=passed, cost=cost, total=engine.total_cost(), mass=engine.mass,
                        temperature=engine.T, arrival=hms(info["end"]), inclusion=engine.inclusion_level(),
                        schedule=[[hms(t), station, action, value, note] for t,station,action,value,note in timeline(start,plan)],
                        chemistry={el:engine.pct(el) for el in spec["elements"]}, failures=fails)
    return {"candidates": rows, "best": best}
