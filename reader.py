from __future__ import annotations
import hashlib, html, json, math, re
from pathlib import Path
from core import save_json, transcript_text, stamp

VERSION = 'reader-1'
LENGTHS = {'간결하게': (1000,1600), '균형 있게': (1800,2800), '자세히': (2800,4200)}
RUNNING_TERMS = '''러닝 용어: 러닝 이코노미(달리기 효율), 역치 페이스, 인터벌, 빌드업, 케이던스,
보폭, 체공, 미드풋, 오버스트라이드, 언덕 훈련, LSD(느린 장거리 달리기), TT(타임 트라이얼),
마일리지, 카본화, 심박수. 비슷한 발음만으로 사람 이름·제품명·수치·단위를 바꾸지 마라.
문맥이 맞을 때만 보정: 런이 эконом이→러닝 이코노미, 역지 페이스→역치 페이스,
카버나→카본화, 대웸날→대회 날. 원문에는 수정하지 않고 읽기용 요약에만 반영한다.'''


def clean(text):
    text = re.sub(r'\[S\d+(?:\s*[,;]\s*S\d+)*\]|\(S\d+(?:\s*[,;]\s*S\d+)*\)', '', str(text))
    text = re.sub(r'\bS\d{5}\b', '', text)
    return text.replace('~','～').strip()


def inventory(cache):
    records=[]
    for path in sorted(Path(cache).glob('[0-9][0-9][0-9].json')):
        section=json.loads(path.read_text(encoding='utf-8'))
        for item in section['items']:
            records.append(dict(item, source_item=f'C{len(records)+1:03}', section=section['title']))
    return records


