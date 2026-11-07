"""Interactive local-only Bybit historical market data terminal."""
import math
import os
from datetime import date,datetime,timedelta,timezone

from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import db
from bybit import Bybit, INTERVALS, OI_INTERVALS
from engine import utc_ms, candle_kind, candle_slices, download_candles
from engine import download_observations, download_archives, build_clusters

st.set_page_config(page_title="Bybit History Lab",page_icon="📊",layout="wide")
st.title("Bybit Historical Laboratory")
st.caption("Публичные данные • UTC • Только чтение биржи • Без API-ключей и реальных сделок")

@st.cache_resource
def bybit():
    return Bybit()

@st.cache_data(ttl=180)
def discover(cat):
    return bybit().instruments(cat),bybit().tickers(cat)

@st.cache_data(ttl=600)
def one_hour_range(cat,symbol):
    now=utc_ms(); end=now-now%60000
    raw=bybit().api("/v5/market/kline",dict(category=cat,symbol=symbol,
                  interval="1",start=end-65*60000,end=end-1,limit=100)).get("list",[])
    bars=[r for r in raw if int(r[0])<end]
    if not bars:return float("nan")
    first=min(bars,key=lambda r:int(r[0]))
    opening=float(first[1])
    return (max(float(x[2]) for x in bars)-min(float(x[3]) for x in bars))*100/opening if opening else float("nan")

@st.cache_data(ttl=900)
def oi_changes(cat,symbol):
    """Historical OI 1h and 24h changes, not capitalization."""
    end=utc_ms()
    records=bybit().api("/v5/market/open-interest",dict(
       category=cat,symbol=symbol,intervalTime="1h",startTime=end-28*3600000,
       endTime=end,limit=200)).get("list",[])
    vals=sorted([(int(x["timestamp"]),float(x["openInterest"])) for x in records])
    if not vals:return float("nan"),float("nan")
    ts,current=vals[-1]
    result=[]
    for hours in (1,24):
        eligible=[(t,v) for t,v in vals if t<=ts-hours*3600000]
        if not eligible or not eligible[-1][1]:result.append(float("nan"))
        else:result.append((current/eligible[-1][1]-1)*100)
    return tuple(result)

try:
    db.initialize()
except Exception as exc:
    st.error("PostgreSQL недоступен: "+str(exc))
    st.info("Запустите PostgreSQL и заполните .env. Пароли существующих аккаунтов приложение не изменяет.")
    st.stop()

with st.sidebar:
    st.header("Сканер инструментов")
    category=st.selectbox("Рынок",["linear","spot","inverse"])
    period=st.radio("Волатильность",["24 часа","1 час"],horizontal=True)
    sample=st.slider("Глубина анализа за 1 час",10,150,50,10)
    refresh=st.button("Обновить биржевой список")
if refresh:
    discover.clear()
    one_hour_range.clear()
    oi_changes.clear()

try:
    instruments,tickers=discover(category)
except Exception as exc:
    st.error("Не удалось запросить Bybit: "+str(exc));st.stop()

information={r["symbol"]:r for r in instruments}
frame=[]
for item in tickers:
    symbol=item.get("symbol")
    inst=information.get(symbol)
    if not inst or inst.get("status") not in (None,"Trading"):continue
    last=float(item.get("lastPrice") or 0)
    lo=float(item.get("lowPrice24h") or 0)
    hi=float(item.get("highPrice24h") or 0)
    absolute=abs(float(item.get("price24hPcnt") or 0))*100
    frame.append({"Выбрать":False,"Инструмент":symbol,
        "24ч диапазон, %":(hi-lo)/last*100 if last else float("nan"),
        "24ч изменение, %":float(item.get("price24hPcnt") or 0)*100,
        "24ч оборот":float(item.get("turnover24h") or 0),
        "OI сейчас, стоимость":float(item.get("openInterestValue") or 0) if category!="spot" else float("nan"),
        "OI изменение 1ч, %":float("nan"),"OI изменение 24ч, %":float("nan"),
        "1ч диапазон, %":float("nan"),"Цена":last,
        "Launch ms":int(inst.get("launchTime") or 0)})
if not frame:st.warning("Нет торгуемых инструментов в ответе биржи");st.stop()
data=pd.DataFrame(frame)

if period=="1 час":
    data=data.nlargest(sample,"24ч оборот").copy()
    with st.spinner("Сбор закрытых 1m свечей для рейтинга 1 часа"):
        data["1ч диапазон, %"]=[one_hour_range(category,s) for s in data["Инструмент"]]
st.caption("Диапазон high–low — мера движения цены, НЕ статистическая волатильность. OI — открытый интерес, НЕ капитализация монеты.")
if category!="spot":
    if st.button("Рассчитать изменение OI за 1ч / 24ч для отображаемых инструментов"):
        with st.spinner("Запрос истории открытого интереса"):
            st.session_state["oi_ranks"]={(category,s):oi_changes(category,s) for s in data["Инструмент"]}
    ranking=st.session_state.get("oi_ranks",{})
    data["OI изменение 1ч, %"]=[ranking.get((category,s),(float("nan"),float("nan")))[0] for s in data["Инструмент"]]
    data["OI изменение 24ч, %"]=[ranking.get((category,s),(float("nan"),float("nan")))[1] for s in data["Инструмент"]]

