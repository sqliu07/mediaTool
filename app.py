"""MediaTool Web 层：配置管理、任务调度、进度上报。"""

import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, render_template, request

from filename_parser import parse_filename
from metadata_fetcher import check_tmdb_connection
from movie_processor import (
    DEFAULT_DIR_MODE,
    DEFAULT_FILE_MODE,
    DEFAULT_FILE_SUFFIXES,
    MEDIA_TYPES,
    MEDIA_TYPE_MOVIE,
    STATUS_FAILED,
    apply_permission_tree,
    collect_tasks,
    clear_processed,
    mode_to_string,
    normalize_suffixes,
    parse_mode,
    process_movies,
    processed_stats,
    suffix_set,
)

CONFIG_DIR = os.getenv("MEDIATOOL_CONFIG_DIR", "configs")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOG_DIR = os.getenv("MEDIATOOL_LOG_DIR", "logs")

for _directory in (CONFIG_DIR, LOG_DIR):
    if not os.path.exists(_directory):
        os.makedirs(_directory, exist_ok=True)

current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
log_filename = os.path.join(LOG_DIR, f"media_tools_{current_time}.log")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(16)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(log_filename, encoding="utf-8")],
)
logger = logging.getLogger("movie_manager")

# 后台定时任务调度器
scheduler = BackgroundScheduler()
scheduler.start()


# ---------------------------------------------------------------------------
# 进度状态（线程安全）
# ---------------------------------------------------------------------------
progress_lock = threading.Lock()
progress = {
    "total": 0,
    "processed": 0,
    "success": 0,
    "failed": 0,
    "skipped": 0,
    "linked": 0,
    "completed": False,
    "running": False,
    "stage": "空闲",
    "current_config": "",
    "started_at": None,
    "finished_at": None,
    "errors": [],
    "log_path": log_filename,
}


def _progress_update(**fields):
    with progress_lock:
        progress.update(fields)


def _progress_snapshot():
    with progress_lock:
        snapshot = dict(progress)
        snapshot["errors"] = list(progress["errors"])
    return snapshot


def _begin_run(stage="准备中"):
    """占用执行权；已有任务在跑时返回 False。"""
    with progress_lock:
        if progress["running"]:
            return False
        progress.update({
            "total": 0, "processed": 0, "success": 0, "failed": 0,
            "skipped": 0, "linked": 0, "completed": False, "running": True,
            "stage": stage, "current_config": "", "started_at": time.time(),
            "finished_at": None, "errors": [],
        })
        return True


def _finish_run(stage="已完成"):
    _progress_update(running=False, completed=True, stage=stage,
                     finished_at=time.time(), current_config="")


def _abort_run(message):
    with progress_lock:
        progress.update({
            "running": False, "completed": True, "finished_at": time.time(),
            "stage": message, "current_config": "",
        })
        progress["errors"].append({"file": "任务未启动", "message": message})


def make_progress_callback(config_name=None, reset_on_initialize=True):
    """统一的进度回调，把单文件结果汇总到全局 progress。"""

    def progress_cb(event, value=None, success=True, error_info=None, action=None):
        if event == "initialize":
            with progress_lock:
                if reset_on_initialize:
                    progress.update({
                        "total": int(value or 0), "processed": 0, "success": 0,
                        "failed": 0, "skipped": 0, "linked": 0, "errors": [],
                    })
                progress["stage"] = "处理中"
        elif event == "update":
            with progress_lock:
                progress["processed"] += int(value or 1)
                if action == "skipped":
                    progress["skipped"] += 1
                elif success:
                    progress["success"] += 1
                    if action in ("linked", "copied", "symlinked"):
                        progress["linked"] += 1
                else:
                    progress["failed"] += 1
                    if error_info:
                        item = dict(error_info)
                        if config_name:
                            item.setdefault("config_name", config_name)
                        progress["errors"].append(item)
        elif event == "complete":
            with progress_lock:
                progress["completed"] = True

    return progress_cb


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                configs = json.load(f)
                if isinstance(configs, list):
                    return configs
                logger.error("配置文件格式不正确（应为列表）")
        except Exception as e:
            logger.error(f"加载配置出错：{e}")
    return []


