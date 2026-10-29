(() => {
  const form = document.querySelector('[data-memory-bulk-form]');
  if (!form) return;
  const feedback = document.querySelector('[data-memory-bulk-feedback]');
  const selectAll = form.querySelector('[data-memory-select-all]');
  const clearButton = form.querySelector('[data-memory-clear]');
  const applyButton = form.querySelector('[data-memory-apply]');
  const status = form.elements.status;
  const selected = new Map();
  let busy = false;
  const boxes = () => [...document.querySelectorAll('[data-memory-select]')];
  const eligible = () => boxes().filter((box) => status.value === 'delete' || box.dataset.memoryStatus === 'active');
  const sync = () => {
    boxes().forEach((box) => {
      box.disabled = busy || (status.value !== 'delete' && box.dataset.memoryStatus !== 'active');
      box.checked = selected.has(box.dataset.memorySelect);
    });
    const total = new Set(eligible().map((box) => box.dataset.memorySelect)).size;
    selectAll.checked = total > 0 && selected.size === total;
    selectAll.indeterminate = selected.size > 0 && selected.size < total;
    selectAll.disabled = busy || total === 0;
    clearButton.disabled = busy || selected.size === 0;
    applyButton.disabled = busy || selected.size === 0;
    status.disabled = busy;
    applyButton.textContent = status.value === 'delete' ? '선택 항목 영구 삭제' : '선택 항목 상태 변경';
    form.querySelector('[data-memory-selection-count]').textContent = `${selected.size}개 선택`;
    document.querySelector('[data-memory-results]').inert = busy;
  };
  const reset = () => { selected.clear(); sync(); };
  // Browsers can restore checkbox state on back/forward navigation.
  window.addEventListener('pageshow', reset);
  document.addEventListener('change', (event) => {
    const box = event.target;
    if (!box.matches('[data-memory-select]') || box.disabled || busy) return;
    if (box.checked) selected.set(box.dataset.memorySelect, Number(box.dataset.revision));
    else selected.delete(box.dataset.memorySelect);
    sync();
  });
  selectAll.addEventListener('change', () => {
    if (busy) return;
    selected.clear();
    if (selectAll.checked) eligible().forEach((box) => {
      selected.set(box.dataset.memorySelect, Number(box.dataset.revision));
    });
    sync();
  });
  clearButton.addEventListener('click', reset);
  status.addEventListener('change', reset);

  const render = (results) => {
    const success = results.filter((row) => row.status === 'succeeded').length;
    const failed = results.filter((row) => row.status === 'failed').length;
    const pending = results.length - success - failed;
    feedback.replaceChildren();
    const summary = document.createElement('p');
    summary.textContent = `성공 ${success}개 · 실패 ${failed}개 · 처리 중 ${pending}개`;
    feedback.append(summary);
    const list = document.createElement('ul');
    results.forEach((row) => {
      const item = document.createElement('li');
      item.textContent = `Memory #${row.item_id}: ${row.status}${row.error ? ` — ${row.error}` : ''}`;
      list.append(item);
    });
    feedback.append(list);
  };
  const message = (text) => {
    const paragraph = document.createElement('p');
    paragraph.textContent = text;
    feedback.append(paragraph);
  };
  const refresh = async () => {
    try {
      const response = await fetch(window.location.href, { cache: 'no-store' });
      if (!response.ok) throw new Error('refresh failed');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const selectors = ['[data-memory-results]', '[data-memory-heading]', '[data-memory-pagination]'];
      if (selectors.some((selector) => !page.querySelector(selector))) throw new Error('invalid page');
      selectors.forEach((selector) => {
        document.querySelector(selector).replaceWith(page.querySelector(selector));
      });
    } catch (_) {
      message('목록을 갱신하지 못했습니다. 새로고침하여 최신 상태를 확인하세요.');
    }
  };
  const watch = async (results) => {
    const deadline = Date.now() + 20_000;
    while (results.some((row) => row.status === 'pending') && Date.now() < deadline) {
      await Promise.all(results.filter((row) => row.status === 'pending').map(async (row) => {
        try {
          const response = await fetch(`/admin/commands/${row.command_id}`, {
            headers: { Accept: 'application/json' }, cache: 'no-store',
          });
          if (!response.ok) return;
          const command = await response.json();
          if (['succeeded', 'failed'].includes(command.status)) {
            row.status = command.status;
            row.error = command.error_message || command.error_type || '';
          }
        } catch (_) { /* Keep pending commands visible and allow another status check. */ }
      }));
      render(results);
      if (results.some((row) => row.status === 'pending')) {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
      }
    }
    if (results.some((row) => row.status === 'pending')) {
      message('아직 처리 중입니다. 봇이 실행 중인지 확인하고 결과를 다시 조회하세요.');
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.textContent = '결과 다시 조회';
      retry.addEventListener('click', () => { retry.remove(); void watch(results); }, { once: true });
      feedback.append(retry);
      return;
    }
    await refresh();
    busy = false;
    form.elements._request_id.value = Array.from(window.crypto.getRandomValues(new Uint8Array(16)),
      (value) => value.toString(16).padStart(2, '0')).join('');
    reset();
  };
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy || selected.size === 0) return;
    const deleting = status.value === 'delete';
    if (deleting) {
      if (window.prompt(`선택한 memory ${selected.size}개를 DB에서 영구 삭제합니다.\\n수정/조정 이력도 삭제되며 되돌릴 수 없습니다. 원본 대화/백업은 남습니다.\\n진행하려면 DELETE를 입력하세요.`) !== 'DELETE') return;
    } else if (!window.confirm(`선택한 memory ${selected.size}개를 retracted로 변경할까요?\n내용과 출처는 보존되지만 runtime memory에서 제외됩니다.`)) return;
    const body = new URLSearchParams(new FormData(form));
    body.set('confirm', deleting ? 'delete' : 'yes');
    body.set('items', JSON.stringify([...selected].map(([id, revision]) => ({
      item_id: Number(id), expected_revision: revision,
    }))));
    busy = true;
    sync();
    feedback.textContent = deleting ? '영구 삭제를 요청 중입니다…' : '상태 변경을 요청 중입니다…';
    let results;
    try {
      const response = await fetch(form.action, {
        method: 'POST', body, headers: { Accept: 'application/json' },
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '상태 변경 요청 실패');
      results = data.results;
    } catch (error) {
      feedback.textContent = `${error.message}. 결과를 받지 못했다면 같은 선택으로 다시 요청해 확인할 수 있습니다.`;
      busy = false;
      sync();
      return;
    }
    render(results);
    await watch(results);
  });
  reset();
})();
