import { initials, safeUrl, sourceName, workplaceName, date, number } from './logic.mjs';

const paths = {
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  chart: '<path d="M4 3v17h17M8 15v-4m5 4V6m5 9v-6"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Zm0 0v6h6M8 13h8m-8 4h6"/>',
  'arrow-right': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  'arrow-left': '<path d="M20 12H4m6-6-6 6 6 6"/>',
  'arrow-up-right': '<path d="M6 18 18 6M6 6h12v12"/>',
  'arrow-down': '<path d="M12 4v16m-5-5 5 5 5-5"/>',
  'chevron-down': '<path d="m6 9 6 6 6-6"/>',
  pin: '<path d="M20 10c0 6-8 11-8 11S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a18 18 0 0 1 0 18 18 18 0 0 1 0-18Z"/>',
  briefcase: '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V4h8v3M3 12a25 25 0 0 0 18 0m-10 1h2"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 8.5a2.7 2.7 0 1 1 4.3 2.2c-1.3.7-1.8 1.5-1.8 2.8m0 3v.1"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 1 1 8 0v3m-4 4v3"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>',
  sparkles: '<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4Zm7-2v4m-2-2h4"/>',
  compass: '<circle cx="12" cy="12" r="10"/><path d="m16 7-3 6-6 3 3-6Z"/>',
  trend: '<path d="m3 17 6-6 4 4 8-10m-6 0h6v6"/>',
  scan: '<path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3m8 0h3a2 2 0 0 0 2-2v-3M7 8h10m-10 4h10m-10 4h6"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  shield: '<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Z"/><path d="m8 12 3 3 5-6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
};

export const $ = id => document.getElementById(id);
export function el(tag, attrs = {}, ...children) {
  const item = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null) continue;
    if (key === 'class') item.className = value;
    else if (key === 'text') item.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') item.addEventListener(key.slice(2), value);
    else item.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) if (child !== null && child !== undefined) item.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return item;
}
export function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [name, value] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(name, value);
  // Only fixed, authored icon paths enter this sink. User/source text always uses textContent.
  svg.innerHTML = paths[name] || paths.info;
  return svg;
}
export function hydrateIcons(root = document) { root.querySelectorAll('[data-icon]').forEach(item => item.replaceChildren(icon(item.dataset.icon))); }
export function tag(text, tone = '') { return el('span', { class: `tag ${tone}`, text }); }
export function link(url, text, className = 'job-link') {
  const safe = safeUrl(url);
  return safe ? el('a', { href: safe, target: '_blank', rel: 'noopener noreferrer', class: className }, text, icon('arrow-up-right')) : el('span', { class: 'muted', text: 'Chưa có liên kết đến tin gốc' });
}
export const skillName = (slug, overview) => overview?.skills?.find(item => item.slug === slug)?.name || slug;
export function avatar(company) {
  const value = company || 'Chưa có tên công ty';
  const tone = Array.from(value).reduce((sum, c) => sum + c.charCodeAt(0), 0) % 4;
  return el('span', { class: `company-avatar tone-${tone}`, 'aria-hidden': 'true', text: initials(company) });
}
export function meta(text, symbol) { return el('span', {}, icon(symbol), text); }
export function sourceBadge(job) { return el('span', { class: `source-badge ${job.usage_scope === 'local_research_only' ? 'private' : ''}`, title: job.usage_scope === 'local_research_only' ? 'Chỉ dùng để nghiên cứu cá nhân, chưa có quyền phân phối lại' : 'Nguồn đăng tin tuyển dụng', text: `${sourceName(job.source)}${job.usage_scope === 'local_research_only' ? ' · nghiên cứu' : ''}` }); }
const presentationCache = new Map(), presentationQueue = new Map();
let presentationScheduled = false, titleLanguage = 'vi';
const titleBindings = new WeakMap();
function applyTitleLanguage(node) {
  const binding = titleBindings.get(node);
  if (!binding?.value || !binding.title) return;
  const { title, value, buttons } = binding;
  const translated = titleLanguage !== 'original' && value[`title_${titleLanguage}`];
  title.textContent = translated || value.original_title;
  if (translated) title.setAttribute('lang', titleLanguage); else title.removeAttribute('lang');
  buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.language === titleLanguage)));
}

