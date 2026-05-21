const messagesEl = document.getElementById('messages');
const chatEl = document.getElementById('chat');
const form = document.getElementById('composer-form');
const input = document.getElementById('composer-input');
const sendBtn = document.getElementById('composer-send');

let sessionId = null;
let chipsShown = false;

async function init() {
  setBusy(true);
  try {
    const res = await fetch('/api/session', { method: 'POST' });
    if (!res.ok) {
      renderAgent(`Error starting session: ${res.status}`, []);
      return;
    }
    const data = await res.json();
    sessionId = data.session_id;
    renderAgent(data.text, data.cards);
    renderChips(data.examples || []);
  } catch (err) {
    renderAgent(`Error starting session: ${err.message}`, []);
  } finally {
    setBusy(false);
    input.focus();
  }
}

function renderUser(text) {
  hideChips();
  const el = document.createElement('div');
  el.className = 'msg msg-user';
  el.textContent = text;
  messagesEl.appendChild(el);
  scrollToBottom();
}

function renderAgent(text, cards) {
  const el = document.createElement('div');
  el.className = 'msg msg-agent';

  if (text) {
    const textEl = document.createElement('div');
    textEl.className = 'msg-text';
    textEl.textContent = text;
    el.appendChild(textEl);
  }

  if (cards && cards.length) {
    const cardsEl = document.createElement('div');
    cardsEl.className = 'cards';
    for (const card of cards) cardsEl.appendChild(renderCard(card));
    el.appendChild(cardsEl);
  }

  messagesEl.appendChild(el);
  scrollToBottom();
}

function renderCard(card) {
  if (card.kind === 'small') return renderSmallCard(card);
  if (card.kind === 'big') return renderBigCard(card);
  return document.createElement('div');
}

function renderSmallCard(card) {
  const el = document.createElement('div');
  el.className = 'card card-small';
  if (card.highlight) el.classList.add(`card-${card.highlight}`);

  const show = card.show || {};
  const key = document.createElement('div');
  key.className = 'card-small-key';
  key.textContent = show.key || '';

  const summary = document.createElement('div');
  summary.className = 'card-small-summary';
  summary.textContent = show.summary || '';

  const status = document.createElement('div');
  status.className = 'card-small-status';
  status.textContent = show.status || '';

  el.appendChild(key);
  el.appendChild(summary);
  el.appendChild(status);
  return el;
}

function renderBigCard(card) {
  const show = card.show || {};
  const el = document.createElement('div');
  el.className = 'card card-big';

  const header = document.createElement('div');
  header.className = 'card-big-header';

  const keyEl = document.createElement('div');
  keyEl.className = 'card-big-key';
  keyEl.textContent = show.key || '';

  const summaryEl = document.createElement('div');
  summaryEl.className = 'card-big-summary';
  summaryEl.textContent = show.summary || '';

  const statusEl = document.createElement('div');
  statusEl.className = 'card-big-status';
  statusEl.textContent = show.next_status
    ? `${show.status || ''} → ${show.next_status}`
    : (show.status || '');

  header.appendChild(keyEl);
  header.appendChild(summaryEl);
  header.appendChild(statusEl);
  el.appendChild(header);

  const missingBySection = {};
  for (const m of (show.missing_for_next_status || [])) {
    if (!missingBySection[m.section]) missingBySection[m.section] = new Set();
    missingBySection[m.section].add(m.field);
  }

  const sections = show.sections || {};
  const sectionNames = [...new Set([
    ...Object.keys(sections),
    ...Object.keys(missingBySection),
  ])];

  for (const sectionName of sectionNames) {
    const sectionEl = document.createElement('div');
    sectionEl.className = 'card-section';

    const label = document.createElement('div');
    label.className = 'card-section-label';
    label.textContent = sectionName;
    sectionEl.appendChild(label);

    const fields = sections[sectionName] || {};
    const missing = missingBySection[sectionName] || new Set();

    for (const [field, value] of Object.entries(fields)) {
      sectionEl.appendChild(renderField(field, value, missing.has(field), false));
    }
    for (const field of missing) {
      if (Object.prototype.hasOwnProperty.call(fields, field)) continue;
      sectionEl.appendChild(renderField(field, '', true, true));
    }

    el.appendChild(sectionEl);
  }

  return el;
}

function renderField(name, value, isMissing, isBlank) {
  const el = document.createElement('div');
  el.className = 'card-field';
  if (isMissing) el.classList.add('card-field-missing');

  const label = document.createElement('span');
  label.className = 'card-field-label';
  label.textContent = name;

  el.appendChild(label);
  el.appendChild(document.createTextNode(': '));

  if (isBlank) {
    const tag = document.createElement('span');
    tag.className = 'card-field-tag';
    tag.textContent = 'missing';
    el.appendChild(tag);
  } else {
    const v = document.createElement('span');
    v.className = 'card-field-value';
    v.textContent = value;
    el.appendChild(v);
  }
  return el;
}

function renderChips(examples) {
  if (chipsShown || !examples.length) return;
  chipsShown = true;
  const wrap = document.createElement('div');
  wrap.className = 'chips';
  wrap.id = 'chips';
  for (const example of examples) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'chip';
    chip.textContent = example;
    chip.addEventListener('click', () => sendMessage(example));
    wrap.appendChild(chip);
  }
  messagesEl.appendChild(wrap);
  scrollToBottom();
}

function hideChips() {
  const chipsEl = document.getElementById('chips');
  if (chipsEl) chipsEl.remove();
}

function renderThinking() {
  const el = document.createElement('div');
  el.className = 'msg msg-agent msg-thinking';
  el.id = 'thinking';
  el.textContent = 'Thinking…';
  messagesEl.appendChild(el);
  scrollToBottom();
}

function clearThinking() {
  const el = document.getElementById('thinking');
  if (el) el.remove();
}

async function sendMessage(text) {
  if (!text || !sessionId) return;
  setBusy(true);
  renderUser(text);
  renderThinking();
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    clearThinking();
    if (!res.ok) {
      const detail = await res.text();
      renderAgent(`Error: ${res.status} ${detail}`, []);
      return;
    }
    const data = await res.json();
    renderAgent(data.text, data.cards);
  } catch (err) {
    clearThinking();
    renderAgent(`Error: ${err.message}`, []);
  } finally {
    setBusy(false);
    input.focus();
  }
}

function setBusy(busy) {
  input.disabled = busy;
  sendBtn.disabled = busy;
}

function scrollToBottom() {
  chatEl.scrollTop = chatEl.scrollHeight;
}

form.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  sendMessage(text);
});

init();
