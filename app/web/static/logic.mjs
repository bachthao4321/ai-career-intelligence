export const sourceName = source => ({ jobtech: 'JobTech', itviec: 'ITviec', topcv: 'TopCV', employer_ats: 'Tuyển dụng công ty', synthetic: 'Dữ liệu giả lập', 'synthetic-fixture': 'Dữ liệu giả lập' }[source] || source);
export function questionCompletion(value, choices) {
  const normalize = text => text.toLocaleLowerCase('vi').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd');
  const query = normalize(value.trimStart());
  if (!query.trim() || query.includes('\n')) return null;
  return choices.find(text => normalize(text).startsWith(query) && normalize(text) !== query) || null;
}
export const requirementName = value => ({ required: 'bắt buộc', preferred: 'ưu tiên', negated: 'không cần' }[value] || '');
export const requirementTone = value => ({ required: 'amber', preferred: '', negated: 'muted' }[value] || '');
export function skillLabel(name, requirement) { const label = requirementName(requirement); return label ? `${name} · ${label}` : name; }
export const workplaceName = value => ({ remote: 'Từ xa', hybrid: 'Kết hợp từ xa và văn phòng', onsite: 'Tại văn phòng' }[value] || 'Chưa có thông tin');
export const number = value => new Intl.NumberFormat('vi-VN').format(value ?? 0);
export const date = value => value && Number.isFinite(new Date(value).getTime()) ? new Intl.DateTimeFormat('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(value)) : 'Chưa ghi nhận';
export const time = value => new Intl.DateTimeFormat('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', hour: '2-digit', minute: '2-digit' }).format(new Date(value));
export function safeUrl(value) {
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
}
export function initials(value) { return (value || 'IT').trim().split(/\s+/).slice(0, 2).map(word => Array.from(word)[0] || '').join('').toUpperCase(); }
export function savedCredential(value, now = Date.now()) {
  try {
    const data = JSON.parse(value);
    if (!data || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(data.id) || !/^[A-Za-z0-9_-]{43}$/.test(data.token) || !Number.isFinite(Date.parse(data.expires_at)) || Date.parse(data.expires_at) <= now) return null;
    return { id: data.id, token: data.token, expires_at: data.expires_at };
  } catch { return null; }
}
export function duration(seconds) { return seconds >= 3600 && seconds % 3600 === 0 ? `${seconds / 3600} giờ` : `${Math.ceil(seconds / 60)} phút`; }
// Hybrid/dense search always returns nearest neighbours; flag hits that share no query term.
export const matchKind = hit => !hit || !('bm25_score' in hit) ? null : hit.bm25_score === null ? 'semantic' : 'keyword';
export const onlySemantic = items => items.length > 0 && items.every(item => matchKind(item) === 'semantic');
export const columnLabel = key => ({ listing_count: 'Số tin', unique_content_count: 'Việc làm không trùng', skill_slug: 'Kỹ năng', source: 'Nguồn', workplace_type: 'Hình thức', location: 'Địa điểm', company: 'Công ty', title: 'Vị trí', n: 'Số lượng', count: 'Số lượng' }[key] || key.replaceAll('_', ' '));

// Treat line-break tags as formatting while keeping all other markup as text.
export const descriptionText = value => value.replace(/<br\s*\/?\s*>/gi, '\n');

