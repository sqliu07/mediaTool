// 打开/关闭编辑面板
function openEditor() { $('#editor-panel').addClass('open'); }
function closeEditor(){ $('#editor-panel').removeClass('open'); }

// 增加一行路径映射
function addPath(src='', dst='') {
  const idx = Date.now();
  $('#path-mappings').append(`
    <div class="form-row mb-2" data-idx="${idx}">
      <div class="col"><input class="form-control" placeholder="源路径" value="${src}"></div>
      <div class="col"><input class="form-control" placeholder="目标路径" value="${dst}"></div>
      <div class="col-auto">
        <button type="button" class="btn btn-danger btn-sm" onclick="$(this).closest('.form-row').remove()">✖</button>
      </div>
    </div>`);
}

// 删除配置
function deleteConfig(name){
  if(!confirm(`确认删除配置 "${name}" 吗？`)) return;
  fetch(`/delete_config/${name}`,{method:'DELETE'})
    .then(r=>r.json()).then(j=>{
      alert(j.message);
      location.reload();
    });
}

// 点击“新建”
$('#btn-new').click(()=>{
  $('#cfg-form')[0].reset();
  $('#cfg-file-type').val('movie'); // 新建时默认为电影
  $('#path-mappings').empty();
  $('#cfg-enable-scrape').trigger('change');
  addPath();
  openEditor();
});

// 编辑已有配置
function editConfig(name){
  fetch(`/get_config/${name}`)
    .then(r=>r.json())
    .then(cfg=>{
      openEditor();
      $('#cfg-name').val(cfg.name);
      $('#cfg-file-type').val(cfg.file_type || 'movie'); // 加载文件类型，默认为 movie
      $('#cfg-tmdb-api-key').val(cfg.tmdb_api_key || "");
      $('#cfg-suffixes').val(cfg.file_suffixes);
      $('#cfg-rename').val(cfg.rename_rule);
      $('#cfg-interval').val(cfg.schedule_interval || 0);
      $('#path-mappings').empty();
      $('#cfg-enable-scrape').prop('checked', cfg.scrape_metadata !== false);
      $('#cfg-enable-rename').prop('checked', cfg.rename_file !== false);
      cfg.paths.forEach(p=> addPath(p.source,p.target));
      $('#cfg-enable-scrape').trigger('change'); // 联动同步状态
    });
}

// 保存配置
function saveConfig(e){
  e.preventDefault();
  const name = $('#cfg-name').val().trim();
  const file_type = $('#cfg-file-type').val(); // 获取文件类型
  const tmdb_api_key = $('#cfg-tmdb-api-key').val().trim();
  const suffixes = $('#cfg-suffixes').val().trim();
  const rename_rule = $('#cfg-rename').val().trim();
  const schedule_interval = parseInt($('#cfg-interval').val(),10) || 0;
  const scrape_metadata =  $('#cfg-enable-scrape').prop('checked');
  const rename_file = $('#cfg-enable-rename').prop('checked');
  const paths = [];
  $('#path-mappings .form-row').each(function(){
    const src = $(this).find('input').eq(0).val().trim();
    const dst = $(this).find('input').eq(1).val().trim();
    paths.push({source: src, target: dst});
  });
  fetch('/save_config',{
    method:'POST', headers:{'Content-Type':'application/json'},
    // 在发送的数据中包含 file_type
    body: JSON.stringify({
      name, file_type, tmdb_api_key: tmdb_api_key, file_suffixes: suffixes, 
      paths, rename_rule, schedule_interval,
      scrape_metadata,rename_file})
  })
  .then(r=>r.json())
  .then(j=>{
    alert(j.message);
    closeEditor();
    location.reload();
  });
}

