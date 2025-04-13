import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime

def prettify(elem):
    """格式化 XML 输出"""
    rough_string = ET.tostring(elem, encoding='utf-8')
    return minidom.parseString(rough_string).toprettyxml(indent="  ")

def create_nfo(data, file_info, output_path):
    movie = ET.Element("movie")

    ET.SubElement(movie, "title").text = data.get("title", "")
    ET.SubElement(movie, "originaltitle").text = data.get("original_title", "")
    ET.SubElement(movie, "sorttitle")
    ET.SubElement(movie, "epbookmark")
    ET.SubElement(movie, "year").text = str(data.get("year", ""))
    
    # Ratings
    ratings = ET.SubElement(movie, "ratings")
    rating = ET.SubElement(ratings, "rating", {
        "default": "false",
        "max": "10",
        "name": "themoviedb"
    })
    ET.SubElement(rating, "value").text = str(data.get("vote_average", 0))
    ET.SubElement(rating, "votes").text = str(data.get("vote_count", 0))
    
    ET.SubElement(movie, "userrating").text = "0"
    ET.SubElement(movie, "top250").text = "0"

    # Collection
    if data.get("belongs_to_collection"):
        set_tag = ET.SubElement(movie, "set")
        ET.SubElement(set_tag, "name").text = data["belongs_to_collection"].get("name", "")
        ET.SubElement(set_tag, "overview")

    ET.SubElement(movie, "plot").text = data.get("overview", "")
    ET.SubElement(movie, "outline").text = data.get("overview", "")
    ET.SubElement(movie, "tagline").text = data.get("tagline", "")
    ET.SubElement(movie, "runtime").text = str(data.get("runtime", ""))
    ET.SubElement(movie, "mpaa").text = data.get("certification", "")
    ET.SubElement(movie, "certification").text = data.get("certification", "")
    ET.SubElement(movie, "id").text = data.get("imdb_id", "")
    ET.SubElement(movie, "tmdbid").text = str(data.get("id", ""))
    
    uniqueid1 = ET.SubElement(movie, "uniqueid", {"default": "false", "type": "tmdb"})
    uniqueid1.text = str(data.get("id", ""))
    if data.get("imdb_id"):
        uniqueid2 = ET.SubElement(movie, "uniqueid", {"default": "true", "type": "imdb"})
        uniqueid2.text = data.get("imdb_id", "")
    
    for country in data.get("production_countries", []):
        ET.SubElement(movie, "country").text = country.get("name", "")
    
    ET.SubElement(movie, "status")
    ET.SubElement(movie, "code")

    if data.get("release_date"):
        ET.SubElement(movie, "premiered").text = data["release_date"]
    
    ET.SubElement(movie, "watched").text = "false"
    ET.SubElement(movie, "playcount").text = "0"

    # Genres
    for genre in data.get("genres", []):
        ET.SubElement(movie, "genre").text = genre.get("name", "")
    
    # Studio
    if data.get("production_companies"):
        ET.SubElement(movie, "studio").text = data["production_companies"][0].get("name", "")

    # Credits / Writers
    for writer in data.get("writers", []):
        credit = ET.SubElement(movie, "credits", {"tmdbid": str(writer.get("id", ""))})
        credit.text = writer.get("name", "")

    # Directors
    for director in data.get("directors", []):
        ET.SubElement(movie, "director", {"tmdbid": str(director.get("id", ""))}).text = director.get("name", "")

    # Tags
    for keyword in data.get("keywords", []):
        ET.SubElement(movie, "tag").text = keyword.get("name", "")
    
    # Actors
    for actor in data.get("cast", []):
        actor_tag = ET.SubElement(movie, "actor")
        ET.SubElement(actor_tag, "name").text = actor.get("name", "")
        ET.SubElement(actor_tag, "role").text = actor.get("character", "")
        ET.SubElement(actor_tag, "profile").text = f"https://www.themoviedb.org/person/{actor.get('id')}"
        ET.SubElement(actor_tag, "tmdbid").text = str(actor.get("id", ""))

    # Producers
    for p in data.get("producers", []):
        producer = ET.SubElement(movie, "producer", {"tmdbid": str(p.get("id", ""))})
        ET.SubElement(producer, "name").text = p.get("name", "")
        ET.SubElement(producer, "role").text = p.get("job", "")
        ET.SubElement(producer, "profile").text = f"https://www.themoviedb.org/person/{p.get('id')}"

    # Trailer
    if data.get("trailer"):
        ET.SubElement(movie, "trailer").text = data["trailer"]
    
    # Languages
    spoken_languages = [lang.get("name", "") for lang in data.get("spoken_languages", [])]
    ET.SubElement(movie, "languages").text = ", ".join(spoken_languages)

    # 添加 fileinfo
    fileinfo = ET.SubElement(movie, "fileinfo")
    streamdetails = ET.SubElement(fileinfo, "streamdetails")

    video_stream = ET.SubElement(streamdetails, "video")
    for key in ["codec", "aspect", "width", "height", "durationinseconds"]:
        ET.SubElement(video_stream, key).text = str(file_info.get("video", {}).get(key, ""))

    for audio in file_info.get("audio", []):
        audio_tag = ET.SubElement(streamdetails, "audio")
        for key in ["codec", "language", "channels"]:
            ET.SubElement(audio_tag, key).text = str(audio.get(key, ""))

    for sub in file_info.get("subtitles", []):
        subtitle = ET.SubElement(streamdetails, "subtitle")
        ET.SubElement(subtitle, "language").text = sub

    # 其他元信息
    ET.SubElement(movie, "source").text = data.get("source", "WEBRIP")
    ET.SubElement(movie, "edition").text = "NONE"
    ET.SubElement(movie, "original_filename").text = data.get("original_filename", "")
    ET.SubElement(movie, "user_note")
    ET.SubElement(movie, "dateadded").text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(prettify(movie))
