const messagesEl = document.getElementById('messages');
const chatEl = document.getElementById('chat');
const form = document.getElementById('composer-form');
const input = document.getElementById('composer-input');
const sendBtn = document.getElementById('composer-send');

let sessionId = null;
let chipsShown = false;
let busy = false;

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
    // A multi-row list of plain show cards becomes one scrollable show-list
    // element (shows ~4 rows, scrolls for the rest). Single results, big
    // cards, and post-mutation highlighted cards render standalone.
    const isShowList =
      cards.length >= 2 && cards.every((c) => c.kind === 'small' && !c.highlight);
    if (isShowList) {
      cardsEl.appendChild(renderShowList(cards));
    } else {
      for (const card of cards) cardsEl.appendChild(renderCard(card));
    }
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

function renderShowList(cards) {
  const box = document.createElement('div');
  box.className = 'show-list';
  for (const card of cards) box.appendChild(renderSmallCard(card));
  return box;
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

  // Click a card to open its editable form card — typing still works too.
  if (show.key) {
    el.classList.add('card-clickable');
    el.title = `Open ${show.key}`;
    el.addEventListener('click', () => openShowCard(show.key));
  }
  return el;
}

async function openShowCard(key) {
  if (busy) return;
  setBusy(true);
  renderThinking();
  try {
    const res = await fetch(`/api/show/${encodeURIComponent(key)}`);
    clearThinking();
    if (!res.ok) {
      renderAgent(`Couldn't open ${key}.`, []);
      return;
    }
    const data = await res.json();
    renderAgent('', [{ kind: 'big', show: data.show }]);
  } catch (err) {
    clearThinking();
    renderAgent(`Error: ${err.message}`, []);
  } finally {
    setBusy(false);
  }
}

// The big card is the editable show form. It owns local UI state (edit mode,
// which sections are expanded) and re-renders itself in place on every change.
function renderBigCard(card) {
  const el = document.createElement('div');
  el.className = 'card card-big';
  const state = { editing: false, expanded: new Set(), error: null, show: card.show || {} };
  rebuildBigCard(el, state);
  return el;
}

function rebuildBigCard(el, state) {
  const show = state.show;
  el.innerHTML = '';
  el.classList.toggle('card-editing', state.editing);

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
  header.append(keyEl, summaryEl, statusEl);
  el.appendChild(header);

  for (const sec of (show.form || [])) {
    el.appendChild(renderFormSection(el, state, sec));
  }

  if (state.error) {
    const err = document.createElement('div');
    err.className = 'card-error';
    err.textContent = state.error;
    el.appendChild(err);
  }

  const actions = document.createElement('div');
  actions.className = 'card-big-actions';
  if (state.editing) {
    actions.appendChild(cardButton('Save changes', 'btn-primary', () => saveEdits(el, state)));
    actions.appendChild(cardButton('Cancel', 'btn-ghost', () => {
      state.editing = false;
      state.error = null;
      rebuildBigCard(el, state);
    }));
  } else {
    const hint = document.createElement('span');
    hint.className = 'card-edit-hint';
    hint.textContent = 'Click to edit';
    actions.appendChild(hint);
    if (show.next_status) {
      const ready = (show.missing_for_next_status || []).length === 0;
      const trans = cardButton(`Transition to ${show.next_status}`, 'btn-primary', () => doTransition(el, state));
      trans.disabled = !ready;
      trans.title = ready ? '' : 'Fill all required fields and save first';
      actions.appendChild(trans);
    }
  }
  el.appendChild(actions);

  // View mode: clicking the card (not a section toggle or button) enters edit
  // mode and expands sections that still need information.
  el.onclick = state.editing ? null : (e) => {
    if (e.target.closest('.card-section-head') || e.target.closest('button')) return;
    state.editing = true;
    state.error = null;
    for (const sec of (show.form || [])) {
      if (sec.has_missing) state.expanded.add(sec.section);
    }
    rebuildBigCard(el, state);
  };
}

