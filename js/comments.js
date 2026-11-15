const ISSUE_URL = 'https://github.com/JunichiWatanuki/JunichiWatanuki.github.io/issues/new';
const PUBLIC_SITE_URL = 'https://junichiwatanuki.github.io/';
// Keep a margin below common URL length limits, including proxies and browsers.
const MAX_PREFILLED_URL_LENGTH = 6000;

function initComments(section) {
  const form = section.querySelector('[data-comment-form]');
  const fields = section.querySelector('[data-comment-fields]');
  const title = form.querySelector('[name="title"]');
  const body = form.querySelector('[name="comment_body"]');
  const fallback = form.querySelector('[data-comment-fallback]');
  const pathInput = form.querySelector('[data-comment-path]');
  const copyStatus = form.querySelector('[data-comment-copy-status]');
  const error = form.querySelector('[data-comment-error]');
  // This path is already URL-encoded by the build; URLSearchParams encodes it
  // again for transport so GitHub receives the original encoded article URL.
  const postPath = section.dataset.commentPost || '';

  if (!/^data\/contents\/.+\.html$/i.test(postPath)) {
    error.textContent = '対象記事のパスを確認できないため、コメントフォームを利用できません。';
    error.hidden = false;
    return;
  }
  // Always use the public origin, even when this page is viewed in a preview.
  const postUrl = PUBLIC_SITE_URL + postPath;
  pathInput.value = postUrl;
  fields.disabled = false;

  for (const input of [title, body]) {
    input.addEventListener('input', () => input.setCustomValidity(''));
  }

  form.addEventListener('submit', event => {
    event.preventDefault();
    title.setCustomValidity(title.value.trim() ? '' : 'タイトルを入力してください。');
    body.setCustomValidity(body.value.trim() ? '' : 'コメントを入力してください。');
    if (!form.reportValidity()) return;

    const params = new URLSearchParams({
      template: 'comment.yaml',
      post_path: postUrl,
      title: title.value,
      comment_body: body.value
    });
    // comment.yaml applies the label; passing labels in the URL requires the
    // visitor to have label-adding permission and may otherwise result in 404.
    const url = `${ISSUE_URL}?${params.toString()}`;
    if (url.length > MAX_PREFILLED_URL_LENGTH) {
      fallback.hidden = false;
      copyStatus.textContent = '';
      return;
    }

    fallback.hidden = true;
    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
  });

  const copyFields = { title, path: pathInput, body };
  for (const button of form.querySelectorAll('[data-comment-copy]')) {
    button.addEventListener('click', async () => {
      const input = copyFields[button.dataset.commentCopy];
      if (!input) return;
      try {
        if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(input.value);
        copyStatus.textContent = 'コピーしました。GitHubの該当欄に貼り付けてください。';
      } catch {
        input.focus();
        input.select();
        copyStatus.textContent = 'テキストを選択しました。コピー操作をして、GitHubの該当欄に貼り付けてください。';
      }
    });
  }
}

document.querySelectorAll('[data-comment-post]').forEach(initComments);
