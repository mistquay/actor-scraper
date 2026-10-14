import { api, toast } from './api.js';
import { jobTable, pagination } from './tables.js';

const filters = { q: '', state: '', mode: '', from: '', to: '', sort: 'newest', limit: '25', offset: 0 };

function query() {
  const params = new URLSearchParams({ offset: filters.offset, limit: filters.limit, sort: filters.sort });
  for (const key of ['q', 'state', 'mode']) if (filters[key]) params.set(key, filters[key]);
  if (filters.from) params.set('created_after', new Date(`${filters.from}T00:00:00`).getTime() / 1000);
  if (filters.to) {
    const end = new Date(`${filters.to}T00:00:00`);
    end.setDate(end.getDate() + 1);
    params.set('created_before', end.getTime() / 1000);
  }
  return params;
}

export async function renderHistory(root, current = () => true) {
  root.innerHTML = `<div class="page-heading"><div><h1>任务记录</h1><p class="muted">查询处理进度与结果，查看详情或重试未完成演员。</p></div><a class="primary" href="#dashboard">新建任务</a></div>
    <section class="panel history-panel"><form id="history-filters" class="filter-toolbar" role="search" aria-label="筛选任务记录">
      <label class="search-field">搜索<input name="q" type="search" maxlength="200" placeholder="演员姓名 / 任务编号" autocomplete="off"></label>
      <label>任务状态<select name="state"><option value="">全部状态</option><option value="active">进行中</option><option value="queued">准备中</option><option value="running">运行中</option><option value="cancelling">正在停止</option><option value="completed">已完成</option><option value="failed">有失败</option><option value="cancelled">已停止</option><option value="interrupted">已中断</option></select></label>
      <label>模式<select name="mode"><option value="">全部模式</option><option value="preview">预览</option><option value="apply">回写</option></select></label>
      <label>开始日期<input name="from" type="date" aria-label="开始日期"></label>
      <label>结束日期<input name="to" type="date" aria-label="结束日期"></label>
      <div class="filter-actions"><button class="primary" type="submit">筛选</button><button class="secondary" id="clear-filters" type="button">清空</button></div>
    </form><div class="list-toolbar"><span id="history-count" role="status">正在加载…</span><div class="list-options"><label>排序<select name="sort" form="history-filters"><option value="newest">最新优先</option><option value="oldest">最早优先</option></select></label><label>每页<select name="limit" form="history-filters"><option value="25">25 条</option><option value="50">50 条</option><option value="100">100 条</option></select></label><button class="link-button" id="refresh-history" type="button">刷新</button></div></div>
    <p class="list-error" id="history-error" role="alert" hidden></p><div id="history-table"></div><div id="history-pagination" class="pagination"></div></section>`;
  const form = root.querySelector('#history-filters');
  for (const [key, value] of Object.entries(filters)) if (form.elements[key]) form.elements[key].value = value;
  let request = 0;
  let pending = false;
  let searchTimer;
  const load = async () => {
    const ticket = ++request;
    pending = true;
    const params = query();
    root.querySelector('#history-table').setAttribute('aria-busy', 'true');
    root.querySelectorAll('[data-page]').forEach(button => { button.disabled = true; });
    try {
      const data = await api(`/jobs?${params}`);
      if (!current() || ticket !== request) return;
      const limit = Number(filters.limit);
      if (filters.offset && filters.offset >= data.total) {
        filters.offset = Math.max(0, Math.ceil(data.total / limit) - 1) * limit;
        return await load();
      }
      const filtered = Boolean(filters.q || filters.state || filters.mode || filters.from || filters.to);
      root.querySelector('#history-count').textContent = `${filtered ? '筛选结果' : '全部任务'} · ${data.total} 条`;
      root.querySelector('#history-table').innerHTML = jobTable(data.items, filtered);
      root.querySelector('#history-pagination').innerHTML = pagination(filters.offset, limit, data.total);
      root.querySelector('#history-error').hidden = true;
      root.querySelectorAll('[data-page]').forEach(button => {
        button.onclick = () => {
          filters.offset += button.dataset.page === 'next' ? limit : -limit;
          load().catch(() => {});
        };
      });
    } catch (error) {
      if (current() && ticket === request) {
        const message = root.querySelector('#history-error');
        message.textContent = `加载失败：${error.message}。请点击刷新重试。`;
        message.hidden = false;
      }
    } finally {
      if (current() && ticket === request) {
        pending = false;
        root.querySelector('#history-table').setAttribute('aria-busy', 'false');
      }
    }
  };
  const validateDates = () => {
    form.elements.to.setCustomValidity(form.elements.from.value && form.elements.to.value && form.elements.from.value > form.elements.to.value ? '结束日期不能早于开始日期' : '');
  };
  form.onsubmit = event => {
    event.preventDefault();
    clearTimeout(searchTimer);
    validateDates();
    if (!form.reportValidity()) return;
    for (const key of Object.keys(filters)) if (form.elements[key]) filters[key] = form.elements[key].value.trim();
    filters.offset = 0;
    load().catch(error => toast(error.message, true));
  };
  form.elements.q.oninput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { if (current()) form.requestSubmit(); }, 300);
  };
  for (const element of form.elements) if (element.tagName === 'SELECT' || element.type === 'date') element.onchange = () => {
    validateDates();
    form.requestSubmit();
  };
  root.querySelector('#clear-filters').onclick = () => {
    form.reset();
    form.elements.to.setCustomValidity('');
    form.requestSubmit();
  };
  root.querySelector('#refresh-history').onclick = () => load();
  await load();
  return () => { if (!pending) return load(); };
}
