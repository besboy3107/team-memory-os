import json, os, re, sqlite3, subprocess, time, urllib.request, urllib.error, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent

# Load .env without third-party deps
_dotenv = ROOT / '.env'
if _dotenv.exists():
    for _line in _dotenv.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ[_k.strip()] = _v.strip()

DB = Path(os.getenv('DATABASE_PATH', ROOT / 'team_memory.db'))
PORT = int(os.getenv('PORT', '8002'))
LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'openrouter')
LLM_MODEL = os.getenv('LLM_MODEL', 'google/gemini-flash-1.5')
HTTPS_PROXY = os.getenv('HTTPS_PROXY', '')
SOCKS_PROXY = os.getenv('SOCKS_PROXY', '')

if LLM_PROVIDER == 'groq':
    LLM_KEY = os.getenv('GROQ_API_KEY', '')
    LLM_URL = 'https://api.groq.com/openai/v1/chat/completions'
else:
    LLM_KEY = os.getenv('OPENROUTER_API_KEY', '')
    LLM_URL = 'https://openrouter.ai/api/v1/chat/completions'

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS documents('
    'id TEXT PRIMARY KEY,title TEXT,kind TEXT,owner TEXT,body TEXT,created_at TEXT);'
    'CREATE TABLE IF NOT EXISTS memories('
    'id TEXT PRIMARY KEY,title TEXT,decision TEXT,owner TEXT,created_at TEXT);'
    'CREATE TABLE IF NOT EXISTS questions('
    'id TEXT PRIMARY KEY,query TEXT,answer TEXT,sources TEXT,confidence REAL,'
    'needs_review INTEGER,review_reason TEXT,created_at TEXT);'
    'CREATE TABLE IF NOT EXISTS audit_runs('
    'id TEXT PRIMARY KEY,action TEXT,input TEXT,output TEXT,status TEXT,'
    'error TEXT,duration_ms INTEGER,created_at TEXT);'
)

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    for col in ('model TEXT', 'tokens INTEGER DEFAULT 0'):
        try: c.execute(f'ALTER TABLE questions ADD COLUMN {col}'); c.commit()
        except: pass
    return c

def seed(c):
    if c.execute('SELECT COUNT(*) FROM documents').fetchone()[0]: return
    for d in [
        ('doc-sales', 'Продажи: квалификация заявки', 'sales', 'Мария',
         'Квалифицированная заявка должна содержать задачу клиента, срок и следующий шаг. '
         'Если известна только цена — сначала уточняем контекст.'),
        ('doc-onboarding', 'Онбординг нового клиента', 'process', 'Ильхам',
         'После оплаты менеджер отправляет чек-лист, назначает ответственного и фиксирует '
         'дату первого результата. Договорённости сохраняются в карточке клиента.'),
        ('doc-product', 'Продукт: тарифы и ограничения', 'product', 'Команда продукта',
         'Базовый тариф включает один рабочий процесс и поддержку в рабочие часы. '
         'Интеграции оцениваются отдельно после проверки требований.'),
        ('doc-incidents', 'Инциденты: порядок эскалации', 'operations', 'Дежурный',
         'Критичный инцидент фиксируется в журнале, получает владельца и срок реакции. '
         'Если фактов недостаточно — сообщение требует проверки.')
    ]: c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)', (*d, now()))
    for m in [
        ('mem-1', 'Решение: единый формат задач',
         'Используем поля: контекст, владелец, срок, критерий готовности.', 'Ильхам'),
        ('mem-2', 'Решение: ответы клиентам',
         'Не обещаем срок и эффект без подтверждённых вводных.', 'Мария')
    ]: c.execute('INSERT INTO memories VALUES(?,?,?,?,?)', (*m, now()))
    c.commit()

