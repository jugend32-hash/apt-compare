import datetime as dt
import time
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import pandas as pd
import requests
import streamlit as st
import altair as alt
from persist import load, save

st.set_page_config(page_title='A-COMPARE', layout='wide')
st.title('A-COMPARE')
st.caption('V5 · API 진단')
URL='https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade'
now=dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
end=pd.Period(now.date(),freq='M')-1
preset=st.sidebar.radio('분석 기간', ['1년','3년','5년','10년','직접 설정'], index=2, horizontal=True)
if preset=='직접 설정':
    first=st.sidebar.date_input('시작일', (end-59).start_time.date(), max_value=end.end_time.date())
    last=st.sidebar.date_input('종료일', end.end_time.date(), max_value=end.end_time.date())
    if first>last: st.error('시작일이 종료일보다 늦습니다.'); st.stop()
    if first.day!=1 or last!=pd.Period(last,freq='M').end_time.date():
        st.sidebar.info('월별 분석이므로 선택한 시작월과 종료월 전체를 포함합니다.')
    months=pd.period_range(pd.Period(first,freq='M'),pd.Period(last,freq='M'),freq='M')
else:
    months=pd.period_range(end-(int(preset[:-1])*12-1),end,freq='M')
st.sidebar.caption(f'분석 기간: {months[0]} ~ {months[-1]} ({len(months)}개월)')
region=st.sidebar.selectbox('시군구', ['경기도 구리시','경기도 남양주시','직접 입력'])
code={'경기도 구리시':'41310','경기도 남양주시':'41360'}.get(region)
if code is None: code=st.sidebar.text_input('시군구 코드','41310').strip()
if len(code)!=5 or not code.isdigit(): st.error('시군구 코드 5자리를 입력하세요.'); st.stop()
st.sidebar.caption('수집은 시군구 전체, 분석은 법정동별로 선택합니다.')

def fetch(month,key):
    rows=[]; page=1
    while True:
        for attempt in range(3):
            try:
                r=requests.get(URL,params={'serviceKey':unquote(key),'LAWD_CD':code,'DEAL_YMD':month,'pageNo':page,'numOfRows':1000},timeout=(10,40)); r.raise_for_status(); break
            except (requests.Timeout,requests.ConnectionError):
                if attempt==2: raise
                time.sleep(2**attempt)
        root=ET.fromstring(r.content)
        if root.findtext('.//resultCode') not in ('00','000','0'): raise ValueError('API 응답 코드: '+str(root.findtext('.//resultCode') or root.findtext('.//returnReasonCode') or 'unknown'))
        items=root.findall('.//item'); rows.extend([{e.tag:(e.text or '').strip() for e in x} for x in items])
        total=int(root.findtext('.//totalCount') or 0)
        if len(rows)>=total: return rows
        if not items: raise ValueError('페이지 누락')
        page+=1

def safe_error(e):
    if isinstance(e, requests.HTTPError):
        status=e.response.status_code if e.response is not None else 0
        hints={400:'요청 형식 확인',401:'인증키 및 활용 승인 확인',403:'API 접근 권한 확인',404:'API 주소 확인',429:'호출 한도 확인',500:'서버 오류',502:'게이트웨이 오류',503:'서비스 일시 중단'}
        return f'HTTP {status}: '+hints.get(status,'API 응답 오류')
    if isinstance(e, ValueError) and str(e).startswith('API 응답 코드: '):
        code=str(e).split(': ',1)[1]
        return 'API 응답 코드: '+(code if code.isdigit() else 'unknown')
    return type(e).__name__

required = ('MOLIT_API_KEY', 'GITHUB_DATA_REPO', 'GITHUB_DATA_TOKEN')
try:
    missing_secrets = [k for k in required if not str(st.secrets.get(k, '')).strip()]
except Exception:
    missing_secrets = list(required)
if missing_secrets:
    st.error('Streamlit Secrets 설정 누락: ' + ', '.join(missing_secrets))
    st.info('앱 Settings > Secrets에서 위 항목을 최상위 TOML 키로 입력하고 저장하세요.')
    st.stop()
if st.sidebar.button('API 연결 테스트'):
    try:
        test=fetch(months[-1].strftime('%Y%m'),str(st.secrets['MOLIT_API_KEY']).strip())
        st.success(f'API 연결 성공: {months[-1]} / {len(test)}건 (저장하지 않음)')
    except Exception as e:
        st.error('API 연결 실패: '+safe_error(e))
if 'apt_snapshot' not in st.session_state:
    try: st.session_state.apt_snapshot,st.session_state.apt_sha=load()
    except Exception as e:
        status = getattr(getattr(e, 'response', None), 'status_code', None)
        hints = {401:'토큰 인증 실패',403:'토큰 권한 또는 API 제한 확인',404:'저장소 이름 및 토큰 접근 권한 확인'}
        st.error('GitHub 저장소 연결 실패: ' + (hints.get(status, 'HTTP '+str(status)) if status else type(e).__name__))
        st.stop()
