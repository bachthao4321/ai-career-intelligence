import { $, api, el, icon, tag, jobHeading, link, skillName, emptyState, loading, notice, busy, warningText, openDialog, toast } from './ui.js?v=20261118-2';
import { savedCredential, time, date, number, skillLabel } from './logic.mjs?v=20261118-2';

const SESSION_KEY = 'careerlens.cv.v1';
export function initCV(context) {
  let credentials = null, profile = null, uploading = false, matching = null;
  function remember(value) { try { if (value) sessionStorage.setItem(SESSION_KEY, JSON.stringify(value)); else sessionStorage.removeItem(SESSION_KEY); } catch {} }
  function wipe(message = '') {
    matching?.abort(); matching = null;
    credentials = null; profile = null; remember(null);
    $('cv-active').hidden = true; $('cv-empty').hidden = false;
    $('cv-skills').replaceChildren(); $('cv-pages').replaceChildren(); $('cv-expiry').textContent = '';
    $('cv-match-results').replaceChildren(emptyState('Hồ sơ đã sẵn sàng.', 'Chọn “Đối chiếu” để tìm việc làm liên quan đến CV.', 'scan'));
    $('cv-match-results').setAttribute('aria-busy', 'false');
    $('match-form').reset(); $('cv-file').value = '';
    busy($('match-submit'), false, 'Đối chiếu');
    notice($('cv-message'), message, message ? 'warning' : '');
  }
  function expiry() {
    if (!credentials) return false;
    const remaining = Date.parse(credentials.expires_at) - Date.now();
    if (remaining <= 0) { wipe('Phiên CV đã hết hạn. Hãy tải lại hồ sơ để tiếp tục.'); return true; }
    $('cv-expiry').textContent = `Đến ${time(credentials.expires_at)} · ${remaining < 60000 ? 'còn dưới 1 phút' : `còn ${Math.ceil(remaining / 60000)} phút`}`;
    $('cv-expiry').title = `Hết hạn ${time(credentials.expires_at)} ngày ${date(credentials.expires_at)}`;
    return false;
  }
  function authorization(value = credentials) { return { Authorization: `Bearer ${value.token}` }; }
  function showProfile(data) {
    profile = data.profile;
    credentials = { ...credentials, id: data.id, expires_at: data.expires_at };
    remember(credentials);
    $('cv-empty').hidden = true; $('cv-active').hidden = false;
    $('cv-page-count').textContent = `${number(profile.pages.length)} trang đã đọc`;
    $('cv-skill-count').textContent = number(profile.skills.length);
    $('cv-skills').replaceChildren(...(profile.skills.length ? profile.skills.map(skill => {
      const detail = el('details', { class: 'cv-skill' }, el('summary', {}, skill.name, el('span', { text: `${skill.evidence.length} đoạn trích` })));
      for (const span of skill.evidence) {
        const page = profile.pages.find(item => item.number === span.page);
        detail.append(el('p', { class: 'evidence-label', text: `Trang ${span.page}` }));
        if (page) detail.append(el('blockquote', { class: 'evidence-block' }, page.text.slice(Math.max(0, span.start - 55), span.start), el('mark', { text: span.text }), page.text.slice(span.end, Math.min(page.text.length, span.end + 55))));
      }
      return detail;
    }) : [el('p', { class: 'report-note', text: 'Chưa tìm thấy kỹ năng trong CV. Hãy kiểm tra văn bản đã đọc hoặc nhập vị trí bạn muốn tìm.' })]));
    $('cv-pages').replaceChildren(...profile.pages.map(page => el('section', {}, el('h3', { class: 'cv-page-label', text: `TRANG ${page.number}` }), el('pre', { class: 'cv-page-text', text: page.text || 'Trang này chưa có văn bản được đọc.' }))), el('p', { class: 'report-note', text: warningText(profile.warnings) }));
    expiry();
  }
  function matchCard(job) {
    const shared = job.matched_skills.length, total = shared + job.skills_not_seen_in_cv.length;
    const coverage = el('div', { class: 'coverage' });
    coverage.append(el('div', { class: 'coverage-label' }, el('span', { text: total ? 'Kỹ năng trùng với CV' : 'Tin chưa có kỹ năng được nhận diện' }), total ? el('strong', { text: `${shared} / ${total}` }) : null));
    if (total) { const fill = el('span', { class: 'bar-fill' }); fill.style.width = `${shared / total * 100}%`; coverage.append(el('div', { class: 'bar-track', 'aria-hidden': 'true' }, fill)); }
    const compare = el('div', { class: 'comparison-grid' }, el('div', {}, el('h4', { text: 'Có trong CV và tin tuyển dụng' }), ...(job.matched_skills.length ? job.matched_skills.map(skill => tag(skillName(skill.slug, context.overview()), 'green')) : [el('span', { class: 'muted small', text: 'Chưa tìm thấy' })])), el('div', {}, el('h4', { text: 'Chưa thấy trong CV' }), ...(job.skills_not_seen_in_cv.length ? job.skills_not_seen_in_cv.map(skill => tag(skillLabel(skillName(skill.slug, context.overview()), skill.requirement), skill.requirement === 'required' ? 'amber' : '')) : [el('span', { class: 'muted small', text: 'Không có trong phần đã nhận diện' })])));
    const details = el('details', { class: 'comparison-detail' }, el('summary', {}, 'Xem đoạn trích đối chiếu', icon('chevron-down')));
    for (const skill of job.matched_skills) details.append(el('h4', { text: skillName(skill.slug, context.overview()) }), el('blockquote', { class: 'evidence-block', text: `CV · ${skill.cv_evidence.map(span => `trang ${span.page}: “${span.text}”`).join('; ')}\nTin gốc · “${skill.job_evidence.text}”` }));
    if (job.skills_not_seen_in_cv.length) details.append(el('h4', { text: 'Tin tuyển dụng có nhắc, CV chưa đề cập' }), ...job.skills_not_seen_in_cv.map(skill => el('blockquote', { class: 'evidence-block', text: `${skillName(skill.slug, context.overview())} · Tin gốc: “${skill.job_evidence.text}”` })));
    if (job.cv_skills_not_mentioned_in_job.length) details.append(el('h4', { text: 'Có trong CV, chưa thấy trong tin' }), el('p', { class: 'report-note', text: job.cv_skills_not_mentioned_in_job.map(slug => skillName(slug, context.overview())).join(', ') }));
    return el('article', { class: 'match-card' }, jobHeading(job, context.openJob), coverage, compare, total || job.cv_skills_not_mentioned_in_job.length ? details : null, el('div', { class: 'job-card-footer match-footer' }, context.saveButton(job), link(job.source_url, 'Mở tin gốc'), el('button', { class: 'text-button', onclick: () => context.openJob(job.job_id) }, 'Xem công việc', icon('arrow-right'))));
  }
  async function match() {
    if (!credentials || expiry()) return;
    const target = $('target-role').value.trim();
    if (target && target.length < 2) { $('target-role').reportValidity(); return; }
    matching?.abort(); const current = new AbortController(); matching = current;
    const currentCredentials = { ...credentials };
    const json = { top_k: 5, filters: {} };
    if (target) json.target_role = target;
    if ($('cv-location').value.trim()) json.filters.location = $('cv-location').value.trim();
    busy($('match-submit'), true, 'Đang đối chiếu');
    $('cv-match-results').setAttribute('aria-busy', 'true'); $('cv-match-results').replaceChildren(loading('Đang tìm việc làm liên quan đến CV…'));
    try {
      const report = await api(`/cv/${currentCredentials.id}/matches`, { method: 'POST', headers: authorization(currentCredentials), signal: current.signal, json });
      if (matching !== current || credentials?.id !== currentCredentials.id || expiry()) return;
      const header = el('h2', { class: 'match-result-heading' }, 'Việc làm liên quan đến CV ', el('span', { class: 'count-pill', text: number(report.items.length) }));
      const note = el('p', { class: 'report-note', text: `Đã so sánh với ${number(report.unique_documents)} việc làm. “Chưa thấy trong CV” chỉ có nghĩa là CV chưa đề cập kỹ năng đó. Thứ tự kết quả không dự đoán khả năng được tuyển.` });
      $('cv-match-results').replaceChildren(header, note, ...(report.items.length ? report.items.map(matchCard) : [emptyState('Chưa tìm thấy việc làm liên quan', 'Thử một vị trí khác, bỏ bộ lọc địa điểm hoặc kiểm tra kỹ năng/văn bản đã đọc.', 'scan')]));
      const warning = warningText(report.warnings.filter(code => !['literal_mentions_not_verified_proficiency_or_negation', 'pdf_reading_order_may_differ_from_visual_layout'].includes(code)));
      if (warning) $('cv-match-results').append(el('p', { class: 'report-note', text: warning }));
    } catch (error) {
      if (error.name === 'AbortError' || matching !== current) return;
      if (error.status === 404) wipe('Phiên CV đã hết hạn hoặc không còn truy cập được. Hãy tải lại CV.');
      else $('cv-match-results').replaceChildren(emptyState('Chưa thể đối chiếu CV.', error.message, 'info', el('button', { class: 'button secondary', onclick: match, text: 'Thử lại' })));
    } finally { if (matching === current) { busy($('match-submit'), false, 'Đối chiếu'); $('cv-match-results').setAttribute('aria-busy', 'false'); } }
  }
  async function upload(file) {
    if (!file || uploading || credentials) return;
    const max = context.overview()?.cv_max_bytes || 2 * 1024 * 1024;
    if (file.size > max) { notice($('cv-message'), 'File vượt giới hạn 2 MiB. Hãy xuất lại CV gọn hơn.', 'error'); return; }
    if (!/\.pdf$/i.test(file.name) && file.type !== 'application/pdf') { notice($('cv-message'), 'Hãy chọn một file PDF có văn bản.', 'error'); return; }
    uploading = true; $('cv-file').disabled = true; $('drop-zone').setAttribute('aria-busy', 'true');
    notice($('cv-message'), 'Đang đọc văn bản và nhận diện kỹ năng trong CV…');
    try {
      const data = await api('/cv', { method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: file });
      credentials = { id: data.id, token: data.access_token, expires_at: data.expires_at };
      showProfile(data); notice($('cv-message'), ''); toast('Đã đọc CV. Bạn có thể mở từng kỹ năng để xem đoạn trích.');
      if (profile?.skills.length) await match();
    } catch (error) { notice($('cv-message'), error.message, 'error'); }
    finally { uploading = false; $('cv-file').disabled = false; $('drop-zone').setAttribute('aria-busy', 'false'); $('cv-file').value = ''; }
  }
  $('cv-file').addEventListener('change', event => upload(event.target.files?.[0]));
  for (const event of ['dragenter', 'dragover']) $('drop-zone').addEventListener(event, event => { event.preventDefault(); if (!uploading) $('drop-zone').classList.add('dragging'); });
  for (const event of ['dragleave', 'drop']) $('drop-zone').addEventListener(event, event => { event.preventDefault(); $('drop-zone').classList.remove('dragging'); });
  $('drop-zone').addEventListener('drop', event => { if (event.dataTransfer.files.length > 1) notice($('cv-message'), 'Hãy chọn một CV mỗi lần.', 'warning'); else upload(event.dataTransfer.files[0]); });
  $('match-form').addEventListener('submit', event => { event.preventDefault(); match(); });
  $('delete-cv').addEventListener('click', () => { if (credentials && !expiry()) openDialog('delete-dialog'); });
  $('confirm-delete-cv').addEventListener('click', async () => {
    if (!credentials) { $('delete-dialog').close(); return; }
    const current = { ...credentials }; matching?.abort(); matching = null;
    busy($('confirm-delete-cv'), true, 'Đang xóa'); $('match-submit').disabled = true;
    try { await api(`/cv/${current.id}`, { method: 'DELETE', headers: authorization(current) }); wipe(); $('delete-dialog').close(); toast('Đã xóa hồ sơ CV khỏi hệ thống đang chạy.'); }
    catch (error) { if (error.status === 404) { wipe(); $('delete-dialog').close(); toast('Hồ sơ không còn truy cập được.'); } else { $('delete-dialog').close(); notice($('cv-message'), `Chưa xóa được CV. ${error.message}`, 'error'); } }
    finally { busy($('confirm-delete-cv'), false, 'Xóa hồ sơ'); $('match-submit').disabled = false; }
  });
  setInterval(expiry, 1000);
  (async () => {
    let stored; try { stored = savedCredential(sessionStorage.getItem(SESSION_KEY)); } catch {}
    if (!stored) { remember(null); return; }
    credentials = stored; uploading = true; $('cv-file').disabled = true;
    notice($('cv-message'), 'Đang khôi phục phiên CV trong tab này…');
    try { showProfile(await api(`/cv/${stored.id}`, { headers: authorization(stored) })); notice($('cv-message'), ''); }
    catch (error) { if (error.status === 404) wipe('Phiên CV trước đã hết hạn hoặc đã bị xóa.'); else { wipe(); remember(stored); notice($('cv-message'), 'Chưa khôi phục được hồ sơ. Phiên cũ vẫn được giữ trong tab; hãy thử tải lại trang khi máy chủ sẵn sàng.', 'error'); } }
    finally { uploading = false; $('cv-file').disabled = false; }
  })();
}
