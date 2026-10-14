import { api, escapeHtml as e, toast } from './api.js';

const input = (key, label, hint, type = 'text') => `<label>${label}<input name="${key}" type="${type}" ${type === 'number' ? 'step="any"' : ''}><small>${hint}</small></label>`;
const check = (key, label, hint) => `<label class="check"><input name="${key}" type="checkbox"><span>${label}<small>${hint}</small></span></label>`;

export async function renderSettings(root, onSaved, current = () => true) {
  const settings = await api('/settings');
  if (!current()) return;
  root.innerHTML = `<div class="page-heading"><div><p class="eyebrow">PREFERENCES</p><h1>连接与配置</h1><p class="muted">保存一次，后续任务直接使用。运行中的任务保留启动时的配置。</p></div><button id="save-settings" class="primary" type="submit" form="settings-form">保存配置</button></div>
  <form id="settings-form" class="settings-grid">
    <section class="panel"><div class="section-title"><span class="step">01</span><div><h2>连接媒体服务器</h2><p>通过兼容 Emby 的接口更新现有演员。</p></div></div><div class="form-grid">
      ${input('server', 'FYMS / Emby 地址', '支持 /emby 和反代前缀，例如 https://media.example.com')}
      ${input('user_id', '用户 ID', '可选。按对应用户获取演员列表。')}
      ${input('api_key', 'API 密钥', settings.api_key_configured ? '已配置。留空保留现有密钥，保存后不回显。' : '尚未配置。只发送给媒体服务器。', 'password')}
      ${input('proxy', '外部资料与图片代理', '可选，例如 http://127.0.0.1:7890；不作用于 FYMS。')}
    </div><div class="form-actions"><button id="test-connection" type="button" class="secondary">测试已保存的连接</button><span id="connection-result" class="muted"></span></div></section>
    <section class="panel"><div class="section-title"><span class="step">02</span><div><h2>图片来源与更新方式</h2><p>主图和背景分别处理，默认保留已有图片。</p></div></div><div class="form-grid">
      <label>头像来源<select name="avatar_source"><option value="auto">自动 · GRAPHIS 优先</option><option value="graphis">仅 GRAPHIS</option><option value="gfriends">Gfriends 优选</option><option value="profile">AV-LEAGUE 档案头像</option><option value="none">不处理图片</option></select></label>
      <label>GRAPHIS 写真版本<select name="graphis_selection"><option value="classic">经典 · 普通写真优先</option><option value="latest">最新公开写真</option></select></label>
    </div><div class="check-grid">${check('images_only', '只补图片', '保留 MDCNG 的现有文字资料。')}${check('download_images', '下载预览图片', '预览任务也下载图片，方便核对；不会因此写回。')}${check('no_backdrop', '跳过背景', '只处理头像。')}${check('replace_avatar', '替换已有头像', '仅在“回写”任务中生效。')}${check('replace_backdrop', '替换已有背景', '主图与背景互不覆盖。')}${check('overwrite', '更新现有文字资料', '仅更新来源实际有值的字段。')}${check('refresh', '重新获取来源', '忽略有效缓存，本次需要的图片也重新下载。')}</div></section>
    <section class="panel"><div class="section-title"><span class="step">03</span><div><h2>请求速度与缓存</h2><p>用缓存减少重复请求，速率限制按外部主机生效。</p></div></div><div class="form-grid three">
      ${input('workers', '并发演员数', '1–32，默认 4。', 'number')}${input('rps', '每站每秒请求', '默认 2，适当降低可减少限流。', 'number')}${input('timeout', '请求超时（秒）', '默认 10。', 'number')}
      ${input('retries', 'GET 额外重试', '0–5；403 / 429 不立即重试。', 'number')}${input('ttl_days', '资料缓存（天）', '默认 30，资料和选图结果均复用。', 'number')}${input('miss_hours', '未匹配缓存（小时）', '默认 24，避免反复查相同姓名。', 'number')}
      ${input('retention_days', '任务记录保留（天）', '默认 30；清理工具历史记录与对应导出，保留图片缓存。', 'number')}
    </div></section>
    <section class="panel"><div class="section-title"><span class="step">04</span><div><h2>人工来源映射</h2><p>少量同名或艺名无法自动关联时，指定已经确认的来源。</p></div></div><div class="form-grid">
      <label>演员 → AV-LEAGUE ID<textarea name="actor_mapping" rows="5" spellcheck="false" placeholder='{"水野优香":"8553"}'></textarea></label>
      <label>演员 → GRAPHIS 写真 ID<textarea name="graphis_mapping" rows="5" spellcheck="false" placeholder='{"三上悠亚":"200110_yua-mikami4"}'></textarea></label>
    </div></section><div class="settings-footer"><p>缓存、日志和导出都保存在挂载的数据目录中。任务结果自动入库，JSONL 仅在手动导出时生成。</p><button class="primary" type="submit">保存配置</button></div>
  </form>`;
  const form = root.querySelector('form');
  for (const [key, value] of Object.entries(settings)) {
    const el = form.elements.namedItem(key);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = value;
    else if (key.endsWith('_mapping')) el.value = Object.keys(value).length ? JSON.stringify(value, null, 2) : '';
    else el.value = value;
  }
  form.onsubmit = async event => {
    event.preventDefault();
    const button = root.querySelector('#save-settings');
    button.disabled = true;
    try {
      const values = {};
      for (const el of form.elements) {
        if (!el.name) continue;
        values[el.name] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.name.endsWith('_mapping') ? JSON.parse(el.value || '{}') : el.value.trim();
      }
      const saved = await api('/settings', { method: 'PUT', body: JSON.stringify(values) });
      form.elements.api_key.value = '';
      form.elements.api_key.nextElementSibling.textContent = saved.api_key_configured ? '已配置。留空保留现有密钥，保存后不回显。' : '尚未配置。';
      onSaved(saved); toast('配置已保存');
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  };
  root.querySelector('#test-connection').onclick = async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const data = await api('/connection/test', { method: 'POST' });
      if (!current()) return;
      root.querySelector('#connection-result').innerHTML = `<span class="success-text">连接成功 · ${e(data.actors)} 位演员</span>`;
    } catch (error) { if (current()) root.querySelector('#connection-result').textContent = error.message; }
    finally { button.disabled = false; }
  };
}
