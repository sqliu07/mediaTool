<p align="center">
  <img src="https://github.com/user-attachments/assets/c76cce62-d9c7-4809-a134-06e36bd7d756" alt="MediaTool Logo" width="200"/>
</p>


# 🎬 MediaTool - 本地媒体刮削与管理工具

MediaTool 是一个基于 Flask + JavaScript 的本地媒体文件管理工具，支持电影/剧集的批量识别、重命名、元数据抓取与硬链接处理。支持 Jellyfin 兼容 `.nfo` 文件生成。适用于本地 NAS 视频整理用户。

---

## 🚀 功能简介

- 📂 支持多源路径配置，映射到统一目标路径
- 🧠 自动识别媒体文件并匹配 TMDB 信息（支持中文）
- ✂️ 支持自定义重命名规则（`{title}.{year}` 等）
- 🔁 支持硬链接，非破坏式处理原始文件
- 📅 支持定时任务后台自动运行
- 📈 实时前端进度监控 + 错误回显
- ✅ Jellyfin 兼容 `.nfo` 文件生成

---

## 📦 安装 / 部署方式

### 一、使用 Docker 推荐方式

1. 克隆项目：

```bash
git clone https://github.com/yourname/mediaTool.git
cd media-tool
```

2. 构建镜像：

```bash
docker build -t mediatool .
```

3. 启动容器：

```bash
docker run -d -p 5001:5001 -v /your/media:/media \
  -e TZ=Asia/Shanghai \
  -e SECRET_KEY=your-secure-key \
  --name mediatool mediatool
```

你可以绑定宿主机媒体路径并设置时区与密钥。

---

## 💻 使用方式

1. 打开浏览器访问：`http://localhost:5001/`
2. 点击“➕ 新建配置”，填写路径、规则、API Key 等
3. 点击“立即执行任务”
4. 查看任务进度、失败详情、日志路径

---

## 🖼️ UI 截图

![配置页面](https://github.com/user-attachments/assets/389d8fcb-8ba5-4522-b29b-d2893d67e01a)
![任务进度](https://github.com/user-attachments/assets/5f563fa7-ff09-4215-b303-bb3ea0ab5b43)

---

## ☕ 打赏作者

如果你觉得本项目对你有帮助，可以请作者喝一杯咖啡😄：

<img width="300" src="https://github.com/user-attachments/assets/b0efa389-427a-4714-a7c8-23d033da1dc1" />

<img width="300" src="https://github.com/user-attachments/assets/72f51172-a38d-4c56-9afd-cde7a2d8bb94" />


---

## 📄 License

本项目采用 MIT License 许可，欢迎修改、复刻、改进。

---

## 🙏 鸣谢

- [Flask](https://flask.palletsprojects.com/)
- [TMDB](https://www.themoviedb.org/)
- [Bootstrap](https://getbootstrap.com/)
- [Jellyfin 项目](https://jellyfin.org/)
