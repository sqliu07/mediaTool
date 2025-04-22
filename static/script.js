function openEditor() {
    document.getElementById('editor').classList.add('open');
    document.getElementById('mappings').innerHTML = '';
    addMapping(); // 添加默认一个路径映射
  }
  
  function closeEditor() {
    document.getElementById('editor').classList.remove('open');
  }
  
  function addMapping() {
    const div = document.createElement('div');
    div.innerHTML = `
      <label>源路径: <input type="text" name="source"></label>
      <label>目标路径: <input type="text" name="target"></label>
    `;
    document.getElementById('mappings').appendChild(div);
  }
  
  function loadConfigList() {
    fetch('/get_configs')
      .then(res => res.json())
      .then(configs => {
        const list = document.getElementById('config-list');
        list.innerHTML = '';
        configs.forEach(cfg => {
          const li = document.createElement('li');
          li.textContent = cfg.name;
          list.appendChild(li);
        });
      });
  }
  
  function runTask() {
    fetch('/run_task', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            alert(data.message);  // 弹出任务执行状态
        })
        .catch(err => {
            alert("任务执行失败，请重试！");
        });
}
  
  window.onload = loadConfigList;

document.addEventListener('DOMContentLoaded', () => {
  // 侧边栏控制
  document.getElementById('create-config').addEventListener('click', () => {
    document.getElementById('config-editor').classList.add('active');
  });

  document.getElementById('close-editor').addEventListener('click', (e) => {
    e.preventDefault();
    document.getElementById('config-editor').classList.remove('active');
  });

  // 配置表单提交
  document.getElementById('config-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    
    try {
      const response = await fetch('/save_config', {
        method: 'POST',
        body: formData
      });
      
      if(response.ok) {
        window.location.reload();
      }
    } catch (error) {
      console.error('保存失败:', error);
    }
  });
});
  