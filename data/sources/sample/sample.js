import { initCommon, postCategory, postImage, siteUrl } from '../../../js/common.js';
const data = await initCommon();
const list = document.querySelector('[data-popular-posts]');
if (list) {
  for (const [index, post] of (data.posts || []).slice(0, 3).entries()) {
    const link = document.createElement('a'); link.className = 'main-popular-item'; link.href = siteUrl(post.path);
    const image = document.createElement('img'); image.src = siteUrl(postImage(post)); image.alt = '';
    const copy = document.createElement('span'); const label = document.createElement('small'); label.textContent = `${String(index + 1).padStart(2, '0')} / ${postCategory(post)}`;
    const title = document.createElement('strong'); title.textContent = post.title; copy.append(label, title); link.append(image, copy); list.append(link);
  }
}
