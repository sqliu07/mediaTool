from common_imports import *
import xml.etree.ElementTree as ET
from xml.dom import minidom

logger = logging.getLogger(__name__)

def _add_sub_element(parent, tag, text):
    """Helper to add a sub-element only if text is not empty."""
    if text:
        sub = ET.SubElement(parent, tag)
        sub.text = str(text) # Ensure text is string

def _pretty_print_xml(elem):
    """Return a pretty-printed XML string for the Element."""
    rough_string = ET.tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ", encoding='utf-8')

def generate_nfo(metadata, nfo_path, original_filename=""):
    """
    为电影生成 NFO 文件。
    """
    try:
        root = ET.Element("movie")
        _add_sub_element(root, "originalfilename", original_filename)
        _add_sub_element(root, "title", metadata.get("title"))
        _add_sub_element(root, "originaltitle", metadata.get("original_title"))
        _add_sub_element(root, "sorttitle", metadata.get("title")) # 通常与标题相同
        _add_sub_element(root, "rating", metadata.get("vote_average"))
        _add_sub_element(root, "year", metadata.get("year"))
        _add_sub_element(root, "votes", metadata.get("vote_count"))
        _add_sub_element(root, "plot", metadata.get("overview"))
        _add_sub_element(root, "tagline", metadata.get("tagline"))
        _add_sub_element(root, "runtime", metadata.get("runtime"))
        _add_sub_element(root, "premiered", metadata.get("release_date"))
        _add_sub_element(root, "studio", metadata.get("studio"))
        _add_sub_element(root, "country", ", ".join(metadata.get("production_countries", []))) # TMDB API 可能不直接提供国家，需要解析 production_companies

        for genre in metadata.get("genres", []):
            _add_sub_element(root, "genre", genre)

        for lang in metadata.get("spoken_languages", []):
             _add_sub_element(root, "language", lang) # 添加语言信息

        # 唯一 ID
        if metadata.get("tmdbid"):
            uniqueid_tmdb = ET.SubElement(root, "uniqueid", {"type": "tmdb", "default": "true"})
            uniqueid_tmdb.text = str(metadata["tmdbid"])
        if metadata.get("imdb_id"):
            uniqueid_imdb = ET.SubElement(root, "uniqueid", {"type": "imdb"})
            uniqueid_imdb.text = metadata["imdb_id"]

        # 演职员信息
        for director in metadata.get("directors", []):
            _add_sub_element(root, "director", director.get("name"))
        for writer in metadata.get("writers", []):
            _add_sub_element(root, "credits", writer.get("name")) # NFO 通常用 <credits> 表示编剧

        for actor in metadata.get("cast", []):
            actor_elem = ET.SubElement(root, "actor")
            _add_sub_element(actor_elem, "name", actor.get("name"))
            _add_sub_element(actor_elem, "role", actor.get("character"))
            # 可以选择性添加演员头像路径（如果需要）
            # if actor.get("profile_path"):
            #     _add_sub_element(actor_elem, "thumb", f"https://image.tmdb.org/t/p/w185{actor.get('profile_path')}")

        # 电影集信息
        if metadata.get("collection"):
            set_elem = ET.SubElement(root, "set")
            _add_sub_element(set_elem, "name", metadata["collection"].get("name"))
            _add_sub_element(set_elem, "tmdbcolid", metadata["collection"].get("id")) # 添加 TMDB 集合 ID

        # 写入文件
        xml_str = _pretty_print_xml(root)
        with open(nfo_path, "wb") as f: # 以二进制写入 utf-8
            f.write(xml_str)
        logger.info(f"成功生成电影 NFO 文件：{nfo_path}")
        return True

    except Exception as e:
        logger.error(f"生成电影 NFO 文件失败 ({nfo_path}): {e}")
        return False