function renderFormSection(el, state, sec) {
  const wrap = document.createElement('div');
  wrap.className = 'card-section';
  const expanded = state.expanded.has(sec.section);

  const head = document.createElement('button');
  head.type = 'button';
  head.className = 'card-section-head';
  if (sec.has_missing) head.classList.add('section-missing');

  const chevron = document.createElement('span');
  chevron.className = 'card-section-chevron';
  chevron.textContent = expanded ? '▾' : '▸';

  const title = document.createElement('span');
  title.className = 'card-section-title';
  title.textContent = sec.section.toUpperCase();

  head.append(chevron, title);
  if (sec.has_missing) {
    const badge = document.createElement('span');
    badge.className = 'card-section-badge';
    badge.textContent = 'missing info';
    head.appendChild(badge);
  }
  head.addEventListener('click', (e) => {
    e.stopPropagation();
    if (expanded) state.expanded.delete(sec.section);
    else state.expanded.add(sec.section);
    rebuildBigCard(el, state);
  });
  wrap.appendChild(head);

  if (expanded) {
    const body = document.createElement('div');
    body.className = 'card-section-body';
    for (const fld of sec.fields) {
      body.appendChild(state.editing ? renderFieldInput(sec.section, fld) : renderFieldView(fld));
    }
    wrap.appendChild(body);
  }
  return wrap;
}

function renderFieldView(fld) {
  const row = document.createElement('div');
  row.className = 'card-field';
  if (fld.missing) row.classList.add('card-field-missing');

  const label = document.createElement('span');
  label.className = 'card-field-label';
  label.textContent = fld.name;
  row.append(label, document.createTextNode(': '));

  if (fld.value) {
    const v = document.createElement('span');
    v.className = 'card-field-value';
    v.textContent = fld.value;
    row.appendChild(v);
  } else if (fld.missing) {
    const tag = document.createElement('span');
    tag.className = 'card-field-tag';
    tag.textContent = 'missing';
    row.appendChild(tag);
  } else {
    const dash = document.createElement('span');
    dash.className = 'card-field-empty';
    dash.textContent = '—';
    row.appendChild(dash);
  }
  return row;
}

function renderFieldInput(section, fld) {
  const row = document.createElement('label');
  row.className = 'card-field-edit';
  if (fld.missing) row.classList.add('card-field-missing');

  const label = document.createElement('span');
  label.className = 'card-field-label';
  label.textContent = fld.name;

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'card-field-input';
  input.value = fld.value || '';
  input.dataset.section = section;
  input.dataset.field = fld.name;
  if (fld.missing) input.placeholder = 'required for next status';

  row.append(label, input);
  return row;
}

function cardButton(text, cls, onclick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = `card-btn ${cls}`;
  b.textContent = text;
  b.addEventListener('click', (e) => {
    e.stopPropagation();
    onclick();
  });
  return b;
}

function disableCardActions(el) {
  el.querySelectorAll('.card-big-actions button').forEach((b) => { b.disabled = true; });
}

async function saveEdits(el, state) {
  const fields = {};
  el.querySelectorAll('.card-field-input').forEach((inp) => {
    const val = inp.value.trim();
    if (!val) return;
    const sec = inp.dataset.section;
    if (!fields[sec]) fields[sec] = {};
    fields[sec][inp.dataset.field] = val;
  });

  state.error = null;
  disableCardActions(el);
  try {
    const res = await fetch(`/api/show/${encodeURIComponent(state.show.key)}/update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fields }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      state.error = data.error || `Save failed (${res.status})`;
      rebuildBigCard(el, state);
      return;
    }
    state.show = data.updated;
    state.editing = false;
    rebuildBigCard(el, state);
  } catch (err) {
    state.error = err.message;
    rebuildBigCard(el, state);
  }
}

async function doTransition(el, state) {
  const target = state.show.next_status;
  if (!target) return;
  state.error = null;
  disableCardActions(el);
  try {
    const res = await fetch(`/api/show/${encodeURIComponent(state.show.key)}/transition`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_status: target }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      state.error = data.error || `Transition failed (${res.status})`;
      rebuildBigCard(el, state);
      return;
    }
    state.show = data.show;
    state.expanded = new Set();
    rebuildBigCard(el, state);
  } catch (err) {
    state.error = err.message;
    rebuildBigCard(el, state);
  }
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
  if (!text || !sessionId || busy) return;
  document.body.classList.remove('state-empty');
  document.body.classList.add('state-active');
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

function setBusy(value) {
  busy = value;
  input.disabled = value;
  sendBtn.disabled = value;
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
