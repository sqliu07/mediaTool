import os
import json
import logging
from threading import Thread
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from movie_processor import process_movies
from metadata_fetcher import check_tmdb_connection
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
scheduler.start()

# --- 新增的辅助函数 ---
# --- 修改后的辅助函数 ---
def _check_tmdb_connectivity(configs, task_type="任务"):
    """
    检查 TMDB 连接性。
    遍历配置找到第一个 API Key 并检查连接。
    成功则正常返回，失败则抛出相应的 TMDBError 异常。
    """
    first_api_key = None
    for cfg in configs:
        if cfg.get("tmdb_api_key"):
            first_api_key = cfg["tmdb_api_key"]
            break

    if first_api_key:
        logger.info(f"{task_type}：检查 TMDB 连接...")
        if not check_tmdb_connection(first_api_key):
            error_msg = f"{task_type}：无法连接到 TMDB，任务已终止。你可以配置hosts或代理以解决问题。"
            logger.error(error_msg)
            raise TMDBConnectionError(error_msg) # <--- 抛出连接错误异常
        else:
            logger.info(f"{task_type}：TMDB 连接检查通过。")
            # 成功，不返回任何值 (隐式返回 None)
    else:
        error_msg = f"{task_type}：未配置 TMDB API Key，任务终止。"
        logger.error(error_msg)
        raise TMDBApiKeyMissingError(error_msg) # <--- 抛出 Key 缺失异常


def run_scheduled_task():
    """后台定时执行的任务，处理所有配置"""
    logger.info("定时任务开始执行...")
    configs = load_config()
    if not configs:
        logger.info("没有找到配置，定时任务跳过。")
        return

    try:
        # --- 使用辅助函数检查 TMDB 连接 ---
        _check_tmdb_connectivity(configs, task_type="定时任务")
        # --- 检查通过，继续执行 ---
    except TMDBError as e: # 捕获所有 TMDB 相关的错误
        # 日志已在 _check_tmdb_connectivity 中记录
        logger.warning(f"定时任务因 TMDB 检查失败而终止: {e}")
        return # 终止任务

    # --- 检查结束 ---

    # 遍历所有配置并执行处理 (没有进度回调)
    for config_item in configs:
        config_name = config_item.get('name', '未命名配置')
        logger.info(f"开始处理配置: {config_name}")
        try:
            # 注意：定时任务目前不传递进度回调
            process_movies(config_item) # 传递单个配置项
            logger.info(f"配置 '{config_name}' 处理完成。")
        except Exception as e:
            logger.error(f"处理配置 '{config_name}' 时出错: {e}", exc_info=True)

    logger.info("定时任务执行完毕。")


def start_scheduler(interval_minutes=0):
    """启动或更新调度器"""
    global scheduler
    # 移除旧任务
    if scheduler.get_jobs():
        scheduler.remove_all_jobs()
        logger.info("旧的定时任务已移除。")

    # 如果设置了有效间隔，则添加新任务
    # 注意：这里的 interval_minutes 决定了任务运行的频率，
    # 但任务本身会处理所有配置。通常使用第一个配置的间隔，
    # 或者提供一个全局的调度间隔设置。
    if interval_minutes > 0:
        # Ensure it calls run_scheduled_task
        scheduler.add_job(run_scheduled_task, 'interval', minutes=interval_minutes, id='movie_scan_job', replace_existing=True)
        logger.info(f"定时任务已启动，每 {interval_minutes} 分钟执行一次。")
        # 立即运行一次（可选）
        # run_scheduled_task()
    else:
        logger.info("未设置有效的执行间隔，定时任务未启动。")

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

    # try:
    #     process_movies(cfg, progress_callback=progress_cb)
    # except Exception as e:
    #     logger.error(f"处理电影时出错：{e}")

# --- 自定义异常 ---
class TMDBError(Exception):
    """Base exception for TMDB related errors."""
    pass

class TMDBConnectionError(TMDBError):
    """Raised when connection to TMDB fails."""
    pass

class TMDBApiKeyMissingError(TMDBError):
    """Raised when TMDB API Key is missing in config."""
    pass

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
        "file_type": data.get("file_type", "movie"), # <--- 确保获取 file_type
        "file_suffixes": data["file_suffixes"],
        "tmdb_api_key": data["tmdb_api_key"],
        "paths": data["paths"],
        "rename_rule": data.get("rename_rule", ""), # 允许空规则
        "schedule_interval": data.get("schedule_interval", 0),
        "max_threads": data.get("max_threads", 4) # 可以考虑添加线程数配置
    }
    if existing:
        existing.update(entry)
    else:
        configs.append(entry)
    save_config(configs)
    # 重新启动定时器 (如果需要基于第一个配置的间隔)
    if configs:
        start_scheduler(configs[0].get("schedule_interval", 0))
    else:
         scheduler.remove_all_jobs() # 没有配置则停止定时器
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

