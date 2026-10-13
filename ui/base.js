// 启动器和所有工具共用的小工具：图标、提示、弹层、分段控件。
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const RM = matchMedia('(prefers-reduced-motion: reduce)').matches, EASE = 'cubic-bezier(.32,.72,0,1)';
const sleep = ms => new Promise(r => setTimeout(r, ms)), fmt = n => Number(n).toLocaleString('en-US');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const ICONS = {
  sd: '<path d="M7 3h8l4 4v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M9 7v3M12 7v3M15 8v2"/>',
  swap: '<path d="M4 8h13m0 0-3-3m3 3-3 3M20 16H7m0 0 3-3m-3 3 3 3"/>',
  chip: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  list: '<path d="M8 7h11M8 12h11M8 17h11M4.5 7h.01M4.5 12h.01M4.5 17h.01"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/>',
  chev: '<path d="m9 6 6 6-6 6"/>', down: '<path d="m6 9 6 6 6-6"/>', back: '<path d="m15 6-6 6 6 6"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>', dash: '<path d="M6 12h12"/>', x: '<path d="M6 6l12 12M18 6 6 18"/>',
  arrow: '<path d="M4 12h15m0 0-5-5m5 5-5 5"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  game: '<rect x="3" y="7" width="18" height="10" rx="5"/><path d="M8 10v4M6 12h4M15.5 11h.01M17.5 13h.01"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 8-8m-3 3 2 2"/>',
  save: '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
  spark: '<path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/>',
  pad: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9z"/>',
  tag: '<path d="M4 4h7l9 9-7 7-9-9z"/><path d="M8 8h.01"/>',
  layers: '<path d="m12 4 8 4-8 4-8-4zM4 12l8 4 8-4M4 16l8 4 8-4"/>',
  brush: '<path d="M14 4l6 6-8 8H6v-6z"/><path d="M4 20h4"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  file: '<path d="M7 3h7l4 4v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M14 3v4h4"/>',
  plug: '<path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0zM12 16v5"/>',
  eject: '<path d="M12 5 5 14h14zM5 18h14"/>',
  disk: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="2.2"/><path d="M12 3.5v3"/>',
  bolt: '<path d="M13 3 5 13h6l-1 8 8-10h-6z"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  box: '<path d="m4 8 8-4 8 4v8l-8 4-8-4z"/><path d="m4 8 8 4 8-4M12 12v8"/>',
};
const ic = (n, s = 18, c = '') => `<svg class="${c}" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n] || ICONS.box}</svg>`;

