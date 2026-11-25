import { $, api, el, icon, jobCard, emptyState, loading, openDialog, toast, busy } from './ui.js?v=20261120-2';
import { credentialProblem, usernameProblem, loginProblem, normalizeUsername, date, APPLICATION_STATUSES, statusName, statusCounts } from './logic.mjs?v=20261120-2';

// Accounts use an HttpOnly session cookie; this module only tracks who is signed in and which jobs are saved.
export function initAccount(context) {
  let user = null, mode = 'login', pendingSave = null, filter = 'all', items = [];
  const saved = new Set();
  const buttons = new Map();

  function sync(id) {
    const set = buttons.get(id); if (!set) return;
    for (const button of [...set]) {
      if (!button.isConnected && button.dataset.mounted) { set.delete(button); continue; }
      const on = saved.has(id);
      button.setAttribute('aria-pressed', String(on));
      button.replaceChildren(icon(on ? 'bookmark-filled' : 'bookmark'), on ? 'Đã lưu' : 'Lưu');
    }
  }
  const syncAll = () => buttons.forEach((_, id) => sync(id));

  function saveButton(job) {
    const id = job.job_id || job.id;
    const button = el('button', { class: 'text-button save-button', type: 'button', 'aria-pressed': 'false', 'aria-label': `Lưu việc làm: ${job.title}`, onclick: () => toggle(id) });
    if (!buttons.has(id)) buttons.set(id, new Set());
    buttons.get(id).add(button);
    queueMicrotask(() => { button.dataset.mounted = '1'; });
    sync(id);
    return button;
  }

  async function toggle(id) {
    if (!user) { pendingSave = id; openAuth('login', 'Đăng nhập để lưu việc làm và xem lại sau.'); return; }
    const on = saved.has(id);
    if (on) saved.delete(id); else saved.add(id);
    sync(id);
    try {
      await api(`/me/saved-jobs/${encodeURIComponent(id)}`, { method: on ? 'DELETE' : 'PUT' });
      toast(on ? 'Đã bỏ lưu việc làm.' : 'Đã lưu việc làm.');
      if (location.hash === '#saved') renderSaved();
    } catch (error) {
      if (on) saved.add(id); else saved.delete(id);
      sync(id);
      if (error.status === 401) { setUser(null); pendingSave = id; openAuth('login', 'Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.'); }
      else toast(error.message);
    }
  }

  async function loadSaved() {
    if (!user) { saved.clear(); syncAll(); return []; }
    const data = await api('/me/saved-jobs');
    saved.clear(); data.items.forEach(item => saved.add(item.job.id));
    syncAll();
    return data.items;
  }

  function statusControl(item) {
    const job = item.job;
    const select = el('select', { class: 'status-select', 'aria-label': `Trạng thái ứng tuyển: ${job.title}` },
      APPLICATION_STATUSES.map(([key, label]) => el('option', { value: key, text: label })));
    select.value = item.status;
    const stamp = el('span', { class: 'status-stamp', text: `Đổi trạng thái ${date(item.status_updated_at)}` });
    select.addEventListener('change', async () => {
      const previous = item.status, next = select.value;
      item.status = next; select.disabled = true;
      try {
        await api(`/me/saved-jobs/${encodeURIComponent(job.id)}`, { method: 'PATCH', json: { status: next } });
        item.status_updated_at = new Date().toISOString();
        toast(`Đã chuyển sang “${statusName(next)}”.`);
        drawSaved();
      } catch (error) {
        item.status = previous; select.value = previous;
        if (error.status === 401) { setUser(null); renderSaved(); return; }
        toast(error.message);
      } finally { select.disabled = false; }
    });
    return el('div', { class: `status-row status-${item.status}` }, el('label', { class: 'status-label' }, el('span', { text: 'Trạng thái' }), select), stamp);
  }

  function drawSaved() {
    const host = $('saved-jobs'), counts = statusCounts(items);
    if (filter !== 'all' && !counts[filter]) filter = 'all';
    const chip = (key, label, count) => el('button', { class: 'status-filter', type: 'button', 'aria-pressed': String(filter === key), onclick: () => { filter = key; drawSaved(); } }, label, el('span', { class: 'status-filter-count', text: String(count) }));
    const filters = el('div', { class: 'status-filters', role: 'group', 'aria-label': 'Lọc theo trạng thái ứng tuyển' },
      chip('all', 'Tất cả', items.length), APPLICATION_STATUSES.map(([key, label]) => chip(key, label, counts[key])));
    const shown = filter === 'all' ? items : items.filter(item => item.status === filter);
    const cards = shown.map(item => {
      const card = jobCard(item.job, context);
      card.classList.add('saved-card');
      card.prepend(statusControl(item));
      return card;
    });
    host.replaceChildren(filters, ...cards);
  }

  async function renderSaved() {
    const host = $('saved-jobs');
    if (!user) {
      host.replaceChildren(emptyState('Đăng nhập để xem việc đã lưu', 'Lưu các tin bạn quan tâm để xem lại hoặc ứng tuyển sau.', 'bookmark', el('button', { class: 'button primary', type: 'button', onclick: () => openAuth('login') }, 'Đăng nhập'), 'h2'));
      return;
    }
    host.replaceChildren(loading('Đang tải việc đã lưu…'));
    try {
      items = await loadSaved();
      $('saved-count').textContent = String(items.length);
      if (!items.length) { host.replaceChildren(emptyState('Chưa lưu việc làm nào', 'Bấm “Lưu” trên một tin tuyển dụng để thêm vào đây.', 'bookmark', el('a', { class: 'button secondary', href: '#jobs' }, 'Tìm việc làm'), 'h2')); return; }
      drawSaved();
    } catch (error) {
      if (error.status === 401) { setUser(null); renderSaved(); return; }
      host.replaceChildren(emptyState('Chưa tải được việc đã lưu', error.message, 'info', null, 'h2'));
    }
  }

  function renderAccount() {
    const host = $('account-area');
    $('saved-count').textContent = user ? String(saved.size) : '';
    $('saved-count').hidden = !user;
    if (!user) {
      host.replaceChildren(el('button', { class: 'button secondary account-login', type: 'button', onclick: () => openAuth('login') }, icon('user'), 'Đăng nhập'));
      return;
    }
    const menu = el('details', { class: 'account-menu' });
    const close = () => { menu.open = false; };
    menu.append(
      el('summary', { 'aria-label': `Tài khoản: ${user.username}` }, icon('user'), el('span', { class: 'account-email', text: user.username }), icon('chevron-down')),
      el('div', { class: 'account-menu-panel' },
        el('p', { class: 'account-menu-email', text: user.email }),
        el('a', { class: 'account-menu-item', href: '#saved', onclick: close }, icon('bookmark'), 'Việc đã lưu'),
        el('button', { class: 'account-menu-item', type: 'button', onclick: () => { close(); logout(); } }, icon('logout'), 'Đăng xuất'),
        el('button', { class: 'account-menu-item danger', type: 'button', onclick: () => { close(); openDialog('delete-account-dialog'); } }, icon('trash'), 'Xóa tài khoản')));
    menu.addEventListener('keydown', event => { if (event.key === 'Escape' && menu.open) { event.preventDefault(); close(); menu.querySelector('summary').focus(); } });
    host.replaceChildren(menu);
  }

  function setUser(value) {
    user = value;
    if (!user) { saved.clear(); syncAll(); }
    renderAccount();
  }

  function setMode(next) {
    mode = next;
    const register = mode === 'register';
    $('auth-title').textContent = register ? 'Tạo tài khoản' : 'Đăng nhập';
    $('auth-submit').textContent = register ? 'Tạo tài khoản' : 'Đăng nhập';
    $('auth-password').setAttribute('autocomplete', register ? 'new-password' : 'current-password');
    $('auth-email-label').textContent = register ? 'Email' : 'Email hoặc tên người dùng';
    $('auth-email').type = register ? 'email' : 'text';
    $('auth-email').setAttribute('autocomplete', register ? 'email' : 'username');
    $('auth-username-field').hidden = !register;
    $('auth-username').required = register;
    $('auth-hint').hidden = !register;
    $('auth-switch-text').textContent = register ? 'Đã có tài khoản?' : 'Chưa có tài khoản?';
    $('auth-switch').textContent = register ? 'Đăng nhập' : 'Đăng ký';
    $('auth-error').hidden = true;
  }

  function openAuth(next = 'login', reason = '') {
    setMode(next);
    $('auth-reason').textContent = reason; $('auth-reason').hidden = !reason;
    $('auth-password').value = '';
    openDialog('auth-dialog');
    $('auth-email').focus();
  }

  async function submit(event) {
    event.preventDefault();
    const email = $('auth-email').value.trim(), password = $('auth-password').value;
    const register = mode === 'register', username = normalizeUsername($('auth-username').value);
    const problem = register ? credentialProblem(email, password) || usernameProblem(username) : loginProblem(email, password);
    const error = $('auth-error');
    if (problem) { error.textContent = problem; error.hidden = false; return; }
    const button = $('auth-submit');
    busy(button, true, register ? 'Đang tạo tài khoản…' : 'Đang đăng nhập…');
    try {
      const account = await api(`/auth/${register ? 'register' : 'login'}`, { method: 'POST', json: register ? { email, username, password } : { login: email, password } });
      const next = pendingSave; pendingSave = null;
      setUser(account);
      $('auth-dialog').close();
      $('auth-password').value = '';
      toast(register ? 'Đã tạo tài khoản.' : 'Đã đăng nhập.');
      await loadSaved().catch(() => []);
      renderAccount();
      if (next && !saved.has(next)) await toggle(next);
      if (location.hash === '#saved') renderSaved();
    } catch (failure) { error.textContent = failure.message; error.hidden = false; }
    finally { busy(button, false, register ? 'Tạo tài khoản' : 'Đăng nhập'); button.replaceChildren(register ? 'Tạo tài khoản' : 'Đăng nhập'); }
  }

  async function logout() {
    try { await api('/auth/logout', { method: 'POST' }); } catch (error) { toast(error.message); return; }
    setUser(null); toast('Đã đăng xuất.');
    if (location.hash === '#saved') renderSaved();
  }

  async function deleteAccount() {
    const button = $('confirm-delete-account');
    button.disabled = true;
    try {
      await api('/auth/me', { method: 'DELETE' });
      $('delete-account-dialog').close();
      setUser(null); toast('Đã xóa tài khoản và các việc đã lưu.');
      if (location.hash === '#saved') renderSaved();
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  }

  $('auth-form').addEventListener('submit', submit);
  $('auth-switch').addEventListener('click', () => { setMode(mode === 'login' ? 'register' : 'login'); $('auth-email').focus(); });
  $('auth-dialog').addEventListener('close', () => { pendingSave = null; });
  $('confirm-delete-account').addEventListener('click', deleteAccount);
  document.addEventListener('click', event => {
    const menu = document.querySelector('.account-menu[open]');
    if (menu && !menu.contains(event.target)) menu.open = false;
  });

  renderAccount();
  const ready = api('/auth/me').then(setUser, () => setUser(null)).then(() => loadSaved().catch(() => [])).then(renderAccount);
  return { saveButton, renderSaved: () => ready.then(renderSaved) };
}
