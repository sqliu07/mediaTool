<p align="center">
  <img src="https://github.com/user-attachments/assets/c76cce62-d9c7-4809-a134-06e36bd7d756" alt="MediaTool Logo" width="200"/>
</p>


# 🎬 MediaTool - 本地媒体刮削与管理工具

MediaTool 是一个基于 Flask + 原生 JavaScript 的本地媒体文件管理工具，支持电影/剧集的批量识别、重命名、元数据抓取与硬链接处理，并可生成 Jellyfin 兼容的 `.nfo` 文件。适合本地 NAS 视频整理场景。

---

## 🚀 功能简介

- 📂 多源路径配置，按原有相对目录结构映射到统一目标路径
- 🧠 自动识别媒体文件并匹配 TMDB 信息（中文元数据）
- 🌸 支持动画（番剧）：识别 `[组] 作品 - 01`、`第01话`、`EP01` 等番剧命名
- ✂️ 自定义重命名规则（`{title}.{year}`、`{title}.{season_episode}` 等）
- 🔗 三种整理方式：**硬链接（推荐）/ 复制 / 软链接**，源文件不被破坏
- 🔐 权限策略：目录 `2775`（setgid）+ 文件 `664`，可按需改为 777，支持一键批量修正历史文件
- 📅 定时任务后台自动运行
- 📈 实时进度监控（成功 / 跳过 / 失败 / 已链接）+ 失败原因回显
- ✅ Jellyfin 兼容 `.nfo`、`poster.jpg`、`fanart.jpg`、`clearlogo.png`、单集缩略图

---

## 📦 安装 / 部署

### 一、Docker（推荐）

```bash
git clone https://github.com/sqliu07/mediaTool.git
cd mediaTool
docker build -t mediatool .
```

```bash
docker run -d -p 5001:5001 \
  -v /your/media:/media \
  -v ./configs:/app/configs \
  -v ./logs:/app/logs \
  -e TZ=Asia/Shanghai \
  -e SECRET_KEY=your-secure-key \
  -e PUID=1000 -e PGID=2000 \
  --name mediatool mediatool
```

- `PUID` / `PGID`：容器进程的运行身份。**设置为媒体库的属主用户与共享组**，容器写出的文件才会和刮削软件处于同一用户组。
- `configs` / `logs` 建议挂载出来，重建容器不丢配置与日志。

### 二、本地一键启动（推荐）

仓库自带 `run.sh`，会自动创建虚拟环境并在缺依赖时安装：

```bash
chmod +x run.sh      # 首次执行一次
./run.sh             # 启动服务（后台）→ http://localhost:5001/
```

常用子命令：

```bash
./run.sh status      # 运行状态 + HTTP 探测
./run.sh logs        # 实时日志（Ctrl+C 退出）
./run.sh restart     # 重启
./run.sh stop        # 停止
./run.sh debug       # 调试模式（前台，改代码自动重载）→ http://localhost:5002/
./run.sh install     # 重新安装 / 更新依赖
```

可覆盖的参数：`PORT=5002 HOST=127.0.0.1 THREADS=12 ./run.sh start`

### 三、手动启动

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python app.py                      # 调试模式：http://localhost:5002/
# 生产模式：
gunicorn -k gthread -w 1 --threads 8 --timeout 0 -b 0.0.0.0:5001 app:app
```

> ⚠️ 必须使用**单 worker**（`-w 1`）。进度状态保存在进程内存中，且定时任务只应有一份；多 worker 会导致进度显示错乱、定时任务重复执行。
>
> 💡 Python 3.12+ 请使用 `gunicorn>=23`（20.1.0 依赖已被移除的 `pkg_resources`，会报 `ModuleNotFoundError`）。`requirements.txt` 已固定为 23.0.0。

首次启动会自动创建 `configs/`（配置）与 `logs/`（日志）目录，无需手工建。

---

## 📦 发布：打 tag 自动出镜像包

两条流水线并存，按需使用：

| Workflow | 触发 | 产物 |
|---|---|---|
| `.github/workflows/release-image.yml` | push tag（`v*`）或手动触发 | 挂到 **GitHub Release** 的 `mediatool-<版本>-linux-amd64.tar.gz` / `-arm64.tar.gz`（含 `.sha256`） |
| `.github/workflows/docker-build.yml` | 手动触发 | 构建并推送到 DockerHub（可选） |

### 发一个版本

```bash
# 1. 提交并推送代码
git add -A && git commit -m "release: v1.0.0"
git push origin master

