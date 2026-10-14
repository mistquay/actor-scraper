import { escapeHtml as e, badge, dateTime } from './api.js';

export function emptyState(title, message = '') {
  return `<div class="empty compact"><h3>${e(title)}</h3>${message ? `<p>${e(message)}</p>` : ''}</div>`;
}

export function jobTable(jobs, filtered = false) {
  if (!jobs.length) return emptyState(filtered ? '没有符合条件的任务' : '还没有任务记录', filtered ? '调整筛选条件或清空筛选后重试。' : '在工作台创建一个预览任务。');
  return `<div class="table-wrap"><table class="job-table"><thead><tr><th>编号</th><th>演员范围</th><th>模式</th><th>进度</th><th>状态</th><th>结果</th><th>耗时</th><th>创建时间</th><th><span class="sr-only">操作</span></th></tr></thead><tbody>${jobs.map(job => {
    const names = job.request.names || [];
    const scope = job.request.all ? '全部演员' : names.slice(0, 2).join('、');
    const suffix = names.length > 2 ? ` 等 ${names.length} 位` : '';
    const counts = job.summary?.statuses || {};
    const issues = (counts.error || 0) + (counts.partial || 0);
    const seconds = job.summary?.seconds ?? (job.finished_at ? job.finished_at - job.created_at : null);
    const progress = job.total ? Math.min(100, job.done / job.total * 100) : 0;
    return `<tr><td><a class="task-link tabular" href="#task/${job.id}">#${job.id}</a></td><td class="scope-cell"><a class="task-link scope-name" href="#task/${job.id}" title="${e(job.request.all ? '全部演员' : names.join('、'))}">${e(scope)}${suffix}</a>${job.request.limit ? `<span class="scope-limit">上限 ${job.request.limit}</span>` : ''}</td><td><span class="mode-label ${job.request.mode === 'apply' ? 'apply' : ''}">${job.request.mode === 'apply' ? '回写' : '预览'}</span></td><td><div class="table-progress"><span class="tabular">${job.done} / ${job.total || '—'}</span><span class="mini-track" aria-hidden="true"><i style="width:${progress}%"></i></span></div></td><td>${badge(job.state)}</td><td><span class="result-counts" title="已更新 ${counts.updated || 0} · 已预览 ${counts.preview || 0} · 失败或部分失败 ${issues} · 未匹配 ${counts.not_found || 0}">${issues ? `<span class="issue-count">失败 ${issues}</span>` : ''}${counts.not_found ? `<span>未匹配 ${counts.not_found}</span>` : ''}${!issues && !counts.not_found ? `${(counts.updated || 0) + (counts.preview || 0) ? `成功 ${(counts.updated || 0) + (counts.preview || 0)}` : '—'}` : ''}</span></td><td class="tabular">${seconds === null ? '—' : `${Math.max(0, seconds).toFixed(1)}s`}</td><td class="date-cell tabular">${dateTime(job.created_at)}</td><td><a class="row-arrow" href="#task/${job.id}" aria-label="查看任务 ${job.id}">→</a></td></tr>`;
  }).join('')}</tbody></table></div>`;
}

export function pagination(offset, limit, total) {
  const pages = Math.max(1, Math.ceil(total / limit));
  return `<span class="pagination-summary">共 ${total} 条${total ? ` · 显示 ${offset + 1}–${Math.min(offset + limit, total)}` : ''}</span><div class="pagination-actions"><button type="button" class="secondary" data-page="previous" ${offset === 0 ? 'disabled' : ''}>上一页</button><span class="tabular">${Math.floor(offset / limit) + 1} / ${pages}</span><button type="button" class="secondary" data-page="next" ${offset + limit >= total ? 'disabled' : ''}>下一页</button></div>`;
}
