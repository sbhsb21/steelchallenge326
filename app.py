import json
import os
from collections import defaultdict
from contextlib import contextmanager

import pandas as pd
import streamlit as st

import eventlog
from grade_clipboard import parse_composition, parse_process
import model
from run_candidates import rank_runs
import storage

st.set_page_config(page_title="SteelChallenge", layout="wide")
if os.environ.get("RENDER") == "true" or os.environ.get("STEEL_REQUIRE_LOGIN") == "1":
    allowed = {email.strip().casefold() for email in
               os.environ.get("STEEL_ALLOWED_EMAILS", "").split(",") if email.strip()}
    if not allowed:
        st.error("접근 허용 계정이 설정되지 않았습니다.")
        st.stop()
    if not getattr(st.user, "is_logged_in", False):
        st.title("SteelChallenge")
        if st.button("Google 계정으로 로그인"):
            st.login()
        st.stop()
    if st.user.get("email_verified") is not True or st.user.get("email", "").casefold() not in allowed:
        st.error("이 계정에는 접근 권한이 없습니다.")
        if st.button("다른 계정으로 로그인"):
            st.logout()
        st.stop()
    if st.sidebar.button("로그아웃"):
        st.logout()
storage.initialize()
st.title("SteelChallenge · Phase 1")

materials = {m["slot"]: dict(m, composition=json.loads(m["composition"]))
             for m in storage.rows("SELECT * FROM material ORDER BY slot")}
recovery = {(r["slot"], r["facility"], r["element"]): r["fraction"]
            for r in storage.rows("SELECT * FROM recovery")}
samples = defaultdict(dict)
for r in storage.rows("SELECT slot, element, AVG(wt_pct) AS value FROM melt_sample GROUP BY slot, element"):
    samples[r["slot"]][r["element"]] = r["value"]


@contextmanager
def report_errors():
    try:
        yield
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        st.error(str(error))


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def obj(value):
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("JSON 객체를 입력하세요.")
    return parsed


def optional_number(text, label, minimum=0):
    return model.number(float(text), label, minimum) if text.strip() else None


def read_amounts(text, stages):
    items = []
    for slot, kg in obj(text).items():
        material = materials[slot]
        if not material["name"] or material["stage"] not in stages:
            raise ValueError(f"{slot}: 이름과 품목 종류를 확인하세요.")
        items.append((material, model.number(kg, slot)))
    return items


page = st.sidebar.radio("화면", ["후보 탐색", "강종 조건", "품목·회수율", "공정 경로",
                              "계산기", "Run 가져오기·비교"])

