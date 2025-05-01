from common_imports import *

import requests  # 确保导入 requests
from functools import lru_cache

logger = logging.getLogger(__name__)

@lru_cache(maxsize=512)
def fetch_metadata_cached(title, year, api_key, media_type):
    return fetch_metadata(title, year, api_key, media_type)
def check_tmdb_connection(api_key):
    """
    检查与 TMDB 的连接以及 API Key 是否有效。
    尝试访问 TMDB 配置接口。
    """
    if not api_key:
        logger.warning("未提供 TMDB API Key，无法检查连接。")
        return False # 或者 True，取决于是否认为没有 Key 就算连接失败

    check_url = "https://api.themoviedb.org/3/configuration"
    params = {"api_key": api_key}
    try:
        response = requests.get(check_url, params=params, timeout=5) # 设置较短超时
        response.raise_for_status() # 检查 HTTP 错误 (如 401 Unauthorized)
        logger.info("TMDB 连接成功且 API Key 有效。")
        return True
    except requests.exceptions.Timeout:
        logger.error("连接 TMDB 超时。")
        return False
    except requests.exceptions.RequestException as e:
        if e.response is not None:
            if e.response.status_code == 401:
                logger.error(f"TMDB API Key 无效或未授权: {e}")
            else:
                logger.error(f"连接 TMDB 时发生 HTTP 错误: {e}")
        else:
            logger.error(f"连接 TMDB 时发生网络错误: {e}")
        return False
    except Exception as e:
        logger.error(f"检查 TMDB 连接时发生未知错误: {e}")
        return False

# 函数重命名并添加 media_type 参数
def fetch_metadata(media_name, year, tmdb_api_key, media_type='movie'):
    """
    调用 TMDB API 获取媒体（电影或电视剧）详细信息。
    """
    if not tmdb_api_key:
        logger.warning("未配置 TMDB API Key，跳过元数据抓取")
        return {}

    result = {}
    tmdb_media_type = 'tv' if media_type == 'tv_show' else 'movie' # TMDB API 使用 'tv'

    # Step 1: 搜索媒体
    try:
        # 根据 media_type 选择搜索 API 端点
        search_url = f"https://api.themoviedb.org/3/search/{tmdb_media_type}"
        params = {"api_key": tmdb_api_key, "query": media_name, "language": "zh-CN"}
        # 对于电影，可以添加年份进行精确搜索，电视剧通常不需要
        if year and tmdb_media_type == 'movie':
            params["primary_release_year"] = year
        # elif year and tmdb_media_type == 'tv':
        #      params["first_air_date_year"] = year

        resp = requests.get(search_url, params=params, timeout=10)
        resp.raise_for_status()
        media_results = resp.json().get("results", [])
        if not media_results:
            logger.warning(f"未在 TMDB 中找到 {media_type}【{media_name}】")
            return {}

        # 尝试匹配年份（如果提供了）来提高准确性
        best_match = media_results[0]
        if year:
            date_key = 'first_air_date' if tmdb_media_type == 'tv' else 'release_date'
            for item in media_results:
                item_year = item.get(date_key, '')[:4]
                if item_year == str(year):
                    best_match = item
                    break # 找到年份匹配的就用这个

        media_id = best_match["id"]
        result["tmdbid"] = media_id
        result["media_type"] = media_type # 保存媒体类型，方便后续处理

    except Exception as e:
        logger.error(f"TMDB 搜索接口 ({tmdb_media_type}) 出错：{e}")
        return {}

    # Step 2: 获取媒体详情
    try:
        # 根据 media_type 选择详情 API 端点
        detail_url = f"https://api.themoviedb.org/3/{tmdb_media_type}/{media_id}"
        detail_resp = requests.get(detail_url, params={"api_key": tmdb_api_key, "language": "zh-CN", "append_to_response": "credits,keywords,videos,translations"}, timeout=10)
        detail_resp.raise_for_status()
        data = detail_resp.json()

        # 根据 media_type 处理不同的字段名
        is_movie = (tmdb_media_type == 'movie')
        title = data.get("title") if is_movie else data.get("name")
        original_title = data.get("original_title") if is_movie else data.get("original_name")
        release_date = data.get("release_date") if is_movie else data.get("first_air_date")
        year_str = release_date[:4] if release_date else ""
        runtime_list = data.get("episode_run_time", []) if not is_movie else [data.get("runtime")]
        runtime = runtime_list[0] if runtime_list else None

        # 尝试从翻译中获取中文标题
        try:
            translations = data.get("translations", {}).get("translations", [])
            for t in translations:
                 # TMDB 的中文代码有时是 zh-CN 或 zh-Hans
                if t.get("iso_639_1") == "zh" and t.get("iso_3166_1") in ("CN", "SG") and t.get("data"):
                    translated_title = t["data"].get("title") if is_movie else t["data"].get("name")
                    if translated_title:
                        title = translated_title
                        break
        except Exception as e_trans:
            logger.warning(f"解析 TMDB 翻译时出错: {e_trans}")


        result.update({
            "title": title,
            "original_title": original_title,
            "overview": data.get("overview"),
            "tagline": data.get("tagline"),
            "runtime": runtime, # 注意电视剧可能是每集时长
            "release_date": release_date,
            "year": year_str,
            "genres": [g["name"] for g in data.get("genres", [])],
            "studio": ", ".join([c["name"] for c in data.get("production_companies", [])]),
            "spoken_languages": [l.get("name") or l.get("english_name") for l in data.get("spoken_languages", [])], # 优先使用 name
            "vote_average": data.get("vote_average"),
            "vote_count": data.get("vote_count"),
            "poster_path": data.get("poster_path"),
            "imdb_id": data.get("external_ids", {}).get("imdb_id") if not is_movie else data.get("imdb_id"), # 电视剧的imdb id在external_ids里
            "collection": { # 电视剧没有 collection
                "name": data["belongs_to_collection"]["name"],
                "id": data["belongs_to_collection"]["id"]
            } if is_movie and data.get("belongs_to_collection") else None,
            # 电视剧特有信息
            "number_of_seasons": data.get("number_of_seasons") if not is_movie else None,
            "number_of_episodes": data.get("number_of_episodes") if not is_movie else None,
            "status": data.get("status"), # "Returning Series", "Ended", etc.
        })

        # Step 3, 4, 5: 获取演职员、关键词、预告片 (使用 append_to_response 一次性获取)
        credits = data.get("credits", {})
        keywords_data = data.get("keywords", {})
        videos_data = data.get("videos", {})

        # 演职员
        result["cast"] = [
            {
                "name": c["name"],
                "character": c.get("character", ""),
                "profile_path": c.get("profile_path"),
                "id": c["id"]
            }
            for c in credits.get("cast", [])[:20] # 取前20个演员
        ]
        crew = credits.get("crew", [])
        result["directors"] = [{"name": p["name"], "id": p["id"]} for p in crew if p.get("job") == "Director"]
        # 电视剧可能有 'Executive Producer' 作为主要创作人
        writer_jobs = ("Writer", "Screenplay", "Author", "Story")
        producer_jobs = ("Producer", "Executive Producer")
        result["writers"] = [{"name": p["name"], "id": p["id"]} for p in crew if p.get("job") in writer_jobs]
        result["producers"] = [{"name": p["name"], "id": p["id"], "role": p.get("job", "")} for p in crew if p.get("job") in producer_jobs]

        # 关键词
        keyword_list_key = "results" if tmdb_media_type == 'tv' else "keywords" # TV关键词在 'results' 键下
        result["keywords"] = [k["name"] for k in keywords_data.get(keyword_list_key, [])]

        # 预告片
        for v in videos_data.get("results", []):
            if v["type"] == "Trailer" and v["site"] == "YouTube":
                result["trailer"] = f"plugin://plugin.video.youtube/play/?video_id={v['key']}"
                break

    except Exception as e:
        logger.error(f"获取 TMDB {tmdb_media_type} 详情/附加信息失败：{e}")
        # 即使详情失败，如果搜索成功了，也返回部分结果
        if not result.get("title"): # 如果连标题都没有，则认为失败
             return {}


    return result

