import { $, api, el, icon, hydrateIcons, tag, skillName, avatar, meta, link, sourceBadge, jobPresentation, clearJobPresentationCache, loading, emptyState, openDialog, toast } from './ui.js?v=20261118-5';
import { number, sourceName, workplaceName, date, duration, descriptionSegments, descriptionText, skillLabel, requirementTone } from './logic.mjs?v=20261118-5';
import { initJobs } from './jobs.js?v=20261118-5';
import { initAnalytics } from './analytics.js?v=20261118-5';
import { initCV } from './cv.js?v=20261118-5';
import { initAccount } from './account.js?v=20261118-5';

hydrateIcons();
// Scroll offsets follow the actual sticky navigation height, including text scaling.
const sidebar = document.querySelector('.sidebar');
new ResizeObserver(() => document.documentElement.style.setProperty('--sticky-offset',
  `${matchMedia('(max-width: 800px)').matches ? Math.ceil(sidebar.getBoundingClientRect().height) + 12 : 0}px`
)).observe(sidebar);
let overview = null, detailController = null, refreshing = false;
const context = { overview: () => overview, openJob, saveButton: job => account.saveButton(job) };
const account = initAccount(context);
const jobs = initJobs(context);
initAnalytics(context);
initCV(context);

function navigate() {
  const view = ['jobs', 'analytics', 'cv', 'saved'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'jobs';
  document.querySelectorAll('.view').forEach(section => { section.hidden = section.id !== `view-${view}`; });
  document.querySelectorAll('[data-view]').forEach(item => { const active = item.dataset.view === view; item.classList.toggle('active', active); if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current'); });
  document.title = `${{ jobs: 'Việc làm', analytics: 'Hỏi dữ liệu', cv: 'Đối chiếu CV', saved: 'Việc đã lưu' }[view]} · CareerLens`;
  window.scrollTo({ top: 0, behavior: 'instant' });
  if (view === 'saved') account.renderSaved();
}
window.addEventListener('hashchange', navigate); navigate();
document.querySelector('.skip-link').addEventListener('click', event => { event.preventDefault(); $('main').focus(); $('main').scrollIntoView({ block: 'start' }); });
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } }));
for (const id of ['about-button', 'help-button', 'footer-about']) $(id).addEventListener('click', () => openDialog('about-dialog'));

function fillOptions(id, items) {
  const select = $(id), selected = select.value;
  select.replaceChildren(select.options[0], ...items.map(item => el('option', { value: item.value, text: item.label })));
  if (items.some(item => item.value === selected)) select.value = selected;
}
function renderOverview(data) {
  overview = data;
  fillOptions('filter-source', data.sources.map(item => ({ value: item.source, label: `${sourceName(item.source)} · ${number(item.count)}` })));
  fillOptions('filter-skill', data.skills.map(item => ({ value: item.slug, label: item.name })));
  $('about-sources').replaceChildren(...data.sources.map(item => el('div', { class: 'about-source' }, el('span', { text: sourceName(item.source) }), el('strong', { text: `${number(item.count)} tin` }))));
  $('about-scope').textContent = data.scope === 'local_research' ? 'Một số tin từ trang tuyển dụng và công ty chỉ hiển thị trong bản chạy trên máy để nghiên cứu cá nhân; quyền phân phối lại chưa được xác nhận.' : 'Chỉ hiển thị và thống kê các nguồn được phép dùng trong bản công khai. Tin dành riêng cho nghiên cứu cá nhân đã được loại khỏi kết quả.';
  $('about-updated').textContent = data.latest_fetch ? `Dữ liệu được cập nhật gần nhất: ${date(data.latest_fetch)}. Ngày đăng của từng tin có thể khác.` : 'Chưa có tin tuyển dụng để hiển thị.';
  $('cv-upload-help').textContent = `PDF chọn được văn bản · tối đa ${(data.cv_max_bytes / 1024 / 1024).toFixed(0)} MiB · ${data.cv_max_pages} trang`;
  $('cv-privacy-note').textContent = `Xử lý trên máy chủ đang chạy, không gửi tới model bên ngoài. Hồ sơ lưu tạm ${duration(data.cv_ttl_seconds)} và có thể xóa bất cứ lúc nào.`;
  $('planner-note').textContent = data.model_planner_enabled ? 'Bạn có thể hỏi bằng ngôn ngữ tự nhiên. Câu trả lời dựa trên dữ liệu đã thu thập. Bạn có thể mở phần “Xem cách tính” để kiểm tra.' : 'Hiện hỗ trợ các câu hỏi thống kê mẫu; câu hỏi ngoài phạm vi sẽ yêu cầu làm rõ.';
}
async function checkConnection() {
  let ready = false;
  try { ready = (await api('/ready')).status === 'ready'; } catch {}
  $('connection').classList.toggle('offline', !ready);
  $('connection').lastElementChild.textContent = ready ? 'Hệ thống sẵn sàng' : 'Kết nối chưa sẵn sàng';
}
async function refresh() {
  if (refreshing) return;
  refreshing = true; $('refresh-button').disabled = true;
  const results = await Promise.allSettled([api('/platform/overview'), checkConnection()]);
  if (results[0].status === 'fulfilled') renderOverview(results[0].value);
  else {
    if (!overview) { $('about-scope').textContent = 'Chưa đọc được phạm vi hiện tại. Kiểm tra kết nối máy chủ và thử lại.'; }
    toast('Chưa đọc được dữ liệu tổng quan. Bạn có thể thử làm mới.');
  }
  refreshing = false; $('refresh-button').disabled = false;
}
$('refresh-button').addEventListener('click', async () => { clearJobPresentationCache(); await refresh(); await jobs.load(); });

