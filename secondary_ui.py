"""Streamlit entry point for the secondary refining planning calculator."""

import json
import math

import pandas as pd
import streamlit as st

import model
import secondary_model as sm
from secondary_clipboard import parse_setup, parse_caster_time, format_caster_time


def empty(value):
    return value is None or isinstance(value, float) and math.isnan(value)


def render(storage, materials, recovery):
    st.header('2차정련 예측')
    st.caption('Tap 상태 → 공정 순서 → 주조기 도착 상태 · 매뉴얼 2.0 기반 추정')
    st.info('출강 후 래들 상태를 입력하세요. 주조 요구시간은 시뮬레이션 시작부터 남은 시간과 '
            '허용오차를 1 hr 28 mins ±5 mins. 형식으로 입력합니다.')
    seed = st.session_state.get('secondary_seed', {})
    with st.expander('시작 조건 통째로 붙여넣기', expanded=True):
        pasted = st.text_area('Current / Aim / Min / Max 표와 시작 조건', height=200,
                              placeholder='조성표부터 Chosen grade, User Level, 시간·중량·온도까지 함께 붙여넣으세요.',
                              key='ss_setup_paste')
        if st.button('붙여넣은 조건 자동 입력', type='primary'):
            try:
                parsed = parse_setup(pasted)
                st.session_state.secondary_seed = dict(seed, **parsed)
                keys = {'grade':'grade', 'user_level':'level', 'mass_kg':'mass', 'tap_temperature':'tap',
                        'caster':'caster', 'destination':'destination', 'inclusion':'inclusion',
                        'aim_temperature':'aim',
                        'temperature_tolerance':'temp_tol', 'chemistry':'chemistry'}
                for field, suffix in keys.items():
                    if field in parsed:
                        widget_key = 'ss_input_' + suffix
                        if field == 'chemistry':
                            st.session_state.pop(widget_key, None)
                        else:
                            st.session_state[widget_key] = parsed[field]
                if 'required_at' in parsed:
                    st.session_state.ss_input_caster_time = format_caster_time(
                        parsed['required_at'], parsed.get('time_tolerance', seed.get('time_tolerance', 5)))
                st.session_state.pop('secondary_result', None)
                details = [f"조성 {len(parsed['chemistry'])}개 원소"] if 'chemistry' in parsed else []
                for field, label in [('grade', '강종'), ('mass_kg', '중량 kg'),
                                     ('tap_temperature', '출강 °C'), ('required_at', '요구시간'),
                                     ('caster', 'Caster'), ('inclusion', '개재물')]:
                    if field in parsed:
                        details.append(f'{label} {parsed[field]}')
                if 'aim_temperature' in parsed:
                    details.append(f"주조 {parsed['aim_temperature']} ±{parsed['temperature_tolerance']}°C")
                st.session_state.ss_setup_notice = '자동 입력 완료 · ' + ' · '.join(details)
                st.rerun()
            except (ValueError, TypeError, KeyError) as error:
                st.error(str(error))
    if 'ss_setup_notice' in st.session_state:
        st.success(st.session_state.pop('ss_setup_notice'))
    with st.expander('기존 Run 또는 저장한 계산 불러오기'):
        upload = st.file_uploader('계산 입력 JSON', type=['json'], key='ss_upload')
        if st.button('계산 입력 불러오기', disabled=upload is None):
            try:
                data = json.loads(upload.getvalue().decode('utf-8-sig'))
                if data.get('version') != 1 or not isinstance(data.get('config'), dict):
                    raise ValueError('이 계산기에서 저장한 입력 JSON이 필요합니다.')
                sm.simulate(data['config'])
                if data['config']['caster'] not in ['Slab Caster', 'Bloom Caster', 'Billet Caster']:
                    raise ValueError('저장된 Caster를 확인하세요.')
                if data['config']['destination'] not in ['Slab Caster', 'Bloom Caster', 'Billet Caster']:
                    raise ValueError('저장된 도착 Caster를 확인하세요.')
                st.session_state.secondary_seed = data['config']
                for key in list(st.session_state):
                    if key.startswith('ss_input_'):
                        del st.session_state[key]
                st.rerun()
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                st.error(str(error))
        runs = storage.rows('SELECT id,name,actual FROM run ORDER BY id DESC')
        selected = st.selectbox('EAF 실제 결과', [None, *runs],
                                format_func=lambda r: '선택' if r is None else f"{r['id']} · {r['name']}")
        if st.button('이 결과를 Tap 상태로 가져오기', disabled=selected is None):
            try:
                actual = json.loads(selected['actual'])
                process = actual.get('process', {})
                kg = process.get('mass_kg') or (process.get('tapping_mass_t') or 0) * 1000
                if not kg or process.get('temp_c') is None or not actual.get('chemistry'):
                    raise ValueError('선택한 결과에 용강 중량·온도·조성이 필요합니다.')
                st.session_state.secondary_seed = dict(
                    mass_kg=kg, tap_temperature=process['temp_c'], chemistry=actual['chemistry'],
                    eaf_cost_total=actual.get('cost', {}).get('total') or 0)
                for key in list(st.session_state):
                    if key.startswith('ss_input_'):
                        del st.session_state[key]
                st.rerun()
            except (ValueError, TypeError, KeyError) as error:
                st.error(str(error))

    grade = st.text_input('Chosen grade', seed.get('grade', ''), key='ss_input_grade')
    left, right = st.columns(2)
    with left:
        level = st.selectbox('User Level', ['University Student', 'Steel Industry Works Technical'],
                             index=1 if seed.get('user_level') == 'Steel Industry Works Technical' else 0,
                             key='ss_input_level')
        mass = st.number_input('Ladle steel bath mass (kg)', min_value=1.0,
                               value=float(seed.get('mass_kg', 100000)), key='ss_input_mass')
        tap = st.number_input('Tap temperature (°C)', min_value=1.0, max_value=2500.0,
                              value=float(seed.get('tap_temperature', 1650)), key='ss_input_tap')
        casters = ['Slab Caster', 'Bloom Caster', 'Billet Caster']
        caster = st.selectbox('The ladle is required at · 지정 Caster', casters,
                              index=casters.index(seed.get('caster', 'Billet Caster')), key='ss_input_caster')
        inclusions = ['Moderate', 'Low', 'Very low']
        inclusion = st.selectbox('Required inclusion level', inclusions,
                                 index=inclusions.index(seed.get('inclusion', 'Moderate')), key='ss_input_inclusion')
    with right:
        caster_time = st.text_input('Time until ladle required at caster',
                                    format_caster_time(seed.get('required_at', '01:00:00'),
                                                       seed.get('time_tolerance', 5)),
                                    placeholder='1 hr 28 mins ±5 mins.', key='ss_input_caster_time')
        aim = st.number_input('Aim temperature at caster (°C)', min_value=1.0, max_value=2500.0,
                               value=float(seed.get('aim_temperature', 1560)), key='ss_input_aim')
        temp_tol = st.number_input('주조 온도 허용오차 (±°C)', min_value=0.0,
                                   value=float(seed.get('temperature_tolerance', 5)), key='ss_input_temp_tol')
        eaf_cost = st.number_input('EAF 총비용 ($)', min_value=0.0,
                                   value=float(seed.get('eaf_cost_total', 0)), key='ss_input_eaf_cost')
    st.subheader('Tap 조성 및 최종 강종 범위')
    st.caption('모두 wt%입니다. H 2 ppm = 0.0002 wt%. 빈칸은 미확인 또는 제한 없음입니다. '
               '확인된 0은 0을 입력하세요. 예시 목표는 실제 강종 조건으로 바꾸세요.')
    limits = seed.get('limits', {'C': [0.35, 0.40], 'Mn': [0.6, 0.9],
                               'Cr': [0.9, 1.2], 'Mo': [0.15, 0.3], 'Al': [0.015, 0.03]})
    elements = list(dict.fromkeys([*sm.ELEMENTS, *seed.get('chemistry', {})]))
    rows = [dict(원소=e, Tap=seed.get('chemistry', {}).get(e), Aim=seed.get('aims', {}).get(e),
                 Min=limits.get(e, [None, None])[0], Max=limits.get(e, [None, None])[1]) for e in elements]
    chemistry = st.data_editor(pd.DataFrame(rows), hide_index=True, disabled=['원소'],
                               key='ss_input_chemistry', use_container_width=True,
                               column_config={name: st.column_config.NumberColumn(format='%.6f', min_value=0.0,
                                                                                  max_value=100.0)
                                              for name in ['Tap', 'Aim', 'Min', 'Max']})
    st.subheader('공정 순서')
    st.caption('각 행의 시간은 이동 또는 처리 구간 시간입니다. 이동을 별도 행으로 넣으세요. '
               '첨가는 해당 구간 시작에 적용합니다. Ar는 Nm³/min, 압력은 절대압 atm입니다.')
    default_steps = [dict(facility='이동·대기', minutes=5.0, power_mw=0.0, argon=0.0, pressure=1.0),
                     dict(facility='Ladle Furnace', minutes=10.0, power_mw=5.0, argon=0.5, pressure=1.0),
                     dict(facility='이동·대기', minutes=5.0, power_mw=0.0, argon=0.0, pressure=1.0)]
    steps = st.data_editor(pd.DataFrame(seed.get('steps', default_steps)), num_rows='dynamic',
                           hide_index=False, key='ss_input_steps', use_container_width=True,
                           column_config={'facility': st.column_config.SelectboxColumn('설비', options=sm.FACILITIES,
                                                                                       required=True),
                                          'minutes': st.column_config.NumberColumn('시간 (분)', min_value=0.0),
                                          'power_mw': st.column_config.NumberColumn('LF MW', min_value=0.0, max_value=20.0),
                                          'argon': st.column_config.NumberColumn('Ar Nm³/min', min_value=0.0),
                                          'pressure': st.column_config.NumberColumn('절대압 atm', min_value=0.001, max_value=1.0)})
    destination = st.selectbox('계획한 최종 도착 Caster', casters,
                               index=casters.index(seed.get('destination', 'Billet Caster')), key='ss_input_destination')
    st.subheader('합금 첨가')
    st.caption('구간 번호는 위 표의 첫 행부터 1입니다. 조성·원소별 recovery는 JSON 객체로 입력합니다. '
               '첨가물 조성에 생략한 원소는 공급량 0으로 계산합니다. '
               '회수율은 0–1입니다. 각 첨가물 행은 해당 구간의 설비 조건을 사용하세요.')
    catalog = [m for m in materials.values() if m['stage'] == 'secondary' and m['name']]
    if catalog:
        with st.expander('현재 품목 DB에서 첨가 행 작성'):
            material = st.selectbox('2차정련 첨가물', catalog, format_func=lambda m: m['name'])
            facility = st.selectbox('회수율 등록 설비', sm.FACILITIES[1:])
            rec = {e: recovery.get((material['slot'], facility, e)) for e, pct in material['composition'].items()
                   if pct is not None and pct > 0}
            st.json(dict(name=material['name'], composition={e:v for e,v in material['composition'].items() if v is not None},
                         recovery=rec, bulk=recovery.get((material['slot'], facility, 'bulk')),
                         price_per_t=material['price_per_t']))
            st.caption('확인한 값을 아래 첨가 행에 복사하세요. 미등록 회수율은 직접 측정 후 입력하세요.')
            element = st.selectbox('필요량을 계산할 원소', sm.ELEMENTS, key='ss_alloy_element')
            target = st.number_input('목표 wt%', min_value=0.0, max_value=100.0, value=0.1,
                                      format='%.6f', key='ss_alloy_target')
            if st.button('현재 Tap 조성에서 필요 첨가 kg 계산'):
                try:
                    current = chemistry.loc[chemistry['원소'] == element, 'Tap'].iloc[0]
                    if empty(current):
                        raise ValueError('해당 원소의 Tap 조성을 먼저 입력하세요.')
                    kg = model.required_alloy(mass, float(current), target, material, element, recovery, facility)
                    st.success(f'{material["name"]} {kg:.2f} kg')
                    st.caption('이 값은 Tap 상태에서 단일 합금을 넣는 질량수지입니다. '
                               '처리 중 반응과 다른 첨가물의 영향은 전체 공정 예측에서 확인하세요.')
                except (ValueError, KeyError, TypeError) as error:
                    st.error(str(error))
    addition_rows = []
    for a in seed.get('additions', []):
        addition_rows.append(dict(a, composition=json.dumps(a['composition']), recovery=json.dumps(a['recovery'])))
    columns = ['step', 'name', 'kg', 'price_per_t', 'bulk', 'composition', 'recovery']
    additions = st.data_editor(pd.DataFrame(addition_rows, columns=columns), num_rows='dynamic',
                               hide_index=True, key='ss_input_additions', use_container_width=True,
                               column_config={
                                   'step': st.column_config.NumberColumn('구간 번호', min_value=1, step=1),
                                   'kg': st.column_config.NumberColumn('kg', min_value=0.0),
                                   'price_per_t': st.column_config.NumberColumn('$/t', min_value=0.0),
                                   'bulk': st.column_config.NumberColumn('전체 질량 recovery', min_value=0.0, max_value=1.0),
                                   'composition': st.column_config.TextColumn('조성 JSON · {"Al":98,"Fe":2}'),
                                   'recovery': st.column_config.TextColumn('회수율 JSON · {"Al":0.9,"Fe":1}')})
    with st.expander('매뉴얼 기본값과 실측 보정', expanded=False):
        st.warning('탈탄·탈황 k 기본값 0은 해당 반응을 계산하지 않는다는 뜻입니다. '
                   'O₂ 취입을 포함한 경로는 실측 k와 평형값을 입력하세요. '
                   'Al recovery는 탈산 소비 전 유효 회수율로 입력해야 소비량을 중복 반영하지 않습니다.')
        defaults = []
        for facility in sm.FACILITIES:
            vacuum = facility in ['Tank Degasser', 'Recirculating Degasser']
            defaults.append(dict(facility=facility, cooling=0.5 if facility == '이동·대기' else 1.0 if vacuum else 1.5,
                                 per_min={'CAS-OB':30, 'Tank Degasser':10, 'Recirculating Degasser':7.75,
                                          'Ar Stir Station':5.7}.get(facility, 0), argon_price=0.6,
                                 k_c=0.0, c_eq=0.0, k_s=0.0, s_eq=0.0))
        param_rows = ([dict(facility=f, **v) for f, v in seed['parameters'].items()]
                      if 'parameters' in seed else defaults)
        parameters = st.data_editor(pd.DataFrame(param_rows), disabled=['facility'], hide_index=True,
                                    key='ss_input_parameters', use_container_width=True)
        st.caption('cooling = 순 냉각 °C/분, per_min = 고정 $/분, argon_price = $/Nm³, '
                   'k_c·k_s = 반응속도 1/분, c_eq·s_eq = 평형 wt%. '
                   '탈황 k·평형값은 탈산·슬래그·교반 조건을 만족하는 실측 구간에만 적용하세요.')
        efficiency = st.number_input('LF 가열 효율 (0–1)', 0.0, 1.0,
                                      float(seed.get('lf_efficiency', 0.55)), key='ss_input_efficiency')
        electricity = st.number_input('LF 전기 단가 ($/kWh)', 0.0,
                                       value=float(seed.get('electricity_price', 0.0498)), key='ss_input_electricity')
        electrode = st.number_input('LF 전극 비용 ($/MW/분)', 0.0,
                                     value=float(seed.get('electrode_per_mw_min', 0.295)), key='ss_input_electrode')
        al_heat = st.number_input('반응 Al 발열 (°C/kg)', 0.0,
                                  value=float(seed.get('al_heat', 0.12)), key='ss_input_al_heat')
        chill = st.number_input('첨가물 냉각 (°C/kg)', 0.0,
                                value=float(seed.get('addition_chill', 0.006)), key='ss_input_chill')
        mix = st.number_input('최종 첨가 후 필요 혼합시간 (분)', 0.0,
                              value=float(seed.get('mix_minutes', 5)), key='ss_input_mix')
        clean = st.text_input('요구 개재물 등급까지 최종 Al 첨가 후 필요한 실측 시간 (분, 미확인 시 공란)',
                              str(seed.get('clean_minutes', '')), key='ss_input_clean')
    if st.button('2차정련 예측 계산', type='primary'):
        try:
            time_values = parse_caster_time(caster_time)
            required_at = time_values['required_at']
            time_tol = time_values.get('time_tolerance', 0)
            records = steps.to_dict('records')
            if not records:
                raise ValueError('공정 구간을 하나 이상 입력하세요.')
            if clean.strip():
                records[-1]['clean_minutes'] = float(clean)
            addition_records = []
            for a in additions.to_dict('records'):
                if empty(a['step']) or int(a['step']) != a['step'] or not 1 <= int(a['step']) <= len(records):
                    raise ValueError('첨가물 구간 번호를 확인하세요.')
                a['composition'] = json.loads(a['composition'])
                a['recovery'] = json.loads(a['recovery'])
                addition_records.append(a)
            config = dict(grade=grade, user_level=level, mass_kg=mass, tap_temperature=tap, caster=caster,
                          destination=destination, inclusion=inclusion, required_at=required_at,
                          time_tolerance=time_tol, aim_temperature=aim, temperature_tolerance=temp_tol,
                          eaf_cost_total=eaf_cost, chemistry={r['원소']:None if empty(r['Tap']) else r['Tap']
                                                             for r in chemistry.to_dict('records')},
                          aims={r['원소']:None if empty(r['Aim']) else r['Aim'] for r in chemistry.to_dict('records')},
                          limits={r['원소']:[None if empty(r[k]) else r[k] for k in ['Min', 'Max']]
                                  for r in chemistry.to_dict('records')}, steps=records, additions=addition_records,
                          parameters={r['facility']:{k:v for k,v in r.items() if k != 'facility'}
                                      for r in parameters.to_dict('records')},
                          lf_efficiency=efficiency, electricity_price=electricity,
                          electrode_per_mw_min=electrode, al_heat=al_heat,
                          addition_chill=chill, mix_minutes=mix, clean_minutes=clean)
            result = sm.simulate(config)
            st.session_state.secondary_result = dict(config=config, result=result)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            st.error(str(error))
    saved = st.session_state.get('secondary_result')
    if saved:
        result = saved['result']
        st.subheader('마지막 계산 결과')
        st.caption('입력을 바꾼 뒤에는 다시 계산하세요. 미확인 항목이 있으면 전체 합격은 미검증입니다.')
        for warning in result['warnings']:
            st.warning(warning)
        a, b, c = st.columns(3)
        a.metric('도착 온도', f"{result['process']['temp_c']:.1f} °C")
        b.metric('도착 경과시간', f"{result['process']['time_min']:.2f} 분")
        c.metric('최종 용강 기준 총비용', f"${result['cost']['per_t']:.2f}/t")
        st.caption(f"2차정련 추정비 ${result['cost']['secondary']:.2f} · 최종 중량 {result['process']['mass_kg']:.1f} kg")
        st.dataframe(result['checks'], hide_index=True, use_container_width=True)
        history = pd.DataFrame(result['trace'])
        st.line_chart(history, x='분', y='온도')
        chart_elements = st.multiselect('조성 그래프', sm.ELEMENTS, default=['C', 'Mn', 'Al'], key='ss_chart')
        if chart_elements:
            st.line_chart(history, x='분', y=chart_elements)
        exported = dict(version=1, **saved, chemistry=result['chemistry'],
                        process=result['process'], cost=result['cost'])
        st.download_button('입력과 예측 JSON 저장', json.dumps(exported, ensure_ascii=False,
                                                             indent=2, allow_nan=False),
                           'secondary_prediction.json', mime='application/json')
        st.download_button('시간별 예측 CSV', history.to_csv(index=False).encode('utf-8-sig'),
                           'secondary_prediction.csv', mime='text/csv')
