import { api, escapeHtml as e, toast, active, badge, dateTime, states } from './api.js';
import { emptyState, pagination } from './tables.js';

const resultFilters = new Map();

export async function renderTask(root, id, current = () => true) {
  let job = await api(`/jobs/${id}`);
  const filters = resultFilters.get(id) || { q: '', status: '', offset: 0 };
  resultFilters.set(id, filters);
  const resultQuery = () => new URLSearchParams({ offset: filters.offset, q: filters.q, status: filters.status });
  const data = await api(`/jobs/${id}/results?${resultQuery()}`);
  if (!current()) return;
  root.innerHTML = `<div class="page-heading"><div><a class="back-link" href="#history">← 任务记录</a><h1>任务 #${job.id} <span id="task-badge"></span></h1><p class="muted task-scope">${job.request.mode === 'apply' ? '回写' : '预览'} · ${dateTime(job.created_at)} · ${job.request.all ? '全部演员' : e(job.request.names.join('、'))}</p></div><div class="action-group"><a class="secondary" href="/api/jobs/${id}/export">导出 JSONL ↓</a><button class="secondary" id="task-action"></button></div></div>
  <section class="panel progress-panel" id="task-progress"></section>
  <section class="panel results"><div class="panel-heading"><h2>演员结果</h2><span class="muted">点击姓名查看资料与图片</span></div><form id="result-filters" class="filter-toolbar result-filters" role="search" aria-label="筛选演员结果"><label class="search-field">演员姓名<input name="q" type="search" maxlength="200" placeholder="搜索本任务的演员" autocomplete="off"></label><label>处理状态<select name="status"><option value="">全部状态</option><option value="issues">失败 / 部分完成 / 未匹配</option>${['preview', 'updated', 'unchanged', 'not_found', 'partial', 'error', 'cancelled'].map(status => `<option value="${status}">${states[status]}</option>`).join('')}</select></label><div class="filter-actions"><button class="primary" type="submit">筛选</button><button class="secondary" id="clear-results" type="button">清空</button></div></form><p class="list-error" id="results-error" role="alert" hidden></p><div id="results-table"></div><div id="results-pagination" class="pagination"></div></section>`;
  const updateProgress = () => {
    const statuses = job.summary?.statuses || {};
    const percentage = job.total ? Math.min(100, Math.round(job.done / job.total * 100)) : 0;
    root.querySelector('#task-badge').innerHTML = badge(job.state);
    root.querySelector('#task-progress').innerHTML = `<div class="progress-head"><strong>${active(job) ? '任务正在进行' : '本次处理结果'}</strong><span>${job.done} / ${job.total || '—'} 位</span></div><div class="progress-track"><div style="width:${percentage}%"></div></div><p class="muted">${e(job.message || (active(job) ? '正在准备任务' : '任务已结束'))}</p><div class="metrics"><span>已更新 <b>${statuses.updated || 0}</b></span><span>已预览 <b>${statuses.preview || 0}</b></span><span>未匹配 <b>${statuses.not_found || 0}</b></span><span>失败 / 部分完成 <b>${(statuses.error || 0) + (statuses.partial || 0)}</b></span><span>源站请求 <b>${job.summary?.external_requests ?? '—'}</b></span><span>耗时 <b>${job.summary?.seconds !== undefined ? `${job.summary.seconds}s` : '—'}</b></span></div>`;
    const button = root.querySelector('#task-action');
    button.textContent = job.state === 'cancelling' ? '正在停止' : active(job) ? '停止任务' : '重试未完成';
    button.disabled = job.state === 'cancelling';
  };
  updateProgress();
  let rows = data.items;
  const renderRows = () => {
    root.querySelector('#results-table').innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>演员</th><th>来源 / 写真版本</th><th>状态</th><th>主图 / 背景</th><th>耗时</th><th><span class="sr-only">操作</span></th></tr></thead><tbody>${rows.map(row => `<tr><td><button class="actor-link" data-result="${row.result_id}">${e(row.name)}</button></td><td>${e(row.artwork?.primary?.source || row.profile?.source || '—')}<small>${e(row.artwork?.primary?.model_id || '')}</small></td><td>${badge(row.status)}</td><td><span class="tiny-flag ${row.image_written ? 'good' : ''}">${row.image_written ? '已写主图' : row.primary_preview ? '主图已缓存' : '—'}</span> <span class="tiny-flag ${row.backdrop_written ? 'good' : ''}">${row.backdrop_written ? '已写背景' : row.backdrop_preview ? '背景已缓存' : '—'}</span></td><td class="tabular">${row.seconds}s${row.cached ? '<span class="cache-label">缓存</span>' : ''}</td><td><button class="row-arrow" data-result="${row.result_id}" aria-label="查看演员">→</button></td></tr>`).join('')}</tbody></table></div>` : emptyState(filters.q || filters.status ? '没有符合条件的演员结果' : active(job) ? '正在等待演员结果' : '本任务没有演员结果', filters.q || filters.status ? '调整条件或清空筛选。' : '');
    root.querySelectorAll('[data-result]').forEach(button => { button.onclick = () => showResult(rows.find(row => row.result_id === Number(button.dataset.result))); });
  };
  renderRows();
  let total = data.total;
  const renderPagination = () => {
    root.querySelector('#results-pagination').innerHTML = pagination(filters.offset, 50, total);
    root.querySelectorAll('[data-page]').forEach(button => {
      button.onclick = () => { filters.offset += button.dataset.page === 'next' ? 50 : -50; loadPage().catch(error => toast(error.message, true)); };
    });
  };
  renderPagination();
  const form = root.querySelector('#result-filters');
  form.elements.q.value = filters.q;
  form.elements.status.value = filters.status;
  let request = 0;
  let loading = false;
  const loadPage = async () => {
    const ticket = ++request;
    loading = true;
    root.querySelectorAll('[data-page]').forEach(button => { button.disabled = true; });
    root.querySelector('#results-table').setAttribute('aria-busy', 'true');
    try {
      const data = await api(`/jobs/${id}/results?${resultQuery()}`);
      if (!current() || ticket !== request) return;
      if (filters.offset && filters.offset >= data.total) {
        filters.offset = Math.max(0, Math.ceil(data.total / 50) - 1) * 50;
        return await loadPage();
      }
      rows = data.items;
      total = data.total;
      renderRows();
      renderPagination();
      root.querySelector('#results-error').hidden = true;
    } catch (error) {
      if (current() && ticket === request) {
        const message = root.querySelector('#results-error');
        message.textContent = `加载失败：${error.message}。请重新筛选重试。`;
        message.hidden = false;
      }
    } finally {
      if (current() && ticket === request) {
        loading = false;
        root.querySelector('#results-table').setAttribute('aria-busy', 'false');
      }
    }
  };
  let searchTimer;
  form.onsubmit = event => {
    event.preventDefault();
    clearTimeout(searchTimer);
    filters.q = form.elements.q.value.trim();
    filters.status = form.elements.status.value;
    filters.offset = 0;
    loadPage().catch(error => toast(error.message, true));
  };
  form.elements.q.oninput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { if (current()) form.requestSubmit(); }, 300);
  };
  form.elements.status.onchange = () => form.requestSubmit();
  root.querySelector('#clear-results').onclick = () => { form.reset(); form.requestSubmit(); };
  root.querySelector('#task-action').onclick = async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      if (active(job)) { await api(`/jobs/${id}/cancel`, { method: 'POST' }); job = await api(`/jobs/${id}`); if (current()) updateProgress(); toast('已请求停止'); }
      else { const retry = await api(`/jobs/${id}/retry`, { method: 'POST' }); location.hash = `task/${retry.id}`; }
    } catch (error) { toast(error.message, true); if (current()) button.disabled = false; }
  };
  return async () => {
    if (!active(job)) return;
    const fresh = await api(`/jobs/${id}`);
    if (!current()) return;
    job = fresh;
    updateProgress();
    if (!loading) await loadPage();
  };
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
