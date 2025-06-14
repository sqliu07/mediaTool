from common_imports import *
from metadata_fetcher import fetch_metadata_cached, fetch_episode_metadata, download_poster, download_images
from nfo_generator import generate_nfo, generate_tv_nfo, generate_tvshow_nfo
from filename_parser import parse_filename

import subprocess

logger = logging.getLogger(__name__)
def load_processed_set():
    path = os.path.join("configs", "processed.txt")
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())
def process_single_file(file_path, config, rel_dir, target_dir, processed_set=None):
    try:
        logger.info(f"[设置目录权限:{target_dir}]")
        subprocess.run(['chmod', '-R', '777', target_dir], check=True)
        logger.info(f"[设置目录权限成功]")
    except subprocess.CalledProcessError as e:
            logger.error(f"[设置目录权限失败]：{e}")
    try:
        filename = os.path.basename(file_path)
        config_name = config.get("name", "未知")
        dest_dir = os.path.join(target_dir, rel_dir)
        # 判断是否已处理
        if processed_set is not None and file_path.strip() in processed_set:
            logger.info(f"[配置:{config_name}] 已处理文件，跳过写入：{file_path}")
            return True, "重复文件跳过"
        # === 只做硬链接（不抓元数据也不重命名） ===
        if not config.get("scrape_metadata", True) and not config.get("rename_file", True):
            dest_path = create_hardlink_if_needed(file_path, dest_dir, config_name)
            if not dest_path:
                return None  # 跳过记录，跳过计数
            
            record_path = os.path.join("configs", "processed.txt")
            os.makedirs(os.path.dirname(record_path), exist_ok=True)
            with open(record_path, "a", encoding="utf-8") as f:
                f.write(file_path.strip() + "\n")
            
            return True, ""

        # === 正常流程 ===
        dest_path = create_hardlink_if_needed(file_path, dest_dir, config_name)

        file_info = parse_filename(filename)
        if not file_info:
            return False, f"无法解析文件名：{filename}"

        media_type = file_info.get("type", "unknown")
        config_media_type = config.get("file_type", "movie")

        metadata = None
        if config.get("scrape_metadata", True):
            if media_type == "movie" or (media_type == "unknown" and config_media_type == "movie"):
                metadata = fetch_metadata_cached(file_info["title"], file_info.get("year"), config.get("tmdb_api_key", ""), media_type="movie")
            elif media_type == "tv_show" or (media_type == "unknown" and config_media_type == "tv_show"):
                metadata = fetch_metadata_cached(file_info["title"], None, config.get("tmdb_api_key", ""), media_type="tv_show")
                if metadata:
                    metadata["season"] = file_info.get("season")
                    metadata["episode"] = file_info.get("episode")
            if not metadata:
                return False, f"无法获取元数据：{filename}"

        rename_placeholders = {
            "title": metadata.get("title", file_info.get("title")),
            "year": (metadata.get("release_date") or file_info.get("year") or "Unknown")[:4],
            "season": file_info.get("season", "00"),
            "episode": file_info.get("episode", "00"),
            "episode_title": ""
        }
        rename_placeholders["season_episode"] = (
            f"S{rename_placeholders['season']}E{rename_placeholders['episode']}"
            if metadata and metadata.get("media_type") == "tv_show" else ""
        )

        episode_info = None
        if metadata and metadata.get("media_type") == "tv_show":
            episode_info = fetch_episode_metadata(
                metadata.get("tmdbid"),
                metadata.get("season") or file_info.get("season", "1"),
                metadata.get("episode") or file_info.get("episode", "1"),
                config.get("tmdb_api_key", "")
            )
            if episode_info:
                metadata["episode_title"] = episode_info.get("episode_title", "")
                rename_placeholders["episode_title"] = episode_info.get("episode_title", "").replace(" ", "_")[:50]
                metadata["overview"] = episode_info.get("episode_overview") or metadata.get("overview")
                metadata["release_date"] = episode_info.get("episode_air_date") or metadata.get("release_date")
                metadata["directors"] = episode_info.get("episode_directors") or metadata.get("directors")
                metadata["guest_stars"] = episode_info.get("guest_stars", [])

        new_filename = filename
        if config.get("rename_file", True):
            default_rule = "{title}.{year}" if config_media_type == "movie" else "{title}.{season_episode}"
            rename_rule = config.get("rename_rule", default_rule)
            try:
                base = rename_rule.format(**rename_placeholders).rstrip(".")
            except KeyError as e:
                logger.warning(f"[配置:{config_name}] 重命名规则错误：{e}，使用默认")
                base = default_rule.format(**rename_placeholders)
            new_filename = base + os.path.splitext(filename)[1]
            new_path = os.path.join(dest_dir, new_filename)

            if new_filename != filename and not os.path.exists(new_path):
                os.rename(dest_path, new_path)
                logger.info(f"[配置:{config_name}] 重命名媒体文件：{dest_path} -> {new_path}")
                dest_path = new_path

        base_name_no_ext = os.path.splitext(new_filename)[0]
        if config.get("scrape_metadata", True) and metadata:
            nfo_path = os.path.join(dest_dir, base_name_no_ext + ".nfo")
            if metadata.get("media_type") == "movie":
                generate_nfo(metadata, nfo_path, original_filename=filename)
            else:
                generate_tv_nfo(metadata, nfo_path, original_filename=filename)
                still_path = episode_info.get("still_path") if episode_info else None
                if still_path:
                    try:
                        thumb_url = "https://image.tmdb.org/t/p/w500" + still_path
                        thumb_path = os.path.join(dest_dir, base_name_no_ext + "-thumb.jpg")
                        if not os.path.exists(thumb_path):
                            r = requests.get(thumb_url, stream=True, timeout=10)
                            r.raise_for_status()
                            with open(thumb_path, "wb") as f:
                                for chunk in r.iter_content(8192):
                                    f.write(chunk)
                        logger.info(f"[配置:{config_name}] 下载单集缩略图：{thumb_path}")
                    except Exception as e:
                        logger.warning(f"[配置:{config_name}] 下载缩略图失败：{e}")

            download_images(metadata, dest_dir, base_name_no_ext)

            if metadata.get("media_type") == "tv_show":
                tvshow_nfo_path = os.path.join(dest_dir, "tvshow.nfo")
                tvshow_poster_path = os.path.join(dest_dir, "poster.jpg")
                if not os.path.exists(tvshow_nfo_path):
                    generate_tvshow_nfo(metadata, tvshow_nfo_path)
                if not os.path.exists(tvshow_poster_path):
                    temp_path = download_poster(metadata, dest_dir, "tvshow")
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.rename(temp_path, tvshow_poster_path)
                        except Exception as e:
                            logger.warning(f"[配置:{config_name}] 重命名 poster.jpg 失败：{e}")

        record_path = os.path.join("configs", "processed.txt")
        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        with open(record_path, "a", encoding="utf-8") as f:
            f.write(file_path.strip() + "\n")

        return True, ""
    except Exception as e:
        logger.error(f"[配置:{config.get('name', '未知')}] 处理文件 {file_path} 出错：{e}")
        return False, str(e)


