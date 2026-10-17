"""媒体文件处理：扫描源目录 → 建立链接 → 重命名 → 刮削元数据。"""

from common_imports import *

import errno
import os
import shutil
import threading
from dataclasses import dataclass
from typing import Optional

from metadata_fetcher import (
    fetch_metadata_cached,
    fetch_episode_metadata,
    download_poster,
    download_images,
)
from nfo_generator import generate_nfo, generate_tv_nfo, generate_tvshow_nfo
from filename_parser import parse_filename

logger = logging.getLogger(__name__)

# 配置 / 处理记录目录（可用环境变量指向别处，便于测试或多实例共存）
CONFIG_DIR = os.getenv("MEDIATOOL_CONFIG_DIR", "configs")
PROCESSED_FILE = os.path.join(CONFIG_DIR, "processed.txt")
_MAX_NAME_LENGTH = 150
_INVALID_NAME_CHARS = re.compile(r'[\\/\x00-\x1f]')

# 未填写文件后缀时使用的默认值（覆盖常见视频容器）
DEFAULT_FILE_SUFFIXES = (
    ".mp4,.mkv,.avi,.mov,.wmv,.flv,.webm,.m4v,.ts,.m2ts,.rmvb,.rm,.mpg,.mpeg"
)

# 权限默认值：目录带 setgid（2775），新建文件自动继承所在组；文件 664。
# 刮削软件（Jellyfin / Emby / tinyMediaManager）与容器进程加入同一用户组即可读写，
# 无需把媒体库开放成 777。
DEFAULT_DIR_MODE = 0o2775
DEFAULT_FILE_MODE = 0o664

# 链接方式
LINK_MODE_HARDLINK = "hardlink"
LINK_MODE_COPY = "copy"
LINK_MODE_SYMLINK = "symlink"
LINK_MODES = (LINK_MODE_HARDLINK, LINK_MODE_COPY, LINK_MODE_SYMLINK)
LINK_MODE_LABELS = {
    LINK_MODE_HARDLINK: "硬链接",
    LINK_MODE_COPY: "复制",
    LINK_MODE_SYMLINK: "软链接",
}

# 媒体类型
MEDIA_TYPE_MOVIE = "movie"
MEDIA_TYPE_TV = "tv_show"
MEDIA_TYPE_ANIME = "anime"
MEDIA_TYPES = (MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV, MEDIA_TYPE_ANIME)
# 动画在 TMDB 走剧集接口，NFO / 图片 / 重命名都按剧集处理
TV_LIKE_TYPES = (MEDIA_TYPE_TV, MEDIA_TYPE_ANIME)
MEDIA_TYPE_LABELS = {
    MEDIA_TYPE_MOVIE: "电影",
    MEDIA_TYPE_TV: "电视剧",
    MEDIA_TYPE_ANIME: "动画",
}

# 单文件处理结果状态
STATUS_SUCCESS = "success"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


# ---------------------------------------------------------------------------
# 处理记录（增量跳过）
# ---------------------------------------------------------------------------
_processed_lock = threading.Lock()


def processed_key(file_path, target_dir):
    """处理记录的键：源文件 + 目标目录，支持同一源文件链接到多个目标目录。"""
    return "{}|{}".format(
        file_path.strip(), os.path.normpath(os.path.abspath(target_dir))
    )


def load_processed_set():
    """读取 configs/processed.txt 返回集合（兼容旧的“只有路径”的记录格式）。"""
    if not os.path.exists(PROCESSED_FILE):
        return set()
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except OSError as e:
        logger.warning(f"读取处理记录失败：{e}")
        return set()


def is_processed(processed_set, file_path, target_dir):
    if processed_set is None:
        return False
    if processed_key(file_path, target_dir) in processed_set:
        return True
    # 兼容旧记录：只记录了源文件路径
    return file_path.strip() in processed_set


# 处理记录先写内存缓冲，攒够一批再落盘，避免每个文件一次 open/write
_pending_records = []
_PENDING_FLUSH_SIZE = 200


def _flush_records_locked():
    """把缓冲写入 processed.txt（调用方需持有 _processed_lock）。"""
    if not _pending_records:
        return
    try:
        os.makedirs(os.path.dirname(PROCESSED_FILE) or ".", exist_ok=True)
        with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(_pending_records) + "\n")
        _pending_records.clear()
    except OSError as e:
        logger.warning(f"写入处理记录失败：{e}")