snap=st.session_state.apt_snapshot
buckets=snap.get('buckets',{})
keys=[code+'-'+m.strftime('%Y%m') for m in months]
missing=[k for k in keys if k not in buckets]
st.caption(f'{region} ({code}) | {months[0]} ~ {months[-1]} | 수집 완료 {len(months)-len(missing)}/{len(months)}개월 | 마지막 갱신: {snap.get("updated","없음")}')
full=st.sidebar.checkbox('선택 기간 전체 재검증 (과거 취소·정정 반영)')
if st.button('신규 실거래 데이터 갱신' if not missing else '실거래 데이터 수집 / 갱신',type='primary'):
    key=str(st.secrets.get('MOLIT_API_KEY','')).strip()
    if not key: st.error('MOLIT_API_KEY 설정 필요'); st.stop()
    jobs=keys if full else list(dict.fromkeys(missing+[k for k in keys if k in {code+'-'+m.strftime('%Y%m') for m in pd.period_range(end-2,end,freq='M')}]))
    if not jobs: st.info('선택 기간은 모두 저장되어 있습니다. 과거 정정 확인은 전체 재검증을 선택하세요.'); st.stop()
    candidate=dict(buckets); errors=[]; bar=st.progress(0)
    st.caption(f'조회 대상 {len(jobs)}개월 (미수집 및 최근 3개월, 전체 재검증 선택 시 선택 기간 전체)')
    for i,k in enumerate(jobs):
        try: candidate[k]=fetch(k.split('-')[1],key)
        except Exception as e: errors.append(k+': '+safe_error(e))
        bar.progress((i+1)/len(jobs))
    if len(errors)<len(jobs):
        new={'buckets':candidate,'updated':now.strftime('%Y-%m-%d %H:%M')}
        try:
            st.session_state.apt_sha=save(new,st.session_state.apt_sha); st.session_state.apt_snapshot=new; buckets=candidate
            st.success(f'GitHub 저장 완료: {len(jobs)-len(errors)}개월 갱신. 기존 월별 데이터는 유지됩니다.')
            st.rerun() if not errors else None
        except Exception as e: st.error('저장 실패: '+type(e).__name__)
    if errors: st.warning('수집 실패: '+', '.join(errors))
rows=[]
for k in keys:
    for x in buckets.get(k,[]):
        if any(x.get(f,'') not in ('','-') for f in ('cdealDay','cdealType')): continue
        try:
            rows.append({'월':str(pd.Period(k.split('-')[1],freq='M')),'계약일':dt.date(int(x['dealYear']),int(x['dealMonth']),int(x['dealDay'])), '단지': ' / '.join([x.get('umdNm',''),x.get('aptNm',''),x.get('jibun','')]),'면적':float(x['excluUseAr']),'가격':int(x['dealAmount'].replace(',','')),'층':int(x['floor']),'거래방식':x.get('dealingGbn','')})
        except (KeyError,ValueError): continue
if not rows: st.info('먼저 실거래 데이터 갱신 버튼을 눌러주세요.'); st.stop()
df=pd.DataFrame(rows)
df['법정동']=df['단지'].str.split(' / ',regex=False).str[0]
dongs=['전체']+sorted(d for d in df['법정동'].unique() if d)
dong=st.sidebar.selectbox('법정동',dongs,index=dongs.index('교문동') if '교문동' in dongs else 0,key='dong_'+code)
if dong!='전체': df=df[df['법정동']==dong]
names=sorted(df['단지'].unique())
if not names: st.info('선택 지역에 거래가 없습니다.'); st.stop()
def choose(label,word,area):
    idx=next((i for i,n in enumerate(names) if word in n),0)
    name=st.sidebar.selectbox(label,names,index=idx,key=label)
    areas=sorted(df.loc[df['단지']==name,'면적'].unique())
    selected=st.sidebar.selectbox(label+' 전용면적(㎡)',areas,index=min(range(len(areas)),key=lambda i:abs(areas[i]-area)),key=label+'area')
    return name,selected
