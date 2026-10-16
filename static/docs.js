'use strict';
(() => {
  const navigation = document.querySelector('[data-docs-navigation]');
  const toc = document.querySelector('[data-docs-toc]');
  const navigationMedia = window.matchMedia('(max-width: 800px)');
  const tocMedia = window.matchMedia('(max-width: 1180px)');
  const setDisclosure = (element, media) => { if (element) element.open = !media.matches; };
  setDisclosure(navigation, navigationMedia);
  setDisclosure(toc, tocMedia);
  navigationMedia.addEventListener('change', () => setDisclosure(navigation, navigationMedia));
  tocMedia.addEventListener('change', () => setDisclosure(toc, tocMedia));
  document.querySelector('[data-docs-print]')?.addEventListener('click', () => window.print());

  document.querySelectorAll('.docs-prose pre').forEach(pre => {
    const code = pre.querySelector('code');
    if (!code) return;
    const tools = document.createElement('div');
    tools.className = 'docs-code-tools';
    const language = document.createElement('span');
    language.textContent = (code.className.match(/language-([\w-]+)/)?.[1] || 'Пример').toUpperCase();
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Копировать';
    button.setAttribute('aria-label', 'Скопировать пример кода');
    button.addEventListener('click', async () => {
      try {
        if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(code.textContent);
        else {
          const field = document.createElement('textarea');
          field.value = code.textContent;
          field.style.position = 'fixed';
          field.style.opacity = '0';
          document.body.appendChild(field);
          field.select();
          const success = document.execCommand('copy');
          field.remove();
          button.focus();
          if (!success) throw new Error('Copy unavailable');
        }
        button.textContent = 'Скопировано ✓';
      } catch (_) { button.textContent = 'Выделите текст вручную'; }
      window.setTimeout(() => { button.textContent = 'Копировать'; }, 3000);
    });
    tools.append(language, button);
    pre.prepend(tools);
    pre.tabIndex = 0;
  });
  document.querySelectorAll('.docs-prose table').forEach(table => { table.tabIndex = 0; });

  const tocLinks = [...document.querySelectorAll('.docs-toc a')];
  const headings = tocLinks.map(link => document.getElementById(decodeURIComponent(link.hash.slice(1)))).filter(Boolean);
  if (!headings.length) return;
  let scheduled = false;
  const updateLocation = () => {
    scheduled = false;
    let active = headings[0];
    for (const heading of headings) {
      if (heading.getBoundingClientRect().top <= 110) active = heading;
    }
    tocLinks.forEach(link => {
      if (decodeURIComponent(link.hash.slice(1)) === active.id) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  };
  document.addEventListener('scroll', () => {
    if (!scheduled) { scheduled = true; window.requestAnimationFrame(updateLocation); }
  }, { passive: true });
  updateLocation();
})();
