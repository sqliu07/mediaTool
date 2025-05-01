from common_imports import *
from metadata_fetcher import fetch_metadata_cached, download_poster
from nfo_generator import generate_nfo, generate_tv_nfo
from filename_parser import parse_filename

logger = logging.getLogger(__name__)

def process_single_file(file_path, config, rel_dir, target_dir):
    """
    对单个媒体文件进行处理：
      1. 在目标目录建立目录结构；
      2. 创建硬链接；
      3. 调用 API 获取元数据，生成 nfo 文件；
      4. 下载并保存海报；
      5. 根据重命名规则对文件重命名。
    返回：
      (bool, str) - (是否成功, 错误信息)
    """
    try:
        filename = os.path.basename(file_path)
        dest_dir = os.path.join(target_dir, rel_dir)

        # 确保目录存在
        os.makedirs(dest_dir, exist_ok=True)
        logger.info(f"[配置:{config.get('name', '未知')}] 创建目录：{dest_dir}")

        dest_path = os.path.join(dest_dir, filename)

        # 创建硬链接（若目标文件不存在）
        if not os.path.exists(dest_path):
            os.link(file_path, dest_path)
            logger.info(f"[配置:{config.get('name', '未知')}] 创建硬链接：{file_path} -> {dest_path}")
        else:
            logger.info(f"[配置:{config.get('name', '未知')}] 硬链接已存在：{dest_path}")

        # 分析文件信息
        file_info = parse_filename(filename) # <--- 使用新的解析函数
        logger.info(f"[配置:{config.get('name', '未知')}] 解析文件名: {filename}，信息: {file_info}")

        if not file_info:
            error_msg = f"无法解析文件名：{filename}"
            logger.warning(error_msg)
            return False, error_msg

        media_type = file_info.get('type', 'unknown')
        config_media_type = config.get('file_type', 'movie') # 从配置获取期望类型

        metadata = None
        if media_type == 'movie' or (media_type == 'unknown' and config_media_type == 'movie'):
            movie_name = file_info.get('title')
            year = file_info.get('year')
            logger.info(f"[配置:{config.get('name', '未知')}] 作为电视剧处理: {movie_name} ({year})")
            # 使用新的函数名 fetch_metadata 并传递 media_type='movie'
            metadata = fetch_metadata_cached(movie_name, year, config.get("tmdb_api_key", ""), media_type='movie')
            if not metadata:
                error_msg = f"无法获取电影元数据：{filename}"
                logger.warning(error_msg)
                return False, error_msg # 获取元数据失败则终止

        elif media_type == 'tv_show' or (media_type == 'unknown' and config_media_type == 'tv_show'):
            show_name = file_info.get('title')
            # season 和 episode 现在是字符串
            season = file_info.get('season')
            episode = file_info.get('episode')
            # 调整日志格式，直接使用字符串
            logger.info(f"[配置:{config.get('name', '未知')}] 作为电视剧处理: {show_name} S{season}E{episode}")
            # 调用 fetch_metadata 获取电视剧元数据
            # 注意：电视剧搜索通常不依赖年份，所以第二个参数传 None
            metadata = fetch_metadata_cached(show_name, None, config.get("tmdb_api_key", ""), media_type='tv_show')
            if not metadata:
                # 如果获取剧集元数据失败，可以尝试只用剧名搜索剧集信息（不含季/集）
                # 这对于生成剧集级别的 NFO 可能有用
                logger.warning(f"[配置:{config.get('name', '未知')}] 无法获取电视剧元数据：{filename}，尝试仅获取剧集信息...")
                metadata = fetch_metadata_cached(show_name, None, config.get("tmdb_api_key", ""), media_type='tv_show')
                if not metadata:
                    error_msg = f"无法获取电视剧元数据：{filename}"
                    logger.warning(error_msg)
                    return False, error_msg # 获取元数据失败则终止
                else:
                    # 即使只获取到剧集信息，也记录一下季和集信息，方便后续使用
                    metadata['season'] = season # 保存字符串
                    metadata['episode'] = episode # 保存字符串
            else:
                 # 如果获取成功，也记录一下季和集信息
                 metadata['season'] = season # 保存字符串
                 metadata['episode'] = episode # 保存字符串


        else: # Unknown type and unknown config, or mismatch handling
             error_msg = f"无法确定文件类型或与配置不符：{filename}"
             logger.warning(error_msg)
             return False, error_msg


        # --- 后续处理（重命名、NFO、海报）基于获取到的 metadata ---
        if metadata:
            # 根据重命名规则重命名文件
            # 注意：重命名规则可能需要根据电影/电视剧调整
            # 例如，电视剧可能需要 {title}.S{season:02d}E{episode:02d}
            # 当前实现主要针对电影
            rename_placeholders = {
                "title": metadata.get("title", file_info.get("title")),
                "year": metadata.get("release_date", "Unknown")[:4] if metadata.get("release_date") else file_info.get("year", "Unknown"),
                # 获取字符串类型的 season 和 episode，提供默认值
                "season": file_info.get("season", "00"),
                "episode": file_info.get("episode", "00"),
            }
            # 调整 season_episode 格式化，直接拼接字符串
            rename_placeholders["season_episode"] = f"S{rename_placeholders['season']}E{rename_placeholders['episode']}" if metadata.get('media_type') == 'tv_show' else ""

            # 根据媒体类型选择重命名规则，或让用户在配置中指定
            # 注意：如果用户配置的 rename_rule 包含 {season:02d} 这种格式，需要告知用户修改
            default_rename_rule = "{title}.{year}" if metadata.get('media_type') == 'movie' else "{title}.{season_episode}"
            rename_rule = config.get("rename_rule", default_rename_rule)

            try:
                # 使用 .format(**dict) 传递所有占位符
                new_name_base = rename_rule.format(**rename_placeholders)
            except KeyError as e:
                 logger.warning(f"[配置:{config.get('name', '未知')}] 重命名规则 '{rename_rule}' 包含无效占位符 {e} for {filename}. 使用默认规则。")
                 new_name_base = default_rename_rule.format(**rename_placeholders)


            new_ext = os.path.splitext(filename)[1]
            new_filename = new_name_base + new_ext
            new_path = os.path.join(dest_dir, new_filename)

            nfo_filename = f"{os.path.splitext(new_filename)[0]}.nfo"
            nfo_path = os.path.join(dest_dir, nfo_filename)
            poster_path = os.path.join(dest_dir, os.path.splitext(new_filename)[0] + "-poster.jpg")  # 注意：你的 download_poster 就是这么命名的

            if all(os.path.exists(p) for p in [new_path, nfo_path, poster_path]):
                logger.info(f"[配置:{config.get('name', '未知')}] 跳过已处理文件：{new_filename}")
                return True, ""

            # 如果新文件名不存在则执行重命名操作
            current_file_to_rename = dest_path # 初始硬链接路径
            if os.path.exists(new_path) and current_file_to_rename != new_path:
                error_msg = f"重命名目标文件已存在：{new_path}"
                logger.warning(error_msg)
                # 如果目标存在，后续操作（NFO/海报）应基于新文件名
                current_file_to_rename = new_path # 指向已存在的目标文件
                # return False, error_msg # 或者选择报错退出
            elif current_file_to_rename != new_path:
                try:
                    os.rename(current_file_to_rename, new_path)
                    logger.info(f"重命名文件：{current_file_to_rename} -> {new_path}")
                    current_file_to_rename = new_path # 更新当前文件名
                except OSError as e:
                    error_msg = f"重命名文件失败: {e}"
                    logger.error(error_msg)
                    return False, error_msg


            # 生成 nfo 文件（与媒体文件同名）
            nfo_filename = f"{os.path.splitext(new_filename)[0]}.nfo"
            nfo_path = os.path.join(dest_dir, nfo_filename)
            logger.info(f"[配置:{config.get('name', '未知')}] 生成 nfo 文件：{nfo_path}")
            # 根据类型调用不同的 NFO 生成器
            nfo_generated = False
            if metadata.get('media_type') == 'movie': # 使用 metadata 中的类型判断更可靠
                 nfo_generated = generate_nfo(metadata, nfo_path, original_filename=filename)
            elif metadata.get('media_type') == 'tv_show':
                 # 调用 generate_tv_nfo 函数
                 nfo_generated = generate_tv_nfo(metadata, nfo_path, original_filename=filename)

            if not nfo_generated:
                 logger.warning(f"[配置:{config.get('name', '未知')}] 未能生成 NFO 文件 for {new_filename}")


            # 下载并保存海报，使用新的文件名
            logger.info(f"[配置:{config.get('name', '未知')}] 下载海报并保存到：{dest_dir}")
            poster_path = download_poster(metadata, dest_dir, os.path.splitext(new_filename)[0]) # 传递不带扩展名的文件名
            if not poster_path:
                logger.warning(f"[配置:{config.get('name', '未知')}] 未能下载海报：{new_filename}")

        return True, ""

    except Exception as e:
        error_msg = f"处理文件 {file_path} 出错：{str(e)}"
        logger.exception(error_msg) # 使用 exception 记录堆栈信息
        return False, error_msg

