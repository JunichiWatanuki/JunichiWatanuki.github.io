const indexAddress = new URL(document.body.dataset.index || './index.json', location.href);
const rootAddress = new URL(document.body.dataset.root || './', location.href);

export function siteUrl(path) {
  return new URL(path, rootAddress).href;
}

export function postCategory(post) {
  return post.type === 'training' ? 'TRAINING' : post.type === 'sample' ? 'SAMPLE' : 'JOURNAL';
}

export function postImage(post) {
  return typeof post.thumbnail === 'string' && post.thumbnail
    ? post.thumbnail
    : 'assets/junichiwatanuki.jpg';
}

function makeLink(label, path, className) {
  const link = document.createElement('a');
  link.textContent = label;
  link.href = siteUrl(path);
  if (className) link.className = className;
  return link;
}

function childrenOf(item) {
  return Array.isArray(item.children) ? item.children : [];
}

function hasLink(item) {
  return typeof item.href === 'string' && item.href.trim() !== '';
}

// Compare decoded path segments without confusing an encoded slash with a path separator.
function normalizedPath(url) {
  return url.pathname.split('/').map(segment => {
    try {
      return encodeURIComponent(decodeURIComponent(segment));
    } catch {
      return segment.replace(/%[0-9a-f]{2}/gi, escape => escape.toUpperCase());
    }
  }).join('/');
}

function pageKey(url) {
  return `${url.origin}${normalizedPath(url)}`;
}

function samePage(first, second) {
  return pageKey(first) === pageKey(second);
}

function itemAddress(item) {
  if (!hasLink(item)) return null;
  try {
    return new URL(item.href, rootAddress);
  } catch {
    return null;
  }
}

function countArticles(items, postPaths) {
  return items.reduce((count, item) => {
    const children = childrenOf(item);
    const address = itemAddress(item);
    const isPost = address && postPaths.has(pageKey(address));
    return count + (address && (!children.length || isPost) ? 1 : 0)
      + countArticles(children, postPaths);
  }, 0);
}

function makeCategoryList(items, currentPage) {
  const list = document.createElement('ul');
  let hasActive = false;
  items.forEach(item => {
    const li = document.createElement('li');
    const address = itemAddress(item);
    const isActive = currentPage && address && samePage(address, currentPage);
    if (address) {
      const link = makeLink(item.label, item.href);
      if (isActive) link.setAttribute('aria-current', 'page');
      li.append(link);
    }

    const children = childrenOf(item);
    let childHasActive = false;
    if (children.length) {
      li.className = 'main-category-branch';
      const details = document.createElement('details');
      details.className = 'main-category-nested';
      const summary = document.createElement('summary');
      if (address) {
        li.classList.add('main-category-linked-branch');
        summary.setAttribute('aria-label', `${item.label} の下位カテゴリー`);
      } else {
        summary.textContent = item.label;
      }
      const nested = makeCategoryList(children, currentPage);
      childHasActive = nested.hasActive;
      details.open = childHasActive;
      details.append(summary, nested.list);
      li.append(details);
    } else if (!address) {
      li.textContent = item.label;
    }
    list.append(li);
    hasActive = hasActive || Boolean(isActive) || childHasActive;
  });
  return { list, hasActive };
}

