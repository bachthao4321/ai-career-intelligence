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
export const columnLabel = key => ({ listing_count: 'Số tin', unique_content_count: 'Việc làm không trùng', skill_slug: 'Kỹ năng', source: 'Nguồn', workplace_type: 'Hình thức', location: 'Địa điểm', company: 'Công ty', title: 'Vị trí', n: 'Số lượng', count: 'Số lượng' }[key] || key.replaceAll('_', ' '));

// Treat line-break tags as formatting while keeping all other markup as text.
export const descriptionText = value => value.replace(/<br\s*\/?\s*>/gi, '\n');

// Only highlight recognizable section labels; keep their wording and all text intact.
const descriptionHeading = /^(\s*)((?:(?:yêu cầu|quyền lợi) đối với ứng viên|yêu cầu(?: công việc| ứng viên)?|trách nhiệm(?: công việc)?|mô tả công việc|quyền lợi(?: được hưởng)?|phúc lợi|nhiệm vụ(?: chính)?|kỹ năng(?: yêu cầu)?|về (?:công ty|chúng tôi|vị trí|công việc)|giới thiệu(?: về công ty)?|cách (?:ứng tuyển|nộp hồ sơ)|job (?:description|requirements|responsibilities|overview|summary|purpose)|(?:key |your )?responsibilities|(?:required |preferred )?(?:qualifications|skills|requirements)|(?:what you(?:'ll| will) (?:do|bring|need|get)|what we (?:offer|are looking for|provide)|about (?:the (?:role|job|company|team)|this (?:work area|role|position)|us)|who you are|who we are|your (?:profile|role|skills|experience)|how to apply|benefits|additional information|in return|we are looking for individuals who|qualities that will help your application stand out|what you might be working on as a graduate)|arbetsuppgifter|(?:dina |våra )?(?:arbetsuppgifter|kvalifikationer)|(?:vi söker dig som|om (?:tjänsten|rollen|oss|jobbet)|(?:din|vår) profil|krav|meriterande|ansvar|förmåner)))(\s*[:：?])?(?=\s|$)/iu;
export function descriptionSegments(value) {
  const text = descriptionText(value);
  return text.split(/(?<=\n)/).flatMap(line => {
    const match = descriptionHeading.exec(line);
    if (!match || (!match[3] && line.slice(match[0].length).trim())) return [{ text: line, heading: false }];
    return [
      { text: match[1], heading: false },
      { text: match[2] + (match[3] || ''), heading: true },
      { text: line.slice(match[0].length), heading: false },
    ];
  });
}
