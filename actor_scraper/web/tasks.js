import { api, escapeHtml as e, toast, active, badge, dateTime } from './api.js';

const pageOffsets = new Map();

export function jobTable(jobs) {
  if (!jobs.length) return `<div class="empty"><span>◈</span><h3>还没有任务记录</h3><p>创建一个预览任务，先看看将获取哪些资料与图片。</p></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>任务 / 范围</th><th>模式</th><th>进度</th><th>状态</th><th>创建时间</th><th></th></tr></thead><tbody>${jobs.map(job => `<tr><td><a class="task-link" href="#task/${job.id}">#${job.id} ${job.request.all ? '全部演员' : e(job.request.names.slice(0, 2).join('、'))}${job.request.names.length > 2 ? ' 等' : ''}</a><small>${job.request.limit ? `最多 ${job.request.limit} 位` : '按选择范围处理'}</small></td><td>${job.request.mode === 'apply' ? '回写' : '预览'}</td><td><span class="tabular">${job.done} / ${job.total || '—'}</span></td><td>${badge(job.state)}</td><td class="muted">${dateTime(job.created_at)}</td><td><a class="row-arrow" href="#task/${job.id}">↗</a></td></tr>`).join('')}</tbody></table></div>`;
}

export async function renderDashboard(root, settings, current = () => true) {
  const data = await api('/jobs');
  if (!current()) return;
  const jobs = data.items;
  const running = jobs.find(active);
  const done = jobs.filter(job => job.state === 'completed').length;
  const recent = jobs[0];
  root.innerHTML = `<div class="page-heading"><div><p class="eyebrow">YOUR LIBRARY, WELL KEPT</p><h1>演员资料工作台</h1><p class="muted">补全资料、选好图像。每次运行自动保存，随时查看与重试。</p></div><a class="secondary" href="#settings">管理配置 ↗</a></div>
  <div class="stats-grid"><div class="stat"><span>任务状态</span><strong>${running ? '运行中' : '就绪'}</strong><small>${running ? `任务 #${running.id} · ${running.done}/${running.total || '—'}` : '可以创建新任务'}</small></div><div class="stat"><span>已完成任务</span><strong>${done}<em>次</em></strong><small>最近 ${jobs.length} 条记录</small></div><div class="stat"><span>最近缓存命中</span><strong>${recent?.summary?.cache_hits ?? '—'}<em>位</em></strong><small>命中时无需重新抓取档案</small></div><div class="stat accent"><span>默认图片来源</span><strong>${settings.avatar_source === 'auto' ? 'GRAPHIS' : e(settings.avatar_source.toUpperCase())}</strong><small>${settings.no_backdrop ? '只处理头像' : '主图 + 背景分别处理'}</small></div></div>
  <div class="dashboard-grid"><section class="panel new-task"><div class="section-title"><span class="step">↗</span><div><h2>创建刮削任务</h2><p>默认预览，核对来源后再选择回写。</p></div></div>
  <form id="job-form"><label>演员姓名<textarea name="names" rows="5" placeholder="每行一位演员，例如：&#10;三上悠亚&#10;桥本有菜"></textarea><small>支持简繁日文字形与已知艺名关联。</small></label><label class="check"><input name="all" type="checkbox"><span>处理媒体服务器全部演员<small>可设置数量上限，先运行少量样本。</small></span></label><div class="form-grid"><label>数量上限<input name="limit" type="number" min="1" placeholder="留空处理全部选择"></label><label>本次模式<select name="mode"><option value="preview">仅预览 · 获取资料与图片</option><option value="apply">执行回写 · 更新媒体服务器</option></select></label></div><div class="task-policy"><i></i><span>${settings.images_only ? '只补图片，保留现有文字资料' : '补全资料与图片'} · ${settings.replace_avatar || settings.replace_backdrop ? '按已保存的替换设置处理' : '仅补缺失图片'}</span></div><button class="primary wide" type="submit" ${running ? 'disabled' : ''}>${running ? '等待当前任务结束' : '开始任务'} <span>→</span></button></form></section>
  <section class="source-card"><p class="eyebrow">A MATCHED PAIR</p><h2>同一组写真，<br>完整的人物页面。</h2><div class="source-illustration"><div class="portrait-shape"><span>a.</span></div><div class="backdrop-shape"><span>GRAPHIS</span><i></i></div></div><div class="source-facts"><div><span>主图</span><strong>prof.jpg</strong></div><div><span>背景</span><strong>model.jpg</strong></div></div><p>优先 GRAPHIS 公开封面，两种图片独立补全。已有好图保留，缓存跨任务复用。</p><a href="#settings">调整来源与写真版本 <span>↗</span></a></section></div>
  <section class="panel recent"><div class="panel-heading"><h2>最近任务</h2><a href="#history">查看全部 ↗</a></div><div id="recent-jobs">${jobTable(jobs.slice(0, 5))}</div></section>`;
  const form = root.querySelector('#job-form');
  let submitting = false;
  form.elements.all.onchange = () => {
    form.elements.names.disabled = form.elements.all.checked;
    if (form.elements.all.checked && !form.elements.limit.value) form.elements.limit.value = 10;
  };
  form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    submitting = true;
    button.disabled = true;
    try {
      const job = await api('/jobs', { method: 'POST', body: JSON.stringify({ names: form.elements.all.checked ? [] : form.elements.names.value.split(/\n/).map(n => n.trim()).filter(Boolean), all: form.elements.all.checked, limit: form.elements.limit.value ? Number(form.elements.limit.value) : null, mode: form.elements.mode.value }) });
      location.hash = `task/${job.id}`;
    } catch (error) { toast(error.message, true); button.disabled = false; }
    finally { submitting = false; }
  };
  return async () => {
    const { items } = await api('/jobs');
    if (!current()) return;
    const running = items.find(active);
    const stats = root.querySelectorAll('.stat');
    stats[0].querySelector('strong').textContent = running ? '运行中' : '就绪';
    stats[0].querySelector('small').textContent = running ? `任务 #${running.id} · ${running.done}/${running.total || '—'}` : '可以创建新任务';
    stats[1].querySelector('strong').innerHTML = `${items.filter(job => job.state === 'completed').length}<em>次</em>`;
    stats[1].querySelector('small').textContent = `最近 ${items.length} 条记录`;
    stats[2].querySelector('strong').innerHTML = `${items[0]?.summary?.cache_hits ?? '—'}<em>位</em>`;
    const button = form.querySelector('button');
    button.disabled = Boolean(running) || submitting;
    button.innerHTML = `${running ? '等待当前任务结束' : '开始任务'} <span>→</span>`;
    root.querySelector('#recent-jobs').innerHTML = jobTable(items.slice(0, 5));
  };
}

