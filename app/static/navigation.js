(() => {
  let navigating = false;
  const navigate = async (target, options = {}) => {
    if (navigating) return;
    navigating = true;
    window.dispatchEvent(new CustomEvent('videorecback:navigate', { detail: { url: target } }));
    window.VideoRecBackLoading?.show(target, true);
    await window.VideoRecBackMotion?.leave();
    location[options.replace ? 'replace' : 'assign'](target);
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
  document.addEventListener('submit', event => {
    const form = event.target;
    if (event.defaultPrevented || !form.matches('[data-library-search], [data-library-filters]') || form.method.toLowerCase() !== 'get') return;
    const url = new URL(form.action, location.href);
    if (url.origin !== location.origin) return;
    const params = new URLSearchParams(new FormData(form));
    if (event.submitter?.name) params.append(event.submitter.name, event.submitter.value);
    url.search = params.toString();
    event.preventDefault();
    navigate(url.href);
  });
  window.addEventListener('pageshow', () => {
    navigating = false;
    document.querySelectorAll('a[aria-busy]').forEach(link => link.removeAttribute('aria-busy'));
  });
})();
