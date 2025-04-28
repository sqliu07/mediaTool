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
      cfg.paths.forEach(p=> addPath(p.source,p.target));
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
  const paths = [];
  $('#path-mappings .form-row').each(function(){
    const src = $(this).find('input').eq(0).val().trim();
    const dst = $(this).find('input').eq(1).val().trim();
    paths.push({source: src, target: dst});
  });
  fetch('/save_config',{
    method:'POST', headers:{'Content-Type':'application/json'},
    // 在发送的数据中包含 file_type
    body: JSON.stringify({name, file_type, tmdb_api_key: tmdb_api_key, file_suffixes: suffixes, paths, rename_rule, schedule_interval})
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
  $('#progress-bar').css('width','0%').text('0%');
  $('#progress-text').text('0 / 0');
  $('#success-count').text('0');
  $('#failed-count').text('0');
  $('#error-details').empty();
  $('#error-list').hide();
  $('#complete-message').hide();
  
  $('#progressModal').modal('show');
  fetch('/run_task',{method:'POST'})
    .then(()=>{
      let lastProcessed = 0;
      // 每秒刷新一次进度
      const timer = setInterval(()=>{
        fetch('/progress').then(r=>r.json()).then(p=>{
          if(p.processed >= lastProcessed) {
            const pct = p.total>0 ? Math.floor(p.processed*100/p.total) : 0;
            $('#progress-bar').css('width',pct+'%').text(pct+'%');
            $('#progress-text').text(`${p.processed} / ${p.total}`);
            $('#success-count').text(p.success || 0);
            $('#failed-count').text(p.failed || 0);
            
            // 显示错误详情
            if(p.errors && p.errors.length > 0) {
              const errorList = $('#error-details');
              errorList.empty();
              p.errors.forEach(error => {
                errorList.append(`
                  <li class="text-danger mb-2">
                    <strong>${error.file}</strong><br>
                    <small>${error.message}</small>
                  </li>
                `);
              });
              $('#error-list').show();
            }
            
            lastProcessed = p.processed;
          }
          
          if(p.completed || (p.processed >= p.total && p.total > 0)){
            clearInterval(timer);
            // 显示完成消息
            const failed = p.failed || 0;
            const msgDiv = $('#complete-message');
            const logPath = p.log_path || '/logs/movie_manager.log';
            
            if(failed === 0){
              msgDiv.removeClass('alert-warning').addClass('alert-success')
                   .html('✅ 任务完成！所有文件处理成功。');
            } else {
              msgDiv.removeClass('alert-success').addClass('alert-warning')
                   .html(`⚠️ 任务完成！${failed}个文件处理失败。<br>
                         详细错误信息已保存至：<br><code class="bg-light p-1">${logPath}</code>`);
            }
            msgDiv.show();
          }
        });
      }, 1000);
    });
});