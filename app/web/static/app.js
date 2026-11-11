import { $, api, el, icon, hydrateIcons, tag, skillName, avatar, meta, link, sourceBadge, jobPresentation, clearJobPresentationCache, loading, emptyState, openDialog, toast } from './ui.js?v=20261111-6';
import { number, sourceName, workplaceName, date, duration, descriptionSegments, descriptionText } from './logic.mjs?v=20261111-6';
import { initJobs } from './jobs.js?v=20261111-6';
import { initAnalytics } from './analytics.js?v=20261111-6';
import { initCV } from './cv.js?v=20261111-6';

hydrateIcons();
// Scroll offsets follow the actual sticky navigation height, including text scaling.
const sidebar = document.querySelector('.sidebar');
new ResizeObserver(() => document.documentElement.style.setProperty('--sticky-offset',
  `${matchMedia('(max-width: 800px)').matches ? Math.ceil(sidebar.getBoundingClientRect().height) + 12 : 0}px`
)).observe(sidebar);
let overview = null, detailController = null, refreshing = false;
const context = { overview: () => overview, openJob };
const jobs = initJobs(context);
initAnalytics(context);
initCV(context);

function navigate() {
  const view = ['jobs', 'analytics', 'cv'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'jobs';
  document.querySelectorAll('.view').forEach(section => { section.hidden = section.id !== `view-${view}`; });
  document.querySelectorAll('[data-view]').forEach(item => { const active = item.dataset.view === view; item.classList.toggle('active', active); if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current'); });
  $('breadcrumb-current').textContent = { jobs: 'Việc làm', analytics: 'Hỏi dữ liệu', cv: 'Đối chiếu CV' }[view];
  document.title = `${$('breadcrumb-current').textContent} · CareerLens`;
  window.scrollTo({ top: 0, behavior: 'instant' });
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
  $('about-scope').textContent = data.scope === 'local_research' ? 'Bản chạy trên máy có thể bao gồm tin ITviec hoặc TopCV để nghiên cứu cá nhân. Những tin này chưa được cấp quyền phân phối lại.' : 'Chỉ hiển thị và thống kê các nguồn được phép dùng trong bản công khai. Tin dành riêng cho nghiên cứu cá nhân đã được loại khỏi kết quả.';
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
  const note = el('p', { class: 'description-note', text: 'Nội dung do nhà tuyển dụng đăng.' });
  const heading = el('h3', { class: 'detail-section-label', text: 'Mô tả công việc' });
  const header = el('div', { class: 'description-header' }, heading);
  const section = el('section', { class: 'description-section' }, header, note, content);
  const status = el('p', { class: 'description-status', role: 'status' });
  const original = el('button', { class: 'text-button', type: 'button', text: 'Xem bản gốc', hidden: true, onclick: () => {
    title.textContent = job.title; title.removeAttribute('lang');
    renderDescription(content, job.description); content.removeAttribute('lang'); note.textContent = 'Nội dung do nhà tuyển dụng đăng.'; original.hidden = true; status.textContent = '';
  } });
  const select = el('select', { class: 'description-language', 'aria-label': 'Ngôn ngữ dịch công việc' }, ...(job.description_language === 'vi' ? [] : [el('option', { value: 'vi', text: 'Tiếng Việt' })]), ...(job.description_language === 'en' ? [] : [el('option', { value: 'en', text: 'English' })]));
  const singleTarget = select.options.length === 1;
  if (singleTarget) select.hidden = true;
  const buttonText = singleTarget ? `Dịch sang ${select.options[0].textContent}` : 'Dịch toàn bộ';
  const button = el('button', { class: 'button secondary', type: 'button', text: buttonText, onclick: async () => {
    const target = select.value; button.disabled = true; select.disabled = true; button.textContent = 'Đang dịch…';
    status.textContent = 'Đang dịch tiêu đề và mô tả, vui lòng chờ…';
    try {
      const result = await api(`/jobs/${encodeURIComponent(job.id)}/description-translation`, { method: 'POST', json: { target }, signal: AbortSignal.timeout(300000) });
      if (!section.isConnected) return;
      if (typeof result.title !== 'string' || !result.title.trim() || typeof result.text !== 'string' || !result.text.trim()) throw new Error('Bản dịch chưa đầy đủ. Vui lòng thử lại.');
      title.textContent = result.title; title.lang = target;
      renderDescription(content, result.text); content.lang = target;
      note.textContent = 'Bản dịch tự động; hãy đối chiếu với bản gốc.'; original.hidden = false; status.textContent = '';
    } catch (error) { if (section.isConnected) status.textContent = error.name === 'TimeoutError' ? 'Dịch mất quá nhiều thời gian. Vui lòng thử lại.' : error.message; }
    finally { button.disabled = false; select.disabled = false; button.textContent = buttonText; }
  } });
  actionsHost.append(el('div', { class: 'description-actions' }, singleTarget ? null : el('span', { class: 'description-language-label', text: 'Dịch sang' }), select, button, original), status);
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
    const heading = el('div', { class: 'detail-heading' }, avatar(job.company), el('div', { class: 'detail-heading-copy' }, el('p', { class: 'company-name', text: job.company || 'Chưa có tên công ty' }), title), actions);
    const metadata = el('div', { class: 'detail-meta' }, country, meta(job.location || 'Chưa có địa điểm', 'pin'), meta(workplaceName(job.workplace_type), 'briefcase'), deadline, sourceBadge(job), jobPresentation(job, null, deadline, country));
    const sourceLink = link(job.source_url, 'Xem tin tuyển dụng gốc', 'job-source-link');
    const tags = el('div', { class: 'job-tags' }, job.skills.map(skill => tag(skillName(skill.skill_slug, overview))));
    $('job-detail').replaceChildren(heading, metadata, sourceLink, tags, ...(job.skills.length ? [el('p', { class: 'report-note', text: 'Các kỹ năng trên được tìm thấy trong mô tả. Hãy đọc kỹ để biết đâu là yêu cầu bắt buộc hoặc ưu tiên.' })] : []), description, el('div', { class: 'detail-footer' }, el('p', { text: `Cập nhật ${date(job.fetched_at)} · ${job.published_at ? `Đăng ${date(job.published_at)}` : 'Chưa có ngày đăng'}` })));
  } catch (error) { if (error.name !== 'AbortError' && detailController === current) $('job-detail').replaceChildren(emptyState('Chưa thể mở công việc.', error.message, 'info')); }
}

refresh(); jobs.load();