def generate_tv_nfo(metadata, nfo_path, original_filename=""):
    """
    为电视剧集生成 NFO 文件。
    注意：当前 metadata 主要包含剧集信息，缺少单集详情。
    """
    try:
        root = ET.Element("episodedetails")
        _add_sub_element(root, "originalfilename", original_filename)
        # --- 使用剧集信息填充 ---
        _add_sub_element(root, "showtitle", metadata.get("title")) # 剧集标题
        # season 和 episode 现在是字符串
        season_str = metadata.get("season", "00")
        episode_str = metadata.get("episode", "00")
        _add_sub_element(root, "season", season_str)   # 季号 (字符串)
        _add_sub_element(root, "episode", episode_str) # 集号 (字符串)

        # TODO: 获取单集标题。目前使用剧集标题 + SxxExx 作为替代
        # 调整格式化，直接使用字符串
        episode_title = f"{metadata.get('title', 'Unknown Episode')} S{season_str}E{episode_str}"
        _add_sub_element(root, "title", episode_title)

        # TODO: 获取单集剧情。目前使用剧集简介作为替代
        _add_sub_element(root, "plot", metadata.get("overview"))

        _add_sub_element(root, "runtime", metadata.get("runtime")) # 通常是剧集的平均单集时长
        _add_sub_element(root, "premiered", metadata.get("release_date")) # 剧集首播日期
        # TODO: 获取单集播出日期。目前使用剧集首播日期
        _add_sub_element(root, "aired", metadata.get("release_date"))

        _add_sub_element(root, "studio", metadata.get("studio")) # 制片公司/电视台
        _add_sub_element(root, "year", metadata.get("year")) # 剧集首播年份

        for genre in metadata.get("genres", []):
            _add_sub_element(root, "genre", genre)

        # 评分和票数 (剧集的)
        _add_sub_element(root, "rating", metadata.get("vote_average"))
        _add_sub_element(root, "votes", metadata.get("vote_count"))

        # 唯一 ID (剧集的)
        if metadata.get("tmdbid"):
            uniqueid_tmdb = ET.SubElement(root, "uniqueid", {"type": "tmdb", "default": "true"})
            uniqueid_tmdb.text = str(metadata["tmdbid"])
        if metadata.get("imdb_id"):
            uniqueid_imdb = ET.SubElement(root, "uniqueid", {"type": "imdb"})
            uniqueid_imdb.text = metadata["imdb_id"]
        # TODO: 获取 TVDB ID (如果 TMDB API 返回了)
        # if metadata.get("tvdb_id"):
        #     uniqueid_tvdb = ET.SubElement(root, "uniqueid", {"type": "tvdb"})
        #     uniqueid_tvdb.text = metadata["tvdb_id"]

        # 演职员信息 (剧集的)
        for director in metadata.get("directors", []):
            _add_sub_element(root, "director", director.get("name"))
        for writer in metadata.get("writers", []):
            _add_sub_element(root, "credits", writer.get("name")) # NFO 通常用 <credits> 表示编剧

        for actor in metadata.get("cast", []):
            actor_elem = ET.SubElement(root, "actor")
            _add_sub_element(actor_elem, "name", actor.get("name"))
            _add_sub_element(actor_elem, "role", actor.get("character"))
            # TODO: 获取单集特定演员或嘉宾 (Guest Stars)

        # TODO: 添加单集缩略图 <thumb>，目前缺失

        # 写入文件
        xml_str = _pretty_print_xml(root)
        with open(nfo_path, "wb") as f: # 以二进制写入 utf-8
            f.write(xml_str)
        logger.info(f"成功生成电视剧 NFO 文件：{nfo_path}")
        return True

    except Exception as e:
        logger.error(f"生成电视剧 NFO 文件失败 ({nfo_path}): {e}")
        return False

def generate_tvshow_nfo(metadata, nfo_path):
    """
    为整部剧集生成 tvshow.nfo 文件，包含剧名、简介、导演、演员等。
    """
    try:
        root = ET.Element("tvshow")
        _add_sub_element(root, "title", metadata.get("title"))
        _add_sub_element(root, "originaltitle", metadata.get("original_title"))
        _add_sub_element(root, "sorttitle", metadata.get("title"))
        _add_sub_element(root, "plot", metadata.get("overview"))
        _add_sub_element(root, "studio", metadata.get("studio"))
        _add_sub_element(root, "status", metadata.get("status"))
        _add_sub_element(root, "year", metadata.get("year"))
        _add_sub_element(root, "premiered", metadata.get("release_date"))

        for genre in metadata.get("genres", []):
            _add_sub_element(root, "genre", genre)

        for lang in metadata.get("spoken_languages", []):
            _add_sub_element(root, "language", lang)

        _add_sub_element(root, "rating", metadata.get("vote_average"))
        _add_sub_element(root, "votes", metadata.get("vote_count"))

        if metadata.get("tmdbid"):
            uniqueid_tmdb = ET.SubElement(root, "uniqueid", {"type": "tmdb", "default": "true"})
            uniqueid_tmdb.text = str(metadata["tmdbid"])
        if metadata.get("imdb_id"):
            uniqueid_imdb = ET.SubElement(root, "uniqueid", {"type": "imdb"})
            uniqueid_imdb.text = metadata["imdb_id"]

        for director in metadata.get("directors", []):
            _add_sub_element(root, "director", director.get("name"))

        for actor in metadata.get("cast", [])[:20]:
            actor_elem = ET.SubElement(root, "actor")
            _add_sub_element(actor_elem, "name", actor.get("name"))
            _add_sub_element(actor_elem, "role", actor.get("character"))

        # 写入文件
        xml_str = _pretty_print_xml(root)
        with open(nfo_path, "wb") as f:
            f.write(xml_str)
        logger.info(f"成功生成 tvshow.nfo 文件：{nfo_path}")
        return True

    except Exception as e:
        logger.error(f"生成 tvshow.nfo 失败 ({nfo_path}): {e}")
        return False