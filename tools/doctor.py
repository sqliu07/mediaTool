#!/usr/bin/env python3
"""诊断处理记录与目标媒体库的实际状态。

用途：运行后出现「处理记录存在但目标链接已丢失」或重复文件时，
用这个脚本分门别类地数清楚到底发生了什么。

    python3 tools/doctor.py                 # 检查全部记录
    python3 tools/doctor.py --limit 500     # 只查最近 500 条
    python3 tools/doctor.py --sample 8      # 每类最多列 8 个例子
    python3 tools/doctor.py --no-deep       # 不做全库查找（更快）
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from movie_processor import PROCESSED_FILE, CONTENT_DEDUPE_MIN_SIZE  # noqa: E402

CATEGORIES = {
    "ok_inode": "正常：目标目录内同 inode",
    "ok_deep": "目标目录内没有，但在整个媒体库中找到（被移到了别处）",
    "ok_content": "目标目录内没有，但全库有内容相同的文件（改名/复制过）",
    "dest_missing": "目标目录不存在（配置的路径变了？）",
    "src_missing": "源文件已不存在（记录过期）",
    "really_lost": "目标里确实找不到（会被重建）",
    "old_format": "旧格式记录（没有目标文件名，只能靠 inode 判断）",
}


def load_records(limit=None):
    """读取 processed.txt，返回 [(源路径, 目标目录, 目标文件名或空)]。"""
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
                records.append((parts[0], parts[1], parts[2]))
            elif len(parts) == 2:
                records.append((parts[0], parts[1], ""))
            else:
                records.append((parts[0], "", ""))
    if limit:
        records = records[-limit:]
    return records


def scan_dir(dir_path, cache):
    """返回 (inode 集合, 签名集合)，按目录缓存。"""
    if dir_path in cache:
        return cache[dir_path]
    inodes, sigs = {}, {}

    def walk(root):
        for current, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(current, name)
                try:
                    st = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                inodes[(st.st_dev, st.st_ino)] = path
                sigs.setdefault((st.st_size, st.st_mtime_ns), path)

    walk(dir_path)
    cache[dir_path] = (inodes, sigs)
    return cache[dir_path]


def main():
    ap = argparse.ArgumentParser(description="检查处理记录与目标库的实际状态")
    ap.add_argument("--limit", type=int, default=None, help="只检查最近 N 条记录")
    ap.add_argument("--sample", type=int, default=5, help="每类最多展示几个例子")
    ap.add_argument("--no-deep", action="store_true", help="跳过全库查找（更快，但无法识别被移动的文件）")
    args = ap.parse_args()

    records = load_records(args.limit)
    if not records:
        return 1
    print(f"处理记录文件：{os.path.abspath(PROCESSED_FILE)}")
    print(f"共 {len(records)} 条待检查记录\n")

    counts = Counter()
    samples = defaultdict(list)
    dir_cache = {}
    deep_cache = {}

    def note(cat, text):
        counts[cat] += 1
        if len(samples[cat]) < args.sample:
            samples[cat].append(text)

    for src, dest_dir, dest_name in records:
        if not dest_dir:
            note("old_format", src)
            continue
        if not dest_name:
            counts["old_format"] += 1
        if not os.path.exists(src):
            note("src_missing", src)
            continue
        if not os.path.isdir(dest_dir):
            note("dest_missing", f"{src}\n      目标目录: {dest_dir}")
            continue
        try:
            st = os.stat(src)
        except OSError:
            note("src_missing", src)
            continue
        key = (st.st_dev, st.st_ino)

        # 记录里的目标文件名还在？
        if dest_name and os.path.exists(os.path.join(dest_dir, dest_name)):
            counts["ok_inode"] += 1          # 名字命中，最可靠
            continue

        inodes, sigs = scan_dir(dest_dir, dir_cache)
        found = inodes.get(key)
        if found:
            note("ok_deep", f"{src}\n      实际在: {found}（同 inode，只是名字/位置变了）")
            continue

        if args.no_deep:
            note("really_lost", src)
            continue

        root = os.path.dirname(dest_dir) if dest_dir not in dir_cache else dest_dir
        if root not in deep_cache:
            deep_cache[root] = scan_dir(root, {})
        deep_inodes, deep_sigs = deep_cache[root]
        found = deep_inodes.get(key)
        if found:
            note("ok_deep", f"{src}\n      实际在: {found}（同 inode，被移到了别的目录）")
            continue

        if st.st_size >= CONTENT_DEDUPE_MIN_SIZE:
            found = deep_sigs.get((st.st_size, st.st_mtime_ns))
            if found:
                note("ok_content", f"{src}\n      内容相同: {found}")
                continue

        note("really_lost", src)

    print("检查结果：")
    for cat, label in CATEGORIES.items():
        if counts[cat]:
            print(f"  {counts[cat]:6d}  {label}")

    for cat, label in CATEGORIES.items():
        if not samples[cat]:
            continue
        print(f"\n【{label}】示例：")
        for text in samples[cat]:
            print(f"    {text}")

    print("\n结论提示：")
    if counts["ok_deep"] or counts["ok_content"]:
        print("  · 有文件只是被移动/改名了，v1.0.2 起应该能识别，不会再重复。")
    if counts["really_lost"]:
        print("  · 「目标里确实找不到」的文件会被重建；如果它们其实还在，")
        print("    多半是目标库在网络挂载上，inode / 修改时间不稳定。")
    if counts["dest_missing"]:
        print("  · 目标目录不存在的记录指向旧配置，建议清空处理记录后重跑一次。")
    if counts["old_format"]:
        print("  · 旧格式记录只能靠 inode 判断，v1.0.3 起会写入目标文件名，升级后重跑可补齐。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
