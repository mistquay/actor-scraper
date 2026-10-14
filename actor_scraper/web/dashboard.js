import { api, escapeHtml as e, toast, active } from './api.js';
import { jobTable } from './tables.js';

function taskPolicy(settings) {
  const text = settings.images_only ? '保留文字资料' : settings.overwrite ? '更新已有文字字段' : '文字资料仅补缺';
  if (settings.avatar_source === 'none') return `${text} · 不处理图片`;
  return `${text} · 头像${settings.replace_avatar ? '可替换' : '仅补缺'} · ${settings.no_backdrop ? '跳过背景' : `背景${settings.replace_backdrop ? '可替换' : '仅补缺'}`}`;
}

export async function renderDashboard(root, settings, current = () => true) {
  const data = await api('/jobs?limit=5');
  if (!current()) return;
  let running = data.items.find(active);
  root.innerHTML = `<div class="page-heading"><div><h1>工作台</h1><p class="muted">创建任务，补全演员资料与图片。</p></div><a class="secondary" href="#settings">连接与配置</a></div>
    <div class="dashboard-grid"><section class="panel new-task"><div class="panel-heading"><h2>新建任务</h2><span class="mode-label">默认预览</span></div>
    <form id="job-form"><label>演员姓名<textarea name="names" rows="4" placeholder="每行一位演员，例如：&#10;三上悠亚&#10;桥本有菜"></textarea></label><label class="check"><input name="all" type="checkbox"><span>处理服务器全部演员</span></label><div class="form-grid"><label>数量上限<input name="limit" type="number" min="1" max="1000000" placeholder="留空不限制"></label><label>任务模式<select name="mode"><option value="preview">预览资料与图片</option><option value="apply">回写媒体服务器</option></select></label></div><p id="apply-note" class="note-box" hidden>将按当前配置更新服务器中的演员资料与图片，请确认处理范围。</p><div class="task-policy">${taskPolicy(settings)}</div><button class="primary wide" type="submit">开始预览任务</button></form></section>
    <section class="panel task-overview"><div class="panel-heading"><h2>任务概览</h2><span class="status-dot" id="dashboard-state"></span></div><div id="current-task"></div><dl class="overview-details"><div><dt>任务记录</dt><dd id="job-count">${data.total} 条</dd></div><div><dt>图片来源</dt><dd>${e({ auto: '自动 · GRAPHIS 优先', graphis: 'GRAPHIS', gfriends: 'Gfriends', profile: 'AV-LEAGUE', none: '不处理图片' }[settings.avatar_source])}</dd></div><div><dt>图片策略</dt><dd>${settings.replace_avatar ? '替换头像' : '头像补缺'} · ${settings.no_backdrop ? '跳过背景' : settings.replace_backdrop ? '替换背景' : '背景补缺'}</dd></div><div><dt>服务器连接</dt><dd><a href="#settings">${settings.server && settings.api_key_configured ? '已配置 · 查看设置' : '尚未配置 · 前往设置 →'}</a></dd></div></dl></section></div>
    <section class="panel recent"><div class="panel-heading"><h2>最近任务</h2><a href="#history">查看全部 →</a></div><div id="recent-jobs">${jobTable(data.items)}</div></section>`;
  const form = root.querySelector('#job-form');
  let submitting = false;
  const updateState = () => {
    const button = form.querySelector('button');
    button.disabled = Boolean(running) || submitting;
    button.textContent = running ? '等待当前任务结束' : form.elements.mode.value === 'apply' ? '开始回写任务' : '开始预览任务';
    root.querySelector('#apply-note').hidden = form.elements.mode.value !== 'apply';
    root.querySelector('#dashboard-state').textContent = running ? '进行中' : '空闲';
    root.querySelector('#dashboard-state').classList.toggle('running', Boolean(running));
    root.querySelector('#current-task').innerHTML = running ? `<a class="current-task" href="#task/${running.id}"><strong>任务 #${running.id}</strong><span>已处理 ${running.done} / ${running.total || '—'} 位 <b>查看进度 →</b></span></a>` : '<div class="current-task"><strong>可以创建新任务</strong><span>先预览并核对结果，再选择回写。</span></div>';
  };
  updateState();
  form.elements.mode.onchange = updateState;
  form.elements.all.onchange = () => {
    form.elements.names.disabled = form.elements.all.checked;
    if (form.elements.all.checked && !form.elements.limit.value) form.elements.limit.value = 10;
  };
  form.onsubmit = async event => {
    event.preventDefault();
    if (submitting || running) return;
    const names = form.elements.names.value.split(/\n/).map(name => name.trim()).filter(Boolean);
    if (!form.elements.all.checked && !names.length) { toast('请输入演员姓名或选择全部演员', true); form.elements.names.focus(); return; }
    submitting = true;
    updateState();
    try {
      const job = await api('/jobs', { method: 'POST', body: JSON.stringify({ names: form.elements.all.checked ? [] : names, all: form.elements.all.checked, limit: form.elements.limit.value ? Number(form.elements.limit.value) : null, mode: form.elements.mode.value }) });
      location.hash = `task/${job.id}`;
    } catch (error) { toast(error.message, true); }
    finally { submitting = false; if (current()) updateState(); }
  };
  return async () => {
    const data = await api('/jobs?limit=5');
    if (!current()) return;
    running = data.items.find(active);
    updateState();
    root.querySelector('#job-count').textContent = `${data.total} 条`;
    root.querySelector('#recent-jobs').innerHTML = jobTable(data.items);
  };
}