document.addEventListener('click', event => {
  document.querySelectorAll('.translation-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
});

function renderDescription(content, text) {
  const lines = descriptionText(text).split('\n');
  content.replaceChildren(...lines.map(line => el('div', { class: 'description-line' },
    ...descriptionSegments(line).map(part => part.heading
      ? el('strong', { class: 'description-heading', text: part.text })
      : document.createTextNode(part.text)))));
}
function descriptionSection(job, title, actionsHost) {
  const content = el('div', { class: 'job-description' });
  renderDescription(content, job.description);
  const note = el('p', { class: 'description-note', hidden: true });
  const heading = el('h3', { class: 'detail-section-label', text: 'Mô tả công việc' });
  const header = el('div', { class: 'description-header' }, heading);
  const section = el('section', { class: 'description-section' }, header, note, content);
  const status = el('p', { class: 'description-status', role: 'status' });
  const menu = el('details', { class: 'translation-menu' });
  const summaryLabel = el('span', { text: 'Dịch' });
  const summary = el('summary', { 'aria-label': 'Dịch tiêu đề và mô tả công việc' }, icon('globe'), summaryLabel, icon('chevron-down'));
  const options = el('div', { class: 'translation-options', role: 'group', 'aria-label': 'Chọn ngôn ngữ công việc' });
  let current = 'original', busy = false;
  const buttons = [];
  function updateOptions() { buttons.forEach(item => item.setAttribute('aria-pressed', String(item.dataset.language === current))); }
  function closeMenu() { menu.open = false; summary.focus(); }
  async function choose(target) {
    if (busy) return;
    closeMenu();
    if (target === current) return;
    if (target === 'original') {
      title.textContent = job.title; title.removeAttribute('lang');
      renderDescription(content, job.description); content.removeAttribute('lang');
      note.textContent = ''; note.hidden = true; current = 'original';
      summaryLabel.textContent = 'Dịch'; status.textContent = ''; updateOptions(); return;
    }
    busy = true; summary.setAttribute('aria-disabled', 'true'); summaryLabel.textContent = 'Đang dịch…';
    buttons.forEach(item => { item.disabled = true; });
    status.textContent = 'Đang dịch tiêu đề và mô tả, vui lòng chờ…';
    try {
      const result = await api(`/jobs/${encodeURIComponent(job.id)}/description-translation`, { method: 'POST', json: { target }, signal: AbortSignal.timeout(300000) });
      if (!section.isConnected) return;
      if (typeof result.title !== 'string' || !result.title.trim() || typeof result.text !== 'string' || !result.text.trim()) throw new Error('Bản dịch chưa đầy đủ. Vui lòng thử lại.');
      title.textContent = result.title; title.lang = target;
      renderDescription(content, result.text); content.lang = target;
      note.textContent = 'Bản dịch tự động; hãy đối chiếu với bản gốc.'; note.hidden = false; current = target;
      status.textContent = ''; updateOptions();
    } catch (error) { if (section.isConnected) status.textContent = error.name === 'TimeoutError' ? 'Dịch mất quá nhiều thời gian. Vui lòng thử lại.' : error.message; }
    finally {
      busy = false; summary.removeAttribute('aria-disabled');
      buttons.forEach(item => { item.disabled = false; });
      summaryLabel.textContent = current === 'original' ? 'Dịch' : current === 'vi' ? 'Tiếng Việt' : 'English';
    }
  }
  const choices = [['original', 'Bản gốc'], ...(job.description_language === 'vi' ? [] : [['vi', 'Tiếng Việt']]), ...(job.description_language === 'en' ? [] : [['en', 'English']])];
  for (const [language, label] of choices) {
    const item = el('button', { class: 'translation-option', type: 'button', 'data-language': language, 'aria-pressed': 'false', onclick: () => choose(language) }, label, icon('check'));
    buttons.push(item); options.append(item);
  }
  updateOptions();
  menu.append(summary, options);
  summary.addEventListener('click', event => { if (busy) event.preventDefault(); });
  menu.addEventListener('keydown', event => { if (event.key === 'Escape' && menu.open) { event.preventDefault(); event.stopPropagation(); closeMenu(); } });
  actionsHost.append(menu, status);
  return section;
}

async function openJob(id) {
  detailController?.abort(); const current = new AbortController(); detailController = current;
  $('job-detail').replaceChildren(loading('Đang đọc thông tin công việc…')); openDialog('job-dialog');
  try {
    const job = await api(`/jobs/${encodeURIComponent(id)}`, { signal: current.signal });
    if (detailController !== current) return;
    const title = el('h2', { id: 'job-detail-title', text: job.title });
    const deadline = el('span', { class: 'job-deadline', text: 'Đang tải hạn nộp…' });
    const country = el('span', { class: 'job-country detail-country', text: 'Đang tải quốc gia…' });
    const actions = el('div', { class: 'detail-translation' });
    const description = descriptionSection(job, title, actions);
    actions.prepend(context.saveButton(job));
    const heading = el('div', { class: 'detail-heading' }, avatar(job.company), el('div', { class: 'detail-heading-copy' }, el('p', { class: 'company-name', text: job.company || 'Chưa có tên công ty' }), title), actions);
    const metadata = el('div', { class: 'detail-meta' }, country, meta(job.location || 'Chưa có địa điểm', 'pin'), meta(workplaceName(job.workplace_type), 'briefcase'), deadline, sourceBadge(job), jobPresentation(job, null, deadline, country));
    const sourceLink = link(job.source_url, 'Xem tin tuyển dụng gốc', 'job-source-link');
    const order = { required: 0, unspecified: 1, preferred: 2, negated: 3 };
    const tags = el('div', { class: 'job-tags' }, [...job.skills].sort((a, b) => (order[a.requirement] ?? 1) - (order[b.requirement] ?? 1)).map(skill => { const item = tag(skillLabel(skillName(skill.skill_slug, overview), skill.requirement), requirementTone(skill.requirement)); if (skill.requirement_cue) item.title = `Dựa trên: “${skill.requirement_cue}”`; return item; }));
    $('job-detail').replaceChildren(heading, metadata, sourceLink, tags, description, el('div', { class: 'detail-footer' }, el('p', { text: `Cập nhật ${date(job.fetched_at)} · ${job.published_at ? `Đăng ${date(job.published_at)}` : 'Chưa có ngày đăng'}` })));
  } catch (error) { if (error.name !== 'AbortError' && detailController === current) $('job-detail').replaceChildren(emptyState('Chưa thể mở công việc.', error.message, 'info')); }
}

refresh(); jobs.load();
