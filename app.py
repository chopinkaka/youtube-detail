from pathlib import Path
import hashlib, json, uuid
import streamlit as st
from reader import build_brief, render_reader, LENGTHS, clean
from core import (DATA, ROOT, youtube_url, download_audio, transcribe, save_json,
                  transcript_text, srt_text, numeric_segments, stamp, chunks,
                  Gemini, analyze, prompt_package)

st.set_page_config(page_title='영상 깊이 읽기', page_icon='🎧', layout='wide')
st.markdown('''<style>
.stApp {background:#f5f6fa} .block-container {max-width:1180px;padding-top:2.4rem}
h1,h2,h3 {letter-spacing:-.035em;word-break:keep-all} [data-testid="stMetric"] {background:white;padding:18px;border-radius:14px}
[data-testid="stSidebar"] {background:#eaeef5}
[data-testid="stMetricValue"] {font-size:clamp(1.1rem,2vw,1.8rem)} .stButton>button {border-radius:9px}
</style>''', unsafe_allow_html=True)
st.caption('VIDEO → FULL TRANSCRIPT → EVIDENCE → DETAIL')
st.title('영상 깊이 읽기')
st.write('전체 발언을 남기고, 근거와 조건까지 읽습니다.')
st.caption('음성 기반 첫 버전 · 전체 대본은 PC에서 생성 · 화면 속 표·자막·동작 분석은 미포함')
DATA.mkdir(parents=True, exist_ok=True)

with st.sidebar:
    st.subheader('대본 생성 설정')
    st.write('추천 설정 · 한국어 / 표준 정확도')
    st.caption('처음에는 설정을 바꾸지 않고 사용하세요. 이미 만든 대본은 다시 생성할 필요가 없습니다.')
    model, language = 'small', 'ko'
    with st.expander('고급 음성 설정'):
        mode = st.selectbox('처리 방식', ['표준 · 추천', '정확도 우선 · 느림', '빠른 처리 · 정확도 낮음'])
        model = {'표준 · 추천':'small', '정확도 우선 · 느림':'medium', '빠른 처리 · 정확도 낮음':'base'}[mode]
        st.caption('모델이 커질수록 연산량과 메모리 사용이 늘어납니다. 정확도가 항상 보장되는 것은 아닙니다.')
        lang_label = st.selectbox('발언 언어', ['한국어', '영어', '자동 감지'])
        language = {'한국어':'ko', '영어':'en', '자동 감지':None}[lang_label]
    st.caption('대본 생성은 PC에서 처리하며 API 키가 필요하지 않습니다.')
    st.divider()
    st.subheader('저장된 작업')
    jobs = sorted((DATA / 'jobs').glob('*/transcript.json'), key=lambda p:p.stat().st_mtime, reverse=True) if (DATA / 'jobs').exists() else []
    labels = {}
    for p in jobs:
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
            labels[str(p)] = d.get('title', p.parent.name) + ' · ' + p.parent.name[-8:]
        except (ValueError, OSError):
            continue
    selection = st.selectbox('완료된 대본', [''] + list(labels), format_func=lambda x: labels.get(x, '작업 선택'))
    if st.button('선택한 작업 열기', disabled=not selection):
        st.session_state.doc = json.loads(Path(selection).read_text(encoding='utf-8'))
        st.session_state.folder = str(Path(selection).parent)
        st.session_state.pop('report', None)
        st.rerun()