def llm_call(query, docs):
    if not LLM_KEY:
        return None
    ctx = '\n\n'.join(
        f'[{d["id"]}] {d["title"]} (владелец: {d["owner"]})\n{d["body"]}' for d in docs
    )
    payload = json.dumps({
        'model': LLM_MODEL,
        'temperature': 0.2,  # курсовое требование: 0.2
        'messages': [
            {'role': 'system', 'content': (
                'Ты ИИ-ассистент корпоративной базы знаний. '
                'Отвечай ТОЛЬКО на основе предоставленных документов. '
                'Если информации недостаточно — confidence < 0.5, needs_review = true. '
                'Возвращай ТОЛЬКО валидный JSON без markdown-обёртки: '
                '{"answer":string,"sources":[{"id":string,"title":string,"owner":string}],'
                '"confidence":float,"needs_review":bool,"review_reason":string|null}'
            )},
            {'role': 'user', 'content': f'Документы базы знаний:\n{ctx}\n\nВопрос: {query}'}
        ]
    }).encode()
    headers = {
        'Authorization': f'Bearer {LLM_KEY}',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (compatible; TeamMemoryOS/2.0)',
    }
    if LLM_PROVIDER != 'groq':
        headers['HTTP-Referer'] = 'http://localhost:8002'
        headers['X-Title'] = 'Team Memory OS'
    if SOCKS_PROXY:
        cmd = [
            'curl', '-s', '--socks5', SOCKS_PROXY,
            '-X', 'POST', LLM_URL,
            '-H', f'Authorization: Bearer {LLM_KEY}',
            '-H', 'Content-Type: application/json',
            '-H', 'User-Agent: Mozilla/5.0 (compatible; TeamMemoryOS/2.0)',
            '-d', payload.decode(),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f'curl error: {proc.stderr.decode()[:200]}')
        data = json.loads(proc.stdout)
    else:
        req = urllib.request.Request(LLM_URL, data=payload, headers=headers, method='POST')
        if HTTPS_PROXY:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({'https': HTTPS_PROXY})
            )
            ctx_mgr = opener.open(req, timeout=25)
        else:
            ctx_mgr = urllib.request.urlopen(req, timeout=25)
        with ctx_mgr as resp:
            data = json.loads(resp.read())
    text = data['choices'][0]['message']['content'].strip()
    if '```' in text:
        text = re.sub(r'```\w*', '', text).strip()
    parsed = json.loads(text)
    usage = data.get('usage', {})
    return {**parsed, '_model': LLM_MODEL, '_tokens': usage.get('total_tokens', 0)}

def keyword_search(query, docs):
    terms = re.findall(r'[а-яёa-z0-9]{4,}', query.lower())
    ranked = sorted(
        [(sum(t in f"{d['title']} {d['body']}".lower() for t in terms), d) for d in docs],
        key=lambda x: x[0],
        reverse=True
    )
    ranked = [(s, d) for s, d in ranked if s]
    needs_review = (
        not ranked or len(terms) < 2
        or any(x in query.lower() for x in ('точно', 'срочно', 'неизвестно', '???'))
    )
    if ranked:
        d = ranked[0][1]
        conf = 0.58 if needs_review else min(0.95, 0.72 + ranked[0][0] * 0.08)
        return {
            'answer': f'По базе найден источник «{d["title"]}». {d["body"]}',
            'sources': [{'id': d['id'], 'title': d['title'], 'owner': d['owner']}],
            'confidence': conf, 'needs_review': needs_review,
            'review_reason': 'Уточните вопрос или проверьте ответ вручную.' if needs_review else None,
            '_model': 'keyword-search', '_tokens': 0
        }
    return {
        'answer': 'В базе знаний не найдено подтверждения для ответа.',
        'sources': [], 'confidence': 0.25, 'needs_review': True,
        'review_reason': 'Нет релевантных источников в базе.',
        '_model': 'keyword-search', '_tokens': 0
    }