// 工具的应用图标。做法参照苹果自己的图标：一个色相的饱满渐变做底，
// 前景是几块半透明、互相叠压的实心形状，靠透明度分出前后，不加高光条和描边。
const APP_ICONS = {
  // 换包：三层叠在一起的"包"
  swap: { from: '#5AC8FF', to: '#0A6CFF', shade: '#0546B8', art: s => `
    <g opacity=".34">${s('M60 54 96 72 60 90 24 72Z')}</g>
    <g opacity=".62" filter="url(#${s.id}s)">${s('M60 41 96 59 60 77 24 59Z')}</g>
    <g filter="url(#${s.id}s)">${s('M60 28 96 46 60 64 24 46Z', `url(#${s.id}g)`)}</g>` },
  // 换区：一块圆盘，切出来一角
  disk: { from: '#FFCB52', to: '#FF7B02', shade: '#B64A00', art: s => `
    <g opacity=".5">${s('M56 66V34a32 32 0 1 0 32 32Z')}</g>
    <g filter="url(#${s.id}s)">${s('M65 57V26a31 31 0 0 1 31 31Z', `url(#${s.id}g)`)}</g>` },
  // 刷机：一道闪电，后面跟着它的影子
  bolt: { from: '#C79BFF', to: '#6E3CF2', shade: '#3F1BA8', art: s => `
    <g opacity=".34" transform="translate(-9 6)">${s('M68 22 36 66h20l-6 32 34-46H63Z')}</g>
    <g filter="url(#${s.id}s)">${s('M68 22 36 66h20l-6 32 34-46H63Z', `url(#${s.id}g)`)}</g>` },
  // 还没有的工具
  soon: { from: '#C7C7CC', to: '#8E8E93', shade: '#5B5B60', art: s => `
    <g opacity=".9" fill="#fff"><circle cx="40" cy="60" r="7"/><circle cx="60" cy="60" r="7"/><circle cx="80" cy="60" r="7"/></g>` },
};
let appIconCount = 0;
function appIcon(name, small) {
  const key = name in APP_ICONS ? name : 'soon', icon = APP_ICONS[key], id = 'ai' + (++appIconCount);
  // 形状用同色粗描边把尖角磨圆
  const shape = (d, fill = '#fff') => `<path d="${d}" fill="${fill}" stroke="${fill === '#fff' ? '#fff' : '#fff'}" stroke-width="7" stroke-linejoin="round"/>`;
  shape.id = id;
  return `<span class="ti ${small ? 'sm' : ''}" style="--i3:${icon.to}"><svg viewBox="0 0 120 120" aria-hidden="true">
    <defs>
      <linearGradient id="${id}b" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${icon.from}"/><stop offset="1" stop-color="${icon.to}"/></linearGradient>
      <radialGradient id="${id}h" cx=".28" cy=".12" r=".95"><stop offset="0" stop-color="#fff" stop-opacity=".3"/><stop offset=".6" stop-color="#fff" stop-opacity="0"/></radialGradient>
      <linearGradient id="${id}g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity=".86"/></linearGradient>
      <filter id="${id}s" x="-30%" y="-30%" width="160%" height="170%"><feDropShadow dx="0" dy="3" stdDeviation="3" flood-color="${icon.shade}" flood-opacity=".32"/></filter>
    </defs>
    <rect width="120" height="120" rx="27" fill="url(#${id}b)"/><rect width="120" height="120" rx="27" fill="url(#${id}h)"/>
    ${icon.art(shape)}</svg></span>`;
}

function applyTheme(theme) {
  theme === 'light' || theme === 'dark' ? document.documentElement.dataset.scheme = theme : delete document.documentElement.dataset.scheme;
}

function toast(text) {
  let el = $('#toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; el.setAttribute('role', 'status'); document.body.append(el); }
  el.textContent = text; el.classList.add('show');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove('show'), 3000);
}

function num(el, to) {
  if (!el) return;
  const from = el._v ?? to; el._v = to;
  if (RM || from === to) { el.textContent = fmt(to); return; }
  const t0 = performance.now();
  (function frame(t) {
    const p = Math.min(1, (t - t0) / 450), e = 1 - Math.pow(1 - p, 4);
    el.textContent = fmt(Math.round(from + (to - from) * e));
    if (p < 1 && el._v === to) requestAnimationFrame(frame);
  })(t0);
}

function seg(el, value, onChange) {
  const th = el.querySelector('.th');
  const place = () => { const b = el.querySelector('button.on'); if (b) { th.style.width = b.offsetWidth + 'px'; th.style.transform = `translateX(${b.offsetLeft}px)`; } };
  $$('button', el).forEach(b => {
    b.classList.toggle('on', b.dataset.v === value);
    b.onclick = () => { if (b.classList.contains('on')) return; $$('button', el).forEach(x => x.classList.toggle('on', x === b)); place(); onChange(b.dataset.v); };
  });
  th.style.transition = 'none'; place(); requestAnimationFrame(() => { place(); th.style.transition = ''; });
}

let sheetClose = null;
function sheet({ title, body, foot = '', sm = false, onOpen }) {
  let sc = $('#scrim');
  if (!sc) { sc = document.createElement('div'); sc.id = 'scrim'; sc.className = 'scrim'; document.body.append(sc); }
  sc.hidden = false;
  sc.innerHTML = `<div class="sheet ${sm ? 'sm' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="sheet-h"><h3>${esc(title)}</h3><button class="x" data-x aria-label="关闭">${ic('x', 14)}</button></div><div class="sheet-b">${body}</div>${foot ? `<div class="sheet-f">${foot}</div>` : ''}</div>`;
  requestAnimationFrame(() => requestAnimationFrame(() => sc.classList.add('show')));
  const close = () => { sc.classList.remove('show'); sheetClose = null; setTimeout(() => { if (!sc.classList.contains('show')) { sc.hidden = true; sc.innerHTML = ''; } }, RM ? 0 : 320); };
  sheetClose = close;
  sc.onclick = e => { if (e.target === sc || e.target.closest('[data-x]')) close(); };
  onOpen && onOpen(sc.firstElementChild, close);
  return close;
}

function ask({ title, body, ok = '确定', cancel = '取消', danger = false }) {
  return new Promise(resolve => {
    let done = false; const finish = v => { if (!done) { done = true; resolve(v); } };
    sheet({
      title, sm: true, body: `<div class="muted">${body}</div>`,
      foot: `${cancel ? `<button class="btn" data-no>${cancel}</button>` : ''}<button class="btn ${danger ? 'red' : 'pri'}" data-ok>${ok}</button>`,
      onOpen(el, close) {
        el.querySelector('[data-ok]').onclick = () => { finish(true); close(); };
        const no = el.querySelector('[data-no]'); if (no) no.onclick = () => { finish(false); close(); };
        const sc = $('#scrim'), prev = sc.onclick; sc.onclick = e => { prev(e); if (!sheetClose) finish(false); };
      },
    });
  });
}
const tell = (title, body) => ask({ title, body, ok: '好', cancel: '' });

document.addEventListener('keydown', e => { if (e.key === 'Escape') { if (sheetClose) $('#scrim [data-x]')?.click(); $$('.menu.show').forEach(m => m.classList.remove('show')); } });
document.addEventListener('click', e => { if (!e.target.closest('.menu,[data-menu]')) $$('.menu.show').forEach(m => m.classList.remove('show')); });
document.addEventListener('contextmenu', e => { if (!e.target.closest('input,.sel')) e.preventDefault(); });
