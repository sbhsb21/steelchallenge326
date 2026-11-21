"""Streamlit UI for planning a fresh secondary-refining practice heat."""
import json
import re

import pandas as pd
import streamlit as st

from secondary_clipboard import parse_setup
from secondary_model import clock_minutes
from secondary_search import GRADE_KO, INCL, search
from su_engine import GRADES, TUNDISH
from su_planner import spec_of


def render():
    st.header("2차정련 최저비용 탐색")
    st.caption("출강 전 시작값으로 9개 조업 후보를 비교합니다. 비용은 최종 용강 1 t당 2차정련 비용입니다.")
    grade = st.selectbox("연습 강종", ["eng", "con", "ulc", "lps"], format_func=GRADE_KO.get)
    g = GRADES[grade]
    pasted = st.text_area("이 강종의 Summary와 조성표 붙여넣기 (선택)", key=f"opt_paste_{grade}")
    seed_key = f"opt_seed_{grade}"
    if st.button("시작 조건 적용"):
        try:
            source = re.sub(r"(?i)\bTap(?=\s+Aim\b)", "Current", pasted)
            parsed = parse_setup(re.sub(r"(?<!\d)[~≈]\s*(?=\d)", "", source))
            name = parsed.get("grade", "").lower()
            identifiers = {"eng": ("engineering","4140"), "con": ("construction","beam"),
                           "ulc": ("ultra","ulc","interstitial"), "lps": ("linepipe","line pipe","pipeline")}
            matched = next((k for k, terms in identifiers.items() if any(t in name for t in terms)), None)
            if matched and matched != grade:
                raise ValueError("붙여넣은 강종과 위에서 선택한 강종이 다릅니다.")
            st.session_state[seed_key] = parsed
            st.session_state["opt_revision"] = st.session_state.get("opt_revision", 0) + 1
        except ValueError as error:
            st.error(str(error))
            return
    seed = st.session_state.get(seed_key, {})
    prefix = f"opt_{grade}_{st.session_state.get('opt_revision', 0)}"
    def number(label, field, default, low, high):
        return st.number_input(label, float(low), float(high), float(default), key=prefix+field)
    if seed and len(seed.get("chemistry", {})) != 17:
        st.warning("붙여넣은 조성표가 17개 원소를 모두 포함하지 않습니다. 누락된 행에는 강종 예시가 표시됩니다.")
    st.caption("초깃값은 강종 예시입니다. 이번 회차의 중량·온도·시간·조성과 목표 범위를 확인하세요.")
    uncertain = st.radio("시작값 출처", ["Summary 근삿값", "게임 시작 직후 실측값"], horizontal=True) == "Summary 근삿값"
    left, right = st.columns(2)
    with left:
        mass = number("래들 중량 (kg)", "mass", seed.get("mass_kg", TUNDISH[g["caster"]][1]), 1, 1000000)
        temp = number("출강 온도 (°C)", "T", seed.get("tap_temperature", g["tap_T"]), 1, 2500)
        minutes = number("게임 시작부터 주조까지 (분)", "time",
                         clock_minutes(seed["required_at"]) if "required_at" in seed else sum(g["time"])/2, 1, 240)
        tol = number("주조 시각 허용오차 (±분)", "tol", seed.get("time_tolerance", 3 if "Technical" in seed.get("user_level","") else 5), 0, 60)
    with right:
        nominal = seed.get("aim_temperature", g["T"][0])
        lo = number("주조 최저 온도 (°C)", "lo", nominal-seed.get("temperature_tolerance", g["T"][0]-g["T"][1]), 1, 2500)
        hi = number("주조 최고 온도 (°C)", "hi", nominal+seed.get("temperature_tolerance", g["T"][2]-g["T"][0]), 1, 2500)
        casters = ["billet","bloom","slab"]
        caster = st.selectbox("지정 주조기", casters, index=casters.index(seed.get("caster",g["caster"]).lower().split()[0]),
                              format_func=lambda c:c.title()+" Caster", key=prefix+"caster")
        inc = {"Very low":10, "Low":20, "Moderate":40}.get(seed.get("inclusion"), g["incl"])
        incl = st.selectbox("개재물 상한", list(INCL), index=list(INCL).index(inc), format_func=INCL.get, key=prefix+"incl")
    tap_at = st.number_input("출강 버튼을 누를 게임 경과시간 (초)", 0, 7200, 3, key=prefix+"tap_at")
    analyses = st.number_input("유료 성분 분석 횟수 ($40/회)", 0, 100, 0, key=prefix+"analyses")
    rows = []
    for el, tap, aim, mn, mx in g["spec"]:
        mn,mx = seed.get("limits",{}).get(el,(mn,mx))
        rows.append({"원소":el,"Tap":seed.get("chemistry",{}).get(el,tap),"Min":mn,
                     "Aim":seed.get("aims",{}).get(el,aim),"Max":mx})
    table = st.data_editor(pd.DataFrame(rows), disabled=["원소"], hide_index=True, key=prefix+"table",
                           column_config={c:st.column_config.NumberColumn(format="%.6f", min_value=0.,max_value=100.)
                                          for c in ["Tap","Min","Aim","Max"]})
    comp, limits = {}, {}
    for el,tap,mn,aim,mx in table.itertuples(index=False,name=None):
        comp[el] = None if pd.isna(tap) else float(tap)
        limits[el] = tuple(None if pd.isna(v) else float(v) for v in (mn,aim,mx))
    start = dict(grade=grade,mass=mass,T=temp,comp=comp,op_time=minutes,time_tolerance=tol,
                 tap_at=tap_at,analyses=analyses)
    spec = spec_of(grade, dict(elements=limits,T=(lo,hi),incl=incl,caster=caster))
    inputs = json.dumps([start,spec,uncertain],sort_keys=True)
    if st.button("합격 후보와 최저비용 계산", type="primary"):
        st.session_state.pop("opt_result",None)
        if any(v is None for v in comp.values()) or sum(comp.values()) >= 100:
            st.error("17개 Tap 값을 모두 입력하고 조성 합계를 확인하세요.")
            return
        if tap_at >= 60 * (minutes + tol):
            st.error("출강 시각이 주조 도착 기한을 넘습니다.")
            return
        ordered_bounds = [[v for v in bounds if v is not None] for bounds in limits.values()]
        if lo > hi or any(values != sorted(values) for values in ordered_bounds):
            st.error("온도 또는 조성의 하한·목표·상한 순서를 확인하세요.")
            return
        bar = st.progress(0)
        try:
            result = search(start,spec,uncertain,bar.progress)
            st.session_state.opt_result = (inputs,result)
        finally:
            bar.empty()
    saved = st.session_state.get("opt_result")
    if not saved or saved[0] != inputs:
        return
    result = saved[1]
    st.dataframe(result["candidates"],hide_index=True)
    best = result["best"]
    if not best:
        st.warning("기본 조건을 통과한 후보가 없습니다. 표에서 미달 항목을 확인하세요.")
        return
    if best["passed"] < 27:
        st.warning("30회 중 27회 합격 기준을 충족한 후보가 없습니다. 아래는 기본 조건을 통과한 참고안입니다.")
    st.subheader(best["label"])
    st.write(f"예상 2차정련 비용 **${best['cost']:.2f}/t** · 모의 합격 **{best['passed']}/30** · "
             f"주조 {best['arrival']} · {best['temperature']:.1f} °C")
    st.caption("게임 시작 시각 기준입니다. 같은 시각의 조작은 표 순서대로 시행하세요. 모의 합격 횟수는 실제 게임의 합격률과 다를 수 있습니다.")
    schedule = pd.DataFrame(best["schedule"],columns=["시각","설비","조작","설정·투입량","메모"])
    st.dataframe(schedule,hide_index=True)
    st.download_button("조업 지시서 CSV",schedule.to_csv(index=False).encode("utf-8-sig"),"secondary_schedule.csv","text/csv")
    with st.expander("최종 성분과 비용"):
        st.write(f"도착 중량 {best['mass']:,.1f} kg · 2차정련 총액 ${best['total']:,.2f} · 개재물 지표 {best['inclusion']:.2f}")
        st.dataframe([{"원소":el,"예상 wt%":v,"Min":limits[el][0],"Max":limits[el][2]} for el,v in best["chemistry"].items()],hide_index=True)