# 2. 打 tag 并推送（这一步才会触发构建）
git tag v1.0.0
git push origin v1.0.0
```

Actions 会依次完成：构建 amd64 + arm64 镜像 → 对 amd64 容器做冒烟测试（拉起容器请求 `/progress`）→ `docker save | gzip` 导出 → 发布到该 tag 的 Release。

也可以到 Actions 页面手动运行 **Build Image and Publish Release**，填入 tag 即可。

### 拿到压缩包后

```bash
sha256sum -c mediatool-v1.0.0-linux-amd64.tar.gz.sha256
gzip -dc mediatool-v1.0.0-linux-amd64.tar.gz | docker load
docker run -d --name mediatool -p 5001:5001 \
  -v /你的媒体路径:/media -v ./configs:/app/configs -v ./logs:/app/logs \
  -e TZ=Asia/Shanghai -e SECRET_KEY=随机串 \
  -e PUID=$(id -u) -e PGID=$(id -g) mediatool:v1.0.0
```

> ⚠️ 若构建时报 `Resource not accessible by integration`，去仓库 **Settings → Actions → General → Workflow permissions** 勾选 **Read and write permissions**（Release 需要写权限）。

---

## 💻 使用方式

1. 浏览器访问 `http://localhost:5001/`
2. 点击「＋ 新建配置」，填写源/目标路径、TMDB API Key、重命名规则、链接方式与权限
3. 点击「执行全部任务」或卡片上的「执行」，在进度面板查看结果
4. 需要重跑已处理过的文件时，点击「清空处理记录」（已存在的硬链接会按 inode 自动跳过）

---

## 🌸 动画（番剧）支持

把配置里的「媒体类型」选成 **动画（番剧）** 后：

- 元数据走 TMDB 的**剧集**接口（TMDB 没有独立的"动画"类型）
- 文件名按番剧习惯识别集号：

| 文件名写法 | 识别结果 |
|---|---|
| `[Lilith-Raws] One Piece - 1080 [1080p].mp4` | One Piece，第 1080 集 |
| `[Nekomoe kissaten][Sousou no Frieren][01][1080p][CHS].mp4` | Sousou no Frieren，第 01 集 |
| `[Group] Bocchi the Rock - 03v2 [1080p].mkv` | Bocchi the Rock，第 03 集 |
| `Spy x Family 第05话.mkv` | Spy x Family，第 05 集 |
| `Chainsaw Man EP08.mkv` | Chainsaw Man，第 08 集 |
| `Jujutsu Kaisen S02E03.mkv` | Jujutsu Kaisen，S02E03 |

- 默认重命名规则 `{title}.{season_episode}` → `Sousou no Frieren.S01E01.mkv`（季号默认 01，Jellyfin 可直接识别）
- 识别不到集号时（剧场版、OVA 合集等）不会生成 `S00E00`，直接命名为 `标题.mkv`
- 统计里单独一栏「动画」，不再混进电影/电视剧

> 这套解析**只在「媒体类型 = 动画」的配置里启用**，电影 / 电视剧配置完全不受影响。
> `Movie - 2019` 这类像年份的数字不会被误当成集号。

---

## 🔐 权限设置（让刮削软件可写）

> 界面里不需要理解八进制：权限用三个中文选项表达——**只有运行本工具的账号能改**（755/644）、**我和同一个「组」里的程序都能改（推荐）**（2775/664）、**任何账号都能改**（777/666）。八进制数字只在「自定义（高级）」里出现，选择非预设组合时会自动填入。下面是每个选项背后的原理。

目标：本工具写出的目录与文件，Jellyfin / Emby / tinyMediaManager 等刮削软件**既能读也能写**（它们需要在媒体目录里生成 `.nfo`、海报，或重命名文件）。

### 方案 A：共享用户组 + setgid（推荐，无需 ACL）

```bash
# 1. 建一个共享组（如果已有可跳过）
sudo groupadd -g 2000 media

# 2. 让两个服务用户属于同一组
sudo usermod -aG media jellyfin      # 刮削软件用户
sudo usermod -aG media "$USER"        # 运行本工具的用户

# 3. 媒体库目录归到该组
sudo chgrp -R media /media/library
```

然后在配置里把「目录权限」设为 `2775`、「文件权限」设为 `664`（**默认值**）：

- 目录带 **setgid** 位（`2775` 中的 `2`），新建的子目录与文件会自动继承 `media` 组；
- 组权限 `rw` 保证同组进程可读写，不需要把媒体库开放成 `777`；
- 容器场景把 `PUID` 设为媒体属主、`PGID` 设为 `2000`。

> 如果文件系统不支持 setgid 或组关系难以统一，退一步用 `775 / 664`（仅同组）、或 `777 / 666`（最宽松）。

### 方案 B：POSIX ACL（不同用户也能写，新建文件自动继承）

```bash
sudo setfacl -R  -m g:media:rwX /media/library   # 历史文件
sudo setfacl -R -d -m g:media:rwX /media/library # 默认 ACL，新建文件自动继承
```