if page == "품목·회수율":
    st.header(page)
    stage = st.selectbox("종류", list(storage.COUNTS),
                         format_func={"raw": "원재료", "eaf": "EAF 첨가물",
                                      "secondary": "2차정련 첨가물"}.get)
    slots = [slot for slot, m in materials.items() if m["stage"] == stage]
    st.dataframe([dict({k: v for k, v in materials[slot].items() if k != "details"},
                       composition=dump(materials[slot]["composition"]))
                  for slot in slots], hide_index=True)
    slot = st.selectbox("품목", slots, format_func=lambda s: f"{s} · {materials[s]['name'] or '미등록'}")
    m = materials[slot]
    details = json.loads(m["details"])
    if details:
        with st.expander("UI 형태·산화물·잔량 정보"):
            st.json(details)
    with st.form(f"material-{slot}"):
        name = st.text_input("이름", m["name"] or "")
        price = st.text_input("가격 (통화/t)", "" if m["price_per_t"] is None else str(m["price_per_t"]))
        density = st.text_input("벌크밀도 (t/m³)", "" if m["density_t_m3"] is None else str(m["density_t_m3"]))
        composition = st.text_area("현재 UI tooltip 조성 (wt%, 미확인은 null)", dump(m["composition"]))
        source = st.text_input("출처 / UI 확인일", m["source"] or "")
        if st.form_submit_button("품목 저장"):
            with report_errors():
                values = model.chemistry(obj(composition))
                if not source.strip():
                    raise ValueError("출처를 입력하세요.")
                storage.execute(
                    "UPDATE material SET name=?,price_per_t=?,density_t_m3=?,composition=?,source=? WHERE slot=?",
                    (name.strip() or None, optional_number(price, "가격", -float("inf")),
                     optional_number(density, "밀도", 1e-9), dump(values), source.strip(), slot),
                )
                st.rerun()
    if stage == "raw":
        st.subheader("단일 원재료 용해 실측")
        st.dataframe(storage.rows("SELECT element, wt_pct, evidence FROM melt_sample WHERE slot=?",
                                  (slot,)), hide_index=True)
        with st.form(f"melt-{slot}"):
            element = st.text_input("원소", "C")
            wt_pct = st.number_input("용해 후 wt%", 0.0, 100.0, value=None, format="%.6f")
            evidence = st.text_input("실측 Run ID 또는 로그 파일명")
            if st.form_submit_button("용해 실측 저장"):
                with report_errors():
                    model.chemistry({element: wt_pct})
                    if not evidence.strip() or wt_pct is None:
                        raise ValueError("실측값과 근거를 입력하세요.")
                    storage.execute(
                        """INSERT INTO melt_sample (slot,element,wt_pct,evidence) VALUES (?,?,?,?)
                        ON CONFLICT(slot,element,evidence) DO UPDATE SET wt_pct=excluded.wt_pct""",
                        (slot, element, wt_pct, evidence.strip()),
                    )
                    st.rerun()
    else:
        st.subheader("설비별 회수율")
        st.dataframe(storage.rows("SELECT facility,element,fraction,evidence FROM recovery WHERE slot=?",
                                  (slot,)), hide_index=True)
        with st.form(f"recovery-{slot}"):
            facility = st.text_input("설비", "EAF" if stage == "eaf" else "")
            element = st.text_input("원소 또는 bulk", "bulk")
            fraction = st.number_input("회수율 (0~1)", 0.0, 1.0, value=None, format="%.6f")
            evidence = st.text_input("실측 근거")
            if st.form_submit_button("회수율 저장"):
                with report_errors():
                    if element != "bulk":
                        model.chemistry({element: None})
                    if not facility.strip() or not evidence.strip() or fraction is None:
                        raise ValueError("설비, 회수율과 근거를 입력하세요.")
                    storage.execute(
                        """INSERT INTO recovery VALUES (?,?,?,?,?) ON CONFLICT(slot,facility,element)
                        DO UPDATE SET fraction=excluded.fraction,evidence=excluded.evidence""",
                        (slot, facility.strip(), element, fraction, evidence.strip()),
                    )
                    st.rerun()


def named(table, column):
    return {row["name"]: json.loads(row[column])
            for row in storage.rows(f"SELECT * FROM {table} ORDER BY name")}


def read_limits(table, max_value, elemental=False):
    result = {}
    for _, row in table.iterrows():
        key = row["항목"]
        if not isinstance(key, str) or not key.strip():
            if pd.notna(row["Min"]) or pd.notna(row["Max"]):
                raise ValueError("Min/Max를 지정한 행에는 항목 이름이 필요합니다.")
            continue
        key = key.strip()
        if key in result:
            raise ValueError(f"{key}: 중복 항목입니다.")
        result[key] = [float(row[k]) if pd.notna(row[k]) else None for k in ("Min", "Max")]
    if elemental:
        model.chemistry(dict.fromkeys(result))
    model.validate_limits(result, max_value)
    return result


def limit_editor(values, key):
    rows = [(name, *limits) for name, limits in values.items()]
    frame = pd.DataFrame(rows, columns=["항목", "Min", "Max"]).astype({"Min": float, "Max": float})
    return st.data_editor(frame, hide_index=True, num_rows="dynamic", key=key)


