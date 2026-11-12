'use strict';
const $ = id => document.getElementById(id);
let token = '';
let epoch = 0;
const offsets = { users: 0, licenses: 0, devices: 0 };
const records = { users: [], licenses: [], devices: [] };
const limit = 50;

function message(text, error = false) {
  $('message').textContent = text;
  $('message').className = error ? 'error' : 'success';
}
function logout() {
  epoch += 1; token = '';
  $('adminToken').value = ''; $('newKey').value = ''; $('newKeyPanel').hidden = true;
  $('workspace').hidden = true; $('logout').hidden = true; $('loginPanel').hidden = false;
  $('userForm').reset(); $('userId').value = ''; resetLicense();
  $('summary').replaceChildren();
  for (const kind of Object.keys(records)) {
    records[kind] = []; offsets[kind] = 0; $(kind + 'Rows').replaceChildren();
  }
}
async function api(path, method = 'GET', body) {
  const current = epoch;
  const response = await fetch('/api/v1/admin/' + path, {
    method, cache: 'no-store', credentials: 'omit', redirect: 'error',
    headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined
  });
  if (current !== epoch) throw new Error('Phiên quản trị đã đóng.');
  if (response.status === 401) { logout(); throw new Error('Admin token không hợp lệ.'); }
  let data = null;
  try { data = await response.json(); } catch { /* Keep server internals out of the page. */ }
  if (current !== epoch) throw new Error('Phiên quản trị đã đóng.');
  if (!response.ok) throw new Error(data?.error || `Yêu cầu thất bại (HTTP ${response.status}).`);
  return data;
}
async function run(action) {
  message('');
  try { await action(); } catch (error) { message(error.message || 'Không thể kết nối máy chủ.', true); }
}
function cell(row, value, detail) {
  const td = document.createElement('td'); td.textContent = value ?? '—';
  if (detail) { const small = document.createElement('small'); small.textContent = detail; td.append(small); }
  row.append(td); return td;
}
function badge(row, value) {
  const td = cell(row, ''); const span = document.createElement('span'); span.textContent = value;
  const style = ['active', 'trial', 'revoked', 'disabled', 'banned', 'expired'].includes(value) ? value : '';
  span.className = 'badge ' + style; td.append(span);
}
function button(container, label, action, danger = false) {
  const node = document.createElement('button'); node.type = 'button'; node.textContent = label;
  node.className = danger ? 'danger' : 'secondary';
  node.addEventListener('click', () => run(async () => {
    node.disabled = true; try { await action(); } finally { node.disabled = false; }
  })); container.append(node);
}
const date = value => value ? new Date(value).toLocaleString() : 'Không giới hạn';
function tab(kind) {
  for (const name of Object.keys(records)) $(name + 'Panel').hidden = name !== kind;
  for (const node of document.querySelectorAll('[data-tab]')) node.classList.toggle('selected', node.dataset.tab === kind);
}
function editUser(user) {
  $('userId').value = user.id; $('userName').value = user.display_name; $('userEmail').value = user.email;
  $('userEnabled').checked = user.is_enabled; $('saveUser').textContent = 'Lưu thay đổi';
  $('cancelEdit').hidden = false; tab('users'); $('userName').focus();
}
function resetUser() {
  $('userForm').reset(); $('userId').value = ''; $('saveUser').textContent = 'Tạo người dùng'; $('cancelEdit').hidden = true;
}
function render(kind) {
  const tbody = $(kind + 'Rows'); tbody.replaceChildren();
  for (const item of records[kind]) {
    const row = document.createElement('tr');
    if (kind === 'users') {
      cell(row, item.display_name, item.id); cell(row, item.email); badge(row, item.is_enabled ? 'active' : 'disabled');
      cell(row, date(item.created_at)); const actions = cell(row, '');
      button(actions, 'Sửa', () => editUser(item));
      if (item.is_enabled) button(actions, 'Tạo license', () => { $('licenseUserId').value = item.id; tab('licenses'); });
      button(actions, item.is_enabled ? 'Khóa' : 'Mở khóa', async () => {
        if (!window.confirm(`${item.is_enabled ? 'Khóa' : 'Mở khóa'} tài khoản ${item.email}? License được kiểm tra lại khi thiết bị kết nối online.`)) return;
        await api('users/' + encodeURIComponent(item.id), 'PATCH', {
          display_name: item.display_name, email: item.email, is_enabled: !item.is_enabled
        }); await refresh(); message('Đã cập nhật trạng thái tài khoản.');
      }, item.is_enabled);
    } else if (kind === 'licenses') {
      cell(row, item.id, item.user_id ? 'User: ' + item.user_id : 'Chưa gắn user');
      cell(row, item.tier); cell(row, `${item.activated_devices}/${item.max_devices}`);
      cell(row, date(item.expires_at)); badge(row, item.status); const actions = cell(row, '');
      if (item.status !== 'revoked') button(actions, 'Thu hồi', async () => {
        if (!window.confirm(`Thu hồi license ${item.id}? Key này không thể kích hoạt tiếp. Thiết bị offline vẫn theo entitlement đã cấp.`)) return;
        await api('licenses/' + encodeURIComponent(item.id) + '/revoke', 'POST'); await refresh(); message('Đã thu hồi license.');
      }, true);
    } else {
      cell(row, item.device_hash, item.id); cell(row, item.license_id); badge(row, item.last_known_status);
      cell(row, date(item.trial_expires_at)); cell(row, date(item.last_seen_at));
    }
    tbody.append(row);
  }
  if (!records[kind].length) { const row = document.createElement('tr'); cell(row, 'Chưa có dữ liệu trên trang này.'); tbody.append(row); }
  $(kind + 'Page').textContent = `Trang ${offsets[kind] / limit + 1} · ${records[kind].length} bản ghi`;
  document.querySelector(`[data-page="${kind}:-1"]`).disabled = offsets[kind] === 0;
  document.querySelector(`[data-page="${kind}:1"]`).disabled = records[kind].length < limit || offsets[kind] + limit > 100000;
}
async function load(kind) {
  const data = await api(`${kind}?offset=${offsets[kind]}&limit=${limit}`); records[kind] = data.items; render(kind);
}
async function refresh() {
  const data = await api('summary'); $('summary').replaceChildren();
  for (const [label, key] of [['Người dùng', 'users'], ['Tài khoản được phép', 'enabled_users'], ['License', 'licenses'], ['Thiết bị', 'devices']]) {
    const card = document.createElement('div'); card.className = 'stat';
    const title = document.createElement('span'); title.textContent = label;
    const count = document.createElement('b'); count.textContent = data[key]; card.append(title, count); $('summary').append(card);
  }
  await Promise.all(Object.keys(records).map(load));
}
$('loginForm').addEventListener('submit', event => {
  event.preventDefault();
  run(async () => {
    token = $('adminToken').value.trim(); $('adminToken').value = ''; epoch += 1;
    try {
      await refresh(); $('workspace').hidden = false; $('logout').hidden = false; $('loginPanel').hidden = true;
      message('Đã kết nối quản trị.');
    } catch (error) { logout(); throw error; }
  });
});
$('logout').addEventListener('click', () => { logout(); resetUser(); message('Đã đăng xuất.'); });
$('refresh').addEventListener('click', () => run(refresh));
for (const node of document.querySelectorAll('[data-tab]')) node.addEventListener('click', () => tab(node.dataset.tab));
for (const node of document.querySelectorAll('[data-page]')) node.addEventListener('click', () => run(async () => {
  const [kind, direction] = node.dataset.page.split(':'); const previous = offsets[kind];
  offsets[kind] = Math.max(0, previous + Number(direction) * limit);
  try { await load(kind); } catch (error) { offsets[kind] = previous; throw error; }
}));
$('cancelEdit').addEventListener('click', resetUser);
$('userForm').addEventListener('submit', event => {
  event.preventDefault(); const submit = event.submitter;
  run(async () => {
    submit.disabled = true;
    try {
      const id = $('userId').value;
      await api(id ? 'users/' + encodeURIComponent(id) : 'users', id ? 'PATCH' : 'POST', {
        display_name: $('userName').value.trim(), email: $('userEmail').value.trim(), is_enabled: $('userEnabled').checked
      }); resetUser(); offsets.users = 0; await refresh(); message('Đã lưu người dùng.');
    } finally { submit.disabled = false; }
  });
});
function expiryMode() { $('expiresAt').disabled = $('tier').value === 'lifetime'; $('expiresAt').required = !$('expiresAt').disabled; }
$('tier').addEventListener('change', expiryMode);
function resetLicense() {
  $('licenseForm').reset(); expiryMode();
  const expiry = new Date(Date.now() + 30 * 86400000);
  expiry.setMinutes(expiry.getMinutes() - expiry.getTimezoneOffset());
  $('expiresAt').value = expiry.toISOString().slice(0, 16);
}
resetLicense();
$('licenseForm').addEventListener('submit', event => {
  event.preventDefault(); const submit = event.submitter;
  run(async () => {
    submit.disabled = true;
    try {
      const expiry = $('tier').value === 'lifetime' ? null : new Date($('expiresAt').value).toISOString();
      const result = await api('licenses', 'POST', { tier: $('tier').value, max_devices: Number($('maxDevices').value),
        expires_at: expiry, user_id: $('licenseUserId').value.trim() || null });
      $('newKey').value = result.license_key; $('newKeyPanel').hidden = false; offsets.licenses = 0;
      await refresh(); message('License đã tạo. Hãy lưu key hiển thị một lần.');
    } finally { submit.disabled = false; }
  });
});
$('hideKey').addEventListener('click', () => { $('newKey').value = ''; $('newKeyPanel').hidden = true; });
$('copyKey').addEventListener('click', () => run(async () => {
  if (!navigator.clipboard) throw new Error('Clipboard yêu cầu HTTPS. Chọn key và sao chép thủ công.');
  await navigator.clipboard.writeText($('newKey').value); message('Đã sao chép key.');
}));
window.addEventListener('pagehide', logout);
