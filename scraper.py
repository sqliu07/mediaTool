import os
import re
import json
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime

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

import requests
import logging

logger = logging.getLogger(__name__)

def fetch_movie_metadata(movie_name, year, tmdb_api_key):
    """
    调用 TMDB API 获取电影详细信息，包括：
    - 标题、简介、评分、预告片、关键词等
    - 演职员（导演、编剧、制片人、演员）
    - 输出结构适配 generate_nfo() 函数
    """
    if not tmdb_api_key:
        logger.warning("未配置 TMDB API Key，跳过元数据抓取")
        return {}

    result = {}

    # Step 1: 搜索电影
    try:
        search_url = "https://api.themoviedb.org/3/search/movie"
        params = {"api_key": tmdb_api_key, "query": movie_name}
        if year:
            params["year"] = year

        resp = requests.get(search_url, params=params, timeout=10)
        resp.raise_for_status()
        movies = resp.json().get("results", [])
        if not movies:
            logger.warning(f"未在 TMDB 中找到电影【{movie_name}】")
            return {}

        movie_id = movies[0]["id"]
        result["tmdbid"] = movie_id
    except Exception as e:
        logger.error(f"TMDB 搜索接口出错：{e}")
        return {}

    # Step 2: 获取电影详情
    try:
        detail_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
        detail_resp = requests.get(detail_url, params={"api_key": tmdb_api_key, "language": "zh-CN"}, timeout=10)
        detail_resp.raise_for_status()
        data = detail_resp.json()

        result.update({
            "title": data.get("title"),
            "original_title": data.get("original_title"),
            "overview": data.get("overview"),
            "tagline": data.get("tagline"),
            "runtime": data.get("runtime"),
            "release_date": data.get("release_date"),
            "year": data.get("release_date", "")[:4],
            "genres": [g["name"] for g in data.get("genres", [])],
            "studio": ", ".join([c["name"] for c in data.get("production_companies", [])]),
            "spoken_languages": [l["name"] for l in data.get("spoken_languages", [])],
            "vote_average": data.get("vote_average"),
            "vote_count": data.get("vote_count"),
            "poster_path": data.get("poster_path"),
            "imdb_id": data.get("imdb_id"),
            "collection": {
                "name": data["belongs_to_collection"]["name"],
                "id": data["belongs_to_collection"]["id"]
            } if data.get("belongs_to_collection") else None
        })
    except Exception as e:
        logger.error(f"获取 TMDB 电影详情失败：{e}")

    # Step 3: 获取演职员信息
    try:
        credits_url = f"https://api.themoviedb.org/3/movie/{movie_id}/credits"
        credits_resp = requests.get(credits_url, params={"api_key": tmdb_api_key}, timeout=10)
        credits_resp.raise_for_status()
        credits = credits_resp.json()

        result["cast"] = [
            {
                "name": c["name"],
                "character": c.get("character", ""),
                "profile_path": c.get("profile_path"),
                "id": c["id"]
            }
            for c in credits.get("cast", [])[:20]
        ]

        result["directors"] = [
            {"name": p["name"], "id": p["id"]}
            for p in credits.get("crew", []) if p.get("job") == "Director"
        ]

        result["writers"] = [
            {"name": p["name"], "id": p["id"]}
            for p in credits.get("crew", []) if p.get("job") in ("Writer", "Screenplay", "Author")
        ]

        result["producers"] = [
            {"name": p["name"], "id": p["id"], "role": p.get("job", "")}
            for p in credits.get("crew", []) if "producer" in p.get("job", "").lower()
        ]
    except Exception as e:
        logger.error(f"获取 TMDB 演职员失败：{e}")

    # Step 4: 获取关键词
    try:
        keywords_url = f"https://api.themoviedb.org/3/movie/{movie_id}/keywords"
        kw_resp = requests.get(keywords_url, params={"api_key": tmdb_api_key}, timeout=10)
        kw_resp.raise_for_status()
        result["keywords"] = [k["name"] for k in kw_resp.json().get("keywords", [])]
    except Exception as e:
        logger.warning(f"获取 TMDB 关键词失败：{e}")

    # Step 5: 获取预告片链接（YouTube）
    try:
        videos_url = f"https://api.themoviedb.org/3/movie/{movie_id}/videos"
        video_resp = requests.get(videos_url, params={"api_key": tmdb_api_key}, timeout=10)
        video_resp.raise_for_status()
        for v in video_resp.json().get("results", []):
            if v["type"] == "Trailer" and v["site"] == "YouTube":
                result["trailer"] = f"plugin://plugin.video.youtube/play/?video_id={v['key']}"
                break
    except Exception as e:
        logger.warning(f"获取 TMDB 视频失败：{e}")

    # Step 6: 获取中文翻译（可选）
    try:
        translations_url = f"https://api.themoviedb.org/3/movie/{movie_id}/translations"
        trans_resp = requests.get(translations_url, params={"api_key": tmdb_api_key}, timeout=10)
        trans_resp.raise_for_status()
        translations = trans_resp.json().get("translations", [])
        for t in translations:
            if t["iso_639_1"] == "zh" and t.get("data", {}).get("title"):
                result["title"] = t["data"]["title"]
                break
    except Exception as e:
        logger.warning(f"获取 TMDB 中文翻译失败：{e}")

    return result

