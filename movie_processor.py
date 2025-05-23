from common_imports import *
from metadata_fetcher import fetch_metadata_cached, fetch_episode_metadata, download_poster, download_images
from nfo_generator import generate_nfo, generate_tv_nfo, generate_tvshow_nfo
from filename_parser import parse_filename

logger = logging.getLogger(__name__)

def process_single_file(file_path, config, rel_dir, target_dir):
    try:
        filename = os.path.basename(file_path)
        dest_dir = os.path.join(target_dir, rel_dir)
        os.makedirs(dest_dir, exist_ok=True)

        # === 处理记录检查 ===
        record_path = os.path.join("configs", "processed.txt")
        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        if os.path.exists(record_path):
            with open(record_path, "r", encoding="utf-8") as f:
                if file_path.strip() in f.read():
                    logger.info(f"[配置:{config.get('name')}] 已记录处理过该文件，跳过：{file_path}")
                    return True, ""

        # === inode 检查 ===
        try:
            source_inode = os.stat(file_path).st_ino
            for fname in os.listdir(dest_dir):
                fpath = os.path.join(dest_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                if os.stat(fpath).st_ino == source_inode:
                    logger.info(f"[配置:{config.get('name')}] 已存在硬链接目标文件，跳过：{file_path}")
                    return True, ""
        except Exception as e:
            logger.warning(f"[inode 检查失败]：{e}")

        filename = os.path.basename(file_path)
        dest_dir = os.path.join(target_dir, rel_dir)
        os.makedirs(dest_dir, exist_ok=True)

        dest_path = os.path.join(dest_dir, filename)
        if not os.path.exists(dest_path):
            os.link(file_path, dest_path)
            logger.info(f"[配置:{config.get('name', '未知')}] 创建硬链接：{dest_path}")
        else:
            logger.info(f"[配置:{config.get('name', '未知')}] 硬链接已存在：{dest_path}")

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
            if metadata.get("media_type") == "tv_show" else ""
        )

        # 获取单集元数据（用于 episode_title）
        if metadata.get("media_type") == "tv_show":
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

        # 重命名主文件
        new_filename = filename
        if config.get("rename_file", True):
            default_rule = "{title}.{year}" if config_media_type == "movie" else "{title}.{season_episode}"
            rename_rule = config.get("rename_rule", default_rule)
            try:
                base = rename_rule.format(**rename_placeholders).rstrip(".")
            except KeyError as e:
                logger.warning(f"[配置:{config.get('name')}] 重命名规则错误：{e}，使用默认")
                base = default_rule.format(**rename_placeholders)
            new_filename = base + os.path.splitext(filename)[1]
            new_path = os.path.join(dest_dir, new_filename)

            if new_filename != filename and not os.path.exists(new_path):
                os.rename(dest_path, new_path)
                logger.info(f"[配置:{config.get('name', '未知')}] 重命名媒体文件：{dest_path} -> {new_path}")
                dest_path = new_path

            old_base = os.path.splitext(filename)[0]
            new_base = os.path.splitext(new_filename)[0]

            for suffix in [".nfo", "-poster.jpg"]:
                old_file = os.path.join(dest_dir, old_base + suffix)
                if os.path.exists(old_file):
                    try:
                        os.remove(old_file)
                        logger.info(f"[配置:{config.get('name')}] 已删除旧附属文件：{old_file}")
                    except Exception as e:
                        logger.warning(f"[配置:{config.get('name')}] 无法删除旧附属文件 {old_file}：{e}")

            for suffix in [".nfo", "-poster.jpg"]:
                src = os.path.join(dest_dir, old_base + suffix)
                dst = os.path.join(dest_dir, new_base + suffix)
                if os.path.exists(src) and not os.path.exists(dst):
                    os.rename(src, dst)
                    logger.info(f"[配置:{config.get('name')}] 重命名附属文件：{src} -> {dst}")

        # 生成 NFO / poster / thumb
        if config.get("scrape_metadata", True) and metadata:
            base_name_no_ext = os.path.splitext(new_filename)[0]
            nfo_path = os.path.join(dest_dir, base_name_no_ext + ".nfo")
            poster_path = os.path.join(dest_dir, base_name_no_ext + "-poster.jpg")

            logger.info(f"[配置:{config.get('name', '未知')}] 生成 nfo: {nfo_path}")

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
                            logger.info(f"[配置:{config.get('name', '未知')}] 下载单集缩略图：{thumb_path}")
                    except Exception as e:
                        logger.warning(f"[配置:{config.get('name', '未知')}] 下载缩略图失败：{e}")

            download_images(metadata, dest_dir, base_name_no_ext)

            if metadata.get("media_type") == "tv_show":
                tvshow_nfo_path = os.path.join(dest_dir, "tvshow.nfo")
                tvshow_poster_path = os.path.join(dest_dir, "poster.jpg")
                if not os.path.exists(tvshow_nfo_path):
                    generate_tvshow_nfo(metadata, tvshow_nfo_path)
                if not os.path.exists(tvshow_poster_path):
                    temp_path = download_poster(metadata, dest_dir, "tvshow")
                    try:
                        if os.path.exists(temp_path):
                            os.rename(temp_path, tvshow_poster_path)
                            logger.info(f"[配置:{config.get('name', '未知')}] 重命名剧集封面：{temp_path} -> {tvshow_poster_path}")
                    except Exception as e:
                        logger.warning(f"[配置:{config.get('name', '未知')}] 重命名 poster.jpg 失败：{e}")

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

    with ThreadPoolExecutor(max_workers=config.get("max_threads", 4)) as exe:
        futures = {exe.submit(process_single_file, f, config, rel, tgt): f for f, rel, tgt in tasks}
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
