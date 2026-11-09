import test from 'node:test';
import assert from 'node:assert/strict';
import { safeUrl, savedCredential, initials, duration, sourceName } from '../../app/web/static/logic.mjs';

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