def save_config(configs):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(configs, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"保存配置出错：{e}")
        return False


# ---------------------------------------------------------------------------
# TMDB 连通性
# ---------------------------------------------------------------------------
class TMDBError(Exception):
    """TMDB 相关错误基类。"""


class TMDBConnectionError(TMDBError):
    """无法连接 TMDB。"""


class TMDBApiKeyMissingError(TMDBError):
    """配置中缺少 TMDB API Key。"""


def _check_tmdb_connectivity(configs, task_type="任务"):
    api_key = next((c.get("tmdb_api_key") for c in configs if c.get("tmdb_api_key")), None)
    if not api_key:
        message = f"{task_type}：未配置 TMDB API Key，任务终止。"
        logger.error(message)
        raise TMDBApiKeyMissingError(message)
    logger.info(f"{task_type}：检查 TMDB 连接...")
    if not check_tmdb_connection(api_key):
        message = f"{task_type}：无法连接到 TMDB，任务已终止。可配置 hosts 或代理后重试。"
        logger.error(message)
        raise TMDBConnectionError(message)
    logger.info(f"{task_type}：TMDB 连接检查通过。")


def _needs_tmdb(configs):
    return any(cfg.get("scrape_metadata", True) for cfg in configs)


# ---------------------------------------------------------------------------
# 任务执行
# ---------------------------------------------------------------------------
def run_scheduled_task():
    """定时任务：处理所有启用的配置。"""
    logger.info("定时任务开始执行...")
    configs = [cfg for cfg in load_config() if cfg.get("enabled", True)]
    if not configs:
        logger.info("没有启用的配置，定时任务跳过。")
        return

    if _needs_tmdb(configs):
        try:
            _check_tmdb_connectivity(configs, task_type="定时任务")
        except TMDBError as e:
            logger.warning(f"定时任务因 TMDB 检查失败而终止：{e}")
            return

    for config_item in configs:
        name = config_item.get("name", "未命名配置")
        try:
            process_movies(config_item)
            logger.info(f"配置 '{name}' 处理完成。")
        except Exception as e:
            logger.error(f"处理配置 '{name}' 时出错：{e}", exc_info=True)
    logger.info("定时任务执行完毕。")


def start_scheduler(interval_minutes=0):
    if scheduler.get_jobs():
        scheduler.remove_all_jobs()
        logger.info("旧的定时任务已移除。")
    try:
        interval = int(interval_minutes or 0)
    except (TypeError, ValueError):
        interval = 0
    if interval > 0:
        scheduler.add_job(run_scheduled_task, "interval", minutes=interval,
                          id="movie_scan_job", replace_existing=True)
        logger.info(f"定时任务已启动，每 {interval} 分钟执行一次。")
    else:
        logger.info("未设置有效的执行间隔，定时任务未启动。")


def run_all_configs_sequentially_wrapper():
    """顺序处理所有启用的配置，汇总进度。"""
    configs = [cfg for cfg in load_config() if cfg.get("enabled", True)]
    try:
        if not configs:
            logger.info("没有启用的配置，任务跳过。")
            _progress_update(stage="没有启用的配置")
            return

        total = 0
        for cfg in configs:
            try:
                total += len(collect_tasks(cfg))
            except Exception as e:
                logger.warning(f"预估配置 '{cfg.get('name', '未命名')}' 文件数失败：{e}")
        _progress_update(total=total)

        for i, cfg in enumerate(configs):
            name = cfg.get("name", f"配置 {i + 1}")
            logger.info(f"开始顺序处理配置 {i + 1}/{len(configs)}：{name}")
            _progress_update(current_config=name)
            callback = make_progress_callback(config_name=name,
                                              reset_on_initialize=False)
            try:
                process_movies(cfg, progress_callback=callback)
            except Exception as e:
                logger.error(f"配置 '{name}' 执行异常：{e}", exc_info=True)
                callback("update", 1, False,
                         {"file": "全局错误", "message": f"配置 {name} 执行异常：{e}"},
                         action=STATUS_FAILED)
    finally:
        _finish_run()