# wrapper for single config processing with progress (used by the new multi-config wrapper)
def run_single_config_wrapper(cfg, progress_callback):
    config_name = cfg.get('name', '未命名配置')
    logger.info(f"开始处理单个配置: {config_name}")
    try:
        process_movies(cfg, progress_callback=progress_callback)
        logger.info(f"单个配置 '{config_name}' 处理完成。")
    except Exception as e:
        error_msg = f"处理配置 '{config_name}' 时出错：{str(e)}"
        logger.error(error_msg, exc_info=True)
        # Notify progress callback about the error if possible
        if progress_callback:
             try:
                 # Attempt to report a generic error for this config run
                 progress_callback("update", None, success=False, error_info={"file": f"配置 {config_name} 出错", "message": error_msg})
                 progress_callback("complete", None) # Mark as complete even on error
             except Exception as cb_e:
                 logger.error(f"调用进度回调时出错: {cb_e}")


# New wrapper to run all configs sequentially with combined progress
def run_all_configs_sequentially_wrapper():
    """按顺序处理所有配置，并在全局 progress 对象中报告累积进度"""
    global progress
    configs = load_config()
    if not configs:
        logger.info("没有找到配置，任务跳过。")
        # Reset progress and mark as complete if no configs
        progress["total"] = 0
        progress["processed"] = 0
        progress["success"] = 0
        progress["failed"] = 0
        progress["completed"] = True
        progress["errors"] = []
        return

    # Initialize overall progress
    overall_total = 0
    overall_processed = 0
    overall_success = 0
    overall_failed = 0
    overall_errors = []
    progress["completed"] = False # Mark as not completed initially

    # --- First pass: Estimate total files across all configs ---
    # This is tricky without actually running scan_files for all.
    # We might need to adjust process_movies to return the count first,
    # or just update the total dynamically.
    # For simplicity now, we'll update total within the loop.
    progress["total"] = 0 # Start total at 0, will increment
    progress["processed"] = 0
    progress["success"] = 0
    progress["failed"] = 0
    progress["errors"] = []


    def create_progress_callback(config_index, total_configs):
        """Creates a progress callback for a specific config run"""
        def progress_cb(event, value, success=True, error_info=None):
            global progress
            if event == "initialize":
                # Add to the overall total
                progress["total"] += value
            elif event == "update":
                progress["processed"] += 1
                if success:
                    progress["success"] += 1
                else:
                    progress["failed"] += 1
                    if error_info:
                        # Prepend config name to error info for clarity
                        error_info["config_name"] = configs[config_index].get('name', f'配置 {config_index+1}')
                        progress["errors"].append(error_info)
            # We handle 'complete' event after the loop finishes
        return progress_cb

    # --- Second pass: Process each config sequentially ---
    for i, config_item in enumerate(configs):
        config_name = config_item.get('name', f'配置 {i+1}')
        logger.info(f"开始顺序处理配置 {i+1}/{len(configs)}: {config_name}")
        callback_for_this_config = create_progress_callback(i, len(configs))
        try:
            # Call process_movies directly, passing the specific callback
            process_movies(config_item, progress_callback=callback_for_this_config)
            logger.info(f"顺序处理配置 '{config_name}' 完成。")
        except Exception as e:
            error_msg = f"处理配置 '{config_name}' 时出错: {str(e)}"
            logger.error(error_msg, exc_info=True)
            # Report error via progress if possible
            if callback_for_this_config:
                 try:
                     callback_for_this_config("update", None, success=False, error_info={"file": f"配置 {config_name} 启动时出错", "message": error_msg})
                 except Exception as cb_e:
                     logger.error(f"调用进度回调时出错: {cb_e}")

    # Mark overall completion
    progress["completed"] = True
    logger.info("所有配置顺序处理完毕。")


@app.route("/run_task", methods=["POST"])
def run_task():
    """手动触发任务，顺序处理所有配置"""
    global progress
    configs = load_config()
    if not configs:
        return jsonify({"message": "没有可用的配置"}), 400

    try:
        # --- 使用辅助函数检查 TMDB 连接 ---
        _check_tmdb_connectivity(configs, task_type="手动任务")
        # --- 检查通过，继续执行 ---
    except (TMDBConnectionError, TMDBApiKeyMissingError) as e: # <--- 合并两个异常类型
        # 连接失败或 Key 缺失
        return jsonify({"message": str(e)}), 503 # 返回具体的错误消息
    except TMDBError as e: # 捕获其他可能的 TMDB 错误 (如果未来添加)
         logger.error(f"TMDB 检查时发生未知错误: {e}", exc_info=True) # 添加日志记录
         return jsonify({"message": f"TMDB 检查时发生未知错误: {e}"}), 500
    # --- 检查结束 ---

    # 清零进度 (将在 wrapper 中重新初始化)
    progress["total"] = 0
    progress["processed"] = 0
    progress["success"] = 0
    progress["failed"] = 0
    progress["completed"] = False
    progress["errors"] = []

    # 后台运行新的 wrapper 函数
    thread = Thread(target=run_all_configs_sequentially_wrapper)
    thread.start()
    return jsonify({"message": "任务已启动，将按顺序处理所有配置。"}), 202

@app.route("/progress", methods=["GET"])
def get_progress():
    # 使用全局变量中的日志路径
    return jsonify(progress)

if __name__ == "__main__":
    # 程序启动时加载一次定时器
    cfgs = load_config()
    if cfgs:
        # 启动时加载配置并设置初始调度器
        initial_configs = load_config()
        initial_interval = 0
        if initial_configs:
            # 使用第一个配置的间隔来设置调度频率
            initial_interval = initial_configs[0].get("schedule_interval", 0)
        start_scheduler(initial_interval)
    app.run(host="0.0.0.0", port=5001, debug=False)