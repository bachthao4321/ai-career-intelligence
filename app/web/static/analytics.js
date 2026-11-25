import { $, api, el, icon, emptyState, loading, busy, jobCard, skillName, warningText, link } from './ui.js?v=20261125-1';
import { number, sourceName, workplaceName, columnLabel, questionCompletion } from './logic.mjs?v=20261125-1';

export function initAnalytics(context) {
  let controller = null;
  const questionInput = $('ask-question');
  const suggestion = $('ask-completion');
  const options = [
    'Có bao nhiêu tin nhắc đến Python?',
    'Có bao nhiêu tin tuyển dụng?',
    'Có bao nhiêu tin nhắc đến Java?',
    'Trong các job Data Engineer hiện có, công nghệ cloud nào được nhắc nhiều nhất?',
    'Nguồn nào có nhiều tin nhất?',
    'Top 10 skills',
    'Tìm việc Python',
  ];
  let completion = null;
  function updateCompletion() {
    const atEnd = questionInput.selectionStart === questionInput.value.length && questionInput.selectionEnd === questionInput.value.length;
    completion = atEnd ? (questionInput.value ? questionCompletion(questionInput.value, options) : options[0]) : null;
    suggestion.hidden = !completion || !questionInput.value;
    $('ask-completion-text').textContent = completion || '';
    $('ask-completion-status').textContent = completion ? `Gợi ý: ${completion}. Nhấn Tab để điền hoặc Escape để bỏ qua.` : '';
  }
  function acceptCompletion() {
    if (!completion) return;
    questionInput.value = completion;
    questionInput.setCustomValidity('');
    completion = null;
    suggestion.hidden = true;
    $('ask-completion-status').textContent = '';
    questionInput.focus();
    questionInput.setSelectionRange(questionInput.value.length, questionInput.value.length);
  }
  const display = (key, value) => value === null ? 'Chưa có thông tin' : key === 'source' ? sourceName(value) : key === 'skill_slug' ? skillName(value, context.overview()) : key === 'workplace_type' ? workplaceName(value) : typeof value === 'number' ? number(value) : String(value);
  function render(answer, question) {
    const host = $('ask-result');
    const layout = document.querySelector('.analytics-layout');
    layout.classList.toggle('has-answer', answer.status === 'ok');
    $('question-suggestion-toggle').hidden = answer.status !== 'ok';
    $('question-suggestion-toggle').setAttribute('aria-expanded', 'false');
    document.querySelector('.question-suggestions').hidden = answer.status === 'ok';
    if (answer.status !== 'ok') {
      host.replaceChildren(emptyState(answer.status === 'needs_clarification' ? 'Bạn muốn hỏi cụ thể về điều gì?' : answer.status === 'rejected' ? 'Hãy thử câu hỏi đơn giản hơn' : 'Chưa tải được thống kê', answer.status === 'needs_clarification' ? 'Bạn có thể chọn một câu hỏi mẫu phía trên. Những yêu cầu phức tạp có thể chưa được hỗ trợ.' : answer.answer, 'info', null, 'h2'));
      return;
    }
    if (answer.search) {
      host.replaceChildren(el('div', { class: 'notice' }, icon('search'), el('span', { text: `Tìm thấy ${number(answer.search.items.length)} kết quả liên quan để bạn tham khảo. Thứ tự thể hiện mức độ liên quan, chưa phải đánh giá phù hợp.` })), ...answer.search.items.map(item => jobCard(item, context)));
      if (!answer.search.items.length) host.append(emptyState('Chưa có công việc được trả về.', 'Hãy thử một từ khóa khác hoặc sang trang Việc làm để chọn bộ lọc.'));
      return;
    }
    const data = answer.analytics;
    const card = el('section', { class: 'answer-card' }, el('h2', { class: 'answer-header' }, 'Trả lời'));
    const first = data?.rows?.[0];
    let summary = '';
    if (first && data.insight_type === 'cloud_data_engineer') {
      summary = `Trong các tin có tiêu đề Data Engineer, ${display('skill_slug', first.skill_slug)} được nhắc trong ${number(first.listing_count)} tin${data.rows.length > 1 && data.rows[1].listing_count === first.listing_count ? ' (đồng hạng cao nhất)' : ', nhiều nhất trong các nền tảng được so sánh'}. Một tin có thể nhắc nhiều nền tảng.`;
    } else if (first && data.insight_type?.startsWith('skill_count:')) {
      summary = `Có ${number(first.listing_count)} tin nhắc đến ${skillName(data.insight_type.split(':')[1], context.overview())} trong dữ liệu hiện có.`;
    } else if (first && Number.isFinite(first.listing_count) && Number.isFinite(first.unique_content_count)) {
      summary = `Dữ liệu hiện có ${number(first.listing_count)} tin, tương ứng ${number(first.unique_content_count)} nhóm nội dung không trùng.`;
    } else if (first && data.insight_type === 'source_counts' && typeof first.source === 'string' && Number.isFinite(first.listing_count)) {
      summary = `${display('source', first.source)} đứng đầu kết quả với ${number(first.listing_count)} tin.`;
    } else if (first && typeof first.skill_slug === 'string' && Number.isFinite(first.listing_count)) {
      summary = `${display('skill_slug', first.skill_slug)} xuất hiện trong ${number(first.listing_count)} tin ở dòng đầu của kết quả.`;
    } else if (first) summary = `Truy vấn trả về ${number(data.rows.length)} dòng kết quả. Xem bảng bên dưới để đối chiếu.`;
    if (summary) card.append(el('p', { class: 'answer-summary', text: summary }));
    if (!data || !data.rows.length) card.append(emptyState('Chưa có dữ liệu phù hợp', 'Thử một phạm vi khác hoặc hỏi lại với điều kiện rộng hơn.', 'chart'));
    else {
      const labelKey = data.columns.find(key => ['skill_slug', 'source', 'workplace_type', 'location'].includes(key));
      const countKey = data.columns.find(key => ['listing_count', 'n', 'count'].includes(key));
      const showChart = labelKey && countKey && data.rows.every(row => Number.isFinite(row[countKey]) && row[countKey] >= 0);
      if (showChart) {
        const max = Math.max(1, ...data.rows.map(row => row[countKey]));
        card.append(el('div', { class: 'answer-chart' }, data.rows.map(row => {
          const fill = el('span', { class: 'bar-fill' }); fill.style.width = `${row[countKey] / max * 100}%`;
          return el('div', {}, el('div', { class: 'bar-label' }, el('span', { text: display(labelKey, row[labelKey]) }), el('strong', { text: `${number(row[countKey])} tin` })), el('div', { class: 'bar-track', 'aria-hidden': 'true' }, fill));
        })));
      }
      const table = el('div', { class: 'result-table-wrap' }, el('table', { class: 'result-table' }, el('caption', { class: 'sr-only', text: `Kết quả cho câu hỏi: ${question}` }), el('thead', {}, el('tr', {}, data.columns.map(key => el('th', { scope: 'col', text: columnLabel(key) })))), el('tbody', {}, data.rows.map(row => el('tr', {}, data.columns.map(key => el('td', { text: display(key, row[key]) })))))));
      if (!showChart && !(data.rows.length === 1 && (data.insight_type?.startsWith('skill_count:') || (Number.isFinite(first?.listing_count) && Number.isFinite(first?.unique_content_count))))) card.append(table);
    }
    card.append(el('p', { class: 'answer-note', text: 'Số liệu chỉ dựa trên các tin đã thu thập. Kỹ năng được nhắc trong tin chưa chắc là yêu cầu bắt buộc.' }));
    if (answer.evidence?.length) card.append(el('div', { class: 'answer-evidence' }, el('h3', { text: 'Tin làm dẫn chứng' }), el('p', { text: 'Một số tin thuộc nhóm đã được thống kê:' }), el('ul', {}, answer.evidence.map(item => el('li', {}, el('button', { class: 'text-button evidence-title', type: 'button', onclick: () => context.openJob(item.job_id), text: item.title }), el('span', { text: `${item.company || 'Chưa rõ công ty'}${item.location ? ` · ${item.location}` : ''} · ${skillName(item.skill_slug, context.overview())}` }), link(item.source_url, 'Tin gốc'))))));
    const warnings = warningText(answer.warnings); if (warnings) card.append(el('p', { class: 'answer-note', text: warnings }));
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
    catch (error) { if (error.name !== 'AbortError' && current === controller) $('ask-result').replaceChildren(emptyState('Chưa thể trả lời câu hỏi.', error.message, 'info', el('button', { class: 'button secondary', onclick: ask, text: 'Thử lại' }), 'h2')); }
    finally { if (current === controller) { busy($('ask-submit'), false, 'Hỏi dữ liệu'); $('ask-result').setAttribute('aria-busy', 'false'); } }
  }
  $('ask-form').addEventListener('submit', event => { event.preventDefault(); ask(); });
  $('question-suggestion-toggle').addEventListener('click', event => {
    const expanded = event.currentTarget.getAttribute('aria-expanded') !== 'true';
    event.currentTarget.setAttribute('aria-expanded', String(expanded));
    document.querySelector('.question-suggestions').hidden = !expanded;
  });
  questionInput.addEventListener('input', () => { questionInput.setCustomValidity(''); updateCompletion(); });
  questionInput.addEventListener('focus', updateCompletion);
  questionInput.addEventListener('click', updateCompletion);
  questionInput.addEventListener('keyup', event => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) updateCompletion(); });
  questionInput.addEventListener('keydown', event => {
    if (event.isComposing) return;
    if (event.key === 'Tab' && !event.shiftKey && completion) { event.preventDefault(); acceptCompletion(); }
    else if (event.key === 'Escape' && completion) { completion = null; suggestion.hidden = true; $('ask-completion-status').textContent = ''; }
    else if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); ask(); }
  });
  suggestion.addEventListener('click', acceptCompletion);
  document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => { questionInput.value = button.dataset.question; completion = null; suggestion.hidden = true; $('ask-completion-status').textContent = ''; ask(); }));
}