def run_single_config_wrapper(name, cfg):
    try:
        logger.info(f"开始执行配置：{name}")
        process_movies(cfg, progress_callback=make_progress_callback(config_name=name))
        logger.info(f"配置 {name} 执行完成。")
    except Exception as e:
        logger.error(f"执行配置 {name} 时发生错误：{e}", exc_info=True)
        with progress_lock:
            progress["failed"] += 1
            progress["errors"].append({"file": "全局错误", "config_name": name,
                                       "message": str(e)})
    finally:
        _finish_run()


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def config_page():
    return render_template("index.html", configs=load_config(), progress=_progress_snapshot())


@app.route("/configs", methods=["GET"])
def list_configs():
    configs = load_config()
    for cfg in configs:
        cfg["api_key_configured"] = bool(cfg.get("tmdb_api_key"))
        cfg.pop("tmdb_api_key", None)
    return jsonify(configs)


@app.route("/save_config", methods=["POST"])
def save_config_route():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"message": "配置名称不能为空"}), 400

    paths = [
        {"source": (p.get("source") or "").strip(), "target": (p.get("target") or "").strip()}
        for p in (data.get("paths") or [])
    ]
    paths = [p for p in paths if p["source"] and p["target"]]
    if not paths:
        return jsonify({"message": "至少需要一组有效的路径映射（源路径 + 目标路径）"}), 400

    # 后缀留空 -> 使用默认后缀，不再报错
    suffixes = normalize_suffixes(data.get("file_suffixes"))

    configs = load_config()
    existing = next((c for c in configs if c.get("name") == name), None)
    entry = {
        "name": name,
        "file_type": (data.get("file_type") if data.get("file_type") in MEDIA_TYPES
                      else MEDIA_TYPE_MOVIE),
        "file_suffixes": suffixes,
        "tmdb_api_key": (data.get("tmdb_api_key") or "").strip(),
        "paths": paths,
        "rename_rule": data.get("rename_rule", ""),
        "schedule_interval": int(data.get("schedule_interval") or 0),
        "max_threads": int(data.get("max_threads") or 4),
        "scrape_metadata": bool(data.get("scrape_metadata", True)),
        "rename_file": bool(data.get("rename_file", True)),
        "link_mode": data.get("link_mode", "hardlink"),
        "fix_permissions": bool(data.get("fix_permissions", True)),
        "dir_mode": mode_to_string(parse_mode(data.get("dir_mode"), DEFAULT_DIR_MODE)),
        "file_mode": mode_to_string(parse_mode(data.get("file_mode"), DEFAULT_FILE_MODE)),
        "enabled": bool(data.get("enabled", existing.get("enabled", True) if existing else True)),
    }
    if existing:
        existing.update(entry)
    else:
        configs.append(entry)

    if not save_config(configs):
        return jsonify({"message": "配置保存失败，请检查日志与目录权限"}), 500

    if configs:
        start_scheduler(configs[0].get("schedule_interval", 0))
    else:
        scheduler.remove_all_jobs()
    return jsonify({"message": "配置保存成功！"})


@app.route("/delete_config/<name>", methods=["DELETE"])
def delete_config_route(name):
    configs = load_config()
    new_configs = [c for c in configs if c.get("name") != name]
    if len(new_configs) == len(configs):
        return jsonify({"message": "配置未找到"}), 404
    if not save_config(new_configs):
        return jsonify({"message": "删除失败，请检查日志"}), 500
    if new_configs:
        start_scheduler(new_configs[0].get("schedule_interval", 0))
    else:
        scheduler.remove_all_jobs()
    return jsonify({"message": f"配置 '{name}' 已删除"})


@app.route("/get_config/<name>", methods=["GET"])
def get_config_route(name):
    cfg = next((c for c in load_config() if c.get("name") == name), None)
    if not cfg:
        return jsonify({"message": "配置未找到"}), 404
    return jsonify(cfg)


