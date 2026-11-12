import test from 'node:test';
import assert from 'node:assert/strict';
import { safeUrl, savedCredential, initials, duration, sourceName, descriptionText, descriptionSegments } from '../../app/web/static/logic.mjs';

test('source links allow only credential-free HTTP(S), including hostile schemes', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,<script>x</script>', 'file:///etc/passwd', '//evil.test', 'https://user:secret@example.com', 'not a url']) assert.equal(safeUrl(url), null);
  assert.equal(safeUrl('https://example.com/jobs?q=python'), 'https://example.com/jobs?q=python');
});
test('CV credentials are bounded, expiring and limited to the three required fields', () => {
  const value = { id: 'ab123456-1234-1234-1234-abcdef123456', token: 'A'.repeat(43), expires_at: '2030-01-01T12:00:00Z', profile: 'must not persist' };
  const now = Date.parse('2030-01-01T11:59:00Z');
  assert.deepEqual(Object.keys(savedCredential(JSON.stringify(value), now)), ['id', 'token', 'expires_at']);
  assert.equal(savedCredential(JSON.stringify(value), Date.parse('2030-01-01T12:00:00Z')), null);
  for (const update of [{ token: 'bad token' }, { id: '../secret' }, { expires_at: 'not-a-date' }]) assert.equal(savedCredential(JSON.stringify({ ...value, ...update }), now), null);
  assert.equal(savedCredential('{invalid}', now), null);
});
test('labels preserve actual source names and do not imply a user account', () => {
  assert.equal(initials('Ericsson AB'), 'EA');
  assert.equal(sourceName('jobtech'), 'JobTech');
  assert.equal(sourceName('unknown-source'), 'unknown-source');
  assert.equal(duration(3600), '1 giờ');
  assert.equal(duration(90), '2 phút');
});

test('description line breaks render as newlines without interpreting other markup', () => {
  assert.equal(descriptionText('A<br>B<BR />C<br/>D'), 'A\nB\nC\nD');
  assert.equal(descriptionText('<script>alert(1)</script><br>React'), '<script>alert(1)</script>\nReact');
  assert.equal(descriptionText('Giữ nguyên <bravo> và C++'), 'Giữ nguyên <bravo> và C++');
});

test('description highlights section labels without changing source text or treating bullets as headings', () => {
  const input = 'Yêu cầu công việc:\n• Biết Python\nResponsibilities: Design APIs.\nVi söker dig som har erfarenhet.\nKvalifikationer\nKrav: <script>x</script>';
  const parts = descriptionSegments(input);
  assert.equal(parts.map(part => part.text).join(''), input);
  assert.deepEqual(parts.filter(part => part.heading).map(part => part.text), ['Yêu cầu công việc:', 'Responsibilities:', 'Kvalifikationer', 'Krav:']);
  assert.equal(descriptionSegments('Yêu cầu Python và SQL').some(part => part.heading), false);
  assert.deepEqual(descriptionSegments('Benefits<br>Health care').filter(part => part.heading).map(part => part.text), ['Benefits']);
});

test('recognizes the actual Arm job section titles', () => {
  const input = 'What you might be working on as a Graduate?\nBody\nWe are looking for individuals who:\nQualifications\nQualities that will help your application stand out:\nAdditional Information:\nIn Return:';
  assert.deepEqual(descriptionSegments(input).filter(part => part.heading).map(part => part.text), [
    'What you might be working on as a Graduate?', 'We are looking for individuals who:',
    'Qualifications', 'Qualities that will help your application stand out:', 'Additional Information:', 'In Return:'
  ]);
});

test('recognizes job headings in the IKEA posting and common alternatives without bolding prose', () => {
  const text = 'About this work area\nWe build a platform.\nYour responsibilities\nWho you are\n- You have 5+ years of experience.\nWhat you will bring:\nOm jobbet\nQuyền lợi đối với ứng viên';
  const parts = descriptionSegments(text);
  assert.equal(parts.map(part => part.text).join(''), text);
  assert.deepEqual(parts.filter(part => part.heading).map(part => part.text), [
    'About this work area', 'Your responsibilities', 'Who you are', 'What you will bring:', 'Om jobbet', 'Quyền lợi đối với ứng viên'
  ]);
  assert.equal(descriptionSegments('Who you are matters to our team.').some(part => part.heading), false);
  assert.equal(descriptionSegments('- Who you are: experienced engineer').some(part => part.heading), false);
});

test('question completion matches prefixes without requiring Vietnamese accents', async () => {
  const { questionCompletion } = await import('../../app/web/static/logic.mjs');
  const options = ['Có bao nhiêu tin nhắc đến Python?', 'Tìm việc Python'];
  assert.equal(questionCompletion('co bao', options), options[0]);
  assert.equal(questionCompletion('TÌM', options), options[1]);
  assert.equal(questionCompletion('', options), null);
  assert.equal(questionCompletion(options[0], options), null);
  assert.equal(questionCompletion('không liên quan', options), null);
  assert.equal(questionCompletion('Có\nbao', options), null);
});