if page == "강종 조건":
    st.header(page)
    grades = named("grade", "limits")
    selected = st.selectbox("강종 선택", [None, *grades], format_func=lambda v: v or "새 강종")
    current = grades.get(selected, {"chemistry": dict.fromkeys(["C", "Mn", "Si", "P", "S"], [None, None]),
                                   "process": dict.fromkeys(["time_min", "temp_c", "basicity"], [None, None])})
    with st.form(f"paste-grade-{selected}"):
        pasted = st.text_area("복사한 조성표 붙여넣기",
                              placeholder="Element / Result / Min / Max 머리글부터 마지막 원소까지 붙여넣으세요.")
        if st.form_submit_button("목표 조성 표에 채우기"):
            with report_errors():
                imported = parse_composition(pasted)
                st.session_state[f"grade-paste-{selected}"] = imported
                revision_key = f"grade-paste-version-{selected}"
                st.session_state[revision_key] = st.session_state.get(revision_key, 0) + 1
                st.success(f"원소 {len(imported)}개를 아래 표에 채웠습니다. 값 확인 후 강종을 저장하세요.")
    imported = st.session_state.get(f"grade-paste-{selected}", current["chemistry"])
    revision = st.session_state.get(f"grade-paste-version-{selected}", 0)
    with st.form(f"paste-process-{selected}"):
        pasted_process = st.text_area("복사한 공정 조건 붙여넣기",
                                      placeholder="시뮬레이터의 온도·질량·시간·CO₂·슬래그 조건을 붙여넣으세요.")
        if st.form_submit_button("공정 조건 표에 채우기"):
            with report_errors():
                grade_name, process_limits = parse_process(pasted_process)
                st.session_state[f"process-paste-{selected}"] = process_limits
                key = f"process-paste-version-{selected}"
                st.session_state[key] = st.session_state.get(key, 0) + 1
                if grade_name and selected is None and not st.session_state.get(f"grade-name-{selected}"):
                    st.session_state[f"grade-name-{selected}"] = grade_name
                st.success(f"공정 조건 {len(process_limits)}개를 아래 표에 채웠습니다.")
    process_values = st.session_state.get(f"process-paste-{selected}", current["process"])
    process_revision = st.session_state.get(f"process-paste-version-{selected}", 0)
    with st.form(f"grade-{selected}"):
        name = st.text_input("강종 이름", selected or "", key=f"grade-name-{selected}")
        st.subheader("목표 조성 (wt%)")
        chemistry = limit_editor(imported, f"chemistry-{selected}-{revision}")
        st.subheader("공정 조건 (분, °C, 슬래그 지표)")
        process = limit_editor(process_values, f"process-{selected}-{process_revision}")
        if st.form_submit_button("강종 저장"):
            with report_errors():
                if not name.strip():
                    raise ValueError("강종 이름을 입력하세요.")
                limits = {"chemistry": read_limits(chemistry, 100, True),
                          "process": read_limits(process, float("inf"))}
                storage.execute("INSERT INTO grade VALUES (?,?) ON CONFLICT(name) DO UPDATE SET limits=excluded.limits",
                                (name.strip(), dump(limits)))
                st.session_state.pop(f"grade-paste-{selected}", None)
                st.session_state.pop(f"process-paste-{selected}", None)
                st.rerun()


if page == "공정 경로":
    st.header(page)
    st.caption("같은 강종에서 여러 경로를 비교하려면 설비별로 등록하세요. 실측으로 확인한 0은 숫자 0으로 입력합니다.")
    routes = named("route", "specification")
    selected = st.selectbox("경로 선택", [None, *routes], format_func=lambda v: v or "새 경로")
    blank = dict.fromkeys(["eaf_min", "move_min", "secondary_min", "mix_min", "tap_c",
                          "move_loss_c", "secondary_delta_c", "steel_yield"])
    blank.update(facility="", evidence="", operations={}, slag={}, retention={}, co2_kg=None,
                 eaf_kg={}, secondary_max_kg={})
    with st.form(f"route-{selected}"):
        name = st.text_input("경로 이름", selected or "")
        spec = st.text_area("경로 설정 JSON · 필드 설명은 README 참고", dump(routes.get(selected, blank)), height=410)
        if st.form_submit_button("경로 저장"):
            with report_errors():
                if not name.strip():
                    raise ValueError("경로 이름을 입력하세요.")
                route = obj(spec)
                model.validate_route(route)
                storage.execute("INSERT INTO route VALUES (?,?) ON CONFLICT(name) DO UPDATE SET specification=excluded.specification",
                                (name.strip(), dump(route)))
                st.rerun()