const flagAssets = new Set(['SE', 'VN', 'DK']);
function showPresentation(node, job, value) {
  if (!node.isConnected || value.original_title !== job.title) return;
  const country = el('div', { class: 'job-country' });
  if (value.country_codes?.length) {
    value.country_codes.forEach((code, index) => {
      if (flagAssets.has(code)) country.append(el('img', { class: 'country-flag', src: `/assets/flags/${code.toLowerCase()}.svg`, alt: '', 'aria-hidden': 'true', width: '24', height: '16' }));
      country.append(el('span', { text: value.country_names[index] || code }));
    });
  } else country.append(icon('globe'), el('span', { text: value.country_label }));
  const title = titleBindings.get(node)?.title;
  const panel = el('div', { class: 'language-menu-panel' },
    el('p', { class: 'language-menu-label', text: 'Quốc gia' }), country);
  if (value.title_en && value.title_vi && title) {
    panel.append(el('p', { class: 'language-menu-label', text: 'Ngôn ngữ tiêu đề' }));
    const buttons = [['vi', 'Tiếng Việt'], ['en', 'English'], ['original', 'Bản gốc']].map(([language, label]) =>
      el('button', { class: 'language-option', type: 'button', 'data-language': language,
        'aria-pressed': 'false', onclick: () => {
          titleLanguage = language;
          document.querySelectorAll('.job-presentation').forEach(applyTitleLanguage);
          node.open = false;
          node.querySelector('summary')?.focus();
        } }, label, icon('check')));
    panel.append(...buttons, el('p', { class: 'language-menu-note', text: 'Bản dịch tiêu đề đã được kiểm tra.' }));
    titleBindings.set(node, { title, value, buttons });
  } else panel.append(el('p', { class: 'language-menu-note', text: 'Chưa có bản dịch tiêu đề được kiểm tra. Đang hiển thị tiêu đề gốc.' }));
  node.replaceChildren(el('summary', { 'aria-label': `Quốc gia và ngôn ngữ tiêu đề: ${job.title}` }, icon('globe'), el('span', { text: value.title_en && value.title_vi ? 'Ngôn ngữ tiêu đề' : 'Tiêu đề gốc' }), icon('chevron-down')), panel);
  if (value.title_en && value.title_vi && title) applyTitleLanguage(node);
}
async function flushPresentations() {
  presentationScheduled = false;
  const queued = [...presentationQueue.entries()]; presentationQueue.clear();
  for (let start = 0; start < queued.length; start += 20) {
    const batch = queued.slice(start, start + 20);
    const params = new URLSearchParams(); batch.forEach(([id]) => params.append('ids', id));
    try {
      const values = await api(`/jobs/presentation?${params}`);
      for (const [id, nodes] of batch) {
        const value = values[id]; if (!value) continue;
        if (presentationCache.size >= 1000) presentationCache.delete(presentationCache.keys().next().value);
        // A missing translation can be filled by the operator while this tab is open.
        if (value.title_en && value.title_vi) presentationCache.set(id, value);
        nodes.forEach(([node, job]) => showPresentation(node, job, value));
      }
    } catch {
      batch.forEach(([, nodes]) => nodes.forEach(([node]) => { if (node.isConnected) node.replaceChildren(el('summary', { text: 'Ngôn ngữ tiêu đề' }), el('p', { class: 'language-menu-panel language-menu-note', text: 'Chưa tải được thông tin quốc gia và bản dịch.' })); }));
    }
  }
}
export function jobPresentation(job, title = null) {
  const node = el('details', { class: 'job-presentation language-menu' });
  titleBindings.set(node, { title });
  const id = job.job_id || job.id;
  const cached = presentationCache.get(id);
  if (cached?.original_title === job.title) queueMicrotask(() => showPresentation(node, job, cached));
  else {
    if (!presentationQueue.has(id)) presentationQueue.set(id, []);
    presentationQueue.get(id).push([node, job]);
    if (!presentationScheduled) { presentationScheduled = true; queueMicrotask(flushPresentations); }
  }
  return node;
}
document.addEventListener('toggle', event => {
  if (event.target instanceof HTMLDetailsElement && event.target.matches('.language-menu') && event.target.open)
    document.querySelectorAll('.language-menu[open]').forEach(menu => { if (menu !== event.target) menu.open = false; });
}, true);
document.addEventListener('click', event => {
  document.querySelectorAll('.language-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const menu = document.activeElement?.closest('.language-menu[open]');
  if (menu) { event.preventDefault(); event.stopPropagation(); menu.open = false; menu.querySelector('summary')?.focus(); }
});
export function clearJobPresentationCache() { presentationCache.clear(); }
export function jobHeading(job, openJob) {
  const title = el('button', { class: 'job-title', type: 'button', onclick: () => openJob(job.job_id || job.id), text: job.title });
  return el('div', { class: 'job-card-heading' }, avatar(job.company), el('div', { class: 'job-card-copy' }, el('h3', { class: 'job-title-heading' }, title), el('p', { class: 'company-name', text: job.company || 'Chưa có tên công ty' }), el('div', { class: 'job-meta' }, meta(job.location || 'Chưa có địa điểm', 'pin'), job.workplace_type && job.workplace_type !== 'unknown' ? meta(workplaceName(job.workplace_type), 'briefcase') : null), ), el('div', { class: 'job-card-tools' }, jobPresentation(job, title), sourceBadge(job)));
}
export function jobCard(job, context) {
  const footer = job.duplicate_listings > 1 ? `${number(job.duplicate_listings)} tin cùng nội dung` : job.fetched_at ? `Cập nhật ${date(job.fetched_at)}` : 'Kiểm tra thông tin trong tin gốc';
  return el('article', { class: 'job-card' }, jobHeading(job, context.openJob), job.excerpt ? el('p', { class: 'job-excerpt', text: job.excerpt }) : null,
    job.skills?.length ? el('div', { class: 'job-tags' }, job.skills.slice(0, 5).map(slug => tag(skillName(slug, context.overview()))), job.skills.length > 5 ? el('button', { class: 'tag overflow-tags', type: 'button', 'aria-label': `Xem thêm ${job.skills.length - 5} kỹ năng: ${job.title}`, onclick: () => context.openJob(job.job_id || job.id), text: `+${job.skills.length - 5}` }) : null) : null,
    el('div', { class: 'job-card-footer' }, el('span', { class: 'job-footnote', text: footer }), el('div', { class: 'job-card-actions' }, link(job.source_url, 'Tin gốc'), el('button', { class: 'text-button', type: 'button', 'aria-label': `Xem chi tiết: ${job.title}`, onclick: () => context.openJob(job.job_id || job.id) }, 'Xem chi tiết', icon('arrow-right')))));
}
export function emptyState(title, message, symbol = 'search', action = null) { return el('div', { class: 'quiet-state' }, icon(symbol), el('h3', { text: title }), el('p', { text: message }), action); }
export function loading(message) { return el('div', { class: 'loading-inline', role: 'status' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), message); }
export function skeletons(count = 3) { return Array.from({ length: count }, () => el('div', { class: 'skeleton', 'aria-hidden': 'true' }, Array.from({ length: 4 }, () => el('span', { class: 'skeleton-line' })))); }
export function notice(box, message, tone = '') { box.hidden = !message; box.className = `notice ${tone}`; box.replaceChildren(icon('info'), el('span', { text: message })); }
export function busy(button, value, label) { if (value) { if (!button.dataset.label) button.dataset.label = button.textContent; button.disabled = true; button.replaceChildren(el('span', { class: 'spinner', 'aria-hidden': 'true' }), label); } else { button.disabled = false; button.replaceChildren(button.dataset.label || label, icon('arrow-right')); delete button.dataset.label; } }

const errorMessages = {
  model_quota_exhausted: 'Đã hết lượt dịch hôm nay. Vui lòng thử lại sau.',
  model_unavailable: 'Chưa thể kết nối dịch vụ dịch. Vui lòng thử lại sau.',
  restricted_source: 'Chưa thể dịch tin này với cấu hình hiện tại.',
  description_too_long: 'Mô tả quá dài để dịch đầy đủ. Vui lòng xem tin gốc.',
  invalid_or_oversize_pdf: 'File không phải PDF hợp lệ hoặc vượt giới hạn 2 MiB.',
  encrypted_pdf_not_supported: 'CV đang được mã hóa hoặc đặt mật khẩu. Hãy dùng PDF không khóa.',
  no_extractable_text_ocr_not_supported: 'Chưa đọc được văn bản trong PDF. Hãy xuất lại CV thành PDF chọn được văn bản; hiện chưa đọc được bản scan.',
  pdf_page_limit: 'PDF vượt giới hạn 10 trang.', pdf_text_limit: 'Văn bản CV quá dài. Hãy dùng phiên bản ngắn gọn hơn.',
  pdf_content_limit: 'Nội dung PDF vượt giới hạn xử lý. Hãy xuất lại bản PDF gọn hơn.',
  pdf_parser_timeout: 'Đọc PDF mất quá nhiều thời gian. Hãy xuất lại file đơn giản hơn.',
  invalid_pdf_or_parser_resource_limit: 'Chưa đọc được PDF này. Hãy xuất lại CV thành một PDF chọn được văn bản, không khóa.',
  pdf_parser_resource_limit: 'PDF cần quá nhiều tài nguyên để xử lý. Hãy xuất lại bản đơn giản hơn.',
  'CV parser busy; retry later': 'Máy chủ đang đọc CV khác. Vui lòng thử lại sau.',
  'CV matching busy; retry later': 'Đang xử lý các yêu cầu đối chiếu khác. Vui lòng thử lại sau.',
  'CV storage capacity reached; retry later': 'Bộ nhớ hồ sơ tạm đang đầy. Vui lòng thử lại sau.',
  'Dense index/model unavailable; run model download and indexing': 'Tìm theo ý nghĩa chưa sẵn sàng. Bạn có thể chuyển sang tìm theo từ khóa.',
  'Corpus limit exceeded; narrow search filters': 'Có quá nhiều tin cho một lượt tìm. Hãy thu hẹp nguồn hoặc địa điểm.',
  'Corpus limit exceeded; narrow filters': 'Hãy thu hẹp nguồn hoặc địa điểm trước khi đối chiếu.',
};
export class ApiError extends Error { constructor(status, detail) { super(errorMessages[detail] || ({ 400: 'Yêu cầu chưa hợp lệ. Hãy kiểm tra thông tin đã nhập.', 404: 'Hồ sơ hoặc công việc không còn truy cập được.', 408: 'Kết nối quá chậm. Vui lòng thử lại.', 413: 'File vượt giới hạn 2 MiB.', 415: 'Hãy chọn một file PDF.', 422: 'Thông tin chưa hợp lệ. Hãy kiểm tra nội dung và bộ lọc.', 503: 'Dịch vụ tạm thời chưa sẵn sàng. Vui lòng thử lại sau.' }[status]) || 'Chưa thể kết nối máy chủ. Vui lòng thử lại.'); this.status = status; } }
export async function api(path, { json, ...options } = {}) {
  const headers = new Headers(options.headers || {});
  if (json !== undefined) headers.set('Content-Type', 'application/json');
  let response;
  try { response = await fetch(path, { ...options, headers, cache: 'no-store', signal: options.signal || AbortSignal.timeout(30000), body: json !== undefined ? JSON.stringify(json) : options.body }); }
  catch (error) { if (error.name === 'AbortError') throw error; throw new ApiError(0); }
  if (!response.ok) { let detail; try { detail = (await response.json()).detail; } catch {} throw new ApiError(response.status, typeof detail === 'string' ? detail : ''); }
  if (response.status === 204) return null;
  try { return await response.json(); } catch { throw new ApiError(502); }
}
const warnings = {
  dense_index_incomplete_run_index: 'Một số tin chưa sẵn sàng để tìm theo ý nghĩa.',
  dense_index_not_ready: 'Tìm theo ý nghĩa chưa sẵn sàng.', EmbeddingUnavailable: 'Tìm theo ý nghĩa chưa sẵn sàng.', VectorBackendUnavailable: 'Tìm theo ý nghĩa chưa sẵn sàng.',
  vector_points_missing_run_index: 'Dữ liệu tìm theo ý nghĩa đang cần cập nhật.', local_source_requires_local_vector_backend: 'Tin nghiên cứu cá nhân chỉ được xử lý trên máy.',
  search_document_limited_to_20000_characters: 'Một số mô tả dài được rút gọn khi tìm kiếm.',
  bm25_document_text_limited_to_20000_characters: 'Một số mô tả dài được rút gọn khi tìm kiếm.',
  dense_chunk_limit_reached_for_some_documents: 'Một số nội dung dài chưa được xử lý toàn bộ.',
  some_pages_without_text_report_may_be_incomplete: 'Một số trang chưa đọc được văn bản nên kết quả đối chiếu có thể chưa đầy đủ.',
  literal_mentions_not_verified_proficiency_or_negation: 'Kỹ năng được tìm từ văn bản; hãy đọc đoạn trích để hiểu ngữ cảnh và mức độ yêu cầu.',
  pdf_reading_order_may_differ_from_visual_layout: 'Thứ tự văn bản có thể khác bố cục PDF. Hãy kiểm tra phần văn bản đã đọc.',
  no_curated_skills_found_add_target_role_or_review_cv_text: 'Chưa tìm thấy kỹ năng trong CV. Hãy kiểm tra văn bản đã đọc hoặc nhập vị trí muốn tìm.',
  no_cv_skills_target_role_only_or_no_matches: 'CV chưa có kỹ năng được nhận diện; kết quả chỉ dựa vào vị trí bạn nhập.',
  some_job_skill_evidence_invalid_excluded_from_comparison: 'Một số kỹ năng không có đoạn trích xác thực nên đã được bỏ qua khi so sánh.',
  row_limit_reached_more_rows_may_exist: 'Đã chạm giới hạn số dòng trả về; có thể còn dữ liệu khác.', long_values_truncated: 'Một số giá trị dài được rút gọn để hiển thị.',
};
export function warningText(codes = []) { return [...new Set(codes.map(code => warnings[code]).filter(Boolean))].join(' '); }
export function openDialog(id) { const dialog = $(id); if (!dialog.open) dialog.showModal(); }
let toastTimer;
export function toast(message) { clearTimeout(toastTimer); $('toast').textContent = message; $('toast').hidden = false; toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4200); }