@app.route("/toggle_config/<name>", methods=["POST"])
def toggle_config_enabled(name):
    data = request.get_json(silent=True) or {}
    configs = load_config()
    cfg = next((c for c in configs if c.get("name") == name), None)
    if not cfg:
        return jsonify({"message": "配置未找到"}), 404
    cfg["enabled"] = bool(data.get("enabled", True))
    if not save_config(configs):
        return jsonify({"message": "保存失败，请检查日志"}), 500
    return jsonify({"message": f"配置 '{name}' 已{'启用' if cfg['enabled'] else '停用'}"})


@app.route("/run_task", methods=["POST"])
def run_task():
    """手动触发任务：顺序处理所有启用的配置。"""
    configs = [cfg for cfg in load_config() if cfg.get("enabled", True)]
    if not configs:
        return jsonify({"message": "没有启用的配置"}), 400

    if not _begin_run(stage="检查 TMDB 连接..."):
        return jsonify({"message": "已有任务正在运行，请等待当前任务结束"}), 409

    if _needs_tmdb(configs):
        try:
            _check_tmdb_connectivity(configs, task_type="手动任务")
        except TMDBError as e:
            _abort_run(str(e))
            return jsonify({"message": str(e)}), 503

    _progress_update(stage="扫描文件中...")
    threading.Thread(target=run_all_configs_sequentially_wrapper, daemon=True).start()
    return jsonify({"message": "任务已启动，将按顺序处理所有配置。"}), 202


@app.route("/run_config/<name>", methods=["POST"])
def run_single_config(name):
    cfg = next((c for c in load_config()
                if c.get("name") == name and c.get("enabled", True)), None)
    if not cfg:
        return jsonify({"message": f"未找到启用的配置：{name}"}), 404

    if not _begin_run(stage=f"准备执行配置 {name}..."):
        return jsonify({"message": "已有任务正在运行，请等待当前任务结束"}), 409

    if cfg.get("scrape_metadata", True):
        try:
            _check_tmdb_connectivity([cfg], task_type=f"配置 {name}")
        except TMDBError as e:
            _abort_run(str(e))
            return jsonify({"message": str(e)}), 503

    _progress_update(current_config=name, stage="扫描文件中...")
    threading.Thread(target=run_single_config_wrapper, args=(name, cfg),
                     daemon=True).start()
    return jsonify({"message": f"配置 {name} 已启动"}), 202


@app.route("/progress", methods=["GET"])
def get_progress():
    snapshot = _progress_snapshot()
    if snapshot.get("started_at") and snapshot.get("finished_at"):
        snapshot["elapsed"] = round(snapshot["finished_at"] - snapshot["started_at"], 1)
    elif snapshot.get("started_at"):
        snapshot["elapsed"] = round(time.time() - snapshot["started_at"], 1)
    else:
        snapshot["elapsed"] = 0
    return jsonify(snapshot)


@app.route("/clear_processed", methods=["POST"])
def clear_processed_route():
    try:
        count = clear_processed()
    except Exception as e:
        logger.error(f"清空处理记录失败：{e}")
        return jsonify({"message": f"清空处理记录失败：{e}"}), 500
    return jsonify({"message": f"已清空 {count} 条处理记录，下次执行将重新扫描全部文件"})


@app.route("/processed", methods=["GET"])
def processed_route():
    """处理记录状态（路径 / 条数 / 更新时间），用于确认记录是否持久化。"""
    return jsonify(processed_stats())


