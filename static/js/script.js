/* MediaTool 前端交互：配置管理、任务进度、媒体库统计（无框架依赖） */
(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };

  var state = {
    pollTimer: null,
    running: false,
    editingName: null,
  };

  /* ------------------------------------------------------------------ */
  /* 通用                                                               */
  /* ------------------------------------------------------------------ */
  function toast(message, type, timeout) {
    var stack = $('#toast-stack');
    if (!stack) { return; }
    var node = document.createElement('div');
    node.className = 'toast is-' + (type || 'info');
    var text = document.createElement('span');
    text.textContent = message;
    node.appendChild(text);
    stack.appendChild(node);
    setTimeout(function () {
      node.classList.add('is-hide');
      setTimeout(function () { node.remove(); }, 220);
    }, timeout || 3600);
  }

  function formatSize(bytes) {
    bytes = Number(bytes) || 0;
    if (bytes >= 1024 * 1024 * 1024) { return (bytes / Math.pow(1024, 3)).toFixed(2) + ' GB'; }
    if (bytes >= 1024 * 1024) { return (bytes / Math.pow(1024, 2)).toFixed(2) + ' MB'; }
    if (bytes >= 1024) { return (bytes / 1024).toFixed(1) + ' KB'; }
    return bytes + ' B';
  }

  function formatElapsed(seconds) {
    seconds = Math.max(0, Math.floor(Number(seconds) || 0));
    var h = String(Math.floor(seconds / 3600)).padStart(2, '0');
    var m = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
    var s = String(seconds % 60).padStart(2, '0');
    return h + ':' + m + ':' + s;
  }

  function setText(sel, value) {
    var el = $(sel);
    if (el) { el.textContent = value; }
  }

  function setRunButtonsDisabled(disabled) {
    $$('[data-action="run-all"], [data-action="run"]').forEach(function (btn) {
      btn.disabled = disabled;
    });
  }

  /* ------------------------------------------------------------------ */
  /* 媒体库统计                                                         */
  /* ------------------------------------------------------------------ */
  function loadStats() {
    setText('#stats-meta', '正在统计…');
    fetch('/stats', { cache: 'no-store' })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.error) { throw new Error(data.error); }
        var movie = data.movie || 0;
        var tv = data.tv_show || 0;
        var total = movie + tv;
        var size = data.size || {};

        setText('#stat-movie', movie);
        setText('#stat-tv', tv);
        setText('#stat-total', total);
        setText('#stat-movie-size', formatSize(size.movie));
        setText('#stat-tv-size', formatSize(size.tv_show));
        setText('#stat-total-size', formatSize((size.movie || 0) + (size.tv_show || 0)));

        var pct = function (n) { return total > 0 ? (n / total * 100).toFixed(2) + '%' : '0%'; };
        var bar = $('#distribution-bar');
        if (bar) {
          bar.children[0].style.width = pct(movie);
          bar.children[1].style.width = pct(tv);
        }

        // 把“按配置兜底 / 空文件 / 读不到大小”说清楚，避免误以为是统计错误
        var notes = [];
        if ((data.fallback || 0) > 0) {
          notes.push((data.fallback) + ' 个文件名没年份或季集，已按配置的媒体类型处理');
        }
        if ((data.empty || 0) > 0) { notes.push((data.empty) + ' 个是 0 字节空文件'); }
        if ((data.unreadable || 0) > 0) { notes.push((data.unreadable) + ' 个读不到大小'); }
        var meta = data.scanned_at ? '统计于 ' + data.scanned_at : '';
        if (notes.length) { meta += ' · ' + notes.join('，'); }
        setText('#stats-meta', meta);
      })
      .catch(function (err) {
        setText('#stats-meta', '统计失败');
        toast('媒体库统计失败：' + err.message, 'error');
      });
  }

  /* ------------------------------------------------------------------ */
  /* 编辑抽屉                                                           */
  /* ------------------------------------------------------------------ */
  function addPathRow(src, dst) {
    var list = $('#path-mappings');
    if (!list) { return; }
    var row = document.createElement('div');
    row.className = 'path-row';

    var srcInput = document.createElement('input');
    srcInput.className = 'form-control';
    srcInput.placeholder = '源路径，例如 /media/downloads';
    srcInput.value = src || '';

    var dstInput = document.createElement('input');
    dstInput.className = 'form-control';
    dstInput.placeholder = '目标路径，例如 /media/movies';
    dstInput.value = dst || '';

    var remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'icon-btn';
    remove.dataset.action = 'remove-path';
    remove.title = '移除该映射';
    remove.textContent = '✕';

    row.appendChild(srcInput);
    row.appendChild(document.createElement('span')).className = 'path-arrow';
    row.lastChild.textContent = '→';
    row.appendChild(dstInput);
    row.appendChild(remove);
    list.appendChild(row);
  }

  function openEditor(name) {
    state.editingName = name || null;
    setText('#editor-title', name ? '编辑配置' : '新建配置');

    var form = $('#cfg-form');
    if (form) { form.reset(); }
    $('#path-mappings').innerHTML = '';
    $('#cfg-enable-scrape').checked = true;
    $('#cfg-enable-rename').checked = true;
    $('#cfg-fix-permissions').checked = true;
    $('#cfg-link-mode').value = 'hardlink';
    $('#cfg-file-type').value = 'movie';
    $('#cfg-interval').value = 0;
    $('#cfg-threads').value = 4;
    $('#cfg-rename').value = '';
    selectPermMode(PERM_DEFAULT.dir, PERM_DEFAULT.file);

    var fixBtn = $('#btn-fix-permissions');
    if (fixBtn) {
      fixBtn.disabled = !name;
      fixBtn.title = name ? '对已有目标目录递归应用下面的权限' : '保存配置后可用';
    }

    if (name) {
      fillConfig(name);
    } else {
      addPathRow();
    }

    $('#editor-panel').classList.add('open');
    $('#editor-panel').setAttribute('aria-hidden', 'false');
    $('#drawer-backdrop').classList.add('open');
    setTimeout(function () { $('#cfg-name').focus(); }, 220);
  }

  function fillConfig(name) {
    fetch('/get_config/' + encodeURIComponent(name))
      .then(function (res) {
        if (!res.ok) { throw new Error('配置不存在'); }
        return res.json();
      })
      .then(function (cfg) {
        $('#cfg-name').value = cfg.name || '';
        $('#cfg-file-type').value = cfg.file_type || 'movie';
        $('#cfg-link-mode').value = cfg.link_mode || 'hardlink';
        $('#cfg-tmdb-api-key').value = cfg.tmdb_api_key || '';
        $('#cfg-suffixes').value = cfg.file_suffixes || '';
        $('#cfg-rename').value = cfg.rename_rule || '';
        $('#cfg-interval').value = cfg.schedule_interval || 0;
        $('#cfg-threads').value = cfg.max_threads || 4;
        $('#cfg-enable-scrape').checked = cfg.scrape_metadata !== false;
        $('#cfg-enable-rename').checked = cfg.rename_file !== false;
        $('#cfg-fix-permissions').checked = cfg.fix_permissions !== false;
        selectPermMode(cfg.dir_mode || PERM_DEFAULT.dir,
                       cfg.file_mode || PERM_DEFAULT.file);

        $('#path-mappings').innerHTML = '';
        (cfg.paths || []).forEach(function (p) { addPathRow(p.source, p.target); });
        if (!(cfg.paths || []).length) { addPathRow(); }
      })
      .catch(function (err) { toast('读取配置失败：' + err.message, 'error'); });
  }

  function closeEditor() {
    $('#editor-panel').classList.remove('open');
    $('#editor-panel').setAttribute('aria-hidden', 'true');
    $('#drawer-backdrop').classList.remove('open');
    state.editingName = null;
  }

  function saveConfig(event) {
    event.preventDefault();

    var name = $('#cfg-name').value.trim();
    if (!name) { toast('请填写配置名称', 'warn'); return; }

    var paths = $$('#path-mappings .path-row').map(function (row) {
      var inputs = row.querySelectorAll('input');
      return { source: inputs[0].value.trim(), target: inputs[1].value.trim() };
    }).filter(function (p) { return p.source && p.target; });

    if (!paths.length) { toast('至少需要一组完整的路径映射', 'warn'); return; }

    var modes = currentPermissionModes();
    if (!modes) { return; }

    var payload = {
      name: name,
      file_type: $('#cfg-file-type').value,
      link_mode: $('#cfg-link-mode').value,
      tmdb_api_key: $('#cfg-tmdb-api-key').value.trim(),
      file_suffixes: $('#cfg-suffixes').value.trim(),
      rename_rule: $('#cfg-rename').value.trim(),
      schedule_interval: parseInt($('#cfg-interval').value, 10) || 0,
      max_threads: parseInt($('#cfg-threads').value, 10) || 4,
      scrape_metadata: $('#cfg-enable-scrape').checked,
      rename_file: $('#cfg-enable-rename').checked,
      fix_permissions: $('#cfg-fix-permissions').checked,
      dir_mode: modes.dir,
      file_mode: modes.file,
      paths: paths,
    };

    fetch('/save_config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok) { throw new Error(body.message || '保存失败'); }
          return body;
        });
      })
      .then(function (body) {
        closeEditor();
        toast(body.message || '保存成功', 'success');
        setTimeout(function () { location.reload(); }, 600);
      })
      .catch(function (err) { toast('保存失败：' + err.message, 'error', 5000); });
  }

  /* ------------------------------------------------------------------ */
  /* 配置动作                                                           */
  /* ------------------------------------------------------------------ */
  function deleteConfig(name) {
    if (!window.confirm('确认删除配置「' + name + '」吗？已链接的文件不会被删除。')) { return; }
    fetch('/delete_config/' + encodeURIComponent(name), { method: 'DELETE' })
      .then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok) { throw new Error(body.message || '删除失败'); }
          return body;
        });
      })
      .then(function (body) {
        toast(body.message || '已删除', 'success');
        setTimeout(function () { location.reload(); }, 500);
      })
      .catch(function (err) { toast('删除失败：' + err.message, 'error'); });
  }

  function toggleConfig(name, enabled) {
    fetch('/toggle_config/' + encodeURIComponent(name), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled }),
    })
      .then(function (res) { return res.json(); })
      .then(function (body) {
        var card = $('.config-card[data-name="' + CSS.escape(name) + '"]');
        if (card) { card.classList.toggle('is-disabled', !enabled); }
        toast(body.message || '状态已更新', enabled ? 'success' : 'info');
      })
      .catch(function (err) { toast('更新失败：' + err.message, 'error'); });
  }

  function clearProcessed() {
    if (!window.confirm('清空处理记录后，下次执行会重新扫描已处理过的文件（已链接的文件会按 inode 自动跳过）。继续？')) {
      return;
    }
    fetch('/clear_processed', { method: 'POST' })
      .then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok) { throw new Error(body.message || '操作失败'); }
          return body;
        });
      })
      .then(function (body) {
        toast(body.message, 'success', 5000);
        loadProcessed();
      })
      .catch(function (err) { toast('清空失败：' + err.message, 'error'); });
  }

  /* 处理记录状态：升级 / 重建容器后用它确认记录是否还在 */
  function loadProcessed() {
    fetch('/processed', { cache: 'no-store' })
      .then(function (res) { return res.json(); })
      .then(function (info) {
        var el = $('#processed-meta');
        if (!el) { return; }
        el.textContent = '已记录 ' + (info.count || 0) + ' 个已处理文件';
        el.title = '记录文件：' + (info.path || 'configs/processed.txt')
          + (info.updated_at ? '\n最后更新：' + info.updated_at : '')
          + '\n升级或重建容器后，如果这里变成 0，说明 configs 目录没有挂载出来。';
      })
      .catch(function () { /* 忽略 */ });
  }

  /* 权限：把八进制藏在“自定义”里，默认用中文选项表达 */
  var PERM_DEFAULT = { dir: '2775', file: '664' };

  function permRadios() {
    return $$('input[name="perm-mode"]');
  }

  function selectedPermRadio() {
    return permRadios().filter(function (r) { return r.checked; })[0] || null;
  }

  function syncPermUi() {
    var selected = selectedPermRadio();
    permRadios().forEach(function (radio) {
      var option = radio.closest('.perm-option');
      if (option) { option.classList.toggle('is-active', radio.checked); }
    });

    var isCustom = !selected || selected.value === 'custom';
    var custom = $('#perm-custom');
    if (custom) { custom.hidden = !isCustom; }

    var warning = $('#perm-warning');
    if (warning) { warning.hidden = !(selected && selected.value === 'private'); }
  }

  function selectPermMode(dirMode, fileMode) {
    var dirValue = parseInt(dirMode, 8);
    var fileValue = parseInt(fileMode, 8);
    var radios = permRadios();
    var match = null;

    radios.forEach(function (radio) {
      if (radio.value === 'custom') { return; }
      if (parseInt(radio.dataset.dir, 8) === dirValue
        && parseInt(radio.dataset.file, 8) === fileValue) {
        match = radio;
      }
    });

    if (!match) {
      match = radios.filter(function (r) { return r.value === 'custom'; })[0] || null;
      if ($('#cfg-dir-mode')) { $('#cfg-dir-mode').value = dirMode || PERM_DEFAULT.dir; }
      if ($('#cfg-file-mode')) { $('#cfg-file-mode').value = fileMode || PERM_DEFAULT.file; }
    }

    // 显式清理选中状态，不依赖浏览器对同名 radio 的默认互斥行为
    radios.forEach(function (radio) { radio.checked = false; });
    if (match) { match.checked = true; }
    syncPermUi();
  }

  function currentPermissionModes() {
    var selected = selectedPermRadio();
    if (selected && selected.value !== 'custom') {
      return { dir: selected.dataset.dir, file: selected.dataset.file, label: selected.value };
    }
    var dir = ($('#cfg-dir-mode').value || '').trim();
    var file = ($('#cfg-file-mode').value || '').trim();
    if (!/^[0-7]{3,4}$/.test(dir) || !/^[0-7]{3,4}$/.test(file)) {
      toast('自定义权限需要 3~4 位八进制数字，例如目录 2775、文件 664', 'warn', 5000);
      return null;
    }
    return { dir: dir, file: file, label: 'custom' };
  }

  function permLabel(modes) {
    var names = {
      private: '只有我能改',
      group: '我和同组成员都能改',
      open: '任何账号都能改',
    };
    return names[modes.label] || ('目录 ' + modes.dir + ' / 文件 ' + modes.file);
  }

  function fixPermissions() {
    var name = state.editingName;
    if (!name) { toast('请先保存配置，再修正已有目录权限', 'warn'); return; }

    var modes = currentPermissionModes();
    if (!modes) { return; }

    var btn = $('#btn-fix-permissions');
    var original = btn.textContent;

    if (!window.confirm('将对「' + name + '」的目标目录递归应用「' + permLabel(modes)
      + '」的权限（目录 ' + modes.dir + '、文件 ' + modes.file + '）。\n\n'
      + '注意：硬链接文件与源文件共享同一份数据，源文件权限也会一起改变。继续？')) {
      return;
    }

    btn.disabled = true;
    btn.textContent = '正在修正…';

    fetch('/fix_permissions/' + encodeURIComponent(name), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dir_mode: modes.dir, file_mode: modes.file }),
    })
      .then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok) { throw new Error(body.message || '修正失败'); }
          return body;
        });
      })
      .then(function (body) { toast(body.message, 'success', 6000); })
      .catch(function (err) { toast('权限修正失败：' + err.message, 'error', 6000); })
      .then(function () {
        btn.disabled = false;
        btn.textContent = original;
      });
  }

  /* ------------------------------------------------------------------ */
  /* 进度面板                                                           */
  /* ------------------------------------------------------------------ */
  function resetProgressDisplay() {
    $('#progress-fill').style.width = '0%';
    setText('#progress-percent', '0%');
    setText('#progress-count', '0 / 0');
    setText('#progress-elapsed', '耗时 00:00:00');
    setText('#count-success', '0');
    setText('#count-skipped', '0');
    setText('#count-failed', '0');
    setText('#count-linked', '0');
    $('#error-list').innerHTML = '';
    $('#error-block').hidden = true;
    var result = $('#result-block');
    result.hidden = true;
    result.className = 'result-block';
    result.textContent = '';
  }

  function setStage(text) { setText('#progress-stage', text || ''); }

  function openProgress(title) {
    setText('#progress-title', title || '任务进度');
    $('#progress-overlay').classList.add('open');
    $('#progress-overlay').setAttribute('aria-hidden', 'false');
  }

  function closeProgress() {
    $('#progress-overlay').classList.remove('open');
    $('#progress-overlay').setAttribute('aria-hidden', 'true');
    if (state.running) {
      stopPolling();
      toast('任务仍在后台执行，刷新页面可重新查看进度', 'info', 5000);
    }
  }

  function showProgressError(message) {
    var block = $('#result-block');
    block.hidden = false;
    block.className = 'result-block is-error';
    block.textContent = '❌ ' + message;
    setStage('任务未启动');
    state.running = false;
    setRunButtonsDisabled(false);
  }

  function renderErrors(errors) {
    var block = $('#error-block');
    var list = $('#error-list');
    if (!errors || !errors.length) { block.hidden = true; return; }

    block.hidden = false;
    setText('#error-count', String(errors.length));
    list.innerHTML = '';

    var limit = 100;
    errors.slice(0, limit).forEach(function (item) {
      var wrap = document.createElement('div');
      wrap.className = 'error-item';

      var file = document.createElement('div');
      file.className = 'error-file';
      file.textContent = (item.config_name ? '[' + item.config_name + '] ' : '') + (item.file || '未知文件');
      wrap.appendChild(file);

      if (item.message) {
        var msg = document.createElement('div');
        msg.className = 'error-msg';
        msg.textContent = item.message;
        wrap.appendChild(msg);
      }
      list.appendChild(wrap);
    });

    if (errors.length > limit) {
      var more = document.createElement('p');
      more.className = 'hint';
      more.textContent = '另有 ' + (errors.length - limit) + ' 条失败记录，请查看日志';
      list.appendChild(more);
    }
  }

  function renderProgress(p) {
    var total = p.total || 0;
    var processed = p.processed || 0;
    var percent = total > 0 ? Math.min(100, Math.floor(processed * 100 / total)) : 0;

    $('#progress-fill').style.width = percent + '%';
    setText('#progress-percent', percent + '%');
    setText('#progress-count', total > 0 ? processed + ' / ' + total : '正在扫描源目录…');
    setText('#progress-elapsed', '耗时 ' + formatElapsed(p.elapsed));
    setText('#count-success', p.success || 0);
    setText('#count-skipped', p.skipped || 0);
    setText('#count-failed', p.failed || 0);
    setText('#count-linked', p.linked || 0);
    setText('#log-path', p.log_path || '—');

    var stage = p.stage || '处理中';
    if (p.current_config) { stage += ' · ' + p.current_config; }
    setStage(stage);

    renderErrors(p.errors);
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function finishProgress(p) {
    state.running = false;
    setRunButtonsDisabled(false);
    stopPolling();

    var block = $('#result-block');
    block.hidden = false;
    var failed = p.failed || 0;
    var summary = '成功 ' + (p.success || 0) + ' 个，跳过 ' + (p.skipped || 0) + ' 个';
    if (failed > 0) {
      block.className = 'result-block is-warn';
      block.textContent = '⚠️ 任务完成：' + summary + '，失败 ' + failed + ' 个，详见上方列表与日志';
    } else {
      block.className = 'result-block';
      block.textContent = '✅ 任务完成：' + summary + '，没有失败项';
    }
    setStage('已完成');
    loadStats();
    loadProcessed();
  }

  function tick() {
    fetch('/progress', { cache: 'no-store' })
      .then(function (res) {
        if (!res.ok) { throw new Error('HTTP ' + res.status); }
        return res.json();
      })
      .then(function (p) {
        renderProgress(p);
        if (!p.running && p.completed) {
          finishProgress(p);
        }
      })
      .catch(function (err) {
        stopPolling();
        state.running = false;
        setRunButtonsDisabled(false);
        showProgressError('获取进度失败：' + err.message);
      });
  }

  function beginPolling() {
    stopPolling();
    state.running = true;
    setRunButtonsDisabled(true);
    state.pollTimer = setInterval(tick, 1000);
    tick();
  }

  function startRun(url, label) {
    if (state.running) { toast('已有任务在运行', 'warn'); return; }
    openProgress(label ? '执行配置：' + label : '执行全部任务');
    resetProgressDisplay();
    setStage('正在提交任务…');

    fetch(url, { method: 'POST' })
      .then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok) { throw new Error(body.message || '启动失败（HTTP ' + res.status + '）'); }
          return body;
        });
      })
      .then(function (body) {
        setStage(body.message || '任务已启动…');
        beginPolling();
      })
      .catch(function (err) { showProgressError(err.message); });
  }

  /* ------------------------------------------------------------------ */
  /* 事件绑定                                                           */
  /* ------------------------------------------------------------------ */
  document.addEventListener('click', function (event) {
    var trigger = event.target.closest('[data-action]');
    if (!trigger) { return; }
    var action = trigger.dataset.action;
    var name = trigger.dataset.name;

    switch (action) {
      case 'new': openEditor(null); break;
      case 'edit': openEditor(name); break;
      case 'delete': deleteConfig(name); break;
      case 'run': startRun('/run_config/' + encodeURIComponent(name), name); break;
      case 'run-all': startRun('/run_task', null); break;
      case 'close-editor': closeEditor(); break;
      case 'add-path': addPathRow(); break;
      case 'remove-path': {
        var row = trigger.closest('.path-row');
        if (row) { row.remove(); }
        break;
      }
      case 'clear-processed': clearProcessed(); break;
      case 'refresh-stats': loadStats(); break;
      case 'close-progress': closeProgress(); break;
      case 'fix-permissions': fixPermissions(); break;
      default: break;
    }
  });

  document.addEventListener('change', function (event) {
    var el = event.target;
    if (el.classList && el.classList.contains('config-toggle')) {
      toggleConfig(el.dataset.name, el.checked);
      return;
    }
    if (el.name === 'perm-mode') { syncPermUi(); }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') { return; }
    if ($('#progress-overlay').classList.contains('open')) { closeProgress(); }
    else if ($('#editor-panel').classList.contains('open')) { closeEditor(); }
  });

  document.addEventListener('DOMContentLoaded', function () {
    var form = $('#cfg-form');
    if (form) { form.addEventListener('submit', saveConfig); }
    syncPermUi();
    loadStats();
    loadProcessed();
    // 页面刷新后如果任务仍在运行，自动接上进度
    fetch('/progress', { cache: 'no-store' })
      .then(function (res) { return res.json(); })
      .then(function (p) {
        if (p.running) {
          openProgress('任务进行中');
          beginPolling();
        }
      })
      .catch(function () { /* 忽略 */ });
  });
})();