function populateNavigation(data) {
  const menu = Array.isArray(data.menu) ? data.menu : [];
  const top = document.querySelector('[data-top-nav]');
  const mobile = document.querySelector('[data-mobile-nav]');
  const category = document.querySelector('[data-category-nav]');
  if (top) {
    top.replaceChildren(...menu.map(item => makeLink(item.label, item.href)));
    top.append(makeLink('ABOUT', 'index.html#about'));
  }
  if (mobile) {
    mobile.replaceChildren(...menu.map(item => makeLink(item.label, item.href)));
    mobile.append(makeLink('ABOUT', 'index.html#about'));
  }
  if (category) {
    category.replaceChildren();
    const current = new URL(location.href);
    const isHome = samePage(current, rootAddress) || samePage(current, new URL('index.html', rootAddress));
    const postPaths = new Set((Array.isArray(data.posts) ? data.posts : [])
      .map(post => itemAddress({ href: post.path }))
      .filter(Boolean)
      .map(pageKey));
    menu.forEach(item => {
      const children = childrenOf(item);
      if (!children.length) return;
      const details = document.createElement('details');
      details.className = 'main-category-group';
      const nested = makeCategoryList(children, isHome ? null : current);
      const address = itemAddress(item);
      const isBlog = item.label === 'ブログ' || (address && /^#blog(?:-posts)?$/i.test(address.hash));
      details.open = nested.hasActive || (isHome && isBlog);
      const summary = document.createElement('summary');
      const name = document.createElement('span');
      name.textContent = item.label;
      const count = document.createElement('small');
      count.textContent = String(countArticles(children, postPaths)).padStart(2, '0');
      summary.append(name, count);
      details.append(summary, nested.list);
      category.append(details);
    });
    if (!category.children.length) category.textContent = '記事はまだありません。';
  }
}

function restoreCurrentCategory(event) {
  if (!event.persisted) return;
  let element = document.querySelector('[data-category-nav] a[aria-current="page"]');
  while (element) {
    if (element.tagName === 'DETAILS') element.open = true;
    element = element.parentElement;
  }
}

function installMenu() {
  const toggle = document.querySelector('[data-menu-toggle]');
  const menu = document.querySelector('[data-mobile-nav]');
  if (!toggle || !menu) return;
  toggle.addEventListener('click', () => {
    const opened = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!opened));
    menu.hidden = opened;
  });
}

const socialServices = [
  { id: 'facebook', icon: '🄵', label: 'Facebook' },
  { id: 'garminconnect', icon: '⌚', label: 'Garmin Connect' },
  { id: 'x', icon: '🕊️', label: '𝕏' },
  { id: 'instagram', icon: '📷', label: 'Instagram' },
  { id: 'github', icon: '🐱', label: 'GitHub' },
  { id: 'qiita', icon: 'Q', label: 'Qiita' },
  { id: 'youtube', icon: '▶️', label: 'YouTube' },
  { id: 'foursquare', icon: '📍', label: 'Foursquare' },
  { id: 'slideshare', icon: '📊', label: 'SlideShare' },
  { id: '8card', icon: '🪪', label: '8card' }
];

function socialAddress(value) {
  if (typeof value !== 'string' || !/^https:\/\//i.test(value) || /[\s\\\u0000-\u001f\u007f]/.test(value)) return null;
  try {
    const address = new URL(value);
    return address.protocol === 'https:' && address.hostname && !address.username && !address.password ? address.href : null;
  } catch {
    return null;
  }
}

async function loadSocialLinks() {
  const section = document.querySelector('[data-social-section]');
  const nav = section && section.querySelector('[data-social-links]');
  if (!section || !nav) return;
  section.hidden = true;
  nav.replaceChildren();

  try {
    const response = await fetch(siteUrl('assets/profile.json'), { cache: 'no-cache' });
    if (!response.ok) throw new Error(`profile.json: ${response.status}`);
    const data = await response.json();
    const social = data && data.social && typeof data.social === 'object' && !Array.isArray(data.social)
      ? data.social : {};
    const links = socialServices.map(({ id, icon, label }) => {
      const href = socialAddress(social[id]);
      if (!href) return null;
      const link = document.createElement('a');
      link.className = `main-social-link main-social-${id}`;
      link.textContent = `${icon} ${label}`;
      link.href = href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.setAttribute('aria-label', `${label}（新しいタブで開く）`);
      return link;
    }).filter(Boolean);
    nav.replaceChildren(...links);
    section.hidden = links.length === 0;
  } catch (error) {
    console.error('SNSリンクの読み込みに失敗しました。', error);
  }
}

export async function initCommon() {
  installMenu();
  window.addEventListener('pageshow', restoreCurrentCategory);
  void loadSocialLinks();
  try {
    const response = await fetch(indexAddress);
    if (!response.ok) throw new Error(`index.json: ${response.status}`);
    const data = await response.json();
    populateNavigation(data);
    return data;
  } catch (error) {
    console.error('メニューの読み込みに失敗しました。', error);
    const category = document.querySelector('[data-category-nav]');
    if (category) category.textContent = 'カテゴリーを読み込めませんでした。';
    return { menu: [], posts: [] };
  }
}
