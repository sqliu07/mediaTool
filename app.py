import os
import json
import logging
from flask import Flask, render_template, request, redirect, url_for, flash
from scraper import process_movies
from apscheduler.schedulers.background import BackgroundScheduler
from threading import Thread

CONFIG_FILE = "config.json"

app = Flask(__name__)
app.secret_key = "your-secret-key"  # 请自行修改

# 默认配置，用户可在WebUI中修改
DEFAULT_CONFIG = {
    "download_dir": "/nas/download",
    "target_dir": "/nas/movies",
    "tmdb_api_key": "",
    "file_suffixes": ".mp4,.mkv,.avi,.mov",   # 支持硬链接的文件后缀（逗号分隔）
    "rename_rule": "{title} ({year})",          # 自定义重命名规则，格式化参数：title、year
    "max_threads": 4,                           # 并发处理线程数
    "schedule_interval": 0                      # 定时扫描间隔，单位分钟；0表示不启用自动扫描
}

# 日志初始化（同时输出到控制台和文件）
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.StreamHandler(),
                        logging.FileHandler("movie_manager.log", encoding="utf-8")
                    ])
logger = logging.getLogger("movie_manager")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载配置出错：{e}")
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存配置出错：{e}")

# APScheduler 定时任务调度器
scheduler = BackgroundScheduler()

def scheduled_job():
    config = load_config()
    logger.info("【定时任务】开始扫描并处理电影")
    try:
        process_movies(config)
    except Exception as e:
        logger.error(f"【定时任务】执行出现异常：{e}")

def start_scheduler(schedule_interval):
    scheduler.remove_all_jobs()  # 先清除之前的任务
    if schedule_interval and int(schedule_interval) > 0:
        scheduler.add_job(scheduled_job, 'interval', minutes=int(schedule_interval), id="scan_job")
        scheduler.start()
        logger.info(f"调度器启动，每 {schedule_interval} 分钟扫描一次")
    else:
        logger.info("未设置定时扫描任务，调度器未启动")

@app.route("/", methods=["GET", "POST"])
def config_page():
    config = load_config()
    if request.method == "POST":
        # 更新配置
        config["download_dir"]   = request.form.get("download_dir")
        config["target_dir"]     = request.form.get("target_dir")
        config["tmdb_api_key"]   = request.form.get("tmdb_api_key")
        config["file_suffixes"]  = request.form.get("file_suffixes")
        config["rename_rule"]    = request.form.get("rename_rule")
        config["max_threads"]    = int(request.form.get("max_threads") or 4)
        config["schedule_interval"] = int(request.form.get("schedule_interval") or 0)
        save_config(config)
        # 重启定时任务
        start_scheduler(config["schedule_interval"])
        flash("配置保存成功！", "success")
        return redirect(url_for("config_page"))
    return render_template("config.html", config=config)

@app.route("/run", methods=["GET"])
def run_process():
    config = load_config()

    # 单独启动扫描任务到新线程，避免阻塞Flask响应
    thread = Thread(target=process_movies, args=(config,))
    thread.start()
    flash("扫描与处理任务正在后台执行！", "success")
    return redirect(url_for("config_page"))

if __name__ == "__main__":
    # 启动调度器（初次启动时加载配置中的定时扫描间隔）
    conf = load_config()
    start_scheduler(conf.get("schedule_interval", 0))
    app.run(host="0.0.0.0", port=5000, debug=True)