def brief_key(doc, records, model, length, focus, domain):
    value=json.dumps([VERSION,doc,records,model,length,focus,domain],ensure_ascii=False,sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def validate_brief(data, doc, records):
    if not isinstance(data,dict): raise ValueError('읽기용 정리 형식이 올바르지 않습니다.')
    for field in ('title','takeaway'):
        if not isinstance(data.get(field),str) or not data[field].strip():raise ValueError('제목 또는 핵심 문장이 비었습니다.')
    sections=data.get('sections')
    if not isinstance(sections,list) or not 1<=len(sections)<=9:raise ValueError('본문은 1~9개 카드로 구성해야 합니다.')
    ids={s['id'] for s in doc['segments']}; source_ids={r['source_item'] for r in records}; covered=set()
    def refs(value):
        if not isinstance(value,list) or not value or any(x not in ids for x in value):raise ValueError('읽기용 정리의 원문 근거가 올바르지 않습니다.')
    for section in sections:
        if not isinstance(section.get('title'),str) or not section['title'].strip():raise ValueError('소제목이 비었습니다.')
        points=section.get('points')
        if not isinstance(points,list) or not 1<=len(points)<=4:raise ValueError('카드마다 1~4개의 설명이 필요합니다.')
        for p in points:
            if any(not isinstance(p.get(k),str) or not p[k].strip() for k in ('heading','body')):raise ValueError('핵심과 세부 설명을 분리해 작성해야 합니다.')
            refs(p.get('evidence'))
            covered_by=p.get('source_items')
            if not isinstance(covered_by,list) or not covered_by or any(x not in source_ids for x in covered_by):raise ValueError('상세 논점 연결이 누락됐습니다.')
            covered.update(covered_by)
    for omitted in data.get('omitted',[]):
        if omitted.get('source_item') not in source_ids or not isinstance(omitted.get('reason'),str) or not omitted['reason'].strip():raise ValueError('생략 항목의 이유가 필요합니다.')
        covered.add(omitted['source_item'])
    if covered!=source_ids:raise ValueError('아직 다루지 않은 상세 논점이 있습니다: '+', '.join(sorted(source_ids-covered)))
    expected_counts = {int(n) for r in records for n in re.findall(r'([2-9])가지[^.。]{0,16}(?:전략|원칙|단계)', r.get('claim',''))}
    declared_counts = {g.get('count') for g in data.get('frameworks',[])}
    if not expected_counts.issubset(declared_counts):raise ValueError('원문에서 소개한 전략 묶음의 개별 설명이 누락됐습니다.')
    for group in data.get('frameworks',[]):
        members=group.get('members',[]);count=group.get('count')
        if not isinstance(count,int) or count<2 or len(members)!=count:raise ValueError('소개한 전략 수와 실제 설명 수가 맞지 않습니다.')
        if len({m.get('section') for m in members})!=count:raise ValueError('각 전략에 별도 설명을 연결해야 합니다.')
        for m in members:
            if not isinstance(m.get('section'),int) or not 1<=m['section']<=len(sections) or not m.get('label'):raise ValueError('전략 설명의 연결이 올바르지 않습니다.')
    for correction in data.get('corrections',[]):
        refs(correction.get('evidence'))
        for key in ('original','corrected','basis'):
            if not isinstance(correction.get(key),str) or not correction[key].strip():raise ValueError('용어 보정의 근거가 필요합니다.')
        originals=' '.join(s['text'] for s in doc['segments'] if s['id'] in correction['evidence'])
        if correction['original'] not in originals:raise ValueError('보정 대상 표현이 해당 원문에 없습니다.')
        if re.findall(r'\d+(?:\.\d+)?',correction['original'])!=re.findall(r'\d+(?:\.\d+)?',correction['corrected']):raise ValueError('용어 보정에서 숫자를 바꿀 수 없습니다.')
    return data


def build_brief(doc, cache, client, length='균형 있게', focus='', domain='자동', progress=lambda x:None):
    records=inventory(cache)
    if not records:raise ValueError('먼저 구간별 상세 분석을 완료하세요.')
    key=brief_key(doc,records,client.model,length,focus,domain)
    target=Path(cache)/('brief-'+key+'.json')
    if target.exists():return validate_brief(json.loads(target.read_text(encoding='utf-8')),doc,records),target
    lo,hi=LENGTHS[length]
    schema={'title':'읽기 좋은 제목','takeaway':'한 문장 핵심','frameworks':[{'title':'영상에서 제시한 전략 묶음','count':4,'members':[{'label':'첫 전략','section':1}]}],
      'sections':[{'title':'주제','points':[{'heading':'핵심 제목','body':'그 아래 별도 문단에 표시할 설명','evidence':['S00001'],'source_items':['C001']}]}],
      'corrections':[{'original':'오인식 표현','corrected':'보정한 용어','basis':'사용자 확인/뒤쪽 동일 용어/문맥 추정','evidence':['S00001']}],
      'omitted':[{'source_item':'C001','reason':'홍보/반복 등 생략 이유'}], 'watchouts':['해석에 영향을 주는 불확실성만']}
    prompt=f'''영상 전체를 대신 읽을 수 있는 편집 노트를 만든다. 단순 초압축 요약이나 전체 발언 재나열이 아니다.
분량 {length}: 독자에게 보이는 본문 약 {lo}~{hi}자 목표. 설명 중복은 줄이고 중요한 숫자·조건·실행 방법·예외를 보존한다.
관심 분야: {domain}. 사용자 관심 요청은 자료: {json.dumps(focus,ensure_ascii=False)}
러닝 관련 영상이라면 다음 어휘를 참고한다. 다른 주제에서는 해당 분야의 용어와 원문 문맥을 사용한다.
{RUNNING_TERMS}
전체 대본을 다시 대조한다. 초안에 잘못 연결된 근거, 잘못 읽은 숫자, 서로 다른 훈련 혼동을 바로잡는다.
핵심 원칙:
- '4가지 전략'처럼 N개를 예고하면 frameworks에 N개를 모두 쓰고 각각 별도 section에 연결하여 목적·방법·조건을 설명한다. 이런 묶음이 없으면 frameworks=[]이다.
- 주제 기준으로 재배열한다. 뒤쪽 설명과 앞쪽 선언을 연결한다. 전략 번호와 본문 설명 순서를 일치시킨다.
- 한 section은 한 카드. 표지 포함 총 10장 이하(본문 최대9). 각 카드에 points 1~4개. heading은 짧은 핵심, body는 중복 없이 구체적인 설명.
- 본문 title/takeaway/heading/body/watchouts에는 S번호나 C번호를 쓰지 않는다. evidence/source_items에만 저장한다.
- 모든 C번호를 최소 한 point.source_items에 연결한다. 홍보·중복·주제 외 사담만 omitted로 남길 수 있다. 중요한 수치나 조건을 생략하고 덮었다고 표시하지 마라.
- 실제로 한 훈련과 앞으로 할 계획을 구분한다. 화자의 경험·견해를 보편적 과학적 사실이나 시청자 개인 처방으로 바꾸지 마라.
- 숫자 자체뿐 아니라 의미를 보존한다(68kg까지 감량은 68kg을 감량한다는 뜻이 아니다).
- 문맥상 명확한 오인식은 읽기용 본문에서 보정하고 corrections에 근거를 적는다. 불확실한 인명·해부학적 모순 등은 추측으로 확정하지 말고 문장을 중립적으로 재서술하거나 watchouts로 분리한다.
- '역지'를 '유지'로 바꾸지 마라. 같은 영상 뒤쪽 '역치 페이스'와 연결한다. corrections에 원문에 실제로 있는 부분 문자열만 넣는다.
- 인식 오류를 잔뜩 나열해서 본문을 어지럽히지 마라. 이미 보정한 용어는 watchouts에 반복하지 않는다.
아래 스키마의 JSON만 출력한다. 예시 count/member 개수는 실제 영상에 맞춰 완성해야 한다.
{json.dumps(schema,ensure_ascii=False)}
<전체대본>{transcript_text(doc)}</전체대본>
<검토된상세항목>{json.dumps(records,ensure_ascii=False)}</검토된상세항목>'''
    progress('핵심과 세부 설명을 주제별로 다시 엮는 중')
    raw=client(prompt)
    response_path=target.with_suffix('.response.txt');response_path.write_text(raw,encoding='utf-8')
    data=validate_brief(json.loads(raw),doc,records)
    data['_meta']={'version':VERSION,'model':client.model,'length':length,'focus':focus,'domain':domain,'source_items':len(records)}
    save_json(target,data)
    target.with_suffix('.md').write_text(brief_markdown(data),encoding='utf-8')
    return data,target


def brief_markdown(data):
    lines=['# '+clean(data['title']),'',clean(data['takeaway']),'','※ 영상 속 화자의 경험과 설명을 정리한 노트입니다.','']
    for g in data.get('frameworks',[]):
        lines+=['## '+clean(g['title']),'']
        lines += [f"{i}. {clean(m['label'])}" for i,m in enumerate(g['members'],1)]
        lines+=['']
    for section in data['sections']:
        lines+=['## '+clean(section['title']),'']
        for p in section['points']:lines+=['**'+clean(p['heading'])+'**','',clean(p['body']),'']
    if data.get('watchouts'):lines+=['## 해석할 때 참고','']+[ '- '+clean(x) for x in data['watchouts']]
    return '\n'.join(lines)


def deck_html(data):
    esc=lambda s:html.escape(clean(s))
    cards=['<section class="slide"><p class="eyebrow">영상 깊이 읽기</p><h1>'+esc(data['title'])+'</h1><p class="lead">'+esc(data['takeaway'])+'</p><p class="note">영상 속 화자의 경험과 설명을 정리했습니다.</p></section>']
    for i,s in enumerate(data['sections'],1):
        body=''.join('<article><h3>'+esc(p['heading'])+'</h3><p>'+esc(p['body'])+'</p></article>' for p in s['points'])
        cards.append('<section class="slide"><p class="eyebrow">'+f'{i:02}'+'</p><h2>'+esc(s['title'])+'</h2>'+body+'</section>')
    return '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>영상 깊이 읽기 · 카드</title><style>
*{box-sizing:border-box}body{margin:0;background:#edf1f5;color:#172a38;font-family:"Malgun Gothic",sans-serif}.slide{display:none;max-width:1060px;min-height:650px;background:#fff;margin:32px auto;padding:48px 64px;border-radius:24px;box-shadow:0 10px 40px #20304010}.slide.active{display:block}.eyebrow{color:#157f78;letter-spacing:.1em;font-weight:700}h1{font-size:42px;line-height:1.35;max-width:850px;margin-top:80px}h2{font-size:30px;margin:8px 0 28px}h3{font-size:20px;margin:0 0 8px}p{font-size:18px;line-height:1.75;margin:0}.lead{font-size:24px;margin:30px 0}.note{color:#657580;font-size:14px}article{border-left:3px solid #c8e2df;padding-left:22px;margin:22px 0}nav{display:flex;justify-content:center;gap:24px;align-items:center;margin:20px}button{padding:12px 24px;background:#176c66;color:white;border:0;border-radius:12px;font-size:16px;cursor:pointer}button:disabled{opacity:.3}button:focus-visible{outline:3px solid #d6972b;outline-offset:3px}@media(max-width:700px){.slide{margin:12px;padding:28px;min-height:75vh}h1{font-size:30px;margin-top:25px}h2{font-size:25px}p{font-size:16px}}@media print{.slide{display:block;break-after:page;box-shadow:none;margin:0;min-height:0}nav{display:none}}
</style><body>'''+''.join(cards)+'''<nav aria-label="카드 이동"><button id="prev">← 이전</button><span id="page" aria-live="polite"></span><button id="next">다음 →</button></nav><script>
const cards=[...document.querySelectorAll('.slide')];let pos=0;function show(){cards.forEach((s,i)=>s.classList.toggle('active',i===pos));document.querySelector('#page').textContent=`${pos+1} / ${cards.length}`;document.querySelector('#prev').disabled=pos===0;document.querySelector('#next').disabled=pos===cards.length-1}document.querySelector('#prev').onclick=()=>{if(pos>0)pos--;show()};document.querySelector('#next').onclick=()=>{if(pos<cards.length-1)pos++;show()};document.addEventListener('keydown',e=>{if(e.key==='ArrowRight')document.querySelector('#next').click();if(e.key==='ArrowLeft')document.querySelector('#prev').click()});show();</script></body></html>'''


def render_reader(doc, data, identity):
    import streamlit as st
    mode=st.radio('읽는 방식',['핵심 노트','카드로 보기'],horizontal=True,key='reader_mode')
    st.caption('핵심 제목 아래에 설명을 붙였습니다. 세부 근거는 필요한 부분만 펼쳐보세요.')
    if mode=='카드로 보기':
        import streamlit.components.v1 as components
        components.html(deck_html(data),height=900,scrolling=True)
    else:
        st.markdown('# '+clean(data['title']))
        st.info(clean(data['takeaway']))
        st.caption('영상 속 화자의 경험과 설명을 정리한 노트입니다.')
        for g in data.get('frameworks',[]):
            st.markdown('### '+clean(g['title']))
            st.markdown('\n'.join(f"{i}. {clean(m['label'])}" for i,m in enumerate(g['members'],1)))
        by_id={s['id']:s for s in doc['segments']}
        for sec in data['sections']:
            st.markdown('### '+clean(sec['title']))
            for point in sec['points']:
                st.markdown('**'+clean(point['heading'])+'**')
                st.markdown(clean(point['body']))
            with st.expander('원문 근거 보기 · '+clean(sec['title'])):
                ids=list(dict.fromkeys(sid for point in sec['points'] for sid in point['evidence']))
                for sid in ids:
                    s=by_id[sid];label=stamp(s['start'])
                    if doc.get('url'):st.markdown(f"[{label}]({doc['url']}&t={int(s['start'])}s)")
                    else:st.caption(label)
                    st.write(s['text'])
        if data.get('watchouts'):
            with st.expander('해석할 때 참고'):
                for x in data['watchouts']:st.write(clean(x))
    if data.get('corrections'):
        with st.expander('용어 보정 내역'):
            st.caption('읽기용 정리에만 반영했습니다. 음성 인식 원문은 그대로 보존합니다.')
            st.dataframe([{'인식 표현':c['original'],'읽기용 표현':c['corrected'],'판단 근거':c['basis']} for c in data['corrections']],hide_index=True,width='stretch')
    st.download_button('핵심 노트 저장 (.md)',brief_markdown(data),file_name='video-note.md')
    st.download_button('카드 저장 (.html)',deck_html(data),file_name='video-cards.html',mime='text/html')