export async function renderHistory(root, current = () => true) {
  const data = await api('/jobs');
  if (!current()) return;
  root.innerHTML = `<div class="page-heading"><div><p class="eyebrow">ACTIVITY</p><h1>任务记录</h1><p class="muted">范围、来源、结果与耗时都在这里。重试保留原任务范围和处理策略。</p></div><a class="primary" href="#dashboard">新建任务 +</a></div><section class="panel">${jobTable(data.items)}</section>`;
}

export async function renderTask(root, id, current = () => true) {
  const job = await api(`/jobs/${id}`);
  let offset = pageOffsets.get(id) || 0;
  const data = await api(`/jobs/${id}/results?offset=${offset}`);
  if (!current()) return;
  const statuses = job.summary?.statuses || {};
  const percentage = job.total ? Math.round(job.done / job.total * 100) : 0;
  root.innerHTML = `<div class="page-heading"><div><p class="eyebrow">TASK #${job.id}</p><h1>${job.request.mode === 'apply' ? '回写任务' : '预览任务'} ${badge(job.state)}</h1><p class="muted">${dateTime(job.created_at)} · ${job.request.all ? '全部演员' : e(job.request.names.join('、'))}</p></div><div class="action-group"><a class="secondary" href="/api/jobs/${id}/export">导出 JSONL ↓</a><button class="secondary" id="task-action">${active(job) ? '停止任务' : '重试未完成'}</button></div></div>
  <section class="panel progress-panel"><div class="progress-head"><strong>${active(job) ? '任务正在进行' : '本次处理结果'}</strong><span>${job.done} / ${job.total || '—'} 位</span></div><div class="progress-track"><div style="width:${percentage}%"></div></div><p id="task-message" class="muted">${e(job.message || '正在准备任务')}</p><div class="metrics"><span>已更新 <b>${statuses.updated || 0}</b></span><span>已预览 <b>${statuses.preview || 0}</b></span><span>未匹配 <b>${statuses.not_found || 0}</b></span><span>失败 / 部分失败 <b>${(statuses.error || 0) + (statuses.partial || 0)}</b></span><span>源站请求 <b>${job.summary?.external_requests ?? '—'}</b></span><span>耗时 <b>${job.summary?.seconds !== undefined ? `${job.summary.seconds}s` : '—'}</b></span></div></section>
  <section class="panel results"><div class="panel-heading"><h2>演员结果</h2><span class="muted">点击演员查看档案与两种图片</span></div><div id="results-table"></div><div class="pagination"><button class="secondary" id="previous-page" disabled>上一页</button><span id="page-number">第 1 页</span><button class="secondary" id="next-page" ${data.items.length < 50 ? 'disabled' : ''}>下一页</button></div></section>`;
  let rows = data.items;
  const renderRows = () => {
    root.querySelector('#results-table').innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>演员</th><th>来源 / 写真版本</th><th>状态</th><th>主图 / 背景</th><th>耗时</th><th></th></tr></thead><tbody>${rows.map(row => `<tr><td><button class="actor-link" data-result="${row.result_id}">${e(row.name)}</button>${row.profile?.birthday ? `<small>${e(row.profile.birthday)}</small>` : ''}</td><td>${e(row.artwork?.primary?.source || row.profile?.source || '—')}<small>${e(row.artwork?.primary?.model_id || '')}</small></td><td>${badge(row.status)}</td><td><span class="tiny-flag ${row.image_written ? 'good' : ''}">${row.image_written ? '已写主图' : row.primary_preview ? '主图已缓存' : '—'}</span> <span class="tiny-flag ${row.backdrop_written ? 'good' : ''}">${row.backdrop_written ? '已写背景' : row.backdrop_preview ? '背景已缓存' : '—'}</span></td><td class="tabular">${row.seconds}s${row.cached ? '<small>缓存命中</small>' : ''}</td><td><button class="row-arrow" data-result="${row.result_id}" aria-label="查看演员">↗</button></td></tr>`).join('')}</tbody></table></div>` : `<div class="empty compact"><h3>正在等待演员结果</h3><p>任务会逐位保存结果。</p></div>`;
    root.querySelectorAll('[data-result]').forEach(button => { button.onclick = () => showResult(rows.find(row => row.result_id === Number(button.dataset.result))); });
  };
  renderRows();
  const pagination = () => {
    root.querySelector('#previous-page').disabled = offset === 0;
    root.querySelector('#next-page').disabled = rows.length < 50;
    root.querySelector('#page-number').textContent = `第 ${offset / 50 + 1} 页`;
  };
  pagination();
  let loading = false;
  const loadPage = async delta => {
    if (loading) return;
    loading = true;
    const nextOffset = Math.max(0, offset + delta);
    try {
      const data = await api(`/jobs/${id}/results?offset=${nextOffset}`);
      if (!current()) return;
      offset = nextOffset;
      pageOffsets.set(id, offset);
      rows = data.items;
      renderRows();
      pagination();
    } finally { loading = false; }
  };
  root.querySelector('#previous-page').onclick = () => loadPage(-50).catch(error => toast(error.message, true));
  root.querySelector('#next-page').onclick = () => loadPage(50).catch(error => toast(error.message, true));
  root.querySelector('#task-action').onclick = async event => {
    event.currentTarget.disabled = true;
    try {
      if (active(job)) { await api(`/jobs/${id}/cancel`, { method: 'POST' }); toast('已请求停止'); }
      else { const retry = await api(`/jobs/${id}/retry`, { method: 'POST' }); location.hash = `task/${retry.id}`; }
    } catch (error) { toast(error.message, true); event.currentTarget.disabled = false; }
  };
  return job;
}

function showResult(row) {
  const dialog = document.querySelector('#result-dialog');
  document.querySelector('#result-title').textContent = row.name;
  const pictures = `<div class="picture-pair"><div class="portrait-preview">${row.primary_preview ? `<img src="${e(row.primary_preview)}" alt="${e(row.name)}的主图">` : '<span>主图未下载</span>'}<small>主图 · ${e(row.artwork?.primary?.source || '—')}</small></div><div class="backdrop-preview">${row.backdrop_preview ? `<img src="${e(row.backdrop_preview)}" alt="${e(row.name)}的背景">` : '<span>背景未下载</span>'}<small>背景 · ${e(row.artwork?.backdrop?.source || '—')}</small></div></div>`;
  const profile = row.profile || {};
  const details = [['生日', profile.birthday], ['身高', profile.height ? `${profile.height} cm` : ''], ['三围', profile.measurements], ['罩杯', profile.cup], ['出道日期', profile.debut_date], ['别名', profile.aliases?.join('、')]].filter(([,value]) => value);
  document.querySelector('#result-content').innerHTML = `<p class="muted">以下为来源档案与所选图片；回写状态以任务结果为准。</p>${pictures}<div class="detail-tags">${details.map(([label, value]) => `<span>${label} <b>${e(value)}</b></span>`).join('')}</div>${row.error ? `<p class="error-box">${e(row.error)}</p>` : ''}${row.artwork?.warnings?.length ? `<p class="note-box">${e(row.artwork.warnings.join('；'))}</p>` : ''}<details><summary>来源原始资料与拟更新字段</summary><pre>${e(JSON.stringify({ raw_fields: profile.raw_fields || {}, patch: row.patch || {}, artwork: row.artwork }, null, 2))}</pre></details>`;
  dialog.showModal();
}