if page == "후보 탐색":
    st.header(page)
    grades = named("grade", "limits")
    if grades:
        st.subheader("새 강종에 시도할 조업")
        trial_grade = st.selectbox("목표 강종", list(grades), key="trial-grade")
        trial_stage = st.radio("공정 단계", ["EAF", "2차정련"], horizontal=True)
        trial_target = grades[trial_grade]
        if trial_stage == "2차정련":
            trial_target = {"chemistry": trial_target["chemistry"], "process": {}}
            st.caption("2차정련 기록은 최종 조성으로 비교합니다. EAF 공정 조건과 연결 Heat는 별도로 확인하세요.")
        trials = storage.rows("""
            SELECT r.id, r.name, r.actual, r.raw_log,
                   CASE WHEN EXISTS (SELECT 1 FROM pdf_source p WHERE p.run_id=r.id
                      AND json_extract(p.details,'$.process_type')='Secondary Steelmaking')
                   THEN '2차정련' ELSE 'EAF' END AS process_type
            FROM run r
            WHERE EXISTS (SELECT 1 FROM master_run m WHERE m.run_id=r.id)
               OR EXISTS (SELECT 1 FROM pdf_source p WHERE p.run_id=r.id)
        """)
        candidates = rank_runs((r for r in trials if r["process_type"] == trial_stage), trial_target)
        if not candidates:
            st.info("조업 순서를 읽을 수 있는 실측 기록이 없습니다.")
        else:
            st.caption("실제 Run의 배합을 시험 후보로 비교합니다. 범위를 벗어난 항목 수, 미확인 수, 편차, 과거 비용 순으로 정렬합니다.")
            for index, trial in enumerate(candidates[:5]):
                label = f"Run #{trial['id']} · 범위 밖 {trial['failed']}개 · 미확인 {trial['unknown']}개"
                if trial["cost"] is not None:
                    label += f" · 과거 비용 ${trial['cost']:,.2f}/t"
                with st.expander(label, expanded=index == 0):
                    st.write(trial["name"])
                    st.dataframe(trial["checks"], hide_index=True)
                    if trial["baskets"]:
                        st.markdown("**Basket별 장입**")
                        st.dataframe(trial["baskets"], hide_index=True)
                    if trial["additions"]:
                        st.markdown("**첨가물 투입**")
                        st.dataframe(trial["additions"], hide_index=True)
                    st.markdown("**시간순 조업 절차**")
                    st.code(trial["timeline"], language=None)
                    st.download_button("조업 절차 다운로드", trial["timeline"],
                                       f"run-{trial['id']}-steps.txt", key=f"trial-{trial['id']}")
        st.divider()
    st.subheader("배합 최적화")
    st.caption("등록한 경로별로 장입량과 Basket 부피를 제약하고 완성 용강 t당 총비용을 최소화합니다.")
    routes = named("route", "specification")
    if not grades or not routes:
        st.info("강종 조건과 실측 기반 공정 경로를 먼저 등록하세요.")
    else:
        with st.form("optimize"):
            grade = st.selectbox("강종", list(grades))
            route_names = st.multiselect("비교 경로", list(routes), default=list(routes))
            kg = st.number_input("총 원재료 장입량 (kg)", min_value=0.0, value=None)
            caps = st.text_input("Basket별 용량 (m³, 쉼표로 구분)")
            if st.form_submit_button("최저비용 후보 계산"):
                st.session_state.pop("results", None)
                with report_errors():
                    if not route_names or not caps.strip():
                        raise ValueError("경로와 Basket 용량을 입력하세요.")
                    st.session_state["results"] = model.optimize(
                        materials, recovery, samples, {n: routes[n] for n in route_names}, grades[grade],
                        kg, [float(value) for value in caps.split(",")],
                    )
                    st.session_state["result_grade"] = grade
    if "results" in st.session_state:
        results, rejected = st.session_state["results"]
        st.write(f"계산 결과 · {st.session_state['result_grade']}")
        for name, reason in rejected.items():
            st.warning(f"{name} · {reason}")
        if not results:
            st.info("입력 조건을 충족한 모델 후보가 없습니다.")
        for index, candidate in enumerate(results):
            cost = candidate["cost"]["per_t"]
            with st.expander(f"{candidate['route']} · {cost:,.3f} / t", expanded=index == 0):
                st.json({k: v for k, v in candidate.items() if k != "event_log"})
                st.code(candidate["event_log"], language=None)
                st.download_button("후보 JSON 다운로드", dump(candidate), f"candidate-{index+1}.json",
                                   mime="application/json", key=f"candidate-{index}")
                st.download_button("Event Log 초안 다운로드", candidate["event_log"],
                                   f"candidate-{index+1}.log", mime="text/plain", key=f"log-{index}")


