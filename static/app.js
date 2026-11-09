let questions = [], docs = [], memories = [];

const $ = s => document.querySelector(s);
const api = async (path, opts) => (await fetch(path, opts)).json();

function badge(q) {
  return q.needs_review
    ? '<span class="badge review">НУЖНА ПРОВЕРКА</span>'
    : '<span class="badge ok">ПРОВЕРЕНО</span>';
}

function modelBadge(model) {
  if (!model || model === 'keyword-search') return '';
  return `<span class="badge model">◈ ${model.split('/').pop()}</span>`;
}

function confBar(conf) {
  const pct = Math.round(conf * 100);
  const cls = pct < 50 ? 'low' : pct < 72 ? 'mid' : '';
  return `<div class="conf-bar"><div class="conf-fill ${cls}" style="width:${pct}%"></div></div>`;
}

function render() {
  $('#feed').innerHTML = questions.slice(0, 6).map(q => `
    <article class="tile" data-id="${q.id}">
      ${badge(q)}
      <h3>${q.answer.length > 110 ? q.answer.slice(0, 107) + '…' : q.answer}</h3>
      <p>Вопрос: ${q.query}</p>
      ${confBar(q.confidence)}
      <div class="meta">
        ${Math.round(q.confidence * 100)}% уверенности
        · ${new Date(q.created_at).toLocaleDateString('ru-RU')}
        ${q.model && q.model !== 'keyword-search' ? ` · ${q.model.split('/').pop()}` : ''}
      </div>
    </article>
  `).join('') || '<p style="color:var(--muted);padding:20px 0">Задайте первый вопрос базе знаний.</p>';

  $('#documents').innerHTML = docs.map(d => `
    <article class="tile" style="cursor:default">
      <span class="badge kind">${d.kind}</span>
      <h3 style="margin-top:10px">${d.title}</h3>
      <p style="margin:6px 0 10px">${d.body ? d.body.slice(0, 100) + (d.body.length > 100 ? '…' : '') : ''}</p>
      <div class="meta">Владелец: ${d.owner}</div>
    </article>
  `).join('');

  $('#memories').innerHTML = memories.map(m => `
    <article class="tile" style="cursor:default">
      <span class="badge model">MEMORY</span>
      <h3 style="margin-top:10px">${m.title}</h3>
      <p>${m.decision}</p>
      <div class="meta">Владелец: ${m.owner}</div>
    </article>
  `).join('');

  document.querySelectorAll('[data-id]').forEach(el => {
    el.onclick = () => {
      const q = questions.find(x => x.id === el.dataset.id);
      if (q) detail(q);
    };
  });
}

function detail(q) {
  const sources = typeof q.sources === 'string' ? JSON.parse(q.sources) : (q.sources || []);
  $('#mtitle').textContent = q.query;
  $('#mbody').innerHTML = `
    <div class="detail">
      <b>ОТВЕТ</b>
      <p>${q.answer}</p>
      ${q.model ? `<div style="margin-top:10px;display:flex;align-items:center;gap:8px">
        ${modelBadge(q.model)}
        ${q.tokens ? `<span style="font:10px 'DM Mono';color:var(--muted)">${q.tokens} токенов</span>` : ''}
      </div>` : ''}
    </div>
    <div class="detail">
      <b>УВЕРЕННОСТЬ — ${Math.round(q.confidence * 100)}%</b>
      ${confBar(q.confidence)}
    </div>
    <div class="detail">
      <b>ИСТОЧНИКИ</b>
      ${sources.length
        ? sources.map(s => `<div class="source">${s.title}<small>Владелец: ${s.owner}</small></div>`).join('')
        : '<p style="color:var(--muted)">Источников недостаточно для ответа.</p>'}
    </div>
    ${q.needs_review ? `
    <div class="detail">
      <b style="color:var(--amber)">РУЧНАЯ ПРОВЕРКА</b>
      <p>${q.review_reason || 'Ответ требует дополнительной проверки.'}</p>
    </div>` : ''}
  `;
  $('#modal').classList.remove('hidden');
}

