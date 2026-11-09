import { $, api, el, icon, jobCard, notice, emptyState, skeletons, busy, warningText } from './ui.js';
import { number } from './logic.mjs';

export function initJobs(context) {
  let page = 0, data = null, mode = 'browse', controller = null;
  const size = 8;
  function filters() {
    const values = { source: $('filter-source').value, location: $('filter-location').value.trim(), workplace_type: $('filter-workplace').value };
    const result = Object.fromEntries(Object.entries(values).filter(([, value]) => value));
    if ($('filter-skill').value) result.skills = [$('filter-skill').value];
    return result;
  }
  function paint() {
    const items = mode === 'browse' ? data.items : data.items.slice(page * size, (page + 1) * size);
    const total = mode === 'browse' ? data.total : data.items.length;
    $('job-results').replaceChildren(...(items.length ? items.map(item => jobCard(item, context)) : [emptyState('Chưa có kết quả trong phạm vi này.', 'Thử một từ khóa rộng hơn hoặc bỏ bớt bộ lọc.', 'search', el('button', { class: 'button secondary', onclick: reset, text: 'Bỏ bộ lọc và khám phá lại' }))]));
    $('job-results').setAttribute('aria-busy', 'false');
    $('results-heading').firstChild.textContent = mode === 'browse' ? 'Khám phá công việc ' : 'Kết quả tìm kiếm ';
    $('results-count').textContent = number(total);
    $('results-description').textContent = mode === 'browse' ? `${number(data.total_listings)} tin trong bộ lọc · Đã gộp nội dung trùng.` : `${number(data.unique_documents)} nhóm tin được tìm trong bộ lọc · Hiển thị tối đa 20 kết quả.`;
    $('sort-label').replaceChildren(mode === 'browse' ? 'Thu thập gần nhất' : 'Mức liên quan', icon('arrow-down'));
    $('job-pagination').hidden = total <= size;
    $('previous-page').disabled = page === 0;
    $('next-page').disabled = (page + 1) * size >= total;
    $('pagination-label').textContent = `${number(page * size + 1)}–${number(Math.min((page + 1) * size, total))} / ${number(total)} ${mode === 'browse' ? 'nhóm tin' : 'kết quả trả về'}`;
    const warnings = warningText(data.warnings);
    const fallback = mode === 'search' && data.requested_mode === 'hybrid' && data.effective_mode === 'bm25';
    notice($('search-notice'), `${fallback ? 'Đã dùng tìm theo từ khóa vì tìm ngữ nghĩa chưa sẵn sàng. ' : ''}${warnings}`, fallback ? 'warning' : '');
  }
  async function load(resetPage = true) {
    if (resetPage) page = 0;
    const query = $('search-query').value.trim();
    if (query && query.length < 2) { $('search-query').setCustomValidity('Hãy nhập ít nhất 2 ký tự.'); $('search-query').reportValidity(); return; }
    $('search-query').setCustomValidity('');
    controller?.abort();
    const current = new AbortController(); controller = current;
    $('job-results').setAttribute('aria-busy', 'true');
    $('job-results').replaceChildren(...skeletons(3));
    $('job-pagination').hidden = true;
    notice($('search-notice'), '');
    busy($('search-submit'), true, 'Đang tìm');
    try {
      mode = query ? 'search' : 'browse';
      if (query) data = await api('/search', { method: 'POST', signal: current.signal, json: { query, filters: filters(), mode: $('search-mode').value, top_k: 20 } });
      else {
        const params = new URLSearchParams({ limit: String(size), offset: String(page * size) });
        for (const [key, value] of Object.entries(filters())) for (const item of Array.isArray(value) ? value : [value]) params.append(key, item);
        data = await api(`/platform/jobs?${params}`, { signal: current.signal });
      }
      if (current === controller) paint();
    } catch (error) {
      if (error.name === 'AbortError' || current !== controller) return;
      $('job-results').replaceChildren(emptyState('Chưa đọc được danh sách công việc.', error.message, 'info', el('button', { class: 'button secondary', onclick: () => load(false), text: 'Thử lại' })));
      $('job-results').setAttribute('aria-busy', 'false');
      $('results-count').textContent = '—';
      $('results-description').textContent = 'Kiểm tra kết nối hoặc thử lại sau.';
    } finally { if (current === controller) busy($('search-submit'), false, 'Tìm công việc'); }
  }
  function reset() { $('search-form').reset(); $('search-query').setCustomValidity(''); load(); }
  $('search-form').addEventListener('submit', event => { event.preventDefault(); load(); });
  $('search-query').addEventListener('input', () => $('search-query').setCustomValidity(''));
  $('reset-filters').addEventListener('click', reset);
  for (const id of ['filter-source', 'filter-workplace', 'filter-skill']) $(id).addEventListener('change', () => load());
  document.querySelectorAll('[data-query]').forEach(button => button.addEventListener('click', () => { $('search-query').value = button.dataset.query; load(); }));
  $('previous-page').addEventListener('click', () => { if (page > 0) { page--; if (mode === 'browse') load(false); else paint(); $('results-heading').scrollIntoView({ block: 'start', behavior: 'smooth' }); } });
  $('next-page').addEventListener('click', () => { page++; if (mode === 'browse') load(false); else paint(); $('results-heading').scrollIntoView({ block: 'start', behavior: 'smooth' }); });
  return { load, searchSkill(slug) { location.hash = 'jobs'; $('search-query').value = ''; $('filter-skill').value = slug; load(); } };
}