def process_movies(config_or_download_dir, target_dir=None, tmdb_api_key=None, progress_callback=None):
    """
    扫描并并发处理所有匹配的文件，
    支持 progress_callback(event, value)
    """
    # 兼容旧调用
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
    suffixes = [s.strip().lower() for s in config.get("file_suffixes","").split(",") if s.strip()]

    # 收集所有待处理文件
    tasks = []
    failed_files = []  # 记录处理失败的文件
    
    for m in paths:
        src = m["source"]
        tgt = m["target"]
        for root, _, files in os.walk(src):
            rel = os.path.relpath(root, src)
            for f in files:
                if os.path.splitext(f)[1].lower() in suffixes:
                    tasks.append((os.path.join(root, f), rel, tgt))

    total = len(tasks)
    if progress_callback:
        progress_callback("initialize", total)

    # 并发处理
    with ThreadPoolExecutor(max_workers=config.get("max_threads",4)) as exe:
        future_to_file = {
            exe.submit(process_single_file, fp, config, rel, tgt): fp
            for fp, rel, tgt in tasks
        }
        for fut in as_completed(future_to_file):
            fp = future_to_file[fut]
            try:
                success, error_msg = fut.result()
                if not success:
                    failed_files.append((fp, error_msg))
                    if progress_callback:
                        progress_callback("update", 1, False, {
                            "file": fp,
                            "message": error_msg
                        })
                else:
                    if progress_callback:
                        progress_callback("update", 1, True)
            except Exception as e:
                error_msg = f"处理文件 {fp} 出错：{str(e)}"
                failed_files.append((fp, error_msg))
                if progress_callback:
                    progress_callback("update", 1, False, {
                        "file": fp,
                        "message": error_msg
                    })

    # 输出失败文件的汇总信息
    if failed_files:
        logger.warning("以下文件处理失败：")
        for fp, error in failed_files:
            logger.warning(f"[配置:{config.get('name', '未知')}]   - 文件：{fp}")
            logger.warning(f"[配置:{config.get('name', '未知')}]     错误：{error}")
    
    if progress_callback:
        progress_callback("complete", 0)
    
    logger.info(f"[配置:{config.get('name', '未知')}] 全部文件处理完毕！成功：{total - len(failed_files)}，失败：{len(failed_files)}")

