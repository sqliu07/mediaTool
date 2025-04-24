from common_imports import *
import re
import os

def parse_filename(filename):
    """
    从文件名中提取信息，支持电影和电视剧。
    电影示例：
      - "The.Matrix.1999.mkv" => {'type': 'movie', 'title': 'The Matrix', 'year': '1999'}
      - "The Matrix (1999).mp4" => {'type': 'movie', 'title': 'The Matrix', 'year': '1999'}
    电视剧示例：
      - "Breaking.Bad.S01E01.Pilot.1080p.mkv" => {'type': 'tv_show', 'title': 'Breaking Bad', 'season': 1, 'episode': 1}
      - "Game of Thrones - S05E08 - Hardhome.avi" => {'type': 'tv_show', 'title': 'Game of Thrones', 'season': 5, 'episode': 8}
      - "Black.Mirror.S07E01.2024.mkv" => {'type': 'tv_show', 'title': 'Black Mirror', 'season': 7, 'episode': 1}
    返回: 包含解析信息的字典，或 None (如果无法解析)
    """
    name, _ = os.path.splitext(filename)
    name = name.replace('.', ' ').replace('_', ' ') # 替换点和下划线

    # 尝试匹配电视剧格式 (例如 S01E01)
    # 改进正则，使其更健壮，并捕获 S/E 前面的部分作为可能的标题
    tv_match = re.search(r'^(.*?)[\s\._-]*[Ss](\d{1,2})[\s\._-]*[Ee](\d{1,3})(.*)$', name, re.IGNORECASE)
    if tv_match:
        title = tv_match.group(1).strip()
        # 直接保存为字符串，保留前导零
        season = tv_match.group(2)
        episode = tv_match.group(3)
        # remaining_part = tv_match.group(4).strip() # 获取 SxxExx 后面的部分

        # 清理标题：移除末尾可能存在的年份、分辨率、发布组等常见模式
        # 移除常见的年份模式 (YYYY) 或 (YYYY)
        title = re.sub(r'[\s\._-]*\(?(\d{4})\)?[\s\._-]*$', '', title).strip()
        # 移除末尾的 '-' 或其他非字母数字字符
        title = title.rstrip(' .-')

        # 如果标题为空（例如文件名是 S01E01.mkv），则认为解析失败
        if not title:
             pass # 继续尝试电影匹配
        else:
            # 返回字符串格式的 season 和 episode
            return {'type': 'tv_show', 'title': title, 'season': season, 'episode': episode}


    # 尝试匹配电影格式 (包含年份)
    # 改进正则，确保年份是末尾的数字，或者被括号包围
    movie_match = re.search(r'^(.*?)(?:[\s\._-]*\((\d{4})\)|[\s\._-](\d{4}))[\s\._-]*$', name)
    if movie_match:
        title = movie_match.group(1).strip()
        # 年份可能是 group(2) 或 group(3)
        year = movie_match.group(2) or movie_match.group(3)
        # 清理标题末尾的 '-' 或其他非字母数字字符
        title = title.rstrip(' .-')
        # 如果标题为空，则认为解析失败
        if not title:
            pass # 继续尝试无年份匹配
        else:
            return {'type': 'movie', 'title': title, 'year': year}

    # 如果以上都不匹配，返回清理后的原始名称作为标题（无年份/剧集信息）
    # 移除末尾可能的年份括号，以防上面的正则没匹配到
    cleaned_name = re.sub(r'[\s\._-]*\(?(\d{4})\)?[\s\._-]*$', '', name).strip()
    cleaned_name = cleaned_name.rstrip(' .-')
    if not cleaned_name: # 如果清理后为空
        cleaned_name = os.path.splitext(filename)[0] # 使用原始无扩展名

    return {'type': 'unknown', 'title': cleaned_name.strip()}

# 保留旧函数接口，但调用新函数（可选，为了兼容性）
def parse_movie_filename(filename):
    """旧接口，仅返回电影名和年份"""
    info = parse_filename(filename)
    if info and info['type'] == 'movie':
        return info['title'], info['year']
    # 对于电视剧或未知类型，可以返回标题和 None，或根据需要调整
    elif info:
        return info['title'], None
    return os.path.splitext(filename)[0].strip(), None