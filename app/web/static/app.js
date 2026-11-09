import { $, api, el, icon, hydrateIcons, tag, skillName, avatar, meta, link, sourceBadge, loading, emptyState, openDialog, toast } from './ui.js';
import { number, sourceName, workplaceName, date, duration } from './logic.mjs';
import { initJobs } from './jobs.js';
import { initAnalytics } from './analytics.js';
import { initCV } from './cv.js';

hydrateIcons();
let overview = null, detailController = null, refreshing = false;
const context = { overview: () => overview, openJob };
const jobs = initJobs(context);
initAnalytics(context);
initCV(context);

function navigate() {
  const view = ['jobs', 'analytics', 'cv'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'jobs';
  document.querySelectorAll('.view').forEach(section => { section.hidden = section.id !== `view-${view}`; });
  document.querySelectorAll('[data-view]').forEach(item => { const active = item.dataset.view === view; item.classList.toggle('active', active); if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current'); });
  $('breadcrumb-current').textContent = { jobs: 'Khám phá việc làm', analytics: 'Hỏi dữ liệu', cv: 'Đối chiếu CV' }[view];
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
  $('stat-listings').textContent = number(data.listings); $('stat-unique').textContent = number(data.unique_documents); $('stat-sources').textContent = number(data.sources.length);
  $('dataset-caption').textContent = data.scope === 'local_research' ? 'Dataset nghiên cứu local' : 'Dataset theo phạm vi nguồn';
  fillOptions('filter-source', data.sources.map(item => ({ value: item.source, label: `${sourceName(item.source)} · ${number(item.count)}` })));
  fillOptions('filter-skill', data.skills.map(item => ({ value: item.slug, label: item.name })));
  $('top-skills').replaceChildren(...(data.skills.length ? data.skills.slice(0, 5).map(item => {
    const fill = el('span', { class: 'bar-fill' }); fill.style.width = `${item.count / Math.max(1, data.skills[0].count) * 100}%`;
    return el('button', { class: 'skill-chart-button', title: `Tìm tin có kỹ năng ${item.name}`, onclick: () => jobs.searchSkill(item.slug) }, el('div', { class: 'bar-label' }, el('span', { text: item.name }), el('strong', { text: `${number(item.count)} tin` })), el('div', { class: 'bar-track', 'aria-hidden': 'true' }, fill));
  }) : [el('p', { class: 'muted small', text: 'Chưa có kỹ năng được nhận diện trong dữ liệu.' })]));
  $('about-sources').replaceChildren(...data.sources.map(item => el('div', { class: 'about-source' }, el('span', { text: sourceName(item.source) }), el('strong', { text: `${number(item.count)} tin` }))));
  $('about-scope').textContent = data.scope === 'local_research' ? 'Đang dùng phạm vi nghiên cứu local. ITviec/TopCV, nếu có, chỉ dành cho nghiên cứu cá nhân; không được xem là dữ liệu có quyền tái phân phối.' : 'Đang dùng phạm vi nguồn public. Các nguồn dành riêng cho nghiên cứu local đã được loại trước khi thống kê và tìm kiếm.';
  $('about-updated').textContent = data.latest_fetch ? `Lần thu thập gần nhất trong dataset: ${date(data.latest_fetch)}. Đây không phải thời điểm đăng của mọi tin.` : 'Chưa có tin trong dataset. Cần nạp dữ liệu trước khi khám phá.';
  $('cv-upload-help').textContent = `PDF có text · tối đa ${(data.cv_max_bytes / 1024 / 1024).toFixed(0)} MiB · ${data.cv_max_pages} trang`;
  $('cv-privacy-note').textContent = `Xử lý local, không gửi tới model bên ngoài. Hồ sơ lưu tạm ${duration(data.cv_ttl_seconds)} và có thể xóa bất cứ lúc nào.`;
  $('planner-note').textContent = data.model_planner_enabled ? 'Ngoài câu hỏi mẫu, máy chủ có cấu hình planner model. Hãy kiểm tra truy vấn và phạm vi kết quả.' : 'Hiện hỗ trợ các câu hỏi thống kê mẫu; câu hỏi ngoài phạm vi sẽ yêu cầu làm rõ.';
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
    if (!overview) { $('dataset-caption').textContent = 'Chưa đọc được dataset'; $('top-skills').replaceChildren(el('p', { class: 'muted small', text: 'Chưa thể đọc thống kê. Hãy thử làm mới dữ liệu.' })); $('about-scope').textContent = 'Chưa đọc được phạm vi hiện tại. Kiểm tra kết nối máy chủ và thử lại.'; }
    toast('Chưa đọc được dữ liệu tổng quan. Bạn có thể thử làm mới.');
  }
  refreshing = false; $('refresh-button').disabled = false;
}
$('refresh-button').addEventListener('click', async () => { await refresh(); await jobs.load(); });

async function openJob(id) {
  detailController?.abort(); const current = new AbortController(); detailController = current;
  $('job-detail').replaceChildren(loading('Đang đọc thông tin công việc…')); openDialog('job-dialog');
  try {
    const job = await api(`/jobs/${encodeURIComponent(id)}`, { signal: current.signal });
    if (detailController !== current) return;
    const heading = el('div', { class: 'detail-heading' }, avatar(job.company), el('div', {}, el('p', { class: 'company-name', text: job.company || 'Công ty chưa ghi nhận' }), el('h2', { text: job.title })));
    const metadata = el('div', { class: 'detail-meta' }, meta(job.location || 'Địa điểm chưa ghi nhận', 'pin'), meta(workplaceName(job.workplace_type), 'briefcase'), sourceBadge(job));
    const tags = el('div', { class: 'job-tags' }, job.skills.map(skill => tag(skillName(skill.skill_slug, overview))));
    const details = el('details', { class: 'comparison-detail' }, el('summary', {}, 'Bằng chứng kỹ năng trong tin', icon('chevron-down')));
    for (const skill of job.skills) details.append(el('blockquote', { class: 'evidence-block', text: `${skillName(skill.skill_slug, overview)} · “${skill.evidence}”` }));
    $('job-detail').replaceChildren(heading, metadata, tags, el('p', { class: 'report-note', text: 'Các kỹ năng dưới đây là nội dung được nhắc trong tin; chưa phân loại bắt buộc, ưu tiên hoặc phủ định.' }), ...(job.skills.length ? [details] : []), el('h3', { class: 'detail-section-label', text: 'NỘI DUNG TỪ NGUỒN GỐC' }), el('div', { class: 'job-description', text: job.description }), el('div', { class: 'detail-footer' }, el('p', { text: `Thu thập ${date(job.fetched_at)} · ${job.published_at ? `Đăng ${date(job.published_at)}` : 'Chưa ghi nhận ngày đăng'}` }), link(job.source_url, 'Xem tại nguồn gốc', 'button primary')));
  } catch (error) { if (error.name !== 'AbortError' && detailController === current) $('job-detail').replaceChildren(emptyState('Chưa thể mở công việc.', error.message, 'info')); }
}

refresh(); jobs.load();