// 启动任务并监控进度
$('#btn-run').click(()=>{
  // 重置进度显示
  let startTime = Date.now();
  $('#progress-bar').css('width','0%').text('0%');
  $('#progress-text').text('等待开始，正在检查TMDB连通性...'); // 初始状态
  $('#success-count').text('0');
  $('#failed-count').text('0');
  $('#error-details').empty();
  $('#error-list').hide();
  $('#complete-message').hide().removeClass('alert-danger alert-success alert-warning'); // 清除旧样式

  $('#progressModal').modal('show');

  fetch('/run_task',{method:'POST'})
    .then(response => { // <--- 处理响应
      if (!response.ok) { // 检查 HTTP 状态码是否表示成功 (例如 2xx)
        // 如果状态码不是 202 Accepted，说明任务启动失败 (例如 TMDB 检查失败返回 503)
        return response.json().then(err => { throw new Error(err.message || `任务启动失败，状态码: ${response.status}`); });
      }
      // 如果状态码是 202，表示任务已接受并开始在后台运行
      return response.json(); // 继续处理正常的启动消息（虽然我们这里不直接用它）
    })
    .then(()=>{ // <--- 只有在 fetch 成功 (状态码 202) 时才执行
      // 任务已成功启动，开始轮询进度
      $('#progress-text').text('任务已启动，正在获取进度...'); // 更新状态
      let lastProcessed = -1; // 初始化为 -1 确保第一次更新能显示
      // 每秒刷新一次进度
      const timer = setInterval(()=>{
        fetch('/progress').then(r=>r.json()).then(p=>{
          if(p.processed >= lastProcessed) {
            const pct = p.total>0 ? Math.floor(p.processed*100/p.total) : 0;
            $('#progress-bar').css('width',pct+'%').text(pct+'%');
            $('#progress-text').text(`${p.processed} / ${p.total}`);
            const elapsedMs = Date.now() - startTime;
            const elapsedSec = Math.floor(elapsedMs / 1000);
            const hours = String(Math.floor(elapsedSec / 3600)).padStart(2, '0');
            const minutes = String(Math.floor((elapsedSec % 3600) / 60)).padStart(2, '0');
            const seconds = String(elapsedSec % 60).padStart(2, '0');
            $('#elapsed-time').text(`耗时：${hours}:${minutes}:${seconds}`);
            $('#success-count').text(p.success || 0);
            $('#failed-count').text(p.failed || 0);

            // 显示错误详情
            if(p.errors && p.errors.length > 0) {
              const errorList = $('#error-details');
              // 优化：只添加新的错误，避免重复渲染 (如果需要)
              // 这里简单起见，每次都清空重填
              errorList.empty();
              p.errors.forEach(error => {
                const configName = error.config_name ? `[${error.config_name}] ` : '';
                errorList.append(`
                  <li class="mb-2">
                    <strong>${configName}${error.file}</strong><br>
                    <code>${error.message}</code>
                  </li>
                `);
              });
              
              $('#error-list').show();
            } else {
              $('#error-list').hide(); // 没有错误时隐藏列表
            }

            lastProcessed = p.processed;
          }

          if(p.completed || (p.processed >= p.total && p.total > 0 && p.total !== 0)){ // 确保 total 不为 0
            clearInterval(timer);
            // 显示完成消息
            const failed = p.failed || 0;
            const msgDiv = $('#complete-message');
            const logPath = p.log_path || '/logs/media_manager.log'; // 使用后端提供的日志路径

            if(failed === 0 && (!p.errors || p.errors.length === 0)){ // 确保没有错误记录
              msgDiv.removeClass('alert-warning alert-danger').addClass('alert-success')
                   .html('✅ 任务完成！所有文件处理成功。');
            } else {
              msgDiv.removeClass('alert-success alert-danger').addClass('alert-warning')
                   .html(`⚠️ 任务完成！${failed}个文件处理失败。<br>
                         详细错误信息已保存至日志。<br>日志路径: <code class="bg-light p-1">${logPath}</code>`);
            }
            msgDiv.show();
          }
        }).catch(progressError => { // <--- 处理 /progress 请求本身的错误
            console.error("获取进度失败:", progressError);
            clearInterval(timer); // 停止轮询
            const msgDiv = $('#complete-message');
            msgDiv.removeClass('alert-success alert-warning').addClass('alert-danger')
                 .html(`❌ 获取任务进度时出错: ${progressError.message}`)
                 .show();
        });
      }, 1000);
    })
    .catch(error => { // <--- 捕获 /run_task 的错误 (包括我们抛出的 Error)
      console.error("启动任务失败:", error);
      // 在模态框中显示启动错误信息
      const msgDiv = $('#complete-message');
      msgDiv.removeClass('alert-success alert-warning').addClass('alert-danger')
           .html(`❌ 任务启动失败: ${error.message}`)
           .show();
      // 可以选择隐藏进度条等元素
      $('#progress-bar').parent().hide();
      $('#progress-stats').hide();
    });
});

$(document).on('change', '#cfg-enable-scrape', function () {
  const isEnabled = this.checked;
  const $renameCheckbox = $('#cfg-enable-rename');
  const $renameHint = $('#rename-hint');

  $renameCheckbox.prop('disabled', !isEnabled);
  $renameHint.toggleClass('text-danger', !isEnabled)
             .toggleClass('text-muted', isEnabled);

  if (!isEnabled) {
    $renameCheckbox.prop('checked', false);
  }
});

$(document).on('change', '.config-toggle', function () {
  const name = $(this).data('name');
  const enabled = this.checked;

  fetch(`/toggle_config/${name}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled})
  }).then(r => r.json())
    .then(j => console.log(j.message))
    .catch(e => alert("更新失败：" + e.message));
});