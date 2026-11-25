import { $, api, el, icon, jobCard, notice, emptyState, skeletons, busy, warningText } from './ui.js?v=20261125-1';
import { number, onlySemantic } from './logic.mjs?v=20261125-1';

export function initJobs(context) {
  let page = 0, data = null, mode = 'browse', controller = null;
  let applied = [], appliedQuery = '';
  const size = 8;
  const panel = $('filter-panel');
  const compactFilters = matchMedia('(max-width: 1000px)');
  function adaptFilters() { panel.open = !compactFilters.matches; }
  compactFilters.addEventListener('change', adaptFilters);
  adaptFilters();
  function filters() {
    const values = { source: $('filter-source').value, location: $('filter-location').value.trim(), workplace_type: $('filter-workplace').value };
    const result = Object.fromEntries(Object.entries(values).filter(([, value]) => value));
    if ($('filter-skill').value) result.skills = [$('filter-skill').value];
    return result;
  }
  function renderApplied() {
    const selected = [
      ['search-query', 'Từ khóa', appliedQuery],
      ...applied.map(([id, label, value]) => [id, label, value]),
    ].filter(([, , value]) => value);
    const box = $('active-filters');
    const count = applied.filter(([id, , value]) => id !== 'filter-location' && value).length;
    $('filter-count').textContent = String(count);
    $('filter-count').hidden = count === 0;
    box.hidden = !selected.length;
    box.replaceChildren(...[el('span', { text: 'Đang áp dụng' }), ...selected.map(([id, label, value]) =>
      el('button', { class: 'filter-chip', type: 'button', 'aria-label': `Bỏ ${label.toLowerCase()}: ${value}`, onclick: () => {
        $(id).value = '';
        const disclosure = $(id).closest('details');
        if (disclosure) disclosure.open = true;
        load(); $(id).focus();
      } }, el('span', { text: `${label}: ${value}` }), icon('close'))),
      selected.length > 1 ? el('button', { class: 'text-button clear-filters', type: 'button', onclick: reset, text: 'Xóa tất cả' }) : null].filter(Boolean));
  }
  function paint() {
    const items = mode === 'browse' ? data.items : data.items.slice(page * size, (page + 1) * size);
    const total = mode === 'browse' ? data.total : data.items.length;
    $('job-results').replaceChildren(...(items.length ? items.map(item => jobCard(item, context)) : [emptyState('Chưa tìm thấy việc làm phù hợp', 'Thử một từ khóa rộng hơn hoặc bỏ bớt bộ lọc.', 'search', el('button', { class: 'button secondary', onclick: reset, text: 'Xóa bộ lọc' }))]));
    $('job-results').setAttribute('aria-busy', 'false');
    $('results-heading').firstChild.textContent = mode === 'browse' ? 'Việc làm ' : 'Kết quả tìm kiếm ';
    $('results-count').textContent = number(total);
    $('results-status').textContent = mode === 'browse' ? `${number(total)} việc làm phù hợp. Trang ${page + 1}.` : `${number(total)} kết quả được trả về cho “${appliedQuery}”. Trang ${page + 1}.`;
    $('results-description').textContent = mode === 'browse' ? `${number(data.total_listings)} tin trong bộ lọc · Đã gộp nội dung trùng.` : `${number(data.unique_documents)} việc làm được tìm kiếm · Hiển thị tối đa 20 kết quả.`;
    $('sort-label').replaceChildren(mode === 'browse' ? 'Mới cập nhật' : 'Liên quan nhất', icon('arrow-down'));
    $('job-pagination').hidden = total <= size;
    $('previous-page').disabled = page === 0;
    $('next-page').disabled = (page + 1) * size >= total;
    $('pagination-label').textContent = `${number(page * size + 1)}–${number(Math.min((page + 1) * size, total))} / ${number(total)} ${mode === 'browse' ? 'việc làm' : 'kết quả trả về'}`;
    const warnings = warningText(data.warnings);
    const fallback = mode === 'search' && data.requested_mode === 'hybrid' && data.effective_mode === 'bm25';
    const semantic = mode === 'search' && data.effective_mode === 'hybrid' && onlySemantic(data.items);
    const semanticNote = semantic ? `Không có tin nào chứa từ khóa “${appliedQuery}”. Các kết quả dưới đây chỉ gần về ý nghĩa và có thể không liên quan. ` : '';
    notice($('search-notice'), `${fallback ? 'Đã dùng tìm theo từ khóa vì tìm ngữ nghĩa chưa sẵn sàng. ' : ''}${semanticNote}${warnings}`, fallback || semantic ? 'warning' : '');
    renderApplied();
  }
  async function load(resetPage = true) {
    if (resetPage) page = 0;
    const query = $('search-query').value.trim();
    if (query && query.length < 2) { $('search-query').setCustomValidity('Hãy nhập ít nhất 2 ký tự.'); $('search-query').reportValidity(); return; }
    $('search-query').setCustomValidity('');
    controller?.abort();
    const current = new AbortController(); controller = current;
    $('job-results').setAttribute('aria-busy', 'true');
    $('results-status').textContent = 'Đang tải công việc…';
    $('job-results').replaceChildren(...skeletons(3));
    $('job-pagination').hidden = true;
    notice($('search-notice'), '');
    busy($('search-submit'), true, 'Đang tìm');
    try {
      const nextMode = query ? 'search' : 'browse';
      const selectedFilters = filters();
      const nextApplied = [['filter-location', 'Địa điểm'], ['filter-source', 'Nguồn'], ['filter-workplace', 'Hình thức'], ['filter-skill', 'Kỹ năng']].map(([id, label]) => {
        const field = $(id);
        return [id, label, field.value ? (field.tagName === 'SELECT' ? field.selectedOptions[0].textContent : field.value.trim()) : ''];
      });
      let nextData;
      if (query) nextData = await api('/search', { method: 'POST', signal: current.signal, json: { query, filters: selectedFilters, mode: $('search-mode').value, top_k: 20 } });
      else {
        const params = new URLSearchParams({ limit: String(size), offset: String(page * size) });
        for (const [key, value] of Object.entries(selectedFilters)) for (const item of Array.isArray(value) ? value : [value]) params.append(key, item);
        nextData = await api(`/platform/jobs?${params}`, { signal: current.signal });
      }
      if (current !== controller) return false;
      data = nextData; mode = nextMode; applied = nextApplied; appliedQuery = query;
      paint();
      return true;
    } catch (error) {
      if (error.name === 'AbortError' || current !== controller) return false;
      $('job-results').replaceChildren(emptyState('Chưa đọc được danh sách công việc.', error.message, 'info', el('button', { class: 'button secondary', onclick: () => load(false), text: 'Thử lại' })));
      $('job-results').setAttribute('aria-busy', 'false');
      $('results-count').textContent = '—';
      $('results-description').textContent = 'Kiểm tra kết nối hoặc thử lại sau.';
      $('results-status').textContent = 'Chưa tải được công việc. Hãy thử lại.';
      return true;
    } finally { if (current === controller) busy($('search-submit'), false, 'Tìm việc'); }
  }
  function reset() { $('search-form').reset(); $('search-query').setCustomValidity(''); load(); }
  function focusResults() {
    $('results-heading').focus({ preventScroll: true });
    const applied = $('active-filters');
    (applied.hidden ? $('results-heading') : applied).scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }
  $('search-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (await load()) {
      if (compactFilters.matches) panel.open = false;
      focusResults();
    }
  });
  $('search-query').addEventListener('input', () => $('search-query').setCustomValidity(''));
  $('reset-filters').addEventListener('click', reset);
  for (const id of ['filter-source', 'filter-workplace', 'filter-skill']) $(id).addEventListener('change', () => load());
  document.querySelectorAll('[data-query]').forEach(button => button.addEventListener('click', async () => { $('search-query').value = button.dataset.query; if (await load()) focusResults(); }));
  async function changePage(direction) {
    page += direction;
    if (mode === 'browse') { if (!await load(false)) return; } else paint();
    focusResults();
  }
  $('previous-page').addEventListener('click', () => { if (page > 0) changePage(-1); });
  $('next-page').addEventListener('click', () => changePage(1));
  return { load, searchSkill(slug) { location.hash = 'jobs'; $('search-query').value = ''; $('filter-skill').value = slug; load(); } };
}
