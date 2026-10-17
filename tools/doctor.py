#!/usr/bin/env python3
"""诊断处理记录与目标媒体库的实际状态。

运行后出现「处理记录存在但目标链接已丢失」或者重复文件时，用这个脚本
分门别类地数清楚到底发生了什么。

    python3 tools/doctor.py                 # 检查全部记录
    python3 tools/doctor.py --limit 500     # 只查最近 500 条
    python3 tools/doctor.py --sample 8      # 每类最多列 8 个例子
    python3 tools/doctor.py --no-deep       # 不扫描整个媒体库（更快）
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from movie_processor import (  # noqa: E402
    CONFIG_DIR,
    CONTENT_DEDUPE_MIN_SIZE,
    PROCESSED_FILE,
)

CATEGORIES = {
    "ok_name": "正常：记录里的目标文件名还在",
    "ok_inode": "正常：目标目录内找到同 inode",
    "ok_moved": "目标目录内没有，但在媒体库其他位置找到（被移动 / 改名）",
    "ok_content": "目标目录内没有，但库里有内容相同的文件（被复制替换过）",
    "legacy_found": "旧格式记录（只有源路径），能在媒体库里找到",
    "legacy_missing": "旧格式记录（只有源路径），媒体库里找不到",
    "dest_missing": "目标目录不存在（配置路径变过？）",
    "src_missing": "源文件已不存在（记录过期）",
    "really_lost": "确实找不到（下次运行会被重建）",
}


def load_target_roots():
    """从配置里读出所有目标媒体库根目录。"""
    path = os.path.join(CONFIG_DIR, "config.json")
    roots = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            configs = json.load(f)
    except Exception:
        return roots
    if not isinstance(configs, list):
        return roots
    for cfg in configs:
        for mapping in cfg.get("paths", []) or []:
            target = (mapping or {}).get("target")
            if target and target not in roots:
                roots.append(target)
    return roots


def load_records(limit=None):
    """读取 processed.txt，返回 [(源路径, 目标目录, 目标文件名)]，后两项可能为空。"""
    if not os.path.exists(PROCESSED_FILE):
        print(f"找不到处理记录：{os.path.abspath(PROCESSED_FILE)}")
        return []
    records = []
    with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 3:
                records.append((parts[0], parts[1], "|".join(parts[2:])))
            elif len(parts) == 2:
                records.append((parts[0], parts[1], ""))
            else:
                records.append((parts[0], "", ""))
    if limit:
        records = records[-limit:]
    return records


def build_tree_index(root, cache):
    """整棵目录树的索引：inode -> 路径、(大小, mtime) -> 路径。"""
    if root in cache:
        return cache[root]
    inodes, signatures = {}, {}
    if os.path.isdir(root):
        for current, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(current, name)
                try:
                    st = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                inodes.setdefault((st.st_dev, st.st_ino), path)
                signatures.setdefault((st.st_size, st.st_mtime_ns), path)
    cache[root] = (inodes, signatures)
    return cache[root]


def _under(path, root):
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


def main():
    ap = argparse.ArgumentParser(description="检查处理记录与目标库的实际状态")
    ap.add_argument("--limit", type=int, default=None, help="只检查最近 N 条记录")
    ap.add_argument("--sample", type=int, default=5, help="每类最多展示几个例子")
    ap.add_argument("--no-deep", action="store_true", help="跳过整库扫描（更快）")
    args = ap.parse_args()

    records = load_records(args.limit)
    if not records:
        return 1
    targets = load_target_roots()

    print(f"处理记录文件：{os.path.abspath(PROCESSED_FILE)}")
    print(f"待检查记录：{len(records)} 条")
    print(f"配置里的目标根目录：{len(targets)} 个")
    for target in targets:
        print(f"    {target}  {'（存在）' if os.path.isdir(target) else '（不存在！）'}")
    print()

    counts = Counter()
    samples = defaultdict(list)
    tree_cache = {}

    def note(cat, text):
        counts[cat] += 1
        if len(samples[cat]) < args.sample:
            samples[cat].append(text)

    def lookup(paths, key):
        for root in paths:
            inodes, _ = build_tree_index(root, tree_cache)
            if key in inodes:
                return inodes[key]
        return None

    def lookup_signature(paths, signature):
        for root in paths:
            _inodes, signatures = build_tree_index(root, tree_cache)
            hit = signatures.get(signature)
            if hit:
                return hit
        return None

    for src, dest_dir, dest_name in records:
        try:
            st = os.stat(src)
        except OSError:
            note("src_missing", src)
            continue
        key = (st.st_dev, st.st_ino)

        # —— 单段旧记录：不知道处理到哪个目标目录，只能到各媒体库里找 ——
        if not dest_dir:
            if args.no_deep:
                note("legacy_missing", src)
                continue
            found = lookup(targets, key)
            if found:
                note("legacy_found", f"{src}\n      实际在: {found}")
            else:
                note("legacy_missing", src)
            continue

        # —— 有目标目录：先看记录里的目标文件名 ——
        if dest_name and os.path.exists(os.path.join(dest_dir, dest_name)):
            counts["ok_name"] += 1
            continue

        dest_exists = os.path.isdir(dest_dir)

        # 先在媒体库里找这个文件（同 inode）：目标目录不存在时也要找，
        # 因为文件可能被 TMM 之类整理进了别的目录结构
        found = None if args.no_deep else lookup(targets, key)
        if found:
            if dest_exists and _under(found, dest_dir):
                note("ok_inode", f"{src}\n      目标目录内: {found}")
            else:
                note("ok_moved", f"{src}\n      实际在: {found}")
            continue

        if not dest_exists:
            note("dest_missing", f"{src}\n      目标目录: {dest_dir}")
            continue

        # 内容一致（被复制替换过）
        if not args.no_deep and st.st_size >= CONTENT_DEDUPE_MIN_SIZE:
            hit = lookup_signature(targets, (st.st_size, st.st_mtime_ns))
            if hit:
                note("ok_content", f"{src}\n      内容相同: {hit}")
                continue

        note("really_lost", src)

    print("检查结果：")
    for cat, label in CATEGORIES.items():
        if counts[cat]:
            print(f"  {counts[cat]:6d}  {label}")
    if not sum(counts.values()):
        print("  （没有匹配的记录）")

    for cat, label in CATEGORIES.items():
        if not samples[cat]:
            continue
        print(f"\n【{label}】示例：")
        for text in samples[cat]:
            print(f"    {text}")

    print("\n结论提示：")
    if counts["ok_moved"] or counts["ok_content"]:
        print("  · 有文件只是被移动 / 改名 / 替换过，v1.0.2 起能识别，不会再重复创建。")
    if counts["legacy_found"] or counts["legacy_missing"]:
        print("  · 存在「只有源路径」的旧格式记录：它们不知道处理到了哪个目标目录、叫什么名字，")
        print("    只能靠 inode 判断。建议清空处理记录，用 v1.0.3 重跑一次补齐三段格式")
        print("    （从 v1.0.2 起，已存在的文件不会被重复创建）。")
    if counts["really_lost"]:
        print("  · 「确实找不到」的文件会被重建；如果它们其实还在，多半是媒体库在网络挂载上，")
        print("    inode / 修改时间不稳定。")
    if counts["dest_missing"]:
        print("  · 目标目录不存在的记录指向旧配置，建议清空记录后重跑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
