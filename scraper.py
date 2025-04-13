import os
import re
import json
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed



logger = logging.getLogger("movie_manager")

def parse_movie_filename(filename):
    """
    从电影文件名中提取信息，示例：
      - "The.Matrix.1999.mkv" => ("The Matrix", "1999")
      - "The Matrix (1999).mp4" => ("The Matrix", "1999")
    """
    name, _ = os.path.splitext(filename)
    # 替换点为空格
    name = name.replace('.', ' ')
    # 使用正则匹配年份 (4位数字)
    m = re.search(r'(.*?)[\s\(]?(\d{4})[\)\s]?', name)
    if m:
        movie_name = m.group(1).strip()
        year = m.group(2)
        return movie_name, year
    return name.strip(), None

def fetch_movie_metadata(movie_name, year, tmdb_api_key):
    """
    调用 TMDB API 获取电影详细信息
    如果需要使用 TinyMediaManager （TMM）或其他专业 API，可在此扩展。
    注意：tmdb_api_key 必须预先配置。
    """
    if not tmdb_api_key:
        logger.warning("未配置 TMDB API Key，跳过元数据抓取")
        return {}
    search_url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": tmdb_api_key, "query": movie_name}
    if year:
        params["year"] = year
    try:
        resp = requests.get(search_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if results:
            logger.info(f"获取到电影【{movie_name}】的元数据")
            return results[0]
        else:
            logger.warning(f"未在 TMDB 中找到电影【{movie_name}】")
    except Exception as e:
        logger.error(f"调用 TMDB API 出错：{e}")
    return {}

def download_poster(metadata, target_dir, movie_name, year, config):
    """
    下载电影海报并使用重命名规则生成与电影文件一致的海报文件名
    """
    poster_path = ""
    try:
        poster_url = metadata.get('poster_path')
        if poster_url:
            # TMDB 海报 URL 基础地址
            base_url = "https://image.tmdb.org/t/p/w500"
            full_url = base_url + poster_url
            # 下载海报
            response = requests.get(full_url, stream=True)
            # if response.status_code == 200:
            #     # 使用重命名规则生成海报文件名
            #     new_filename = config.get("rename_rule", "{title} ({year})").format(
            #         title=movie_name,
            #         year=year
            #     )
            poster_filename = f"{movie_name}-poster.jpg"
            poster_path = os.path.join(target_dir, poster_filename)
            with open(poster_path, 'wb') as poster_file:
                for chunk in response.iter_content(1024):
                    poster_file.write(chunk)
            logger.info(f"下载并保存海报：{poster_path}")
            # else:
            #     logger.warning(f"无法下载海报，状态码：{response.status_code}")
    except Exception as e:
        logger.error(f"下载海报时出错：{e}")
    return poster_path



def generate_nfo(metadata, output_path):
    """
    根据电影 metadata 生成简单的 nfo 文件（XML 格式）
    """
    xml_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<movie>
    <title>{metadata.get('title', '')}</title>
    <originaltitle>{metadata.get('original_title', '')}</originaltitle>
    <year>{metadata.get('release_date','')[:4]}</year>
    <plot>{metadata.get('overview','')}</plot>
    <poster>{metadata.get('poster_path', '')}</poster>
</movie>
"""
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(xml_content)
        logger.info(f"生成 NFO 文件：{output_path}")
    except Exception as e:
        logger.error(f"写入 NFO 文件 {output_path} 出错：{e}")

def process_single_file(file_path, config, rel_dir):
    """
    对单个电影文件进行处理：
      1. 在目标目录建立目录结构；
      2. 创建硬链接；
      3. 调用 API 获取元数据，生成 nfo 文件；
      4. 下载并保存海报；
      5. 根据重命名规则对文件重命名。
    """
    try:
        filename = os.path.basename(file_path)
        dest_dir = os.path.join(config["target_dir"], rel_dir)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, filename)

        # 创建硬链接（若目标文件不存在）
        if not os.path.exists(dest_path):
            os.link(file_path, dest_path)
            logger.info(f"创建硬链接：{file_path} -> {dest_path}")
        else:
            logger.info(f"硬链接已存在：{dest_path}")

        # 分析电影信息
        movie_name, year = parse_movie_filename(filename)
        metadata = fetch_movie_metadata(movie_name, year, config.get("tmdb_api_key", ""))

        # 根据重命名规则重命名文件
        if metadata and "title" in metadata:
            # 使用重命名规则，若year缺失则置为Unknown
            new_name = config.get("rename_rule", "{title} ({year})").format(
                title=metadata.get("title", movie_name),
                year=metadata.get("release_date", "Unknown")[:4] if metadata.get("release_date") else "Unknown"
            )
            new_ext = os.path.splitext(filename)[1]
            new_filename = new_name + new_ext
            new_path = os.path.join(dest_dir, new_filename)
            # 如果新文件名不存在则执行重命名操作
            if not os.path.exists(new_path):
                os.rename(dest_path, new_path)
                logger.info(f"重命名文件：{dest_path} -> {new_path}")
            else:
                logger.warning(f"重命名目标文件已存在：{new_path}")

        # 生成 nfo 文件（与电影同名）
        nfo_filename = f"{os.path.splitext(new_filename)[0]}.nfo"
        nfo_path = os.path.join(dest_dir, nfo_filename)
        generate_nfo(metadata, nfo_path)
        
        # 下载并保存海报，使用新的文件名
        download_poster(metadata, dest_dir, new_filename, year, config)

    except Exception as e:
        logger.error(f"处理文件 {file_path} 出错：{e}")

def process_movies(config_or_download_dir, target_dir=None, tmdb_api_key=None):
    """
    扫描下载目录，并对符合条件的文件进行并发处理。
    支持调用时传入单个 config（字典），否则按老接口兼容三个参数。
    配置参数包括：
      - download_dir: 硬链接源目录
      - target_dir: 目标目录
      - tmdb_api_key: TMDB API Key
      - file_suffixes: 以逗号分隔的后缀列表，例如 ".mp4,.mkv"
      - max_threads: 并发处理线程数
    """
    # 支持两种调用方式：传入配置字典或分别传参
    if isinstance(config_or_download_dir, dict):
        config = config_or_download_dir
    else:
        config = {
            "download_dir": config_or_download_dir,
            "target_dir": target_dir,
            "tmdb_api_key": tmdb_api_key,
            "file_suffixes": ".mp4,.mkv,.avi,.mov",
            "rename_rule": "{title} ({year})",
            "max_threads": 4
        }
        
    download_dir = config["download_dir"]
    
    if not os.path.isdir(download_dir):
        raise Exception(f"下载目录不存在：{download_dir}")
    
    os.makedirs(config["target_dir"], exist_ok=True)
    
    # 将 file_suffixes 转换为列表，并统一小写
    suffixes = [s.strip().lower() for s in config.get("file_suffixes", "").split(",") if s.strip()]
    
    tasks = []
    with ThreadPoolExecutor(max_workers=config.get("max_threads", 4)) as executor:
        for root, dirs, files in os.walk(download_dir):
            # 获取相对路径用于复制目录结构
            rel_dir = os.path.relpath(root, download_dir)
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in suffixes:
                    file_path = os.path.join(root, file)
                    tasks.append(executor.submit(process_single_file, file_path, config, rel_dir))
        # 等待所有任务完成
        for future in as_completed(tasks):
            try:
                future.result()
            except Exception as e:
                logger.error(f"线程任务异常：{e}")
    logger.info("全部电影文件处理完毕！")
