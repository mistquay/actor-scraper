export async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    if (response.status === 401 && path !== '/login') window.dispatchEvent(new Event('session-expired'));
    throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`);
  }
  return data;
}

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}

export function toast(message, error = false) {
  const el = document.querySelector('#toast');
  el.textContent = message;
  el.classList.toggle('danger', error);
  el.hidden = false;
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

export const states = { queued: '准备中', running: '运行中', cancelling: '正在停止', cancelled: '已停止', completed: '已完成', failed: '有失败', interrupted: '已中断', preview: '已预览', updated: '已更新', unchanged: '无需更新', not_found: '未匹配', partial: '部分完成', error: '失败' };
export const active = job => ['queued', 'running', 'cancelling'].includes(job.state);
export const dateTime = value => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '—';
export const badge = state => `<span class="badge ${['completed', 'updated', 'preview'].includes(state) ? 'good' : ['error', 'failed', 'partial'].includes(state) ? 'bad' : 'neutral'}">${states[state] || escapeHtml(state)}</span>`;
