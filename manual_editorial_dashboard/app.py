"""Authenticated, day-scoped private Migration OSINT editorial dashboard."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from core import collect
from storage import GitHubStore, StoreError

TZ = ZoneInfo('Europe/Budapest')
st.set_page_config(page_title='Migration OSINT | Privát szerkesztő', page_icon='🛡️', layout='wide')
st.title('Migration OSINT – Manuális szerkesztői dashboard')
st.caption('Egy kiválasztott nap · eredeti szöveg és magyar fordítás · auditált döntések')


def setting(name, default=''):
    """Support environment variables and Streamlit server-side secrets."""
    value = os.environ.get(name)
    if value is not None and value != '':
        return value
    try:
        return st.secrets.get(name, default)
    except (FileNotFoundError, KeyError):
        return default


# No archive access before authenticated and authorized Google OIDC session.
if not hasattr(st, 'user') or not st.user.is_logged_in:
    st.info('Jelentkezz be az engedélyezett Google-fiókkal.')
    if st.button('Bejelentkezés Google-fiókkal'):
        st.login('google')
    st.stop()

allowed = str(setting('EDITORIAL_ALLOWED_EMAIL')).strip().lower()
email = str(st.user.get('email', '')).strip().lower()
verified = st.user.get('email_verified', False)
if not allowed or email != allowed or verified not in (True, 'true', 'True'):
    st.error('Nincs jogosultságod a szerkesztői felülethez.')
    if st.button('Kijelentkezés'):
        st.logout()
    st.stop()

with st.sidebar:
    st.success('Azonosított szerkesztő')
    st.caption(email)
    if st.button('Kijelentkezés'):
        st.logout()

# storage.py reads environment variables. Populate only on the server, after authorization.
for name in ('EDITORIAL_PRIVATE_REPO', 'EDITORIAL_GITHUB_TOKEN'):
    value = str(setting(name)).strip()
    if value:
        os.environ[name] = value
try:
    store = GitHubStore()
except StoreError as exc:
    st.error(str(exc))
    st.stop()

today = datetime.now(TZ).date()
selected = st.date_input('Ellenőrzendő nap', value=today-timedelta(days=1), max_value=today, format='YYYY-MM-DD')
if isinstance(selected, tuple):
    st.error('Egyszerre pontosan egy nap választható.')
    st.stop()
day = selected.isoformat()

try:
    data, _ = store.read(f'archive/{day}.json', default=None)
    overrides, sha = store.read(f'reviews/{day}.json', default={'schema_version': 1, 'date': day, 'decisions': {}})
except (StoreError, ValueError) as exc:
    st.error(f'GitHub-adatok olvasása sikertelen: {exc}')
    st.stop()

if data is None:
    st.warning('Erre a napra még nincs privát nyersarchívum.')
    st.stop()
if not isinstance(data, dict) or data.get('date') != day:
    st.error('Az archívum dátuma vagy formátuma hibás.')
    st.stop()
if not isinstance(overrides, dict) or overrides.get('date') != day or not isinstance(overrides.get('decisions'), dict):
    st.error('A döntési napló hibás vagy másik naphoz tartozik.')
    st.stop()

entries = collect(data, selected)
decisions = overrides['decisions']
reviewed = sum(e['key'] in decisions for e in entries)
a, b, c = st.columns(3)
a.metric('Rekordok', len(entries))
b.metric('Ellenőrizve', reviewed)
c.metric('Hátralévő', len(entries)-reviewed)
st.caption('A 2026-10-17 előtti rekordok nem tekinthetők automatikusan AI-ellenőrzöttnek.')

@st.cache_data(show_spinner=False, ttl=86400)
def translate(text, model):
    token = str(setting('OPENAI_API_KEY')).strip()
    if not token:
        raise RuntimeError('A fordításhoz OPENAI_API_KEY szükséges.')
    response = requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers={'Authorization': f'Bearer {token}'},
        json={'model': model, 'temperature': 0, 'messages': [
            {'role': 'system', 'content': 'Fordítsd hűen magyarra a szöveget. Ne ellenőrizd az állításokat, ne egészítsd ki, és ne hagyj el információt. Őrizd meg a dátumokat, számokat és a bizonytalanságot.'},
            {'role': 'user', 'content': text[:16000]},
        ]},
        timeout=75,
    )
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content']

for entry in entries:
    record = entry['record']
    key = entry['key']
    prev = decisions.get(key, {})
    if not isinstance(prev, dict):
        st.error(f'Hibás korábbi döntés: {key}')
        continue
    old_key = entry['legacy_key']
    old_review = decisions.get(old_key) if old_key != key else None
    text = str(record.get('text') or record.get('representative_text') or record.get('content') or record.get('description') or '')
    title = record.get('event_type') or record.get('title') or 'Esemény'
    with st.expander(f"{key} · {title} · {'✓' if prev else '○'}"):
        st.caption(f"Forráslista: {entry['section']} · Gyűjtés: {record.get('collected_at') or record.get('first_collected_at') or '-'}")
        ai = record.get('ai_editorial_decision')
        st.caption(f"AI-döntés: {ai if ai else 'Nem szerepel a nyersarchívumban / nincs ellenőrizve'}")
        if old_review is not None:
            st.warning('Ehhez a rekordhoz egy korábbi kulcsformátumú döntés is tartozhat. Az összerendelést ellenőrizni kell; a rendszer nem másolja át automatikusan.')
        left, right = st.columns(2)
        with left:
            st.text_area('Eredeti bejegyzés', text, disabled=True, height=200, key='original-'+key)
        with right:
            hu_key = 'hu-'+day+'-'+key
            if st.button('Magyar fordítás', key='translate-'+key, disabled=not bool(text.strip())):
                try:
                    st.session_state[hu_key] = translate(text, str(setting('EDITORIAL_TRANSLATION_MODEL', 'gpt-4.1-mini')))
                except (requests.RequestException, RuntimeError, KeyError, IndexError, ValueError) as exc:
                    st.error(f'Fordítási hiba: {exc}')
            st.text_area('Magyarul (gépi fordítás)', st.session_state.get(hu_key, ''), disabled=True, height=200, key='translated-'+key)
        url = record.get('source_url') or record.get('url')
        if isinstance(url, str) and url.startswith(('https://', 'http://')):
            st.link_button('Eredeti forrás', url)
        with st.form('review-'+day+'-'+key):
            options = ['PUBLISH', 'REVIEW', 'EXCLUDE']
            decision = st.selectbox('Szerkesztői döntés', options, index=options.index(prev['decision']) if prev.get('decision') in options else 1)
            location = st.text_input('Helyszín javítása', value=prev.get('location', ''))
            event_type = st.text_input('Eseménytípus javítása', value=prev.get('event_type', ''))
            summary = st.text_area('Magyar összefoglaló / javítás', value=prev.get('summary_hu', ''))
            note = st.text_area('Indoklás (kötelező)', value=prev.get('note', ''))
            save = st.form_submit_button('Döntés mentése')
        if save:
            if not note.strip():
                st.error('Adj meg indoklást.')
            else:
                # Keep a per-record history; the current decision remains available for future publication.
                timestamp = datetime.now(TZ).isoformat()
                history = list(prev.get('history', [])) if isinstance(prev.get('history', []), list) else []
                if prev:
                    history.append({k: v for k, v in prev.items() if k != 'history'})
                new = dict(overrides)
                new['decisions'] = dict(decisions)
                new['decisions'][key] = {
                    'decision': decision, 'note': note.strip(), 'location': location.strip(),
                    'event_type': event_type.strip(), 'summary_hu': summary.strip(),
                    'reviewed_by': email, 'reviewed_at': timestamp,
                    'original_ai_decision': ai, 'source_url': url,
                    'record_id': key, 'section': entry['section'], 'history': history,
                }
                try:
                    store.write(f'reviews/{day}.json', new, sha)
                    st.success('A döntés mentve a privát GitHub-repóba.')
                    st.rerun()
                except StoreError as exc:
                    st.error(str(exc))

st.caption('A kézi döntések kizárólag a privát repóba kerülnek. A nyilvános dashboard alkalmazása külön ellenőrzött lépés.')
