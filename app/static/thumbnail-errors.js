(() => {
  const trigger = document.querySelector('[data-thumbnail-errors]');
  const dialog = document.querySelector('[data-thumbnail-errors-dialog]');
  if (!trigger || !dialog) return;
  const list = dialog.querySelector('[data-errors-list]');
  const status = dialog.querySelector('[data-errors-status]');
  const feedback = dialog.querySelector('[data-errors-feedback]');
  const more = dialog.querySelector('[data-errors-more]');
  const retryAll = dialog.querySelector('[data-errors-retry-all]');
  const entries = new Map();
  let offset = 0;
  let loading = false;
  let retrying = false;
  let needsRefresh = false;
  let controller;
  let pollTimer;
  let generation = 0;
  let totalFailed = 0;
  const active = video => ['pending', 'running'].includes(video.job_status);
  const announce = message => { feedback.textContent = message; };
  const make = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const update = (entry, video) => {
    entry.video = { ...entry.video, ...video };
    const state = entry.video;
    entry.reason.textContent = state.thumb_status === 'ready' ? '封面已恢复' : state.reason;
    entry.hint.textContent = state.thumb_status === 'ready' ? '可以继续浏览视频。' : state.suggestion;
    entry.failed.hidden = state.thumb_status === 'ready';
    entry.failed.textContent = `上次失败：${state.failed_at_label}`;
    entry.log.textContent = state.thumb_error || '暂未记录技术日志。';
    const text = state.thumb_status === 'ready' ? '封面已生成' : state.job_status === 'running' ? '正在生成' :
      state.job_status === 'pending' ? '已排队' : '重试封面';
    entry.retry.textContent = text;
    entry.retry.disabled = state.thumb_status === 'ready' || active(state) || retrying;
    entry.state.textContent = state.thumb_status === 'ready' ? '封面已生成成功，可关闭后继续浏览。' :
      state.job_status === 'error' ? '重试未成功，请根据原因检查原视频。' : active(state) ? `${text}，无需重复提交。` : '';
  };
  const controls = () => {
    retryAll.disabled = retrying || loading || totalFailed === 0;
    more.disabled = loading || retrying;
    more.textContent = needsRefresh ? '刷新列表' : '加载更多';
  };
  const schedulePoll = () => {
    clearTimeout(pollTimer);
    if (dialog.open && [...entries.values()].some(entry => active(entry.video))) pollTimer = setTimeout(poll, 2500);
  };
  const poll = async () => {
    if (!dialog.open) return;
    if (document.hidden || loading || retrying) { schedulePoll(); return; }
    const ids = [...entries.values()].filter(entry => active(entry.video)).map(entry => entry.video.id);
    if (!ids.length) return;
    const current = generation;
    try {
      const response = await fetch(`/home/thumbnail-status?ids=${ids.join(',')}`, { cache: 'no-store', signal: AbortSignal.timeout(8000) });
      if (!response.ok) throw new Error('Status unavailable');
      const result = await response.json();
      if (current !== generation || !dialog.open) return;
      const returned = new Set(result.videos.map(video => video.id));
      for (const video of result.videos) {
        const entry = entries.get(video.id);
        if (entry) update(entry, video);
      }
      for (const id of ids) if (!returned.has(id)) {
        const entry = entries.get(id);
        update(entry, { job_status: 'missing', thumb_status: 'missing' });
        entry.retry.disabled = true;
        entry.state.textContent = '视频已不在当前影像库中，请刷新列表。';
      }
      totalFailed = result.total;
      trigger.textContent = result.total ? `${result.total} 个封面失败` : '查看封面处理结果';
      status.textContent = `当前 ${result.total} 个封面失败；下方保留本次处理结果。`;
      controls();
      if (![...entries.values()].some(entry => active(entry.video))) announce('本次重试已结束，请查看每项处理结果。');
    } catch { if (dialog.open) announce('暂时无法更新进度，可点击刷新列表重试；已提交的任务会继续处理。'); }
    schedulePoll();
  };
  const retry = async videoId => {
    if (retrying) return;
    retrying = true;
    for (const entry of entries.values()) update(entry, {});
    controls();
    const current = generation;
    try {
      const body = new URLSearchParams();
      if (videoId !== undefined) body.set('video_id', String(videoId));
      const response = await fetch('/home/thumbnail-errors/retry', { method: 'POST', body, signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error('Retry unavailable');
      const result = await response.json();
      if (current !== generation || !dialog.open) return;
      for (const id of result.video_ids) {
        const entry = entries.get(id);
        if (entry) update(entry, { thumb_status: 'pending', job_status: 'pending' });
      }
      needsRefresh = true;
      totalFailed = Math.max(0, totalFailed - result.queued);
      announce(result.queued ? `已提交 ${result.queued} 个失败封面，正在后台处理。` : '没有需要重新入队的失败项，已有任务将继续处理；可刷新查看状态。');
    } catch { if (dialog.open) announce('未能确认提交结果，请刷新列表查看；重复提交不会创建重复任务。'); }
    finally {
      retrying = false;
      if (current !== generation) { if (dialog.open) load(true); return; }
      for (const entry of entries.values()) update(entry, {});
      controls();
      schedulePoll();
    }
  };
  const copyDetails = async entry => {
    const value = `${entry.video.name}\n${entry.video.path}\n${entry.video.thumb_error || ''}`;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(value);
    } catch {
      const field = make('textarea', value);
      field.readOnly = true;
      field.className = 'copy-log-field';
      dialog.append(field);
      field.select();
      let copied = false;
      try { copied = document.execCommand('copy'); } catch {}
      field.remove();
      entry.copy.focus();
      if (!copied) { announce('复制未成功，请展开技术详情后手动复制。'); return; }
    }
    announce('已复制文件信息和技术日志。');
  };
  const render = video => {
    if (entries.has(video.id)) return;
    const item = make('li');
    const link = make('a', video.name);
    link.href = `/video/${video.id}`;
    const entry = { video, reason: make('strong'), hint: make('p'), failed: make('p'), log: make('pre'),
      state: make('p', '', 'thumbnail-retry-state'), retry: make('button'), copy: make('button', '复制详情') };
    entry.copy.type = entry.retry.type = 'button';
    entry.retry.setAttribute('aria-label', `重试 ${video.name} 的封面`);
    entry.retry.addEventListener('click', () => retry(video.id));
    entry.copy.addEventListener('click', () => copyDetails(entry));
    const details = make('details');
    details.append(make('summary', '技术详情'), make('p', video.path), entry.log, entry.copy);
    item.append(link, entry.reason, entry.hint, entry.failed, entry.state, entry.retry, details);
    entries.set(video.id, entry);
    update(entry, video);
    list.append(item);
  };
  const load = async (reset = false) => {
    if (loading || retrying) return;
    if (reset) {
      generation += 1;
      clearTimeout(pollTimer);
      entries.clear(); list.replaceChildren(); offset = 0; needsRefresh = false;
    }
    loading = true;
    controls();
    status.textContent = '正在载入';
    controller = new AbortController();
    const current = generation;
    try {
      const response = await fetch(`/home/thumbnail-errors?offset=${offset}`, { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error('Thumbnail errors unavailable');
      const result = await response.json();
      if (current !== generation || !dialog.open) return;
      totalFailed = result.total;
      result.videos.forEach(render);
      offset = result.next_offset;
      more.hidden = offset === null;
      status.textContent = result.total ? `共 ${result.total} 个视频，已显示 ${entries.size} 个` : '目前没有封面失败的视频，后台任务可在影像库概览查看。';
    } catch (error) {
      if (error.name !== 'AbortError' && dialog.open) {
        status.textContent = '列表载入失败，请点击刷新列表重试。';
      }
    } finally { if (current === generation) { loading = false; controls(); schedulePoll(); } }
  };
  trigger.addEventListener('click', () => { controller?.abort(); loading = false; dialog.showModal(); announce(''); load(true); });
  retryAll.addEventListener('click', () => retry());
  more.addEventListener('click', () => load(needsRefresh));
  dialog.querySelector('[data-errors-refresh]').addEventListener('click', () => load(true));
  list.addEventListener('click', event => { if (event.target.closest('a')) dialog.close(); });
  dialog.querySelector('[data-errors-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('keydown', event => { if (event.key === 'Escape') event.stopPropagation(); });
  dialog.addEventListener('close', () => {
    generation += 1; controller?.abort(); clearTimeout(pollTimer);
    trigger.focus({ preventScroll: true });
  });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
  });
})();
