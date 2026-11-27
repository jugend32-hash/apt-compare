import base64,gzip,json,requests,streamlit as st

def endpoint():
    repo=st.secrets['GITHUB_DATA_REPO']
    return f'https://api.github.com/repos/{repo}/contents/apt-compare-data.json.gz'
def headers():
    return {'Authorization':'Bearer '+st.secrets['GITHUB_DATA_TOKEN'],'Accept':'application/vnd.github+json'}
def load():
    r=requests.get(endpoint(),headers=headers(),timeout=30)
    if r.status_code==404:
        # Verify repository access before treating missing file as initial state.
        check=requests.get('https://api.github.com/repos/'+st.secrets['GITHUB_DATA_REPO'],headers=headers(),timeout=20); check.raise_for_status()
        if not check.json().get('private'): raise ValueError('비공개 저장소 필요')
        return {},None
    r.raise_for_status(); j=r.json()
    if j.get('content'): raw=base64.b64decode(j['content'])
    else:
        q=requests.get(endpoint(),headers={**headers(),'Accept':'application/vnd.github.raw+json'},timeout=30); q.raise_for_status(); raw=q.content
    return json.loads(gzip.decompress(raw)),j['sha']
def save(data,sha):
    raw=gzip.compress(json.dumps(data,ensure_ascii=False,allow_nan=False).encode())
    body={'message':'Update apartment comparison data','content':base64.b64encode(raw).decode()}
    if sha: body['sha']=sha
    r=requests.put(endpoint(),headers=headers(),json=body,timeout=90); r.raise_for_status(); return r.json()['content']['sha']