@st.fragment(run_every=10)
def running_jobs():
    partials = sorted((DATA / 'jobs').glob('*/transcript.partial.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not partials:
        return
    p = partials[0]
    completed = p.parent / 'transcript.json'
    try:
        live = json.loads((completed if completed.exists() else p).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    if live.get('status') == 'processing':
        last = max((x['end'] for x in live['segments']), default=0)
        st.info('대본 생성 중: ' + live['title'])
        st.progress(min(last / max(live['duration'], 1), .99), text=f"{stamp(last)} / {stamp(live['duration'])} · {len(live['segments'])}개 발언 저장")
        st.caption('10초마다 저장 상태를 갱신합니다. 처리 중인 앱/작업 프로세스가 종료되면 전사도 중단됩니다.')
    elif live.get('status') == 'complete' and st.session_state.get('folder') != str(p.parent):
        st.success('열 수 있는 완료 대본: ' + live['title'])
        if st.button('완료된 대본 열기', key='open_latest_complete'):
            st.session_state.doc = live
            st.session_state.folder = str(p.parent)
            st.session_state.pop('report', None)
            st.rerun()

running_jobs()

with st.expander('1. 영상 또는 음성 입력', expanded='doc' not in st.session_state):
    source = st.radio('입력 방식', ['유튜브 링크', '영상·음성 파일'], horizontal=True)
    url = st.text_input('유튜브 영상 주소', placeholder='https://www.youtube.com/watch?v=...', disabled=source != '유튜브 링크')
    upload = st.file_uploader('파일 선택 (최대 1GB)', type=['mp3','mp4','m4a','wav','webm','ogg','flac','mkv','mov'], disabled=source != '영상·음성 파일')
    st.caption('자동 자막 유무와 관계없이 음성 인식을 수행합니다. 다운로드가 제한된 영상은 파일 입력을 사용하세요.')
    if st.button('전체 대본 만들기', type='primary'):
        try:
            if source == '유튜브 링크':
                canonical = youtube_url(url)
            elif not upload:
                raise ValueError('영상 또는 음성 파일을 선택하세요.')
            folder = DATA / 'jobs' / uuid.uuid4().hex[:16]
            folder.mkdir(parents=True)
            st.session_state.pop('doc', None)
            st.session_state.pop('report', None)
            with st.status('대본 생성 중', expanded=True) as status:
                message = st.empty()
                if source == '유튜브 링크':
                    path, meta = download_audio(canonical, folder, message.write)
                else:
                    path = folder / ('source' + Path(upload.name).suffix.lower())
                    path.write_bytes(upload.getbuffer())
                    meta = {'title': Path(upload.name).stem, 'url': '', 'expected_duration': None}
                save_json(folder / 'source.json', meta)
                doc = transcribe(path, meta, folder, model, language, message.write)
                status.update(label='전체 음성 처리 완료', state='complete', expanded=False)
            st.session_state.doc = doc
            st.session_state.folder = str(folder)
            st.rerun()
        except Exception as e:
            st.error(str(e))
            st.info('이 작업은 완료되지 않았습니다. 이미 처리된 대본 조각은 작업 폴더의 transcript.partial.json에 남습니다.')

if 'doc' not in st.session_state:
    st.info('링크나 파일을 넣으면 시간 정보가 있는 전체 발언록을 만듭니다. 이후 상세 분석을 시작할 수 있습니다.')
    a,b,c = st.columns(3)
    a.metric('01 · 대본', '전체 발언')
    b.metric('02 · 디테일', '수치와 조건')
    c.metric('03 · 근거', '시간으로 확인')
    st.stop()

doc = st.session_state.doc
folder = Path(st.session_state.folder)
st.subheader(doc['title'])
a,b,c,d = st.columns(4)
a.metric('음성 길이', f"{int(doc['duration'])//60}분 {int(doc['duration'])%60}초")
b.metric('발언 구간', len(doc['segments']))
c.metric('인식 검토 표시', sum(bool(s.get('review')) for s in doc['segments']))
d.metric('상세 분석 구간', len(chunks(doc['segments'])))
st.caption('전체 음성 처리 완료는 발언의 100% 정확한 인식을 뜻하지 않습니다. 숫자·이름·전문용어는 원음과 대조하세요.')
if doc.get('duration_warning'):
    st.warning('영상 정보의 길이와 받은 음성 길이가 다릅니다. 전체 음성이 확보됐는지 확인이 필요합니다.')

t3,t1,t2,t4 = st.tabs(['핵심 정리', '전체 대본', '수치·조건과 확인 필요', '내보내기'])
with t1:
    media = next((p for p in folder.glob('source.*') if p.suffix != '.json'), None)
    if media:
        selected_time = st.number_input('원음 확인 시작 시각 (초)', min_value=0, max_value=int(doc['duration']), value=0)
        audio_mime = {'.webm':'audio/webm', '.mp3':'audio/mpeg', '.mp4':'video/mp4', '.m4a':'audio/mp4', '.wav':'audio/wav', '.ogg':'audio/ogg', '.flac':'audio/flac'}.get(media.suffix.lower(), 'application/octet-stream')
        st.audio(str(media), format=audio_mime, start_time=selected_time)
    query = st.text_input('대본 검색', placeholder='예: 페이스, 4주, 회복')
    segments = [s for s in doc['segments'] if query.lower() in s['text'].lower()]
    st.caption(f'{len(segments)}개 발언 · 인식 원문을 임의로 고치지 않고 보존합니다.')
    for s in segments:
        c1,c2 = st.columns([1,7])
        label = stamp(s['start'])
        if doc.get('url'):
            c1.markdown(f"[{label}]({doc['url']}&t={int(s['start'])}s)")
        else:
            c1.caption(label)
        c2.write(s['text'])
        if s.get('review'): c2.caption('인식 검토 필요')
with t2:
    st.write('수치·단위·조건이 포함된 발언을 먼저 모았습니다. 해석 전의 원문입니다.')
    st.dataframe([{'시간':stamp(s['start']), '발언':s['text']} for s in numeric_segments(doc)], hide_index=True, width='stretch')
    with st.expander('음성 인식 검토가 필요한 발언'):
        for s in doc['segments']:
            if s.get('review'):
                st.write(f"{stamp(s['start'])} {s['text']}")
    with st.expander('12초 이상 발언이 인식되지 않은 구간'):
        st.caption('무음·음악·시연일 수도 있고, 발언 누락일 수도 있습니다. 이 표시만으로 누락 여부를 판정하지 않습니다.')
        for gap in doc.get('review_gaps', []):
            st.write(f"{stamp(gap['start'])}–{stamp(gap['end'])}")
        if not doc.get('review_gaps'):
            st.write('12초 이상의 공백이 없습니다.')
with t3:
    brief_files = sorted(folder.glob('analysis/*/brief-*.json'), key=lambda p:p.stat().st_mtime, reverse=True)
    analysis_caches = sorted(folder.glob('analysis/*/completed.json'), key=lambda p:p.stat().st_mtime, reverse=True)
    with st.expander('분석 설정 · 분량과 관심 주제', expanded=not brief_files):
        st.write('전체 논점을 추출한 뒤, 중복을 줄이고 핵심과 세부 설명을 다시 엮습니다.')
        reading_length = st.select_slider('읽을 분량', options=list(LENGTHS), value='균형 있게')
        domain = st.selectbox('영상 분야', ['자동','러닝·마라톤','일반'])
        focus = st.text_input('특히 알고 싶은 내용 (선택)', placeholder='예: 실제 훈련 루틴과 페이스, 횟수 중심')
        st.caption('분량은 편집 목표입니다. 중요한 조건과 수치는 유지하고, 근거 원문은 접어서 제공합니다.')
        st.caption('Gemini로 대본과 분석 자료를 전송합니다. 무료 서비스의 입력·출력은 Google 제품 개선에 사용될 수 있습니다.')
        st.markdown('[무료 API 키 관리](https://aistudio.google.com/api-keys) · [무료 한도 및 과금 안내](https://ai.google.dev/gemini-api/docs/billing)')
        key = st.text_input('Gemini API 키', type='password', help='이 프로그램은 키를 파일에 저장하지 않습니다. 이 입력란에만 넣으세요.')
        if 'pending_model' in st.session_state:
            st.session_state.gemini_model_name = st.session_state.pop('pending_model')
        with st.expander('고급 분석 설정 · 모델 확인 / 변경'):
            gemini_model = st.text_input('Gemini 모델', value='gemini-3.5-flash-lite', key='gemini_model_name')
            key_id = hashlib.sha256(key.encode()).hexdigest() if key else ''
            if st.session_state.get('models_key_id') != key_id:
                st.session_state.pop('available_models', None)
            if st.button('이 키로 모델 목록 조회', disabled=not key):
                try:
                    model_status = st.empty()
                    st.session_state.available_models = Gemini(key, progress=model_status.write).list_models()
                    st.session_state.models_key_id = key_id
                    st.success('모델 목록 조회 성공. 실제 생성 가능 여부와 무료 한도는 별도입니다.')
                except Exception as e:st.error(str(e))
            if st.session_state.get('available_models'):
                choice = st.selectbox('조회된 텍스트 분석 모델', st.session_state.available_models)
                if st.button('선택한 모델 적용'):
                    st.session_state.pending_model = choice
                    st.rerun()
        free_confirmed = st.checkbox('이 키의 프로젝트가 Free Tier임을 확인했습니다.')
        st.caption('완료된 대본과 구간 분석은 재사용합니다. 한도에 걸리면 멈추며 유료 모델로 자동 전환하지 않습니다.')
        if st.button('대본을 Gemini로 보내 상세 분석', type='primary', disabled=not (key and free_confirmed)):
            with st.status('핵심 정리 만드는 중', expanded=True) as status:
                try:
                    progress = st.empty(); phase = st.empty()
                    client = Gemini(key, gemini_model.strip(), progress=progress.write)
                    report, cache = analyze(doc, folder, client, phase.write)
                    st.session_state.report = report
                    data, path = build_brief(doc, cache, client, reading_length, focus, domain, phase.write)
                    status.update(label='핵심 정리 완료', state='complete', expanded=False)
                    st.rerun()
                except Exception as e:
                    status.update(label='정리를 완료하지 못했습니다 · 대본 보존됨', state='error', expanded=True)
                    st.error(str(e))
        if analysis_caches and st.button('저장된 분석으로 읽기용 정리 만들기', disabled=not (key and free_confirmed)):
            with st.status('저장된 분석을 다시 엮는 중', expanded=True) as status:
                try:
                    progress=st.empty()
                    client=Gemini(key,gemini_model.strip(),progress=progress.write)
                    data,path=build_brief(doc,analysis_caches[0].parent,client,reading_length,focus,domain,progress.write)
                    status.update(label='핵심 정리 완료',state='complete',expanded=False)
                    st.rerun()
                except Exception as e:
                    status.update(label='정리를 완료하지 못했습니다 · 기존 분석 보존됨',state='error')
                    st.error(str(e))
    if brief_files:
        chosen=brief_files[0]
        data=json.loads(chosen.read_text(encoding='utf-8'))
        if len(brief_files)>1:
            chosen=Path(st.selectbox('저장된 정리', [str(p) for p in brief_files], format_func=lambda x: json.loads(Path(x).read_text(encoding='utf-8')).get('_meta',{}).get('length','기본')+' · '+Path(x).stem[-6:]))
            data=json.loads(chosen.read_text(encoding='utf-8'))
        meta=data.get('_meta',{})
        if meta.get('editor_reviewed'): st.caption('원문과 대조해 편집한 예시')
        st.caption('저장된 정리 · '+meta.get('length','균형 있게')+(' · 관심: '+meta['focus'] if meta.get('focus') else ''))
        render_reader(doc,data,str(chosen))
    else:
        st.info('읽기용 정리를 만들면 핵심 노트와 10장 이하의 카드를 함께 볼 수 있습니다.')
        saved_reports=sorted(folder.glob('analysis/*/report.md'),key=lambda p:p.stat().st_mtime,reverse=True)
        if saved_reports:
            with st.expander('기존 상세 분석 펼쳐보기'):
                st.markdown(clean(saved_reports[0].read_text(encoding='utf-8')))
    with st.expander('API 키 없이 사용하기'):
        st.caption('전체 대본과 분석 지시문을 원하는 AI 서비스에 직접 넣을 수 있습니다.')
        st.download_button('전체 대본 + 상세 분석 지시문 받기', prompt_package(doc), file_name='analysis-input.txt')
with t4:
    st.download_button('전체 대본 TXT', transcript_text(doc), file_name='transcript.txt')
    st.download_button('시간 자막 SRT', srt_text(doc), file_name='transcript.srt')
    st.download_button('원본 데이터 JSON', json.dumps(doc, ensure_ascii=False, indent=2), file_name='transcript.json')
    st.caption('저장 위치: ' + str(folder))