def ask(p, c):
    q = str(p.get('query', '')).strip()
    if not q: raise ValueError('Поле query обязательно')
    docs = [dict(x) for x in c.execute('SELECT * FROM documents')]
    doc_ids = {d['id'] for d in docs}
    result, llm_err = None, None
    try:
        result = llm_call(q, docs)
    except Exception as e:
        llm_err = str(e)
    if result is None:
        result = keyword_search(q, docs)
        if llm_err:
            result['needs_review'] = True
            result['review_reason'] = f'LLM недоступен ({llm_err[:80]}), резервный поиск.'
    model = result.pop('_model', 'unknown')
    tokens = result.pop('_tokens', 0)
    sources = [s for s in result.get('sources', []) if s.get('id') in doc_ids]
    return {
        'query': q, 'answer': result['answer'], 'sources': sources,
        'confidence': float(result.get('confidence', 0.5)),
        'needs_review': bool(result.get('needs_review', False)),
        'review_reason': result.get('review_reason'),
        'model': model, 'tokens': tokens, 'schema_version': '1.0'
    }


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def json_out(self, data, code=200):
        b = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = urlparse(self.path).path
        c = db(); seed(c)
        if path == '/':
            b = (ROOT / 'static/index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers(); self.wfile.write(b); return
        if path.startswith('/static/'):
            f = ROOT / path.lstrip('/')
            b = f.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/css' if f.suffix == '.css' else 'application/javascript')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers(); self.wfile.write(b); return
        if path == '/api/documents':
            return self.json_out([dict(x) for x in c.execute('SELECT * FROM documents')])
        if path == '/api/memories':
            return self.json_out([dict(x) for x in c.execute('SELECT * FROM memories')])
        if path == '/api/questions':
            return self.json_out([dict(x) for x in c.execute('SELECT * FROM questions ORDER BY created_at DESC')])
        if path == '/api/audit':
            return self.json_out([dict(x) for x in c.execute('SELECT * FROM audit_runs ORDER BY created_at DESC')])
        if path == '/api/stats':
            d = c.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
            q = c.execute('SELECT COUNT(*) FROM questions').fetchone()[0]
            r = c.execute('SELECT COUNT(*) FROM questions WHERE needs_review=1').fetchone()[0]
            try:
                lq = c.execute(
                    "SELECT COUNT(*) FROM questions WHERE model != 'keyword-search' AND model IS NOT NULL"
                ).fetchone()[0]
            except: lq = 0
            return self.json_out({
                'documents': d, 'questions': q, 'review': r,
                'quality': round((q - r) / q * 100) if q else 0,
                'llm_calls': lq, 'llm_active': bool(LLM_KEY), 'llm_model': LLM_MODEL
            })
        if path == '/api/export':
            return self.json_out([dict(x) for x in c.execute('SELECT * FROM questions ORDER BY created_at DESC')])
        return self.json_out({'error': 'not_found'}, 404)

    def do_POST(self):
        if self.path != '/api/ask':
            return self.json_out({'error': 'not_found'}, 404)
        start = time.perf_counter()
        try:
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}'
            p = json.loads(raw)
        except json.JSONDecodeError:
            return self.json_out({'error': 'Некорректный JSON'}, 400)
        c = db(); seed(c)
        try:
            r = ask(p, c)
            rid = str(uuid.uuid4()); r['id'] = rid
            ms = round((time.perf_counter() - start) * 1000)
            c.execute(
                'INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)',
                (rid, r['query'], r['answer'],
                 json.dumps(r['sources'], ensure_ascii=False),
                 r['confidence'], int(r['needs_review']), r['review_reason'],
                 now(), r.get('model'), r.get('tokens', 0))
            )
            c.execute(
                'INSERT INTO audit_runs VALUES(?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()), 'knowledge.ask',
                 json.dumps(p, ensure_ascii=False),
                 json.dumps(r, ensure_ascii=False),
                 'success', None, ms, now())
            )
            c.commit()
            self.json_out(r, 201)
        except Exception as e:
            self.json_out({'error': str(e)}, 400)


if __name__ == '__main__':
    c = db(); seed(c); c.close()
    mode = f'LLM={LLM_MODEL} via {LLM_PROVIDER}' if LLM_KEY else 'режим: keyword-search (нет API ключа)'
    print(f'Team Memory OS: http://localhost:{PORT}  [{mode}]')
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
