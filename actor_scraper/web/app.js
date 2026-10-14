import { api, toast, active } from './api.js';
import { renderSettings } from './settings.js';
import { renderDashboard, renderHistory, renderTask } from './tasks.js';

let settings;
let pollTimer;
let navigating = 0;
const content = document.querySelector('#content');

function showLogin() {
  navigating++;
  clearInterval(pollTimer);
  document.querySelector('#result-dialog').close();
  document.querySelector('#app').hidden = true;
  document.querySelector('#login').hidden = false;
}

function connection(saved) {
  settings = saved;
  const configured = Boolean(saved.server && saved.api_key_configured);
  document.querySelector('#connection-state').textContent = configured ? 'FYMS 已配置' : '尚未配置 FYMS';
  document.querySelector('.connection').classList.toggle('configured', configured);
}

async function route() {
  clearInterval(pollTimer);
  const page = location.hash.slice(1) || 'dashboard';
  const ticket = ++navigating;
  const current = () => ticket === navigating;
  const nav = page.startsWith('task/') ? 'history' : page;
  document.querySelectorAll('[data-nav]').forEach(link => link.classList.toggle('active', link.dataset.nav === nav));
  document.querySelector('#breadcrumb').textContent = { dashboard: '工作台', history: '任务记录', settings: '连接与配置' }[nav] || '工作台';
  content.innerHTML = '<div class="loading"><span></span>正在加载…</div>';
  try {
    if (page === 'settings') await renderSettings(content, connection, current);
    else if (page === 'history') await renderHistory(content, current);
    else if (/^task\/\d+$/.test(page)) {
      const id = page.split('/')[1];
      let job = await renderTask(content, id, current);
      let busy = false;
      if (current() && job && active(job)) pollTimer = setInterval(async () => {
        if (busy || ticket !== navigating) return;
        busy = true;
        try {
          const fresh = await api(`/jobs/${id}`);
          if (ticket !== navigating) return;
          if (fresh.done !== job.done || fresh.state !== job.state || fresh.message !== job.message) job = await renderTask(content, id, current);
          if (current() && job && !active(job)) clearInterval(pollTimer);
        } catch (error) { toast(error.message, true); }
        finally { busy = false; }
      }, 2000);
    } else {
      const refresh = await renderDashboard(content, settings, current);
      let busy = false;
      if (current() && refresh) pollTimer = setInterval(async () => {
        if (busy || !current()) return;
        busy = true;
        try { await refresh(); } catch (error) { if (current()) toast(error.message, true); }
        finally { busy = false; }
      }, 3000);
    }
  } catch (error) { toast(error.message, true); }
}

async function enter() {
  connection(await api('/settings'));
  document.querySelector('#login').hidden = true;
  document.querySelector('#app').hidden = false;
  await route();
}

document.querySelector('#login-form').onsubmit = async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  button.disabled = true;
  try {
    await api('/login', { method: 'POST', body: JSON.stringify({ password: document.querySelector('#password').value }) });
    document.querySelector('#password').value = '';
    document.querySelector('#login-error').textContent = '';
    await enter();
  } catch (error) { document.querySelector('#login-error').textContent = error.message; }
  finally { button.disabled = false; }
};
document.querySelector('#logout').onclick = async () => { await api('/logout', { method: 'POST' }); showLogin(); };
document.querySelector('#close-result').onclick = () => document.querySelector('#result-dialog').close();
window.addEventListener('session-expired', showLogin);
window.addEventListener('hashchange', () => { if (!document.querySelector('#app').hidden) route(); });
try { await api('/session'); await enter(); } catch { showLogin(); }
