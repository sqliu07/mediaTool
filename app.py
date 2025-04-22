import os
import json
import logging
from threading import Thread
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from scraper import process_movies
from datetime import datetime

CONFIG_FILE = "configs/config.json"

# 创建日志目录
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 生成日志文件名（包含时间戳）
current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
log_filename = os.path.join(LOG_DIR, f"media_tools_{current_time}.log")

app = Flask(__name__)
app.secret_key = "your-secret-key"  # 请修改

# 日志初始化
logging.basicConfig(level=logging.DEBUG,
                   format='%(asctime)s - %(levelname)s - %(message)s',
                   handlers=[
                       logging.StreamHandler(),
                       logging.FileHandler(log_filename, encoding="utf-8")
                   ])
logger = logging.getLogger("movie_manager")

# 全局进度状态
progress = {
    "total": 0,
    "processed": 0,
    "success": 0,
    "failed": 0,
    "completed": False,
    "errors": [],
    "log_path": log_filename  # 使用新的日志文件路径
}

# 加载/保存配置
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                configs = json.load(f)
                if isinstance(configs, list):
                    return configs
        except Exception as e:
            logger.error(f"加载配置出错：{e}")
    return []

def save_config(configs):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(configs, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存配置出错：{e}")

# APScheduler
scheduler = BackgroundScheduler()

def scheduled_job():
    configs = load_config()
    if not configs:
        return
    cfg = configs[0]
    logger.info("【定时任务】开始扫描并处理电影")
    # 启动一次后台任务
    thread = Thread(target=run_process_wrapper, args=(cfg,))
    thread.start()

def start_scheduler(interval_min):
    scheduler.remove_all_jobs()
    if interval_min and interval_min > 0:
        scheduler.add_job(scheduled_job, 'interval', minutes=interval_min, id="scan_job")
        scheduler.start()
        logger.info(f"调度器启动：每 {interval_min} 分钟扫描一次")
    else:
        logger.info("未设置定时扫描，调度器未启动")

# wrapper，将进度回调传给 process_movies
def run_process_wrapper(cfg):
    def progress_cb(event, value, success=True, error_info=None):
        if event == "initialize":
            progress["total"] = value
            progress["processed"] = 0
            progress["success"] = 0
            progress["failed"] = 0
            progress["completed"] = False
            progress["errors"] = []  # 清空错误列表
        elif event == "update":
            progress["processed"] += 1
            if success:
                progress["success"] += 1
            else:
                progress["failed"] += 1
                if error_info:
                    progress["errors"].append(error_info)
        elif event == "complete":
            progress["completed"] = True

    try:
        process_movies(cfg, progress_callback=progress_cb)
    except Exception as e:
        error_msg = f"处理电影时出错：{str(e)}"
        logger.error(error_msg)
        progress["completed"] = True
        progress["errors"].append({
            "file": "全局错误",
            "message": error_msg
        })

    try:
        process_movies(cfg, progress_callback=progress_cb)
    except Exception as e:
        logger.error(f"处理电影时出错：{e}")

@app.route("/", methods=["GET"])
def config_page():
    configs = load_config()
    return render_template("index.html", configs=configs)

@app.route("/save_config", methods=["POST"])
def save_config_route():
    data = request.get_json()
    configs = load_config()
    existing = next((c for c in configs if c["name"] == data["name"]), None)
    entry = {
        "name": data["name"],
        "file_suffixes": data["file_suffixes"],
        "tmdb_api_key": data["tmdb_api_key"],
        "paths": data["paths"],
        "rename_rule": data.get("rename_rule", "{title}{ext}"),
        "schedule_interval": data.get("schedule_interval", 0)
    }
    if existing:
        existing.update(entry)
    else:
        configs.append(entry)
    save_config(configs)
    # 重新启动定时器
    start_scheduler(entry["schedule_interval"])
    return jsonify({"message": "配置保存成功！"})

@app.route("/delete_config/<name>", methods=["DELETE"])
def delete_config_route(name):
    configs = load_config()
    new_configs = [c for c in configs if c["name"] != name]
    if len(new_configs) == len(configs):
        return jsonify({"message": "配置未找到"}), 404
    save_config(new_configs)
    # 如果删除的是第一个配置，需要重启定时器
    if new_configs:
        start_scheduler(new_configs[0].get("schedule_interval", 0))
    else:
        scheduler.remove_all_jobs()
    return jsonify({"message": "删除成功"})

@app.route("/get_config/<name>", methods=["GET"])
def get_config_route(name):
    configs = load_config()
    cfg = next((c for c in configs if c["name"] == name), None)
    if not cfg:
        return jsonify({"message": "配置未找到"}), 404
    return jsonify(cfg)

@app.route("/run_task", methods=["POST"])
def run_task():
    configs = load_config()
    if not configs:
        return jsonify({"message": "没有可用的配置"}), 400
    cfg = configs[0]
    # 清零进度
    progress["total"] = 0
    progress["processed"] = 0
    progress["success"] = 0
    progress["failed"] = 0
    progress["completed"] = False
    # 后台运行
    thread = Thread(target=run_process_wrapper, args=(cfg,))
    thread.start()
    return jsonify({"message": "任务已启动"}), 202

@app.route("/progress", methods=["GET"])
def get_progress():
    # 使用全局变量中的日志路径
    return jsonify(progress)

if __name__ == "__main__":
    # 程序启动时加载一次定时器
    cfgs = load_config()
    if cfgs:
        start_scheduler(cfgs[0].get("schedule_interval", 0))
    app.run(host="0.0.0.0", port=5000, debug=True)