left,right=st.columns([2,2])
with left:search=st.text_input("Поиск тикера",placeholder="BTC, ETH, SOL").upper()
with right:
    columns=["1ч диапазон, %"] if period=="1 час" else ["24ч диапазон, %","24ч изменение, %","24ч оборот"]
    if category!="spot":columns+=["OI сейчас, стоимость","OI изменение 1ч, %","OI изменение 24ч, %"]
    sort=st.selectbox("Упорядочить по",columns)
if search:data=data[data["Инструмент"].str.contains(search,regex=False)]
data=data.sort_values(sort,ascending=False,na_position="last")
data_view=data.drop(columns=["Launch ms"])
edited=st.data_editor(data_view,hide_index=True,use_container_width=True,
     disabled=[c for c in data_view.columns if c!="Выбрать"],
     key="selector_"+category+"_"+period)
st.caption("Поставьте галочки слева у одного или нескольких инструментов.")
if st.button("Подтвердить выбранные инструменты",type="primary"):
    st.session_state["selection"]=[(category,s) for s in edited.loc[edited["Выбрать"],"Инструмент"].tolist()]
chosen=[s for c,s in st.session_state.get("selection",[]) if c==category]
st.write("**Выбрано:**",", ".join(chosen) or "ничего")

if chosen:
    st.divider()
    st.subheader("Исторические данные")
    selected=st.selectbox("Инструмент для просмотра",chosen)
    launch=information[selected].get("launchTime")
    try:
        listing=date.fromtimestamp(int(launch)/1000) if int(launch or 0)>0 else date(2020,1,1)
    except (ValueError,OverflowError,TypeError):listing=date(2020,1,1)
    c1,c2,c3=st.columns(3)
    with c1:begin=st.date_input("Начало (UTC)",value=listing)
    with c2:end=st.date_input("Конец (UTC, включительно)",value=date.today())
    with c3:mode=st.selectbox("Состав загрузки",["Всё: API + архивы","Все API-показатели","Только тиковые архивы"])
    if begin>end:st.error("Начальная дата позже конечной")
    st.caption("Для каждого выбранного инструмента будут запрошены все 13 периодов свечей и доступные показатели OI/long-short/funding. Известные архивы выбираются по опубликованному списку.")
    if st.button("Найти ежедневные архивы для выбранных инструментов"):
        for sym in chosen:
            try:
                days=bybit().archive_days(category,sym)
                st.session_state["archive_"+category+"_"+sym]=days
            except Exception as exc:st.warning(sym+": не удалось проверить индекс архива: "+str(exc))
    archival=st.session_state.get("archive_"+category+"_"+selected)
    if archival is None:
        st.info("Архивные дни ещё не проверены. Поиск обязателен перед скачиванием тиков.")
    elif archival:
        filtered=[d for d in archival if begin<=d<=end]
        st.write("Файлов в выбранном периоде:",len(filtered),"из",len(archival),
                 "Первый в архиве:",archival[0],"Последний:",archival[-1])
        expect=set(pd.date_range(max(begin,archival[0]),min(end,archival[-1]),freq="D").date)
        holes=sorted(expect-set(filtered))
        if holes:st.warning("Дней без файлов внутри индекса: "+str(len(holes))+
                    ". Это неизвестное покрытие, а не нулевые сделки.")
    else:st.warning("Индекс доступен, но файлов не найдено. Это НЕ подтверждение отсутствия сделок.")

    if st.button("Скачать данные по всем выбранным инструментам",type="primary",disabled=begin>end):
        a=int(datetime.combine(begin,datetime.min.time(),tzinfo=timezone.utc).timestamp()*1000)
        b=min(utc_ms(),int(datetime.combine(end+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc).timestamp()*1000))
        jobs=[]
        for sym in chosen:
            if mode!="Только тиковые архивы":
                for kind in candle_kind(category):
                    for tf in INTERVALS:jobs.append((sym,"candle",kind,tf))
                if category!="spot":
                    for tf in OI_INTERVALS:
                        jobs.append((sym,"observation","oi",tf))
                        jobs.append((sym,"observation","ratio",tf))
                    jobs.append((sym,"observation","funding","event"))
            if mode!="Все API-показатели":
                days=st.session_state.get("archive_"+category+"_"+sym)
                if days is None:st.warning(sym+": нет проверенного индекса архива, архив не запущен")
                else:jobs.append((sym,"archive","archive",days))
        status=st.empty();bar=st.progress(0.0)
        for index,(sym,job,kind,tf) in enumerate(jobs):
            status.write("Задача %s/%s: %s — %s %s"%(index+1,len(jobs),sym,kind,tf if job!="archive" else ""))
            def update(i,total,name):
                bar.progress(min(1.0,(index+i/max(total,1))/max(len(jobs),1)))
            if job=="candle":
                download_candles(bybit(),category,sym,kind,tf,a,b,update)
            elif job=="observation":
                download_observations(bybit(),category,sym,kind,tf,a,b,update)
            else:
                download_archives(bybit(),category,sym,[d for d in tf if begin<=d<=end],update)
                try: build_clusters(category,sym)
                except Exception as exc:st.error(sym+": кластеризация не завершена: "+str(exc))
        bar.progress(1.0)
        status.success("Очередь задач выполнена. Проверьте таблицу: ошибки, пробелы и пустые периоды НЕ считаются подтверждёнными.")

    st.subheader("Контроль полноты и ошибок")
    status_rows=db.report(category,selected)
    if status_rows:
        audit=pd.DataFrame(status_rows,columns=["Набор","Таймфрейм","Статус","Число сегментов"])
        st.dataframe(audit,use_container_width=True,hide_index=True)
        total=int(audit["Число сегментов"].sum())
        good=int(audit.loc[audit["Статус"]=="complete","Число сегментов"].sum())
        st.metric("Проверенных сегментов в задании",("%0.1f%%"%(100*good/total)) if total else "—")
        st.caption("100% здесь относится ТОЛЬКО к зарегистрированным сегментам, не ко всей истории существования Bybit.")
    else:st.info("Нет завершённых попыток загрузки.")

    with st.expander("Сверка тиковых архивов с минутными свечами"):
        st.caption("Сопоставляет объём архивных сделок и официальных закрытых 1m свечей за выбранные даты. "
                   "Для некоторых деривативов единицы объёма могут различаться.")
        if st.button("Проверить минутные объёмы",key="reconcile_"+category+"_"+selected):
            try:
                comparison=db.reconcile_minute_volume(category,selected,begin,end)
                if comparison:
                    st.dataframe(pd.DataFrame(comparison,columns=[
                      "Результат","Минут","Сделок","Макс. абсолютное расхождение"]),
                      use_container_width=True,hide_index=True)
                else: st.info("Нет полных архивных файлов с тиками в выбранном периоде.")
            except Exception as exc: st.error("Ошибка сверки: "+str(exc))

    st.subheader("Интерактивный график")
    x,y,z=st.columns(3)
    with x:tf=st.selectbox("Таймфрейм",INTERVALS,index=3)
    with y:kind=st.selectbox("Тип цены",candle_kind(category))
    with z:indicator=st.selectbox("Индикатор",["delta","volume","oi","ratio","funding","none"])
    rows=db.series(category,selected,kind,tf)
    if rows:
        prices=pd.DataFrame(rows,columns=["ts","open","high","low","close","volume"])
        prices["time"]=pd.to_datetime(prices.ts,unit="ms",utc=True)
        fig=make_subplots(rows=2,cols=1,shared_xaxes=True,vertical_spacing=0.05,row_heights=[0.73,0.27])
        fig.add_trace(go.Candlestick(x=prices.time,open=prices.open,high=prices.high,
          low=prices.low,close=prices.close,name=kind),row=1,col=1)
        lower=False
        if indicator=="volume":
            fig.add_trace(go.Bar(x=prices.time,y=prices.volume,name="Volume"),row=2,col=1)
            lower=True
        elif indicator=="delta":
            vals=db.cluster_totals(category,selected,tf)
            if vals:
                fig.add_trace(go.Bar(x=[datetime.fromtimestamp(r[0]/1000,timezone.utc) for r in vals],
                    y=[float(r[3]) for r in vals],name="Taker volume delta"),row=2,col=1)
                lower=True
        elif indicator in ("oi","ratio","funding"):
            dataset_tf="1h" if indicator in ("oi","ratio") else "event"
            vals=db.series(category,selected,indicator,dataset_tf)
            if vals:
                fig.add_trace(go.Scatter(x=[datetime.fromtimestamp(r[0]/1000,timezone.utc) for r in vals],
                    y=[float(r[1]) for r in vals],mode="lines+markers",name=indicator),row=2,col=1)
                lower=True
        if indicator!="none" and not lower:st.warning("Для выбранного индикатора нет сохранённых данных.")
        fig.update_layout(title=selected+" • "+kind+" • "+tf+" • UTC",
               height=750,template="plotly_dark",xaxis_rangeslider_visible=False)
        st.plotly_chart(fig,use_container_width=True)
    else:st.info("Сначала загрузите выбранную разновидность свечей.")
    with st.expander("Кластерный footprint по цене"):
        footprint_tf=st.selectbox("Таймфрейм footprint",INTERVALS)
        points=db.footprint(category,selected,footprint_tf)
        if points:
            st.dataframe(pd.DataFrame(points,columns=["Timestamp UTC ms","Цена","Buy","Sell","Delta","Сделок"]),
              use_container_width=True,hide_index=True)
        else:st.caption("Нет сохранённых сделок и агрегатов этого таймфрейма.")

st.divider()
st.caption("Капитализация, исторические ликвидации, полная L2-книга заявок и отсутствующие архивы не могут быть восстановлены из свечей.")