// Only highlight recognizable section labels; keep their wording and all text intact.
const descriptionHeading = /^(\s*)((?:(?:yêu cầu|quyền lợi) đối với ứng viên|yêu cầu(?: công việc| ứng viên)?|trách nhiệm(?: công việc)?|mô tả công việc|quyền lợi(?: được hưởng)?|phúc lợi|nhiệm vụ(?: chính)?|kỹ năng(?: yêu cầu)?|về (?:công ty|chúng tôi|vị trí|công việc)|giới thiệu(?: về công ty)?|cách (?:ứng tuyển|nộp hồ sơ)|job (?:description|requirements|responsibilities|overview|summary|purpose)|(?:key |your )?responsibilities|(?:required |preferred )?(?:qualifications|skills|requirements)|(?:what you(?:'ll| will) (?:do|bring|need|get)|what we (?:offer|are looking for|provide)|about (?:the (?:role|job|company|team)|this (?:work area|role|position)|us)|who you are|who we are|your (?:profile|role|skills|experience)|how to apply|benefits|additional information|in return|we are looking for individuals who|qualities that will help your application stand out|what you might be working on as a graduate)|arbetsuppgifter|(?:dina |våra )?(?:arbetsuppgifter|kvalifikationer)|(?:vi söker dig som|om (?:tjänsten|rollen|oss|jobbet)|(?:din|vår) profil|krav|meriterande|ansvar|förmåner)))(\s*[:：?])?(?=\s|$)/iu;
// Whole-line section titles seen across sources (sv, en, vi), compared after normalization.
const KNOWN_HEADINGS = new Set([
  'ansökan', 'din ansökan', 'om uppdraget', 'övrig information', 'övrigt', 'om dig', 'det är meriterande om du har',
  'personliga egenskaper', 'praktisk information', 'obligatoriska krav', 'om företaget', 'arbetsbeskrivning',
  'uppdragsinformation', 'det här kommer du göra', 'vi erbjuder', 'bra att veta', 'din roll', 'om anställningen',
  'vi ser gärna att du har', 'huvudsakliga arbetsuppgifter', 'ansvarsområden', 'exempel på arbetsuppgifter', 'erfarenhet',
  'vår rekryteringsprocess', 'vi söker dig som har', 'teknik & verktyg', 'kompetens', 'utbildning', 'villkor',
  'du kommer bland annat att', 'i rollen kommer du bland annat att', 'vad du blir en del av', 'kontakt', 'tillträde',
  'what you bring', 'nice to have', 'meritorious', "what you'll do", 'what you will do', 'what you will work on',
  'assignment description', 'company description', 'requirements', 'responsibilities', 'qualifications', 'benefits',
  'about you', 'the role', 'what we offer', "what we're looking for", 'who you are', 'perks', 'must have', 'must-haves',
  'bonus points', 'preferred qualifications', 'minimum qualifications', 'basic qualifications', 'job description',
  'your responsibilities', 'our offer', 'the team', 'why join us', 'tech stack', 'how to apply', 'your profile',
  'thông tin chung', 'mô tả công việc', 'yêu cầu công việc', 'quyền lợi', 'phúc lợi', 'chế độ đãi ngộ', 'kinh nghiệm',
  'địa điểm làm việc', 'thời gian làm việc', 'về chúng tôi', 'giới thiệu công ty', 'công việc của bạn',
  'tại sao bạn sẽ yêu thích làm việc tại đây', 'vad vi erbjuder', 'role summary', 'important notes', 'uppdragsbeskrivning',
  'your impact', 'för att lyckas och trivas i rollen ser vi att du har', 'what you need', 'about us', 'about the company',
  'what you will own', "what you'll own", 'what you will learn', 'where you will work', 'our stack', 'the opportunity',
]);
// "Om Randstad" / "About GreenNode": a short about-the-company title, but not "Om du ..." ("If you ...").
const ABOUT_TITLE = /^(om|about) (?!(du|ni|vi|you|we|this|the)\b)[\p{L}0-9&.' -]{2,40}$/iu;
const headingKey = line => line.trim().toLocaleLowerCase('vi').replace(/[’`]/g, "'").replace(/\s*[:：?]\s*$/, '');
// A standalone short line that is a known title, ends with a colon, or is written in capitals.
export function isHeadingLine(value) {
  const line = value.trim();
  if (line.length < 3 || line.length > 70 || /https?:|www\.|^[-•*–·]/.test(line)) return false;
  if (KNOWN_HEADINGS.has(headingKey(line))) return true;
  if (ABOUT_TITLE.test(line) && line.split(/\s+/).length <= 4) return true;
  if (line.split(/\s+/).length > 9) return false;
  if (/[:：]$/.test(line) && !/[.!?;]\s/.test(line)) return true;
  const letters = line.replace(/[^\p{L}]/gu, '');
  return letters.length >= 4 && line === line.toLocaleUpperCase('vi') && line !== line.toLocaleLowerCase('vi');
}
export function descriptionSegments(value) {
  const text = descriptionText(value);
  return text.split(/(?<=\n)/).flatMap(line => {
    if (isHeadingLine(line)) {
      const start = line.length - line.trimStart().length, end = line.trimEnd().length;
      return [{ text: line.slice(0, start), heading: false }, { text: line.slice(start, end), heading: true }, { text: line.slice(end), heading: false }];
    }
    const match = descriptionHeading.exec(line);
    if (!match || (!match[3] && line.slice(match[0].length).trim())) return [{ text: line, heading: false }];
    return [
      { text: match[1], heading: false },
      { text: match[2] + (match[3] || ''), heading: true },
      { text: line.slice(match[0].length), heading: false },
    ];
  });
}

const passwordProblem = password => password.length < 8 ? 'Mật khẩu cần tối thiểu 8 ký tự.' : password.length > 128 ? 'Mật khẩu tối đa 128 ký tự.' : null;
export const normalizeUsername = value => value.trim().toLowerCase();
export function credentialProblem(email, password) {
  if (!/^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$/.test(email) || email.length > 254) return 'Hãy nhập một địa chỉ email hợp lệ.';
  return passwordProblem(password);
}
// Mirrors the API rule: 3-30 chars of a-z, 0-9, dot or underscore, alphanumeric at both ends.
export function usernameProblem(value) {
  const name = normalizeUsername(value);
  if (name.length < 3 || name.length > 30) return 'Tên người dùng cần 3–30 ký tự.';
  if (!/^[a-z0-9][a-z0-9._]{1,28}[a-z0-9]$/.test(name)) return 'Tên người dùng chỉ gồm chữ thường, số, dấu chấm hoặc gạch dưới, và bắt đầu, kết thúc bằng chữ hoặc số.';
  return null;
}
export function loginProblem(login, password) {
  if (login.trim().length < 3) return 'Hãy nhập email hoặc tên người dùng.';
  return passwordProblem(password);
}

// Application pipeline for saved jobs; keys mirror the API's ApplicationStatus.
export const APPLICATION_STATUSES = [
  ['saved', 'Đã lưu'], ['applied', 'Đã nộp'], ['interviewing', 'Phỏng vấn'], ['offer', 'Nhận offer'], ['rejected', 'Không đạt'],
];
export const statusName = value => (APPLICATION_STATUSES.find(([key]) => key === value) || [, 'Đã lưu'])[1];
export function statusCounts(items) {
  const counts = Object.fromEntries(APPLICATION_STATUSES.map(([key]) => [key, 0]));
  for (const item of items) if (item.status in counts) counts[item.status] += 1;
  return counts;
}

// Estimated translation time from source length: about 4 ms per character measured on the
// local model (8,208 characters in ~31 s), plus a fixed start-up cost.
export function translationEstimateMs(characters) {
  return Math.min(240000, Math.max(5000, 3000 + 4 * Math.max(0, characters)));
}

// The API answers once, so progress is an estimate: linear to 90% at the expected time,
// then easing towards 99% until the real result arrives. Never reports 100% on its own.
export function translationProgress(elapsedMs, estimateMs) {
  const elapsed = Math.max(0, elapsedMs);
  if (elapsed < estimateMs) return Math.floor((90 * elapsed) / estimateMs);
  return Math.min(99, Math.floor(90 + 9 * (1 - Math.exp(-(elapsed - estimateMs) / estimateMs))));
}