@app.route("/fix_permissions/<name>", methods=["POST"])
def fix_permissions_route(name):
    """对某个配置的目标目录做一次性递归权限修正（批量 chmod，不逐文件递归）。"""
    cfg = next((c for c in load_config() if c.get("name") == name), None)
    if not cfg:
        return jsonify({"message": "配置未找到"}), 404

    targets = [p.get("target") for p in cfg.get("paths", []) if p.get("target")]
    if not targets:
        return jsonify({"message": "该配置没有目标目录"}), 400
    missing = [t for t in targets if not os.path.isdir(t)]
    if len(missing) == len(targets):
        return jsonify({"message": "目标目录不存在，请检查路径映射"}), 400

    # 允许请求体临时覆盖权限值，便于先试后存
    data = request.get_json(silent=True) or {}
    dir_mode = parse_mode(data.get("dir_mode") or cfg.get("dir_mode"), DEFAULT_DIR_MODE)
    file_mode = parse_mode(data.get("file_mode") or cfg.get("file_mode"), DEFAULT_FILE_MODE)

    started = time.time()
    logger.info(f"开始修正配置 '{name}' 的目录权限：{mode_to_string(dir_mode)} / "
                f"{mode_to_string(file_mode)}")
    stats = apply_permission_tree(targets, dir_mode, file_mode)
    elapsed = round(time.time() - started, 2)
    logger.info(f"权限修正完成：{stats}")
    return jsonify({
        "message": (f"已修正 {stats['dirs']} 个目录、{stats['files']} 个文件"
                    f"（{mode_to_string(dir_mode)} / {mode_to_string(file_mode)}），"
                    f"耗时 {elapsed}s"),
        "stats": stats,
        "elapsed": elapsed,
    })


@app.route("/stats", methods=["GET"])
def media_stats():
    """按「执行时会怎么处理」来分类统计，与 process_single_file 的判定保持一致。

    - 文件名能判断出类型（带年份 / S01E01）：按文件名判断
    - 判断不出（如 Iron Man.mkv）：用该配置的「媒体类型」兜底，并计入 fallback
    """
    stats = {
        "movie": 0, "tv_show": 0, "anime": 0,
        "fallback": 0,        # 靠配置的媒体类型兜底的文件数（文件名缺年份/季集）
        "size": {"movie": 0, "tv_show": 0, "anime": 0},
        "unreadable": 0,      # 文件存在但读不到大小（断链软链接 / 挂载失效 / 权限不足）
        "empty": 0,           # 0 字节的空文件
    }
    try:
        for cfg in load_config():
            suffixes = suffix_set(cfg.get("file_suffixes"))
            config_type = cfg.get("file_type", MEDIA_TYPE_MOVIE)
            if config_type not in MEDIA_TYPES:
                config_type = MEDIA_TYPE_MOVIE

            for mapping in cfg.get("paths", []):
                target = mapping.get("target")
                if not target or not os.path.isdir(target):
                    continue
                for root, _, files in os.walk(target):
                    for file in files:
                        if not any(file.lower().endswith(s) for s in suffixes):
                            continue
                        try:
                            info = parse_filename(file, config_type)
                        except Exception:
                            info = {}

                        media_type = info.get("type", "unknown")
                        if media_type in MEDIA_TYPES:
                            if media_type != config_type:
                                # 文件名判断出的类型优先（与执行逻辑一致）
                                logger.debug(f"{file}：文件名判断为 {media_type}，配置为 {config_type}")
                        else:
                            media_type = config_type
                            stats["fallback"] += 1
                        stats[media_type] += 1

                        path = os.path.join(root, file)
                        try:
                            size = os.path.getsize(path)
                        except OSError as e:
                            # 文件在列目录后消失、断链软链接、挂载异常等
                            stats["unreadable"] += 1
                            logger.warning(f"无法读取文件大小：{path}（{e}）")
                            continue
                        if size == 0:
                            stats["empty"] += 1
                        stats["size"][media_type] += size
        stats["scanned_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return jsonify(stats)
    except Exception as e:
        logger.error(f"统计接口异常：{e}")
        return jsonify({"error": str(e)}), 500


# APScheduler 在路由定义之后启动，避免导入期副作用
_cfgs = load_config()
if _cfgs:
    start_scheduler(_cfgs[0].get("schedule_interval", 0))

_record_info = processed_stats()
logger.info(
    f"处理记录：{_record_info['count']} 条（{_record_info['path']}）"
    + ("" if _record_info["exists"] else "；文件尚不存在")
)

# --- 仅本地调试 ---
if __name__ == "__main__":
    debug_port = int(os.getenv("PORT", "5002"))
    print(f"[调试模式] Flask 自带服务器启动：http://127.0.0.1:{debug_port}/")
    app.run(host="0.0.0.0", port=debug_port, debug=True)