配置好后甚至可以关闭本工具的「自动修正新建文件权限」，由 ACL 统一兜底（需要文件系统支持 ACL，部分 SMB/NTFS 挂载不支持）。

### 方案 C：`777 / 666` 全开放

最省事、也最宽松，仅建议在完全隔离的内网 / 单用户 NAS 上使用。

### 方案 D：刮削软件在 Windows、通过 Samba 修改媒体库

Windows 不认识 Linux 权限，真正在 NAS 上落盘的是 **Samba 服务端用的那个 Linux 账号**，它由 `smb.conf` 决定：

| Samba 配置 | 实际写盘的身份 | 推荐选择 |
|---|---|---|
| 没配（用户名密码登录） | 登录的 SMB 用户对应的 Linux 账号 | 不确定时选「任何账号都能改」 |
| `guest ok = yes`（匿名共享） | `guest account`，通常是 `nobody` | **必须**选「任何账号都能改」 |
| `force user = xxx` | 固定为 `xxx` | 选「只有运行本工具的账号能改」即可 |

想让权限保持较严格，就在共享里统一身份并放宽掩码：

```ini
[media]
   path = /media/library
   read only = no
   force user = youruser        # 换成运行 MediaTool 的 Linux 用户名
   create mask = 0664           # Windows 新建的文件：同组可读写
   directory mask = 2775        # Windows 新建的目录：带 setgid，继承分组
```

配置后选「只有运行本工具的账号能改」（755/644）或「我和同一个组里的程序都能改」（2775/664）都没问题。

> ⚠️ 通过 SMB 的注意事项：
> - Windows 上看到的硬链接是**两个独立文件**（名字不同、指向同一份数据），删掉其中一个不会影响另一个；
> - 若 `create mask` 过严（如 `0744`），本工具可能读不到 Windows 那边新建的文件，建议放开到 `0664`；
> - Windows 端删除后重建同名文件会改变 inode，重新扫描时可能出现 `xxx (1).mkv`；`configs/processed.txt` 的增量记录可以避免大部分重复处理。

### 本工具如何处理权限

| 行为 | 说明 |
|---|---|
| 只处理新建对象 | 只对本次新建的目录与文件执行 `chmod`，不再像旧版那样对每个文件做 `chmod -R 777`（性能与安全都差） |
| 目录权限 | 新建目录（含中间层级）+ 目标目录本身；每次运行每个目录最多修正一次 |
| 文件权限 | 新建媒体文件，以及本次生成的 `.nfo`、海报、缩略图 |
| 软链接不 chmod | 否则会顺着链接改到源文件 |
| 硬链接会连带源文件 | 硬链接与源文件共享同一 inode，`chmod` 会同时改变源文件权限（通常正是所需） |
| 批量修正 | 编辑配置时点「立即修正该配置已有目录的权限」，对目标目录树做一次性递归修正 |

---

## 🧹 为什么会冒出重复文件

### 判断「这个文件处理过没有」

每次运行会依次判断，任一命中就跳过：

1. **处理记录里的目标文件名还在**：最可靠，不依赖 inode
2. **目标目录内同 inode**：正常的硬链接（同一个文件的两个名字）
3. **整个目标库内同 inode**：文件被外部工具重命名过，或挪进了 `电影名 (年份)/` 子目录
4. **大小 + 修改时间一致 + 内容抽样一致**：文件被外部工具用「复制后删除」的方式重命名过，inode 已经变了（只对 > 1MB 的文件启用）
5. 以上都对不上，才判定"目标已丢失"并重建

### 排查工具

```bash
./run.sh doctor              # 检查全部记录
./run.sh doctor --limit 500  # 只查最近 500 条
./run.sh doctor --sample 8   # 每类多列几个例子
```

输出会按类别统计「正常 / 被移到别处 / 内容相同 / 目标目录不存在 / 源文件不存在 / 确实丢失」，
并给出具体路径，一眼就能看出重复是怎么来的。

### 仍然会产生重复的两种情况

| 情况 | 原因 | 对策 |
|---|---|---|
| 重建容器时没挂载 `configs/` | 处理记录丢失；如果与此同时目标文件还是用「复制+删除」方式重命名过，inode 也变了 | **务必挂载 `configs/`**（见上面 compose 示例） |
| 目标库位于 SMB / 网络挂载 | 部分网络文件系统的 `inode`、`mtime` 不真实或不稳定，第 2~4 条都可能失效 | 让 MediaTool 与媒体库在同一台机器的本地盘上运行 |

> 用 TMM / Jellyfin 在 Windows 上整理媒体库时，如果它采用「复制新文件 + 删除旧文件」而不是「原地重命名」，
> 硬链接关系就断了（新文件是一个独立副本）。第 4 条能兜住这种情况，但抽样比对不是 100% 可靠。

