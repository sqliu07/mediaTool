from common_imports import *
import re
import os

# ---------------------------------------------------------------------------
# 动画（番剧）命名解析
#
# 只在配置的「媒体类型」选了「动画」时启用，不影响电影 / 剧集配置。
# 支持的常见写法：
#   [Lilith-Raws] One Piece - 1080 [Baha][WEB-DL][1080p].mp4   -> One Piece, 第 1080 集
#   [Nekomoe kissaten][Sousou no Frieren][01][1080p][CHS].mp4  -> Sousou no Frieren, 第 01 集
#   Title - 01v2.mkv / Title 第01话.mkv / Title EP01.mkv
#   Title S01E02.mkv（常规季集写法也认）
# ---------------------------------------------------------------------------
_ANIME_TAG_RE = re.compile(r'\[[^\]]*\]|【[^】]*】|\([^)]*\)')
_ANIME_NOISE_RE = re.compile(
    r'\b(?:1080p|720p|2160p|480p|4k|8bit|10bit|web-?dl|web-?rip|bdrip|bd|bluray|'
    r'hdtv|dvdrip|x264|x265|h\.?264|h\.?265|hevc|avc|aac|flac|ac3|eac3|ddp?|opus|'
    r'chs|cht|gb|big5|jpsc|jptc|jpn|eng|sc|tc|mkv|mp4|avi|v2)\b',
    re.IGNORECASE,
)

_ANIME_EPISODE_PATTERNS = [
    # S01E01
    (re.compile(r'[Ss](\d{1,2})[\s._-]*[Ee](\d{1,4})'), 'season_episode'),
    # 第01话 / 第1集 / 第01話
    (re.compile(r'第\s*(\d{1,4})\s*[话話集]'), 'episode'),
    # EP01 / Ep.01 / E01（前面不能紧跟字母，避免和 S01E01 冲突）
    (re.compile(r'(?<![A-Za-z])E[Pp]?[.\s_-]?(\d{1,4})(?![\dA-Za-z])'), 'episode'),
    # [01] —— 整段方括号只有数字
    (re.compile(r'\[(\d{1,4})\]'), 'episode'),
    # " - 01" / " - 01v2"（排除 1900-2099 这种像年份的数字）
    (re.compile(r'[\s_]-[\s_]*(\d{1,4})(?:v\d+)?(?![\dA-Za-z])'), 'episode'),
]


def _looks_like_year(value):
    try:
        return 1900 <= int(value) <= 2099
    except (TypeError, ValueError):
        return False


def _anime_clean_title(raw):
    """从动画文件名片段里清理出标题。

    两种常见排版：
      1) [Group] Title - 01 [1080p]        -> 标题在方括号外
      2) [Group][Title][01][1080p][CHS]    -> 标题也在方括号里
    """
    # 先看方括号 / 圆括号之外的文本
    outside = _ANIME_TAG_RE.sub(' ', raw)
    outside = _ANIME_NOISE_RE.sub(' ', outside)
    outside = re.sub(r'[\s._]+', ' ', outside).strip(' -_.~')
    if outside:
        return re.sub(r'[\s._-]+(?:19|20)\d{2}$', '', outside).strip(' -_.~')

    # 标题整段在方括号里：约定第一段是字幕组、第二段是作品名
    segments = []
    for match in re.finditer(r'\[([^\]]*)\]|【([^】]*)】', raw):
        text = (match.group(1) or match.group(2) or "").strip()
        if not text:
            continue
        cleaned = _ANIME_NOISE_RE.sub(' ', text)
        cleaned = re.sub(r'[\s._]+', ' ', cleaned).strip(' -_.~')
        if not cleaned or re.fullmatch(r'\d{1,4}', cleaned):
            continue                      # 纯集号 / 分辨率 / 语言标签
        segments.append(cleaned)

    if not segments:
        return ""
    title = segments[1] if len(segments) >= 2 else segments[0]
    return re.sub(r'[\s._-]+(?:19|20)\d{2}$', '', title).strip(' -_.~')


def parse_anime_filename(filename):
    """按动画（番剧）习惯解析文件名。

    返回 {'type': 'anime', 'title': ..., 'season': '01', 'episode': 'NN'}；
    识别不到集号时返回的字典里没有 season / episode。
    标题清理后为空则返回 None（交由调用方走通用逻辑）。
    """
    name = os.path.splitext(filename)[0]
    if not name.strip():
        return None

    season, episode = None, None
    title_part = name

    for pattern, kind in _ANIME_EPISODE_PATTERNS:
        match = pattern.search(name)
        if not match:
            continue
        if kind == 'season_episode':
            season = match.group(1).zfill(2)
            episode = match.group(2).zfill(2)
        else:
            candidate = match.group(1)
            # " - 2019" 这种更像年份，跳过继续找
            if _looks_like_year(candidate) and len(candidate) == 4:
                continue
            episode = candidate.zfill(2)
        title_part = name[:match.start()]
        break

    title = _anime_clean_title(title_part)
    if not title:
        title = _anime_clean_title(name[:120])
    if not title:
        return None

    result = {'type': 'anime', 'title': title}
    if episode is not None:
        result['season'] = season or '01'
        result['episode'] = episode
    return result


def parse_filename(filename, media_type_hint=None):
    """
    从文件名中提取信息，支持电影、电视剧和动画。

    电影示例：
      - "The.Matrix.1999.mkv" => {'type': 'movie', 'title': 'The Matrix', 'year': '1999'}
      - "The Matrix (1999).mp4" => {'type': 'movie', 'title': 'The Matrix', 'year': '1999'}
    电视剧示例：
      - "Breaking.Bad.S01E01.Pilot.1080p.mkv" => {'type': 'tv_show', 'title': 'Breaking Bad', 'season': '01', 'episode': '01'}
      - "Game of Thrones - S05E08 - Hardhome.avi" => {'type': 'tv_show', 'title': 'Game of Thrones', 'season': '05', 'episode': '08'}
    动画示例（仅当 media_type_hint='anime' 时启用）：
      - "[Group] One Piece - 1080 [1080p].mp4" => {'type': 'anime', 'title': 'One Piece', 'season': '01', 'episode': '1080'}
    返回: 包含解析信息的字典，或 None (如果无法解析)
    """
    hint = (media_type_hint or "").strip().lower()
    if hint == "anime":
        anime_info = parse_anime_filename(filename)
        if anime_info:
            return anime_info

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
    movie_match = re.search(r'^(.*?)[\s\._-]*[\(\[]?(\d{4})[\)\]]?', name)
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