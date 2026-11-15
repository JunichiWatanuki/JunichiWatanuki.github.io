import { initCommon, postCategory, postImage, siteUrl } from './js/common.js';

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}

function postCard(post, index) {
  const link = document.createElement('a');
  link.className = 'main-post-card';
  link.href = siteUrl(post.path);
  const figure = document.createElement('div');
  figure.className = 'main-post-image';
  const image = document.createElement('img');
  image.src = siteUrl(postImage(post));
  image.alt = '';
  image.loading = 'lazy';
  figure.append(image);
  const category = text('span', 'main-post-category', postCategory(post));
  figure.append(category);
  const copy = document.createElement('div');
  copy.className = 'main-post-copy';
  copy.append(text('time', 'main-post-date', post.date.replaceAll('-', '.')));
  copy.append(text('h3', '', post.title));
  copy.append(text('p', 'main-post-excerpt', post.excerpt || ''));
  copy.append(text('span', 'main-card-more', 'READ MORE  ↗'));
  link.append(figure, copy);
  return link;
}

function archiveRow(post) {
  const link = document.createElement('a');
  link.className = 'main-archive-row';
  link.href = siteUrl(post.path);
  link.append(text('time', '', post.date.replaceAll('-', '.')), text('span', '', post.title), text('span', 'main-archive-arrow', '↗'));
  return link;
}

function popularRow(post, index) {
  const link = document.createElement('a');
  link.className = 'main-popular-item';
  link.href = siteUrl(post.path);
  const image = document.createElement('img');
  image.src = siteUrl(postImage(post));
  image.alt = '';
  image.loading = 'lazy';
  const body = document.createElement('span');
  body.append(text('small', '', `${String(index + 1).padStart(2, '0')}  /  ${postCategory(post)}`));
  body.append(text('strong', '', post.title));
  link.append(image, body);
  return link;
}

const data = await initCommon();
const posts = Array.isArray(data.posts) ? data.posts : [];
const latest = document.querySelector('[data-latest-posts]');
if (latest) latest.replaceChildren(...(posts.length ? posts.slice(0, 4).map(postCard) : [text('p', 'main-loading', '記事はまだありません。')]));
const popular = document.querySelector('[data-popular-posts]');
if (popular) popular.replaceChildren(...posts.slice(0, 3).map(popularRow));
for (const kind of ['blog', 'training', 'sample']) {
  const target = document.querySelector(`[data-archive-${kind}]`);
  if (target) target.replaceChildren(...posts.filter(post => post.type === kind).map(archiveRow));
}
