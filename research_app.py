"""Run locally: streamlit run research_app.py --server.address 127.0.0.1"""
import json
import os
import uuid
from datetime import datetime,timezone
import plotly.graph_objects as go
import streamlit as st
from src.trader_intelligence.demo import fixture
from src.trader_intelligence.imports import import_bytes
from src.trader_intelligence.service import Research
from src.trader_intelligence.store import Store

st.set_page_config(page_title='Trader research',layout='wide')
st.title('Исследование раскрытых сделок')
st.caption('Локальный сценарий H • UTC • Без реальной торговли')
dsn=os.environ.get('TESTER_H_DSN')
if not dsn:
    st.info('Укажите TESTER_H_DSN для отдельной исследовательской базы tester.'); st.stop()
store=Store(dsn); service=Research(store)
tenant='local-researcher'  # fixed trusted local scope; E/G must bind authenticated identity
try:
    store.migrate()
    if st.button('Загрузить синтетический пример'):
        d=fixture();store.put(tenant,d);st.session_state['dataset']=d
    uploaded=st.file_uploader('Разрешённый CSV/JSON экспорт',type=['csv','json'])
    manifest=st.text_area('Манифест источника (JSON)',value=json.dumps(fixture()['manifest'],indent=2))
    consent=st.checkbox('Имею разрешение на обработку этого файла и указал основание в rights_ref')
    if st.button('Импортировать файл',disabled=not uploaded or not consent):
        d=import_bytes(uploaded.getvalue(),uploaded.name.rsplit('.',1)[-1],json.loads(manifest))
        store.put(tenant,d); st.session_state['dataset']=d
    if 'dataset' not in st.session_state: st.stop()
    d=st.session_state['dataset']
    st.warning('СИНТЕТИЧЕСКИЕ ДАННЫЕ' if d['manifest']['synthetic'] else 'Полнота и начальная позиция заявлены автором экспорта; независимый аудит отсутствует.')
    events=[e for e in d['events'] if e['kind']=='DISCLOSED_EXECUTION']
    if not events: st.info('Нет идентифицированных раскрытых исполнений.');st.stop()
    choice=st.selectbox('Выберите сделку для анализа',range(len(events)),format_func=lambda i:
                        f"{events[i]['symbol']} · {events[i]['event_id']} · {events[i]['side']} · {events[i]['price']}")
    e=events[choice]
    q={k:e[k] for k in ('event_id','source','trader_id','venue','category','symbol')}
    q.update(dataset_revision=d['dataset_revision'],start_ms=0,
             end_ms=max(x['timestamp_ms'] for x in d['events'])+1,
             as_of_ms=max(x['observed_at_ms'] for x in d['events']))
    plan=service.plan(tenant,q)
    st.json(plan)
    if st.button('Одобрить план и рассчитать'):
        result=service.run(tenant,str(uuid.uuid4()),plan,approved_digest=plan['plan_digest'])
        st.session_state['result']=result
    r=st.session_state.get('result')
    if r and r['plan_digest']==plan['plan_digest']:
        # Re-check source permission on every rerender.
        r=service.read_run(tenant,r['request_id'])
        fig=go.Figure()
        for classification,symbol,color in [('DISCLOSED','triangle-up','#159b8e'),('INFERRED','diamond','#bd7a22')]:
            points=[m for m in r['overlays'] if m['classification']==classification]
            if points:
                fig.add_trace(go.Scatter(x=[datetime.fromtimestamp(m['timestamp_ms']/1000,timezone.utc) for m in points],
                    y=[float(m['price']) for m in points],mode='markers',name=classification,
                    marker=dict(symbol=symbol,color=color,size=13),
                    text=[f"{m['event_id']} {m['side']} | lag={m['disclosure_lag_ms']} ms" for m in points]))
        fig.update_layout(title='Раскрытые исполнения · рыночные свечи отсутствуют',xaxis_title='UTC',yaxis_title='Цена')
        st.plotly_chart(fig,width='stretch')
        st.json(r['reconstruction'])
        st.caption('SL/TP, net PnL, комиссии и funding неизвестны. Реконструкция — условная модель.')
        st.download_button('Скачать результат JSON',json.dumps(r,indent=2),file_name='trader-research.json')
        # Plot submission is not a browser render ACK; do not claim D has acknowledged.
        st.caption('Подтверждение рендера D: отсутствует')
except (ValueError,LookupError,PermissionError) as exc:
    st.error(str(exc))