if page == "계산기":
    st.header(page)
    tool = st.selectbox("계산", ["초기 조성·Basket", "2차정련 합금", "총비용"])
    target_slot = None
    if tool == "2차정련 합금":
        target_slot = st.selectbox("필요량 역산용 첨가물 (선택)", [None, *[
            s for s, m in materials.items() if m["stage"] == "secondary" and m["name"]
        ]], format_func=lambda s: materials[s]["name"] if s else "선택 안 함")
    with st.form(f"calc-{tool}"):
        quantities = st.text_area('품목별 kg JSON (예시 {"raw-01": 1000})', "{}")
        if tool == "초기 조성·Basket":
            elements = st.text_input("원소 (쉼표로 구분)", "C,Mn,Si,P,S")
            cap = st.number_input("이 Basket의 한도 (m³)", min_value=0.0, value=None)
        elif tool == "2차정련 합금":
            mass = st.number_input("첨가 전 용강 (kg)", min_value=0.0, value=None)
            base = st.text_area("첨가 전 조성 (wt% JSON)", "{}")
            facility = st.text_input("설비 이름")
            if target_slot:
                element = st.text_input("역산 대상 원소", "Mn")
                target = st.number_input("목표 wt%", 0.0, 100.0, value=None, format="%.6f")
        else:
            mass = st.number_input("완성 용강 (kg)", min_value=0.0, value=None)
            operations = st.text_area("전력·산소·이동 등 항목별 비용 (통화 JSON)", "{}")
        if st.form_submit_button("계산"):
            with report_errors():
                if tool == "초기 조성·Basket":
                    items = read_amounts(quantities, {"raw"})
                    keys = [v.strip() for v in elements.split(",") if v.strip()]
                    model.chemistry(dict.fromkeys(keys))
                    st.json({"UI 조성 질량가중 wt%": model.blend(items, keys)})
                    used = model.volume(items)
                    st.json({"사용 m³": used, "한도 m³": cap,
                             "한도 충족": None if cap is None else used <= cap})
                elif tool == "2차정련 합금":
                    base_values = model.chemistry(obj(base))
                    if target_slot:
                        kg_needed = model.required_alloy(
                            mass, base_values.get(element), target, materials[target_slot],
                            element, recovery, facility.strip(),
                        )
                        _, after = model.alloy(mass, base_values, [(materials[target_slot], kg_needed)],
                                               recovery, facility.strip())
                        st.json({"단독 필요량 kg": kg_needed, "단독 첨가 후 wt%": after})
                    mass, result = model.alloy(mass, base_values,
                                               read_amounts(quantities, {"secondary"}),
                                               recovery, facility.strip())
                    st.json({"용강 kg": mass, "첨가 후 wt%": result})
                else:
                    st.json(model.costs(read_amounts(quantities, set(storage.COUNTS)), obj(operations), mass))