def download_poster(metadata, target_dir, movie_name):
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
def generate_nfo(metadata, output_path, original_filename=None):
    logger.info(f"正在生成nfo...")
    def create_element(parent, tag, text=None, attrib=None):
        elem = ET.SubElement(parent, tag)
        if text:
            elem.text = text
        if attrib:
            for k, v in attrib.items():
                elem.set(k, v)
        return elem

    movie = ET.Element('movie')
    movie.insert(0, ET.Comment(f"created on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} by tinyMediaManager 5.1.4 for JELLYFIN"))

    create_element(movie, 'title', metadata.get('title'))
    create_element(movie, 'originaltitle', metadata.get('original_title') or metadata.get('title'))
    create_element(movie, 'sorttitle', '')
    create_element(movie, 'epbookmark')
    create_element(movie, 'year', str(metadata.get('year') or ''))

    # Ratings
    ratings = create_element(movie, 'ratings')
    rating = create_element(ratings, 'rating', attrib={'default': 'false', 'max': '10', 'name': 'themoviedb'})
    create_element(rating, 'value', str(metadata.get('vote_average', 0)))
    create_element(rating, 'votes', str(metadata.get('vote_count', 0)))
    create_element(movie, 'userrating', '0')
    create_element(movie, 'top250', '0')

    # # Set info
    if metadata.get('collection'):
        set_tag = create_element(movie, 'set')
        create_element(set_tag, 'name', metadata['collection']['name'])
        create_element(set_tag, 'overview', '')

    create_element(movie, 'plot', metadata.get('overview', ''))
    create_element(movie, 'outline', metadata.get('overview', ''))
    create_element(movie, 'tagline', metadata.get('tagline', ''))
    create_element(movie, 'runtime', str(int(metadata.get('runtime', 0))))
    create_element(movie, 'mpaa', metadata.get('certification', ''))
    create_element(movie, 'certification', metadata.get('certification', ''))

    create_element(movie, 'id', metadata.get('imdb_id', ''))
    create_element(movie, 'tmdbid', str(metadata.get('id', '')))

    # uniqueid_list = [
    #     {'type': 'tmdb', 'default': 'false', 'id': metadata.get('id')},
    #     {'type': 'tmdbSet', 'default': 'false', 'id': metadata.get('collection', {}).get('id')},
    #     {'type': 'imdb', 'default': 'true', 'id': metadata.get('imdb_id')},
    #     {'type': 'wikidata', 'default': 'false', 'id': metadata.get('wikidata_id')}
    # ]
    # for uid in uniqueid_list:
    #     if uid['id']:
    #         create_element(movie, 'uniqueid', str(uid['id']), attrib={'type': uid['type'], 'default': uid['default']})

    # create_element(movie, 'country', ', '.join(metadata.get('production_countries', [])))
    # create_element(movie, 'status')
    # create_element(movie, 'code')
    # create_element(movie, 'premiered', metadata.get('release_date', ''))
    # create_element(movie, 'watched', 'false')
    # create_element(movie, 'playcount', '0')

    #Genres
    for genre in metadata.get('genres', []):
        create_element(movie, 'genre', genre)

    create_element(movie, 'studio', metadata.get('studio', ''))

    # Credits (writers)
    for writer in metadata.get('writers', []):
        create_element(movie, 'credits', writer['name'], attrib={'tmdbid': str(writer['id'])})

    # Directors
    for director in metadata.get('directors', []):
        create_element(movie, 'director', director['name'], attrib={'tmdbid': str(director['id'])})

    # Tags
    for tag in metadata.get('keywords', []):
        create_element(movie, 'tag', tag)

    # Actors
    for actor in metadata.get('cast', []):
        actor_tag = create_element(movie, 'actor')
        create_element(actor_tag, 'name', actor['name'])
        create_element(actor_tag, 'role', actor.get('character', ''))
        create_element(actor_tag, 'profile', f"https://www.themoviedb.org/person/{actor['id']}")
        create_element(actor_tag, 'tmdbid', str(actor['id']))

    # Producers
    for producer in metadata.get('producers', []):
        prod_tag = create_element(movie, 'producer', attrib={'tmdbid': str(producer['id'])})
        create_element(prod_tag, 'name', producer['name'])
        create_element(prod_tag, 'role', producer.get('role', ''))
        create_element(prod_tag, 'profile', f"https://www.themoviedb.org/person/{producer['id']}")

    # Trailer
    if metadata.get('trailer'):
        create_element(movie, 'trailer', metadata['trailer'])

    # Languages
    if metadata.get('spoken_languages'):
        langs = ', '.join(metadata['spoken_languages'])
        create_element(movie, 'languages', langs)

    # Date added
    create_element(movie, 'dateadded', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    # File info (mocked for now; can be extended from mediainfo)
    fileinfo = create_element(movie, 'fileinfo')
    streamdetails = create_element(fileinfo, 'streamdetails')
    video = create_element(streamdetails, 'video')
    create_element(video, 'codec', 'h264')
    create_element(video, 'aspect', '2.4')
    create_element(video, 'width', '1920')
    create_element(video, 'height', '804')
    create_element(video, 'durationinseconds', str(metadata.get('runtime', 0) * 60))

    audio = create_element(streamdetails, 'audio')
    create_element(audio, 'codec', 'EAC3')
    create_element(audio, 'language', 'eng')
    create_element(audio, 'channels', '8')

    subtitle = create_element(streamdetails, 'subtitle')
    create_element(subtitle, 'language', 'eng')

    # TinyMediaManager metadata
    create_element(movie, 'source', 'WEBRIP')
    create_element(movie, 'edition', 'NONE')
    create_element(movie, 'original_filename', original_filename or '')
    create_element(movie, 'user_note')

    rough_string = ET.tostring(movie, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    pretty_xml = reparsed.toprettyxml(indent="  ")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(pretty_xml)

def process_single_file(file_path, config, rel_dir, target_dir):
    """
    对单个电影文件进行处理：
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
        logger.debug(f"创建目录：{dest_dir}")

        dest_path = os.path.join(dest_dir, filename)

        # 创建硬链接（若目标文件不存在）
        if not os.path.exists(dest_path):
            os.link(file_path, dest_path)
            logger.info(f"创建硬链接：{file_path} -> {dest_path}")
        else:
            logger.info(f"硬链接已存在：{dest_path}")

        # 分析电影信息
        movie_name, year = parse_movie_filename(filename)
        logger.debug(f"解析文件名: {filename}，电影名: {movie_name}, 年份: {year}")
        metadata = fetch_movie_metadata(movie_name, year, config.get("tmdb_api_key", ""))

        if not metadata:
            error_msg = f"无法获取电影元数据：{filename}"
            logger.warning(error_msg)
            return False, error_msg

        # 根据重命名规则重命名文件
        if "title" in metadata:
            new_name = config.get("rename_rule", "{title}{year}").format(
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
                error_msg = f"重命名目标文件已存在：{new_path}"
                logger.warning(error_msg)
                # return False, error_msg

            # 生成 nfo 文件（与电影同名）
            nfo_filename = f"{os.path.splitext(new_filename)[0]}.nfo"
            nfo_path = os.path.join(dest_dir, nfo_filename)
            logger.debug(f"生成 nfo 文件：{nfo_path}")
            generate_nfo(metadata, nfo_path, filename)

            # 下载并保存海报，使用新的文件名
            logger.debug(f"下载海报并保存到：{dest_dir}")
            poster_path = download_poster(metadata, dest_dir, new_filename)
            if not poster_path:
                logger.warning(f"未能下载海报：{new_filename}")

        return True, ""

    except Exception as e:
        error_msg = f"处理文件 {file_path} 出错：{str(e)}"
        logger.error(error_msg)
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
            logger.warning(f"  - 文件：{fp}")
            logger.warning(f"    错误：{error}")
    
    if progress_callback:
        progress_callback("complete", 0)
    
    logger.info(f"全部文件处理完毕！成功：{total - len(failed_files)}，失败：{len(failed_files)}")