# download_poster 函数基本不用改，因为它只依赖 metadata['poster_path']
# 但可以考虑让 movie_name 参数更通用，比如叫 media_file_stem
def download_poster(metadata, target_dir, media_file_stem):
    """
    下载媒体海报。
    """
    poster_path = ""
    try:
        poster_url = metadata.get('poster_path')
        if poster_url:
            base_url = "https://image.tmdb.org/t/p/w500" # 可以考虑提供更高分辨率选项 w780, w1280, original
            full_url = base_url + poster_url
            response = requests.get(full_url, stream=True, timeout=20) # 增加超时
            response.raise_for_status() # 检查请求是否成功

            # 使用传入的文件名主干来命名海报
            poster_filename = f"{media_file_stem}-poster.jpg"
            poster_path = os.path.join(target_dir, poster_filename)
            with open(poster_path, 'wb') as poster_file:
                for chunk in response.iter_content(8192): # 增大 chunk size
                    poster_file.write(chunk)
            logger.info(f"下载并保存海报：{poster_path}")

    except requests.exceptions.RequestException as e:
         logger.error(f"下载海报网络请求出错 ({full_url}): {e}")
    except IOError as e:
         logger.error(f"写入海报文件时出错 ({poster_path}): {e}")
    except Exception as e:
        logger.error(f"下载海报时发生未知错误：{e}")
    return poster_path

def fetch_episode_metadata(tv_id, season, episode, api_key):
    """
    从 TMDB 获取单集元数据（标题、简介、首播日期等）。
    """
    url = f"https://api.themoviedb.org/3/tv/{tv_id}/season/{season}/episode/{episode}"
    params = {
        "api_key": api_key,
        "language": "zh-CN",  # 可换 en-US
        "append_to_response": "credits"
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {
            "episode_title": data.get("name"),
            "episode_overview": data.get("overview"),
            "episode_air_date": data.get("air_date"),
            "still_path": data.get("still_path"),
            "guest_stars": data.get("guest_stars", []),
            "episode_directors": [
                {"name": c["name"], "id": c["id"]}
                for c in data.get("credits", {}).get("crew", [])
                if c.get("job") == "Director"
            ],
        }
    except Exception as e:
        logger.warning(f"获取单集元数据失败（S{season}E{episode}）: {e}")
        return {}