### 已经产生重复了怎么办

```bash
# 1. 备份处理记录
cp configs/processed.txt configs/processed.txt.bak

# 2. 页面上点「清空处理记录」，然后重新「执行任务」
#    已存在的文件会被识别为"已存在"并跳过，同时把记录补齐
# 3. 手动删掉多余的副本（通常是多出来的原名文件或带 (1) 的文件）
```

清理时保留**被刮削软件整理过的那份**（它旁边有正确命名的 `.nfo` / 海报），删掉 MediaTool 重新生成的副本。

---

## ⚙️ 配置项说明

| 字段 | 说明 |
|---|---|
| `paths` | 源目录 → 目标目录映射，可多组；保留源目录下的相对层级 |
| `file_type` | `movie` / `tv_show` / `anime`，影响默认重命名规则与元数据接口（动画走 TMDB 剧集接口） |
| `file_suffixes` | 参与处理的扩展名，如 `.mp4,.mkv`（不区分大小写） |
| `link_mode` | `hardlink` / `copy` / `symlink`，默认硬链接 |
| `rename_rule` | 占位符：`{title}` `{year}` `{season}` `{episode}` `{season_episode}` `{episode_title}` `{ext}` |
| `scrape_metadata` | 是否抓取 TMDB 元数据并生成 nfo/图片 |
| `rename_file` | 是否按规则重命名（关闭刮削时用文件名中的信息重命名） |
| `fix_permissions` | 是否自动修正新建文件权限 |
| `dir_mode` / `file_mode` | 目录 / 文件权限，八进制字符串，默认 `2775` / `664` |
| `max_threads` | 单配置并发线程数，默认 4 |
| `schedule_interval` | 定时扫描间隔（分钟），0 = 关闭 |
| `enabled` | 是否参与「执行全部任务」 |

---

## 📁 运行时文件

```
configs/config.json     # 配置（建议挂载持久化）
configs/processed.txt   # 处理记录，用于增量跳过（可清空）
logs/media_tools_*.log  # 运行日志
logs/server.log         # run.sh 的输出
```

### 怎么判断一个文件「处理过」

判断依据只有一处：`configs/processed.txt`。每行一条，格式为

```
源文件的绝对路径|目标目录的绝对路径|目标文件名
/home/user/downloads/Iron Man (2008).mkv|/media/movies|Iron Man.2008.mkv
```

第三段是最终的目标文件名（v1.0.3 起写入）。有了它，下次运行只要这个文件还在原处就直接跳过，
**完全不依赖 inode**——即使媒体库重新挂载、inode 变了、文件被复制替换过也不受影响。
旧的两段记录仍然兼容。

- 每次任务开始把整个文件读进内存集合，处理每个文件前用 `源路径|目标目录` 查一次
- 命中就跳过；未命中才建链接/刮削
- 硬链接模式还会用 inode 复核：目标目录里如果已经没有这个文件的链接（被手动删了），会自动重建
- 兼容旧格式：只有源路径、没有 `|` 的行，按「该源文件对所有目标都已处理」处理

### 🔄 升级 / 重建容器后，处理记录还在吗

| 情况 | 记录 | 结果 |
|---|---|---|
| `configs/` 挂载到宿主机（推荐的 `docker run` / compose） | 保留 | 重建容器、升级镜像后完全无缝，已处理文件继续跳过 |
| 没挂载 `configs/` | 随旧容器一起丢失 | 不会产生重复文件（硬链接按 inode 去重，已有链接会被识别为"已存在"跳过），但会重新请求 TMDB、重新生成 nfo 与海报 |
| 挂载了，但容器内路径变了（`/media` → `/mnt/media`） | 记录还在但键不匹配 | 这些文件会被当成未处理，重新跑一遍 |
| `docker compose down -v` / `docker volume rm` | 删除 | 记录归零 |

> 页面上「清空处理记录」左边会显示 **已记录 N 个已处理文件**，鼠标悬停能看到记录文件路径和最后更新时间。
> 升级容器后如果这个数字变成 0，说明 `configs` 目录没有挂载出来。启动日志里也会打印一行 `处理记录：N 条（路径）`。

推荐的 compose 写法（卷固定、路径固定）：

```yaml
services:
  mediatool:
    image: mediatool
    ports: ["5001:5001"]
    environment:
      TZ: Asia/Shanghai
      SECRET_KEY: change-me
      PUID: "1000"
      PGID: "2000"
    volumes:
      - /mnt/data/media:/media               # 媒体库（容器内路径不要随意改）
      - ./configs:/app/configs               # 必须：保存配置与处理记录
      - ./logs:/app/logs                     # 可选：保存日志
    restart: unless-stopped
```

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
