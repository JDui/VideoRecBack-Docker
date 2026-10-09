(() => {
  let navigating = false;
  const navigate = (target, options = {}) => {
    if (navigating) return;
    navigating = true;
    window.dispatchEvent(new CustomEvent('videorecback:navigate', { detail: { url: target } }));
    window.VideoRecBackLoading?.show(target, true);
    requestAnimationFrame(() => requestAnimationFrame(() => location[options.replace ? 'replace' : 'assign'](target)));
  };
  window.VideoRecBackNavigate = navigate;
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || !/^\/(?:$|library$|settings$|video\/\d+(?:\/play)?$)/.test(url.pathname)) return;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    event.preventDefault();
    if (url.href === location.href) return;
    link.setAttribute('aria-busy', 'true');
    navigate(url.href);
  });
  window.addEventListener('pageshow', () => {
    navigating = false;
    document.querySelectorAll('a[aria-busy]').forEach(link => link.removeAttribute('aria-busy'));
  });
})();