def record_processed(file_path, target_dir, processed_set=None):
    """记录一个文件已处理（线程安全，批量落盘）。

    若该记录本次运行的文件里已经存在（例如目标丢失后重建），则不重复写入。
    """
    if processed_set is not None and is_processed(processed_set, file_path, target_dir):
        return
    line = processed_key(file_path, target_dir)
    with _processed_lock:
        _pending_records.append(line)
        if len(_pending_records) >= _PENDING_FLUSH_SIZE:
            _flush_records_locked()


def flush_processed():
    """把尚未落盘的处理记录写出去（任务结束时调用）。"""
    with _processed_lock:
        _flush_records_locked()


def clear_processed():
    """清空处理记录，返回删除的行数。"""
    with _processed_lock:
        _pending_records.clear()
        if not os.path.exists(PROCESSED_FILE):
            return 0
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            count = sum(1 for line in f if line.strip())
        os.remove(PROCESSED_FILE)
        return count


def processed_stats():
    """处理记录文件的状态：路径、条数、最后更新时间。

    重建 / 升级容器后，这个条数就是判断 configs 卷是否挂载成功的直接依据。
    """
    info = {
        "path": os.path.abspath(PROCESSED_FILE),
        "count": 0,
        "exists": os.path.exists(PROCESSED_FILE),
        "updated_at": None,
    }
    if not info["exists"]:
        return info
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            info["count"] = sum(1 for line in f if line.strip())
        info["updated_at"] = datetime.fromtimestamp(
            os.path.getmtime(PROCESSED_FILE)
        ).strftime("%Y-%m-%d %H:%M:%S")
    except OSError as e:
        logger.warning(f"读取处理记录状态失败：{e}")
    return info


