(() => {
  const trigger = document.querySelector('[data-thumbnail-errors]');
  const dialog = document.querySelector('[data-thumbnail-errors-dialog]');
  if (!trigger || !dialog) return;
  const list = dialog.querySelector('[data-errors-list]');
  const status = dialog.querySelector('[data-errors-status]');
  const more = dialog.querySelector('[data-errors-more]');
  let offset = 0;
  let loading = false;
  let controller;
  const load = async () => {
    if (loading) return;
    loading = true;
    more.disabled = true;
    status.textContent = '正在载入';
    const request = new AbortController();
    controller = request;
    try {
      const response = await fetch(`/home/thumbnail-errors?offset=${offset}`, { cache: 'no-store', signal: request.signal });
      if (!response.ok) throw new Error('Thumbnail errors unavailable');
      const result = await response.json();
      if (controller !== request || !dialog.open) return;
      more.textContent = '加载更多';
      for (const video of result.videos) {
        const item = document.createElement('li');
        const link = document.createElement('a');
        link.href = `/video/${video.id}`;
        link.textContent = video.name;
        const path = document.createElement('p');
        path.textContent = video.path;
        const error = document.createElement('p');
        error.textContent = video.thumb_error || '封面生成失败，暂未记录具体原因。';
        item.append(link, path, error);
        list.append(item);
      }
      offset = result.next_offset;
      more.hidden = offset === null;
      status.textContent = result.total ? `共 ${result.total} 个视频，已显示 ${list.childElementCount} 个` : '目前没有封面失败的视频。';
    } catch (error) {
      if (controller === request && error.name !== 'AbortError') { status.textContent = '载入失败，请重试。'; more.textContent = '重试'; more.hidden = false; }
    } finally { if (controller === request) { loading = false; more.disabled = false; } }
  };
  trigger.addEventListener('click', () => {
    controller?.abort(); loading = false;
    list.replaceChildren(); offset = 0; more.textContent = '加载更多'; more.hidden = true;
    dialog.showModal(); load();
  });
  more.addEventListener('click', load);
  list.addEventListener('click', event => { if (event.target.closest('a')) dialog.close(); });
  dialog.querySelector('[data-errors-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => controller?.abort());
  dialog.addEventListener('click', event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });
})();
