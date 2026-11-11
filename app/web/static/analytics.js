import { $, api, el, icon, emptyState, loading, busy, jobCard, skillName, warningText } from './ui.js';
import { number, sourceName, workplaceName, columnLabel } from './logic.mjs';

export function initAnalytics(context) {
  let controller = null;
  const display = (key, value) => value === null ? 'Chưa có thông tin' : key === 'source' ? sourceName(value) : key === 'skill_slug' ? skillName(value, context.overview()) : key === 'workplace_type' ? workplaceName(value) : typeof value === 'number' ? number(value) : String(value);
  function render(answer, question) {
    const host = $('ask-result');
    if (answer.status !== 'ok') {
      host.replaceChildren(emptyState(answer.status === 'needs_clarification' ? 'Bạn muốn hỏi cụ thể về điều gì?' : answer.status === 'rejected' ? 'Hãy thử câu hỏi đơn giản hơn' : 'Chưa tải được thống kê', answer.status === 'needs_clarification' ? 'Bạn có thể chọn một câu hỏi mẫu phía trên. Những yêu cầu phức tạp có thể chưa được hỗ trợ.' : answer.answer, 'info'));
      return;
    }
    if (answer.search) {
      host.replaceChildren(el('div', { class: 'notice' }, icon('search'), el('span', { text: 'Dưới đây là những việc làm liên quan đến câu hỏi của bạn.' })), ...answer.search.items.map(item => jobCard(item, context)));
      if (!answer.search.items.length) host.append(emptyState('Chưa có công việc được trả về.', 'Hãy thử một từ khóa khác hoặc sang trang Việc làm để chọn bộ lọc.'));
      return;
    }
    const data = answer.analytics;
    const card = el('section', { class: 'answer-card' }, el('div', { class: 'answer-header' }, icon('check'), 'KẾT QUẢ THỐNG KÊ'), el('h2', { class: 'answer-question', text: question }));
    if (!data || !data.rows.length) card.append(emptyState('Chưa có dữ liệu phù hợp', 'Thử một phạm vi khác hoặc hỏi lại với điều kiện rộng hơn.', 'chart'));
    else {
      if (data.rows.length === 1 && data.columns.every(key => typeof data.rows[0][key] === 'number')) card.append(el('div', { class: 'answer-metrics' }, data.columns.map(key => el('div', { class: 'answer-metric' }, el('strong', { text: number(data.rows[0][key]) }), el('span', { text: columnLabel(key) })))));
      const labelKey = data.columns.find(key => ['skill_slug', 'source', 'workplace_type', 'location'].includes(key));
      const countKey = data.columns.find(key => ['listing_count', 'n', 'count'].includes(key));
      if (labelKey && countKey && data.rows.every(row => Number.isFinite(row[countKey]) && row[countKey] >= 0)) {
        const max = Math.max(1, ...data.rows.map(row => row[countKey]));
        card.append(el('div', { class: 'answer-chart' }, data.rows.map(row => {
          const fill = el('span', { class: 'bar-fill' }); fill.style.width = `${row[countKey] / max * 100}%`;
          return el('div', {}, el('div', { class: 'bar-label' }, el('span', { text: display(labelKey, row[labelKey]) }), el('strong', { text: `${number(row[countKey])} tin` })), el('div', { class: 'bar-track', 'aria-hidden': 'true' }, fill));
        })));
      }
      card.append(el('div', { class: 'result-table-wrap' }, el('table', { class: 'result-table' }, el('caption', { class: 'sr-only', text: `Kết quả cho câu hỏi: ${question}` }), el('thead', {}, el('tr', {}, data.columns.map(key => el('th', { scope: 'col', text: columnLabel(key) })))), el('tbody', {}, data.rows.map(row => el('tr', {}, data.columns.map(key => el('td', { text: display(key, row[key]) }))))))));
    }
    card.append(el('p', { class: 'answer-note', text: 'Số liệu chỉ dựa trên các tin đã thu thập. Kỹ năng được nhắc trong tin chưa chắc là yêu cầu bắt buộc.' }));
    const warnings = warningText(answer.warnings); if (warnings) card.append(el('p', { class: 'answer-note', text: warnings }));
    if (data?.sql) card.append(el('details', { class: 'sql-details' }, el('summary', {}, 'Xem cách tính', icon('chevron-down')), el('pre', { text: data.sql })));
    host.replaceChildren(card);
  }
  async function ask() {
    const question = $('ask-question').value.trim();
    if (question.length < 2) { $('ask-question').setCustomValidity('Hãy nhập câu hỏi ít nhất 2 ký tự.'); $('ask-question').reportValidity(); return; }
    $('ask-question').setCustomValidity('');
    controller?.abort(); const current = new AbortController(); controller = current;
    busy($('ask-submit'), true, 'Đang đọc dữ liệu');
    $('ask-result').setAttribute('aria-busy', 'true'); $('ask-result').replaceChildren(loading('Đang tìm câu trả lời…'));
    try { const answer = await api('/ask', { method: 'POST', signal: current.signal, json: { question, top_k: 5 } }); if (controller === current) render(answer, question); }
    catch (error) { if (error.name !== 'AbortError' && current === controller) $('ask-result').replaceChildren(emptyState('Chưa thể trả lời câu hỏi.', error.message, 'info', el('button', { class: 'button secondary', onclick: ask, text: 'Thử lại' }))); }
    finally { if (current === controller) { busy($('ask-submit'), false, 'Hỏi dữ liệu'); $('ask-result').setAttribute('aria-busy', 'false'); } }
  }
  $('ask-form').addEventListener('submit', event => { event.preventDefault(); ask(); });
  $('ask-question').addEventListener('input', () => $('ask-question').setCustomValidity(''));
  $('ask-question').addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); ask(); } });
  document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => { $('ask-question').value = button.dataset.question; ask(); }));
}