def process_movies(config_or_download_dir, target_dir=None, tmdb_api_key=None, progress_callback=None):
    if isinstance(config_or_download_dir, dict):
        config = config_or_download_dir
    else:
        config = {
            "download_dir": config_or_download_dir,
            "target_dir": target_dir,
            "tmdb_api_key": tmdb_api_key,
            "file_suffixes": ".mp4,.mkv,.avi,.mov",
            "rename_rule": "{title}.{year}{ext}",
            "max_threads": 4
        }

    paths = config.get("paths", [{"source": config.get("download_dir"), "target": config.get("target_dir")}])
    suffixes = [s.strip().lower() for s in config.get("file_suffixes", "").split(",") if s.strip()]
    tasks, failed = [], []

    for m in paths:
        src, tgt = m["source"], m["target"]
        for root, _, files in os.walk(src):
            rel = os.path.relpath(root, src)
            for f in files:
                if os.path.splitext(f)[1].lower() in suffixes:
                    tasks.append((os.path.join(root, f), rel, tgt))

    total = len(tasks)
    if progress_callback: progress_callback("initialize", total)
    processed_set = load_processed_set()

    with ThreadPoolExecutor(max_workers=config.get("max_threads", 4)) as exe:
        
        futures = {exe.submit(process_single_file, f, config, rel, tgt, processed_set): f for f, rel, tgt in tasks}
        for fut in as_completed(futures):
            f = futures[fut]
            try:
                success, msg = fut.result()
                if not success:
                    failed.append((f, msg))
                    if progress_callback:
                        progress_callback("update", 1, False, {"file": f, "message": msg})
                elif progress_callback:
                    progress_callback("update", 1, True)
            except Exception as e:
                failed.append((f, str(e)))
                if progress_callback:
                    progress_callback("update", 1, False, {"file": f, "message": str(e)})

    if progress_callback: progress_callback("complete", 0)
    logger.info(f"[配置:{config.get('name', '未知')}] 总共处理：{total}，失败：{len(failed)}")

def create_hardlink_if_needed(src_path, dest_dir, config_name):
    """
    创建硬链接（如目标已存在则跳过），返回 (最终路径或 None, 消息)。
    """
    try:
        os.makedirs(dest_dir, exist_ok=True)
        filename = os.path.basename(src_path)
        dest_path = os.path.join(dest_dir, filename)

        # 源 inode
        try:
            source_inode = os.stat(src_path).st_ino
        except Exception as e:
            logger.warning(f"[配置:{config_name}] 获取源 inode 失败：{e}")
            source_inode = None

        # 目录内查找相同 inode
        if source_inode:
            for fname in os.listdir(dest_dir):
                fpath = os.path.join(dest_dir, fname)
                try:
                    if os.path.isfile(fpath) and os.stat(fpath).st_ino == source_inode:
                        logger.info(f"[配置:{config_name}] 已存在硬链接目标文件，跳过：{src_path}")
                        return None, "硬链接已存在"
                except Exception:
                    continue

        if os.path.exists(dest_path):
            logger.info(f"[配置:{config_name}] 目标路径已存在但 inode 不同，跳过：{dest_path}")
            return None, "同名文件已存在"

        os.link(src_path, dest_path)
        logger.info(f"[配置:{config_name}] 创建硬链接：{dest_path}")
        return dest_path, ""
    except Exception as e:
        logger.error(f"[配置:{config_name}] 创建硬链接失败：{e}")
        raise