async function refresh() {
  const [stats, qs, ds, ms] = await Promise.all([
    api('/api/stats'),
    api('/api/questions'),
    api('/api/documents'),
    api('/api/memories')
  ]);
  questions = qs; docs = ds; memories = ms;

  const dot = $('#llm-dot');
  const modelEl = $('#llm-model-name');
  if (stats.llm_active) {
    dot.classList.add('active');
    modelEl.textContent = stats.llm_model.split('/').pop();
    modelEl.classList.remove('off');
  } else {
    dot.classList.remove('active');
    modelEl.textContent = 'keyword fallback';
    modelEl.classList.add('off');
  }

  const qClass = stats.quality >= 70 ? '' : stats.quality >= 40 ? ' warn' : ' danger';
  const rClass = stats.review === 0 ? '' : stats.review <= 3 ? ' warn' : ' danger';
  $('#stats').innerHTML = [
    ['ДОКУМЕНТОВ',       stats.documents,              ''],
    ['ВОПРОСОВ',         stats.questions,              ''],
    ['НУЖНА ПРОВЕРКА',   stats.review,                 rClass],
    ['КАЧЕСТВО',         stats.quality + '%',          qClass]
  ].map(([lbl, val, cls]) => `
    <div class="stat${cls}">
      <small>${lbl}</small>
      <strong>${val}</strong>
    </div>
  `).join('');

  render();
}

async function loadAudit() {
  const entries = await api('/api/audit');
  $('#auditList').innerHTML = entries.length
    ? entries.map(e => {
        let query = '';
        try { query = JSON.parse(e.input || '{}').query || ''; } catch {}
        let model = '';
        try {
          const out = JSON.parse(e.output || '{}');
          model = out.model && out.model !== 'keyword-search' ? out.model.split('/').pop() : '';
        } catch {}
        const isErr = e.status !== 'success';
        const isSlow = e.duration_ms > 3000;
        const msClass = isSlow ? 'slow' : 'ok';
        const dt = new Date(e.created_at).toLocaleString('ru-RU', {day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
        return `
          <div class="audit-entry">
            <div class="audit-dot${isErr ? ' error' : ''}"></div>
            <div class="audit-content">
              <div class="audit-query">${query || e.action}</div>
              <div class="audit-meta">
                <span>${dt}</span>
                <span class="audit-ms ${msClass}">${e.duration_ms} мс</span>
                ${model ? `<span class="badge model" style="font-size:9px;padding:2px 6px">◈ ${model}</span>` : ''}
                <span class="${isErr ? 'error' : 'ok'}">${e.status}</span>
              </div>
            </div>
          </div>
        `;
      }).join('')
    : '<p style="color:var(--muted);padding:20px 0">Аудит появится после первого вопроса.</p>';
}

function nav(page) {
  document.querySelectorAll('.page').forEach(s => s.classList.toggle('hidden', s.id !== page));
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.page === page));
  if (page === 'audit') loadAudit();
}

// Ctrl/Cmd+Enter для быстрой отправки
$('#query').addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') $('#ask').click();
});

document.querySelectorAll('nav button').forEach(b => b.onclick = () => nav(b.dataset.page));

$('#close').onclick = () => $('#modal').classList.add('hidden');
$('#modal').addEventListener('click', e => { if (e.target === $('#modal')) $('#modal').classList.add('hidden'); });

$('#ask').onclick = async () => {
  const q = $('#query').value.trim();
  if (!q) {
    const st = $('#status');
    st.textContent = 'Введите вопрос.';
    st.className = 'error';
    return;
  }
  const btn = $('#ask');
  const st = $('#status');
  btn.disabled = true;
  $('#ask-label').textContent = 'Анализирую';
  st.textContent = 'Запрос к базе знаний…';
  st.className = '';

  const result = await api('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: q })
  });

  btn.disabled = false;
  $('#ask-label').textContent = 'Найти ответ';

  if (result.error) {
    st.textContent = 'Ошибка: ' + result.error;
    st.className = 'error';
  } else if (result.needs_review) {
    st.textContent = '⚠ Ответ требует ручной проверки.';
    st.className = 'review';
  } else {
    const pct = Math.round(result.confidence * 100);
    st.textContent = `✓ Ответ найден · ${pct}% уверенности`;
    st.className = '';
  }

  $('#query').value = '';
  await refresh();
};

$('#export').onclick = async () => {
  const data = await api('/api/export');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  a.download = `team-memory-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
};

refresh();