if page == "Run 가져오기·비교":
    st.header(page)
    uploaded = st.file_uploader("Event Log 파일", type=["txt", "log", "csv"])
    pasted = st.text_area("또는 Event Log 붙여넣기")
    raw = pasted
    if uploaded:
        data = uploaded.getvalue()
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        try:
            raw = data.decode(encoding)
        except UnicodeDecodeError:
            try:
                raw = data.decode("cp949")
            except UnicodeDecodeError:
                st.error("텍스트 인코딩을 읽을 수 없습니다. UTF-8로 저장한 파일을 올려주세요.")
                raw = ""
    if raw.strip():
        events = eventlog.parse(raw)
        st.caption(f"{len(events)}행 · 유형 미분류 {sum(e['kind'] == 'unknown' for e in events)}행")
        st.dataframe(events, hide_index=True)
        with st.form("run_import"):
            name = st.text_input("Run 이름", uploaded.name if uploaded else "")
            prediction_file = st.file_uploader("비교할 후보 JSON (선택)", type=["json"])
            if st.form_submit_button("Run 저장"):
                with report_errors():
                    prediction = obj(prediction_file.getvalue().decode("utf-8-sig")) if prediction_file else None
                    run_id, created = storage.import_run(name.strip() or "Run", raw, events, prediction)
                    st.success(f"Run #{run_id} {'저장 완료' if created else '이미 등록됨'}")
    runs = storage.rows("SELECT * FROM run ORDER BY id DESC")
    if runs:
        st.subheader("기록된 Run")
        st.caption(f"저장된 Run {len(runs)}건")
        run = st.selectbox("비교 대상", runs, format_func=lambda r: f"#{r['id']} · {r['name']}")
        actual = json.loads(run["actual"])
        predicted = json.loads(run["prediction"]) if run["prediction"] else {}
        pdf_reports = storage.rows("SELECT source,details FROM pdf_source WHERE run_id=? ORDER BY source", (run["id"],))
        if pdf_reports:
            with st.expander("PDF 실측 및 출처", expanded=True):
                st.json(actual)
                for report_source in pdf_reports:
                    st.write(report_source["source"])
                    st.json(json.loads(report_source["details"]))
        master = storage.rows("SELECT * FROM master_run WHERE run_id=?", (run["id"],))
        if master:
            source = master[0]
            report = json.loads(source["result"])
            analysis = json.loads(source["analysis"])
            st.caption(f"{source['workbook']} · Run {report['Run_ID']} · {report['Run_Date']} · Status {report['Status']}")
            with st.expander("Master 실측 조성·슬래그·시간·비용", expanded=True):
                st.json(actual)
            with st.expander("원재료·첨가량과 로그 대조"):
                st.json({k: v for k, v in report.items() if k.startswith(("RM_", "ADD_")) and v})
                st.json({k: analysis.get(k) for k in ("Steel_Pass", "Slag_Pass", "RM_Report_Log_Match",
                                                       "ADD_Report_Log_Match", "Warning_Text")})
            with st.expander("Master 분석 전체"):
                st.json(analysis)
        with st.form(f"prediction-{run['id']}"):
            candidate_file = st.file_uploader("이 Run에 연결할 예측 후보 JSON", type=["json"])
            if st.form_submit_button("예측 연결"):
                with report_errors():
                    if not candidate_file:
                        raise ValueError("후보 탐색에서 내려받은 JSON 파일을 선택하세요.")
                    prediction = obj(candidate_file.getvalue().decode("utf-8-sig"))
                    if not all(isinstance(prediction.get(k), dict) for k in ("chemistry", "process", "cost")):
                        raise ValueError("후보 JSON의 chemistry, process, cost를 확인하세요.")
                    storage.execute("UPDATE run SET prediction=? WHERE id=?", (dump(prediction), run["id"]))
                    st.rerun()
        timeline = storage.rows("SELECT * FROM event WHERE run_id=? ORDER BY line", (run["id"],))
        points = [{"분": e["seconds"] / 60, "로그 행": e["line"], "원소": element, "wt%": value}
                  for e in timeline if e["kind"] == "sample" and e["seconds"] is not None
                  for element, value in json.loads(e["payload"])["composition"].items()]
        if points:
            frame = pd.DataFrame(points)
            st.line_chart(frame.groupby(["분", "원소"], as_index=False)["wt%"].mean(),
                          x="분", y="wt%", color="원소")
            st.download_button("시계열 CSV", frame.to_csv(index=False).encode("utf-8-sig"), "chemistry.csv")
        with st.expander("원본 로그"):
            st.code(run["raw_log"], language=None)
        with st.form(f"actual-{run['id']}"):
            actual_text = st.text_area("실제 최종 결과 JSON · chemistry / process / cost",
                                       dump(actual or {"chemistry": {}, "process": {}, "cost": {}}))
            if st.form_submit_button("실제 결과 저장"):
                with report_errors():
                    data = obj(actual_text)
                    model.chemistry(data.get("chemistry", {}))
                    for group in ("process", "cost"):
                        for key, value in data.get(group, {}).items():
                            if value is not None:
                                model.number(value, key)
                    storage.execute("UPDATE run SET actual=? WHERE id=?", (dump(data), run["id"]))
                    st.rerun()
        comparisons = []
        for group in ("chemistry", "process", "cost"):
            expected_group = predicted.get(group, {})
            if not isinstance(expected_group, dict):
                continue
            for key, observed in actual.get(group, {}).items():
                expected = expected_group.get(key)
                delta = observed - expected if isinstance(expected, (int, float)) and observed is not None else None
                comparisons.append({"구분": group, "항목": key, "예측": expected, "실측": observed, "실측−예측": delta})
        if comparisons:
            st.dataframe(comparisons, hide_index=True)
        summary = [{"Run": r["id"], "이름": r["name"], "가져온 시각 (UTC)": r["imported_at"],
                    **{f"{g}.{k}": v for g, fields in json.loads(r["actual"]).items()
                       if g in {"chemistry", "process", "cost"} for k, v in fields.items()}}
                   for r in runs]
        st.download_button("Run 비교 CSV", pd.DataFrame(summary).to_csv(index=False).encode("utf-8-sig"),
                           "runs.csv")
    review = storage.rows("SELECT source,reason,transcript FROM pdf_review ORDER BY source")
    if review:
        with st.expander(f"PDF 원본 대조 대기 · {len(review)}개"):
            item = st.selectbox("검토할 PDF", review, format_func=lambda r: r["source"])
            st.caption(item["reason"])
            st.download_button("검토용 텍스트 다운로드", item["transcript"].encode("utf-8-sig"),
                               "pdf-review.txt", mime="text/plain")

