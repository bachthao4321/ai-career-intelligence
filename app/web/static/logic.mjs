export const sourceName = source => ({ jobtech: 'JobTech', itviec: 'ITviec', topcv: 'TopCV', synthetic: 'Dữ liệu giả lập', 'synthetic-fixture': 'Dữ liệu giả lập' }[source] || source);
export const workplaceName = value => ({ remote: 'Remote', hybrid: 'Hybrid', onsite: 'Tại văn phòng' }[value] || 'Chưa ghi nhận');
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
export const columnLabel = key => ({ listing_count: 'Số tin', unique_content_count: 'Nhóm nội dung', skill_slug: 'Kỹ năng', source: 'Nguồn', workplace_type: 'Hình thức', location: 'Địa điểm', company: 'Công ty', title: 'Vị trí', n: 'Số lượng', count: 'Số lượng' }[key] || key.replaceAll('_', ' '));