base=choose('기준 단지','동방',59.97)
a=choose('비교 A','원앙',49.83)
b=choose('비교 B','원앙',52)
st.sidebar.caption('공급평형과 전용면적의 대응은 단지 정보와 대조해 확인하세요.')
if st.sidebar.checkbox('1층 이하 제외'): df=df[df['층']>1]
if st.sidebar.checkbox('직거래 제외'): df=df[df['거래방식']!='직거래']
mode=st.radio('분석 방식',['월별 실거래 평균','최근 3개월 실거래 평균','최근 6개월 실거래 평균'],horizontal=True,index=1)
w={'월별 실거래 평균':1,'최근 3개월 실거래 평균':3,'최근 6개월 실거래 평균':6}[mode]
idx=pd.Index(months.astype(str),name='월'); table=pd.DataFrame(index=idx); detail=[]
labels=['기준','비교 A','비교 B']
for label,(name,area) in zip(labels,[base,a,b]):
    sub=df[(df['단지']==name)&(df['면적']==area)].copy(); sub['구분']=label; detail.append(sub)
    agg=sub.groupby('월')['가격'].agg(['sum','count']).reindex(idx).fillna(0)
    table[label+' 거래건수']=agg['count'].astype(int)
    counts=agg['count'].rolling(w,min_periods=w).sum(); sums=agg['sum'].rolling(w,min_periods=w).sum()
    coverage=pd.Series([k in buckets for k in keys],index=idx).rolling(w,min_periods=w).sum()==w
    table[label]=(sums/counts.where(counts>0)).where(coverage)
for label in labels[1:]: table[label+' 격차']=(table[label]/table['기준']-1)*100
st.caption(' | '.join(f'{l}: {n} {ar}㎡' for l,(n,ar) in zip(labels,[base,a,b])))
st.subheader('매매가격 추이 (만원)'); st.line_chart(table[labels])
st.subheader('가격 격차 추이 (%)')
for label in labels[1:]:
    s=table[label+' 격차']; valid=s.dropna(); st.markdown('**'+label+' vs 기준**')
    if valid.empty: st.warning('비교 가능한 기간 없음'); continue
    avg=valid.mean(); latest=valid.iloc[-1]; month=valid.index[-1]; cols=st.columns(4)
    cols[0].metric('기간 평균',f'{avg:.1f}%'); cols[1].metric('최근 비교 '+month,f'{latest:.1f}%'); cols[2].metric('평균 대비',f'{latest-avg:+.1f}%p'); cols[3].metric('유효 월',f'{len(valid)}/{len(months)}')
    chart=pd.DataFrame({'월':idx,'격차':s.values,'평균':avg,'20% 기준':20,'하단':valid.quantile(.25),'상단':valid.quantile(.75)})
    x=alt.X('월:O',axis=alt.Axis(labelAngle=-60,labelOverlap=True)); band=alt.Chart(chart).mark_area(opacity=.12,color='green').encode(x=x,y='하단:Q',y2='상단:Q')
    line=alt.Chart(chart).transform_fold(['격차','평균','20% 기준'],as_=['항목','값']).mark_line(point=True).encode(x=x,y=alt.Y('값:Q',title='격차 (%)',scale=alt.Scale(zero=False)),color='항목:N',tooltip=['월','항목',alt.Tooltip('값:Q',format='.2f')]); st.altair_chart(band+line,use_container_width=True)
    st.caption(f'최소 {valid.min():.1f}% ({valid.idxmin()}) / 최대 {valid.max():.1f}% ({valid.idxmax()}) / 최근 12개월 평균 {s.tail(12).mean():.1f}%')
    current=table.loc[month,label]; target=current/(1+avg/100); st.write(f'비교 가격 유지 및 평균 격차 회복 가정: 기준 예상 가격 {target:,.0f}만원 (현재 비교 시점 기준 {table.loc[month,"기준"]:,.0f}만원)')
    episodes=[]; start=None
    for m,v in s.items():
        if pd.isna(v): start=None; continue
        if start is None and v>=avg+10: start=m
        elif start is not None and v<=avg:
            episodes.append({'확대 시작':start,'평균 회복':m,'소요 개월':pd.Period(m,freq='M').ordinal-pd.Period(start,freq='M').ordinal}); start=None
    if episodes: st.dataframe(pd.DataFrame(episodes),hide_index=True)
    else: st.caption('평균+10%p 이상 확대 후 평균 이하로 회복한 연속 관측 사례 없음')
st.caption('선택 기간 평균은 유효 월 기준입니다. 전체 저장 기간 평균은 별도 기간을 선택하여 비교할 수 있습니다.')
st.caption('이동평균은 해당 기간의 모든 거래를 건수 가중 평균합니다. 거래 없는 월은 월별 모드에서 공란입니다. 평균회귀는 보장되지 않으며 비교 단지 하락으로도 격차가 축소될 수 있습니다.')
st.subheader('월별 비교표'); st.dataframe(table.round(2).sort_index(ascending=False)); st.download_button('월별 비교 CSV',table.to_csv().encode('utf-8-sig'),'monthly.csv')
raw=pd.concat(detail,ignore_index=True); st.subheader('개별 실거래'); st.dataframe(raw); st.download_button('개별 거래 CSV',raw.to_csv(index=False).encode('utf-8-sig'),'transactions.csv')