# ---------------------------------------------------------------------------
# 目标目录索引：避免每个文件都重新 listdir / stat（原实现是 O(n^2)）
# ---------------------------------------------------------------------------
class TargetDirIndex:
    """缓存目标目录的文件名集合与 inode → 路径映射，供线程池共享。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._names = {}       # dir -> set(filename)
        self._inodes = {}      # dir -> {(st_dev, st_ino): path}
        self._permission_done = set()  # 已经修过权限的目录

    # -- 索引维护 ---------------------------------------------------------
    def scan(self, dir_path):
        """返回 (names, inodes)，同一个目录只扫描一次。"""
        with self._lock:
            if dir_path in self._names:
                return self._names[dir_path], self._inodes[dir_path]

        names, inodes = set(), {}
        try:
            with os.scandir(dir_path) as entries:
                for entry in entries:
                    names.add(entry.name)
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    inodes[(st.st_dev, st.st_ino)] = entry.path
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"扫描目标目录失败（{dir_path}）：{e}")

        with self._lock:
            # 双检：并发下其他线程可能已经建立索引
            if dir_path in self._names:
                return self._names[dir_path], self._inodes[dir_path]
            self._names[dir_path] = names
            self._inodes[dir_path] = inodes
            return names, inodes

    def claim(self, dir_path, filename):
        """原子地占用一个目标文件名；同名已存在时自动加 “(n)” 后缀。"""
        names, _ = self.scan(dir_path)
        with self._lock:
            if filename not in names:
                names.add(filename)
                return filename
            stem, ext = os.path.splitext(filename)
            for i in range(1, 1000):
                candidate = f"{stem} ({i}){ext}"
                if candidate not in names:
                    names.add(candidate)
                    return candidate
        raise RuntimeError(f"目标目录中同名文件过多：{filename}")

    def release(self, dir_path, filename):
        """放弃之前 claim 的名字（创建失败时回滚）。"""
        with self._lock:
            names = self._names.get(dir_path)
            if names is not None:
                names.discard(filename)

    def commit(self, dir_path, path):
        """文件创建/改名成功后登记到索引（名字 + inode）。"""
        names, inodes = self.scan(dir_path)
        try:
            st = os.stat(path)
        except OSError:
            st = None
        with self._lock:
            names.add(os.path.basename(path))
            if st is not None:
                inodes[(st.st_dev, st.st_ino)] = path

    def remove(self, dir_path, path):
        names, inodes = self.scan(dir_path)
        with self._lock:
            names.discard(os.path.basename(path))
            for key, val in list(inodes.items()):
                if val == path:
                    inodes.pop(key, None)

    def move(self, dir_path, old_path, new_path):
        names, inodes = self.scan(dir_path)
        with self._lock:
            names.discard(os.path.basename(old_path))
            names.add(os.path.basename(new_path))
            for key, val in list(inodes.items()):
                if val == old_path:
                    inodes[key] = new_path

    def find_by_inode(self, dir_path, inode_key):
        _, inodes = self.scan(dir_path)
        with self._lock:
            return inodes.get(inode_key)

    # -- 权限 -------------------------------------------------------------
    def mark_permission_fixed(self, dir_path):
        """返回 True 表示本进程内首次处理该目录。"""
        with self._lock:
            if dir_path in self._permission_done:
                return False
            self._permission_done.add(dir_path)
            return True


# ---------------------------------------------------------------------------
# 结果对象
# ---------------------------------------------------------------------------
@dataclass
class LinkResult:
    action: str                      # linked | copied | symlinked | skipped | failed
    path: Optional[str] = None
    message: str = ""
    duplicate: bool = False          # 目标已存在同一文件

    @property
    def ok(self):
        return self.action != STATUS_FAILED


@dataclass
class FileOutcome:
    status: str                      # success | skipped | failed
    message: str = ""
    action: str = ""                 # linked | copied | symlinked


# ---------------------------------------------------------------------------
# 权限策略
# ---------------------------------------------------------------------------
def parse_mode(value, default):
    """解析八进制权限字符串（"775" / "0775" / "0o775" / 509）为 int。"""
    if value is None or value == "":
        return default
    if isinstance(value, int):
        return value if 0 <= value <= 0o7777 else default
    text = str(value).strip().lower().replace("0o", "")
    if not text or any(ch not in "01234567" for ch in text):
        logger.warning(f"权限值不合法：{value!r}，使用默认 {oct(default)}")
        return default
    mode = int(text, 8)
    return mode if 0 <= mode <= 0o7777 else default


def mode_to_string(mode):
    """把 int 权限转成 "2775" 这样的四位八进制字符串。"""
    return format(int(mode) & 0o7777, "04o")


@dataclass
class PermissionPolicy:
    """权限策略：是否启用 + 目录权限 + 文件权限。"""

    enabled: bool = False
    dir_mode: int = DEFAULT_DIR_MODE
    file_mode: int = DEFAULT_FILE_MODE

    @classmethod
    def from_config(cls, config):
        config = config or {}
        return cls(
            enabled=bool(config.get("fix_permissions", True)),
            dir_mode=parse_mode(config.get("dir_mode"), DEFAULT_DIR_MODE),
            file_mode=parse_mode(config.get("file_mode"), DEFAULT_FILE_MODE),
        )

    def describe(self):
        return f"{mode_to_string(self.dir_mode)} / {mode_to_string(self.file_mode)}"


def _chmod(path, mode, errors=None):
    """修改权限；失败只记日志，不影响主流程。"""
    try:
        os.chmod(path, mode)
        return True
    except OSError as e:
        logger.warning(f"设置权限失败（{path} → {mode_to_string(mode)}）：{e}")
        if errors is not None:
            errors += 1
        return False


def _makedirs_tracked(path):
    """创建目录，并返回本次真正新建的目录（从浅到深），用于单独设置权限。"""
    created = []
    current = os.path.abspath(path)
    while current and not os.path.exists(current):
        created.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    os.makedirs(path, exist_ok=True)
    created.reverse()
    return created


def _apply_created_permissions(dest_path, dest_dir, created_dirs, index, policy):
    """只对本次新建的目录 / 文件应用权限（不做任何递归操作）。

    注意：
    - 硬链接与源文件共享同一 inode，chmod 会同时改变源文件权限（通常正是所需）。
    - 软链接不 chmod，否则 chmod 会顺着链接改到源文件。
    """
    if not policy.enabled:
        return
    for directory in created_dirs:
        _chmod(directory, policy.dir_mode)
        index.mark_permission_fixed(directory)
    # 已存在的目标目录每次运行最多修正一次，保证刮削软件能写入
    if index.mark_permission_fixed(dest_dir):
        _chmod(dest_dir, policy.dir_mode)
    if not os.path.islink(dest_path):
        _chmod(dest_path, policy.file_mode)


def apply_permission_tree(target_dirs, dir_mode, file_mode, follow_symlinks=False):
    """对已有媒体库做一次性权限修正（批量，走 os.walk，不走逐文件递归）。

    返回 {"dirs": n, "files": n, "skipped": n, "errors": n}
    """
    stats = {"dirs": 0, "files": 0, "skipped": 0, "errors": 0}
    seen = set()
    for target in target_dirs:
        if not target or not os.path.isdir(target):
            continue
        for current, dirnames, filenames in os.walk(target):
            if current in seen:
                continue
            seen.add(current)
            if _chmod(current, dir_mode, stats):
                stats["dirs"] += 1
            for name in dirnames:
                path = os.path.join(current, name)
                if os.path.islink(path) and not follow_symlinks:
                    stats["skipped"] += 1
                    continue
                if _chmod(path, dir_mode, stats):
                    stats["dirs"] += 1
            for name in filenames:
                path = os.path.join(current, name)
                if os.path.islink(path) and not follow_symlinks:
                    stats["skipped"] += 1
                    continue
                if _chmod(path, file_mode, stats):
                    stats["files"] += 1
    return stats


# ---------------------------------------------------------------------------
# 链接 / 复制
# ---------------------------------------------------------------------------
def normalize_link_mode(mode):
    mode = (mode or LINK_MODE_HARDLINK).strip().lower()
    return mode if mode in LINK_MODES else LINK_MODE_HARDLINK


def _fix_permissions(dest_path, dest_dir, index, policy=None):
    """兼容旧调用：按策略修正单个新建文件（不递归）。"""
    _apply_created_permissions(dest_path, dest_dir, [], index,
                               policy or PermissionPolicy())


def _has_link_in(src_path, dest_dir, index):
    """目标目录里是否已存在与源文件同 inode 的硬链接。"""
    try:
        st = os.stat(src_path)
    except OSError:
        return False
    if not os.path.isdir(dest_dir):
        return False
    return index.find_by_inode(dest_dir, (st.st_dev, st.st_ino)) is not None


def create_link(src_path, dest_dir, config_name="", index=None,
                mode=LINK_MODE_HARDLINK, policy=None):
    """把 src_path 链接（或复制）到 dest_dir，返回 LinkResult。"""
    index = index if index is not None else TargetDirIndex()
    policy = policy or PermissionPolicy()
    mode = normalize_link_mode(mode)
    label = LINK_MODE_LABELS[mode]

    try:
        created_dirs = _makedirs_tracked(dest_dir)
    except OSError as e:
        return LinkResult(STATUS_FAILED, None, f"无法创建目标目录：{e}")

    try:
        src_stat = os.stat(src_path)
    except OSError as e:
        return LinkResult(STATUS_FAILED, None, f"无法读取源文件信息：{e}")

    if not os.path.isfile(src_path):
        return LinkResult(STATUS_FAILED, None, "源路径不是普通文件")

    src_real = os.path.realpath(src_path)
    if os.path.dirname(src_real) == os.path.realpath(dest_dir):
        return LinkResult(STATUS_SKIPPED, src_path, "源文件已位于目标目录，跳过")

    # 硬链接：用 inode 精确判重（同一文件重复处理时不会产生副本）
    if mode == LINK_MODE_HARDLINK:
        existing = index.find_by_inode(
            dest_dir, (src_stat.st_dev, src_stat.st_ino)
        )
        if existing:
            return LinkResult(
                STATUS_SKIPPED, existing,
                f"目标目录已存在同一文件的硬链接：{os.path.basename(existing)}",
                duplicate=True,
            )

    filename = os.path.basename(src_path)
    try:
        name = index.claim(dest_dir, filename)
    except RuntimeError as e:
        return LinkResult(STATUS_FAILED, None, str(e))
    dest_path = os.path.join(dest_dir, name)
    if name != filename:
        logger.warning(f"[配置:{config_name}] 目标目录已有同名文件，改名为：{name}")

    try:
        if mode == LINK_MODE_COPY:
            shutil.copy2(src_path, dest_path)
            action = "copied"
        elif mode == LINK_MODE_SYMLINK:
            os.symlink(src_real, dest_path)
            action = "symlinked"
        else:
            os.link(src_path, dest_path)
            action = "linked"
    except OSError as e:
        index.release(dest_dir, name)
        if e.errno == errno.EXDEV:
            message = (f"{label}失败：源与目标不在同一文件系统（{src_path} → {dest_dir}）。"
                       f"硬链接要求两者在同一分区，请改用“复制”或“软链接”")
        elif e.errno == errno.EEXIST:
            message = f"{label}失败：目标文件已存在 {dest_path}"
        elif e.errno in (errno.EPERM, errno.EACCES):
            message = (f"{label}失败：没有权限，或该位置是 SMB / NFS 等网络挂载"
                       f"（网络共享通常不支持硬链接，请改用“复制”）")
        elif e.errno == errno.EMLINK:
            message = f"{label}失败：该文件的链接数已达文件系统上限"
        elif e.errno == errno.ENOSPC:
            message = f"{label}失败：目标磁盘空间不足"
        elif e.errno == errno.EROFS:
            message = f"{label}失败：目标目录是只读文件系统"
        else:
            message = f"{label}失败：{e}"
        logger.error(f"[配置:{config_name}] {message}（源：{src_path}）")
        return LinkResult(STATUS_FAILED, None, message)

    index.commit(dest_dir, dest_path)
    _apply_created_permissions(dest_path, dest_dir, created_dirs, index, policy)
    logger.info(f"[配置:{config_name}] 已建立{label}：{dest_path}")
    return LinkResult(action, dest_path, "")


# 兼容旧名字（返回 LinkResult 而不是元组）
def create_hardlink_if_needed(src_path, dest_dir, config_name=""):
    return create_link(src_path, dest_dir, config_name, mode=LINK_MODE_HARDLINK)


# ---------------------------------------------------------------------------
# 文件名与占位符
# ---------------------------------------------------------------------------
def sanitize_filename(name):
    name = _INVALID_NAME_CHARS.sub("_", name or "")
    name = name.strip().strip(".").strip()
    if len(name) > _MAX_NAME_LENGTH:
        name = name[:_MAX_NAME_LENGTH].strip()
    return name


def build_rename_placeholders(metadata, file_info, config_media_type, ext):
    metadata = metadata or {}
    file_info = file_info or {}

    title = metadata.get("title") or file_info.get("title") or "Unknown"
    year = (metadata.get("release_date") or file_info.get("year") or "")[:4] or "Unknown"
    season = str(file_info.get("season") or "00")
    episode_raw = file_info.get("episode")
    episode = str(episode_raw or "00")
    episode_title = (metadata.get("episode_title") or "").replace(" ", "_")[:50]

    media_type = metadata.get("media_type") or file_info.get("type")
    if not media_type:
        media_type = config_media_type

    # 动画按剧集处理；没有集号时 season_episode 留空，避免生成 S00E00
    season_episode = ""
    if media_type in TV_LIKE_TYPES and episode_raw:
        season_episode = f"S{season}E{episode}"

    return {
        "title": sanitize_filename(title),
        "year": year,
        "season": season,
        "episode": episode,
        "season_episode": season_episode,
        "episode_title": episode_title,
        "ext": ext,
    }


def _apply_rename(dest_path, dest_dir, config, placeholders, index, config_name):
    """按规则重命名，返回新的目标路径（失败或未启用时返回原路径）。"""
    if not config.get("rename_file", True):
        return dest_path

    ext = placeholders.get("ext") or os.path.splitext(dest_path)[1]
    default_rule = (
        "{title}.{year}" if config.get("file_type", MEDIA_TYPE_MOVIE) == MEDIA_TYPE_MOVIE
        else "{title}.{season_episode}"
    )
    rule = (config.get("rename_rule") or default_rule).strip() or default_rule

    try:
        base = rule.format(**placeholders)
        base = sanitize_filename(base)
        if not base:
            raise KeyError("重命名结果为空")
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(f"[配置:{config_name}] 重命名规则不可用（{e}），回退默认规则")
        base = sanitize_filename(default_rule.format(**placeholders))

    if not base:
        return dest_path

    wanted = base if "{ext}" in rule else base + ext
    if wanted == os.path.basename(dest_path):
        return dest_path

    try:
        new_name = index.claim(dest_dir, wanted)
    except RuntimeError as e:
        logger.warning(f"[配置:{config_name}] {e}")
        return dest_path

    new_path = os.path.join(dest_dir, new_name)
    try:
        os.rename(dest_path, new_path)
    except OSError as e:
        index.release(dest_dir, new_name)
        logger.warning(f"[配置:{config_name}] 重命名失败（{dest_path} → {new_path}）：{e}")
        return dest_path

    index.move(dest_dir, dest_path, new_path)
    if new_name != wanted:
        logger.warning(f"[配置:{config_name}] 目标文件名冲突，改用：{new_name}")
    logger.info(f"[配置:{config_name}] 重命名：{os.path.basename(dest_path)} → {new_name}")
    return new_path


# ---------------------------------------------------------------------------
# 单文件处理
# ---------------------------------------------------------------------------
def process_single_file(file_path, config, rel_dir, target_dir,
                        processed_set=None, index=None):
    config_name = config.get("name", "未知")
    filename = os.path.basename(file_path)
    dest_dir = os.path.normpath(os.path.join(target_dir, rel_dir or "."))
    index = index if index is not None else TargetDirIndex()

    created_path = None
    stage = "link"
    policy = PermissionPolicy.from_config(config)
    mode = normalize_link_mode(config.get("link_mode"))
    try:
        if is_processed(processed_set, file_path, dest_dir):
            # 记录说处理过，但目标目录里的链接可能已被手动删除/改名：
            # 硬链接模式下用 inode 复核一次，目标真的不在了就重新处理。
            if mode == LINK_MODE_HARDLINK and not _has_link_in(file_path, dest_dir, index):
                logger.info(
                    f"[配置:{config_name}] 处理记录存在但目标链接已丢失，重新处理：{file_path}"
                )
            else:
                logger.info(f"[配置:{config_name}] 已处理过，跳过：{file_path}")
                return FileOutcome(STATUS_SKIPPED, "已处理过，跳过")

        link = create_link(
            file_path, dest_dir, config_name,
            index=index,
            mode=mode,
            policy=policy,
        )
        if not link.ok:
            return FileOutcome(STATUS_FAILED, link.message)
        if link.duplicate:
            record_processed(file_path, dest_dir, processed_set)
            return FileOutcome(STATUS_SKIPPED, link.message, link.action)

        created_path = link.path
        dest_path = link.path

        scrape = bool(config.get("scrape_metadata", True))
        do_rename = bool(config.get("rename_file", True))
        config_media_type = config.get("file_type", MEDIA_TYPE_MOVIE)
        if config_media_type not in MEDIA_TYPES:
            config_media_type = MEDIA_TYPE_MOVIE

        # 既不刮削也不重命名：只建链接
        if not scrape and not do_rename:
            record_processed(file_path, dest_dir, processed_set)
            return FileOutcome(STATUS_SUCCESS, "仅建立链接", link.action)

        file_info = parse_filename(filename, config_media_type)
        if not file_info:
            raise ValueError(f"无法解析文件名：{filename}")

        media_type = file_info.get("type", "unknown")
        if media_type == "unknown":
            media_type = config_media_type

        metadata = {}
        if scrape:
            stage = "scrape"
            if media_type in TV_LIKE_TYPES:
                metadata = fetch_metadata_cached(
                    file_info["title"], None,
                    config.get("tmdb_api_key", ""), media_type="tv_show",
                ) or {}
                if metadata:
                    metadata["season"] = file_info.get("season")
                    metadata["episode"] = file_info.get("episode")
            else:
                metadata = fetch_metadata_cached(
                    file_info["title"], file_info.get("year"),
                    config.get("tmdb_api_key", ""), media_type="movie",
                ) or {}
            if not metadata:
                raise LookupError(f"无法获取元数据：{filename}")

        ext = os.path.splitext(filename)[1]
        placeholders = build_rename_placeholders(
            metadata, file_info, config_media_type, ext
        )

        episode_info = {}
        if scrape and metadata.get("media_type") == "tv_show" and file_info.get("episode"):
            episode_info = fetch_episode_metadata(
                metadata.get("tmdbid"),
                file_info.get("season") or metadata.get("season") or "1",
                file_info.get("episode") or metadata.get("episode") or "1",
                config.get("tmdb_api_key", ""),
            ) or {}
            if episode_info:
                metadata["episode_title"] = episode_info.get("episode_title", "")
                placeholders["episode_title"] = (
                    episode_info.get("episode_title", "").replace(" ", "_")[:50]
                )
                metadata["overview"] = (
                    episode_info.get("episode_overview") or metadata.get("overview")
                )
                metadata["release_date"] = (
                    episode_info.get("episode_air_date") or metadata.get("release_date")
                )
                metadata["directors"] = (
                    episode_info.get("episode_directors") or metadata.get("directors")
                )
                metadata["guest_stars"] = episode_info.get("guest_stars", [])

        stage = "rename"
        if do_rename:
            dest_path = _apply_rename(
                dest_path, dest_dir, config, placeholders, index, config_name
            )

        if scrape and metadata:
            stage = "nfo"
            base_name_no_ext = os.path.splitext(os.path.basename(dest_path))[0]
            nfo_path = os.path.join(dest_dir, base_name_no_ext + ".nfo")
            if metadata.get("media_type") == "movie":
                generate_nfo(metadata, nfo_path, original_filename=filename)
            else:
                generate_tv_nfo(metadata, nfo_path, original_filename=filename)
                still_path = episode_info.get("still_path") if episode_info else None
                if still_path:
                    _download_still(still_path, dest_dir,
                                    base_name_no_ext + "-thumb.jpg", config_name)

            download_images(metadata, dest_dir, base_name_no_ext)

            if metadata.get("media_type") == "tv_show":
                _ensure_tvshow_assets(metadata, dest_dir, config_name)

            _apply_asset_permissions(dest_dir, base_name_no_ext, policy)

        record_processed(file_path, dest_dir, processed_set)
        return FileOutcome(STATUS_SUCCESS, "", link.action)

    except Exception as e:
        # 刮削/重命名前失败：回滚刚建立的链接，方便下次重试
        if created_path and stage in ("link", "scrape"):
            try:
                os.remove(created_path)
                index.remove(dest_dir, created_path)
                logger.info(f"[配置:{config_name}] 已回滚链接：{created_path}")
            except OSError:
                pass
        if isinstance(e, (ValueError, LookupError)):
            logger.warning(f"[配置:{config_name}] {e}")
        else:
            logger.error(f"[配置:{config_name}] 处理文件 {file_path} 出错：{e}",
                         exc_info=True)
        return FileOutcome(STATUS_FAILED, str(e))


def _apply_asset_permissions(dest_dir, base_name_no_ext, policy):
    """把文件权限补到本次生成的 .nfo / 海报等衍生文件上（只碰已知文件名）。"""
    if not policy.enabled:
        return
    names = [
        base_name_no_ext + ".nfo",
        base_name_no_ext + "-poster.jpg",
        base_name_no_ext + "-fanart.jpg",
        base_name_no_ext + "-clearlogo.png",
        base_name_no_ext + "-thumb.jpg",
        "tvshow.nfo",
        "poster.jpg",
    ]
    for name in names:
        path = os.path.join(dest_dir, name)
        if os.path.exists(path):
            _chmod(path, policy.file_mode)


def _download_still(still_path, dest_dir, thumb_name, config_name):
    thumb_path = os.path.join(dest_dir, thumb_name)
    if os.path.exists(thumb_path):
        return
    try:
        url = "https://image.tmdb.org/t/p/w500" + still_path
        r = requests.get(url, stream=True, timeout=10)
        r.raise_for_status()
        with open(thumb_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        logger.info(f"[配置:{config_name}] 下载单集缩略图：{thumb_path}")
    except Exception as e:
        logger.warning(f"[配置:{config_name}] 下载缩略图失败：{e}")


def _ensure_tvshow_assets(metadata, dest_dir, config_name):
    tvshow_nfo_path = os.path.join(dest_dir, "tvshow.nfo")
    tvshow_poster_path = os.path.join(dest_dir, "poster.jpg")
    if not os.path.exists(tvshow_nfo_path):
        generate_tvshow_nfo(metadata, tvshow_nfo_path)
    if not os.path.exists(tvshow_poster_path):
        temp_path = download_poster(metadata, dest_dir, "tvshow")
        if temp_path and os.path.exists(temp_path):
            try:
                os.rename(temp_path, tvshow_poster_path)
            except OSError as e:
                logger.warning(f"[配置:{config_name}] 重命名 poster.jpg 失败：{e}")


# ---------------------------------------------------------------------------
# 任务编排
# ---------------------------------------------------------------------------
def normalize_suffixes(value):
    """把配置里的后缀串规范化；为空时返回默认后缀串。"""
    text = (value or "").strip()
    if not text:
        return DEFAULT_FILE_SUFFIXES
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return DEFAULT_FILE_SUFFIXES
    # 统一成小写、去重，并给缺点的项补上点号
    normalized = []
    for part in parts:
        item = part.lower()
        if not item.startswith("."):
            item = "." + item
        if item not in normalized:
            normalized.append(item)
    return ",".join(normalized)


def suffix_set(value):
    """返回小写后缀集合；未配置时使用默认后缀。"""
    return {s for s in normalize_suffixes(value).split(",") if s}


def _is_within(path, parent):
    """path 是否等于 parent 或位于其内部（按真实路径判断）。"""
    if not path or not parent:
        return False
    try:
        path_real = os.path.realpath(path)
        parent_real = os.path.realpath(parent)
    except OSError:
        return False
    return path_real == parent_real or path_real.startswith(parent_real + os.sep)


def _device_of(path):
    """返回 path 所在文件系统的 st_dev；路径不存在时向上找最近的已存在祖先。"""
    if not path:
        return None
    current = os.path.abspath(path)
    while True:
        try:
            return os.stat(current).st_dev
        except OSError:
            parent = os.path.dirname(current)
            if parent == current:
                return None
            current = parent


def collect_tasks(config):
    """扫描配置中的源目录，返回 [(文件路径, 相对目录, 目标目录), ...]。

    目标目录若位于源目录内部（如源 /media、目标 /media/library），
    遍历时会跳过整棵目标子树，避免把已整理的文件再当成源文件自我嵌套。
    """
    paths = config.get("paths") or [
        {"source": config.get("download_dir"), "target": config.get("target_dir")}
    ]
    suffixes = suffix_set(config.get("file_suffixes"))
    config_name = config.get("name", "未知")
    tasks = []
    for mapping in paths:
        src, tgt = mapping.get("source"), mapping.get("target")
        if not src or not tgt or not os.path.isdir(src):
            if src:
                logger.warning(f"[配置:{config_name}] 源目录不存在：{src}")
            continue
        if _is_within(tgt, src):
            logger.warning(
                f"[配置:{config_name}] 目标目录位于源目录内部，扫描时会跳过它：{tgt}"
            )
        for root, dirs, files in os.walk(src):
            if _is_within(root, tgt):
                dirs[:] = []          # 不再深入目标目录
                continue
            rel = os.path.relpath(root, src)
            for f in files:
                if os.path.splitext(f)[1].lower() in suffixes:
                    tasks.append((os.path.join(root, f), rel, tgt))
    return tasks


def process_movies(config_or_download_dir, target_dir=None, tmdb_api_key=None,
                   progress_callback=None):
    """处理一个配置，返回统计字典。"""
    if isinstance(config_or_download_dir, dict):
        config = config_or_download_dir
    else:
        config = {
            "download_dir": config_or_download_dir,
            "target_dir": target_dir,
            "tmdb_api_key": tmdb_api_key,
            "file_suffixes": ".mp4,.mkv,.avi,.mov",
            "rename_rule": "{title}.{year}",
            "max_threads": 4,
        }

    config_name = config.get("name", "未知")

    # 硬链接要求源与目标在同一文件系统，提前一次性提示，而不是每个文件都失败
    if normalize_link_mode(config.get("link_mode")) == LINK_MODE_HARDLINK:
        mappings = config.get("paths") or [
            {"source": config.get("download_dir"), "target": config.get("target_dir")}
        ]
        for mapping in mappings:
            src_dev = _device_of(mapping.get("source"))
            tgt_dev = _device_of(mapping.get("target"))
            if src_dev and tgt_dev and src_dev != tgt_dev:
                logger.warning(
                    f"[配置:{config_name}] 源与目标不在同一文件系统："
                    f"{mapping.get('source')} -> {mapping.get('target')}，"
                    f"硬链接会全部失败，请把链接方式改为“复制”或“软链接”"
                )

    tasks = collect_tasks(config)
    total = len(tasks)
    stats = {STATUS_SUCCESS: 0, STATUS_SKIPPED: 0, STATUS_FAILED: 0}
    linked = 0
    failed = []

    if progress_callback:
        progress_callback("initialize", total)

    if not tasks:
        logger.info(f"[配置:{config_name}] 没有需要处理的文件")
        if progress_callback:
            progress_callback("complete", 0)
        return {"total": 0, **stats, "linked": 0, "failed_files": []}

    processed_set = load_processed_set()
    index = TargetDirIndex()
    workers = max(1, int(config.get("max_threads", 4) or 4))

    try:
        with ThreadPoolExecutor(max_workers=workers) as exe:
            futures = {
                exe.submit(process_single_file, f, config, rel, tgt, processed_set, index): f
                for f, rel, tgt in tasks
            }
            for fut in as_completed(futures):
                file_path = futures[fut]
                try:
                    outcome = fut.result()
                except Exception as e:  # 兜底，避免单个文件异常影响整体
                    outcome = FileOutcome(STATUS_FAILED, str(e))
                stats[outcome.status] = stats.get(outcome.status, 0) + 1
                if outcome.status == STATUS_SUCCESS:
                    linked += 1
                elif outcome.status == STATUS_FAILED:
                    failed.append((file_path, outcome.message))
                if progress_callback:
                    # 成功时上报具体动作（linked / copied / symlinked），否则上报状态
                    if outcome.status == STATUS_SUCCESS:
                        action = outcome.action or STATUS_SUCCESS
                    else:
                        action = outcome.status
                    progress_callback(
                        "update", 1,
                        outcome.status != STATUS_FAILED,
                        {"file": file_path, "message": outcome.message}
                        if outcome.status == STATUS_FAILED else None,
                        action=action,
                    )
    finally:
        flush_processed()      # 把缓冲的处理记录落盘，异常中断也不丢

    if progress_callback:
        progress_callback("complete", 0)
    logger.info(
        f"[配置:{config_name}] 共 {total} 个文件：成功 {stats[STATUS_SUCCESS]}，"
        f"跳过 {stats[STATUS_SKIPPED]}，失败 {stats[STATUS_FAILED]}"
    )
    return {
        "total": total,
        **stats,
        "linked": linked,
        "failed_files": failed,
    }
