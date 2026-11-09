#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""降 AIGC 引擎的共享入口：定位回测工程（harness）与模型。

skill 目录本身不含模型权重（数 GB），因此这里做路径解析：
  1. 环境变量 AIGC_BENCH_HOME
  2. 仓库内默认位置（本项目布局）
  3. 常见备选位置
找不到时给出明确指引，而不是静默失败。
"""

import os
import sys

CANDIDATES = [
    os.environ.get("AIGC_BENCH_HOME"),
    "/Users/manton/CC/projects/dsh开发/aigc-bench",
    os.path.expanduser("~/CC/projects/dsh开发/aigc-bench"),
    os.path.expanduser("~/aigc-bench"),
]

DETECTOR_DIRS = {
    "d_hello": "Hello-SimpleAI__chatgpt-detector-roberta-chinese",
    "d_cmj": "mokawa3018__cmj-chinese-aigc-text-detector",
    "d_zhv3": "yuchuantian__AIGC_detector_zhv3",
}
GENERATOR_DIRS = {
    "qwen": "Qwen__Qwen2.5-1.5B-Instruct_ms",
}


# 带 torch 的解释器候选。**必须是一个列表，不能只写 bench/.venv**：
# 本地实际把依赖装在 ~/.venvs/aigc，而 bench/.venv 并不存在 ——
# 于是"自动切解释器"这条路径在真机上直接失败（回归测试抓到的）。
VENV_CANDIDATES = [
    os.environ.get("AIGC_VENV_PYTHON"),
    os.path.expanduser("~/.venvs/aigc/bin/python"),
]
REQUIRED_MODULES = ("torch", "transformers", "numpy")


def _venv_python(bench):
    """找一个存在且具备推理依赖的解释器。"""
    import subprocess
    cands = [os.path.join(bench, ".venv", "bin", "python")] + VENV_CANDIDATES
    probe = ("import importlib.util,sys; "
             "sys.exit(0 if all(importlib.util.find_spec(m) "
             f"for m in {REQUIRED_MODULES!r}) else 1)")
    for vpy in cands:
        if not vpy or not os.path.isfile(vpy):
            continue
        try:
            check = subprocess.run([vpy, "-c", probe], capture_output=True,
                                   timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if check.returncode == 0:
            return vpy
    return None


def ensure_venv(bench):
    """若当前解释器缺少推理依赖，则切换到完整环境重新执行。

    依赖（torch/transformers）不在 skill 目录里，用户不该操心这件事。
    只在当前环境缺依赖时切换，避免无限递归。
    """
    import importlib.util
    missing = [name for name in REQUIRED_MODULES
               if importlib.util.find_spec(name) is None]
    if not missing:
        return
    if os.environ.get("_AIGC_VENV_REEXEC") == "1":
        raise SystemExit(
            f"已切换过解释器，但仍缺少依赖：{', '.join(missing)}。\n"
            "请显式指定：export AIGC_VENV_PYTHON=/path/to/bin/python\n"
            "或手动安装：python3 -m venv ~/.venvs/aigc && "
            "~/.venvs/aigc/bin/pip install torch transformers numpy")
    vpy = _venv_python(bench)
    if vpy is None:
        tried = [os.path.join(bench, ".venv", "bin", "python")] + [c for c in VENV_CANDIDATES if c]
        raise SystemExit(
            f"缺少依赖：当前 Python 没有 {', '.join(missing)}，也没有找到可用的解释器。\n"
            "已尝试：\n" + "".join(f"  - {t}\n" for t in tried) +
            "创建方式：\n"
            "  python3 -m venv ~/.venvs/aigc\n"
            "  ~/.venvs/aigc/bin/pip install torch transformers numpy\n"
            "或指定：export AIGC_VENV_PYTHON=/path/to/bin/python")
    env = dict(os.environ, _AIGC_VENV_REEXEC="1")
    os.execve(vpy, [vpy, os.path.abspath(sys.argv[0])] + sys.argv[1:], env)


def find_bench():
    for c in CANDIDATES:
        if c and os.path.isdir(os.path.join(c, "harness")):
            return os.path.abspath(c)
    raise SystemExit(
        "找不到回测工程目录（含 harness/ 与 data/models/）。\n"
        "请设置环境变量 AIGC_BENCH_HOME 指向它，例如：\n"
        "  export AIGC_BENCH_HOME=/path/to/aigc-bench\n"
        f"已尝试：{ [c for c in CANDIDATES if c] }")


def ensure_importable(bench):
    h = os.path.join(bench, "harness")
    if h not in sys.path:
        sys.path.insert(0, h)
    return h


def detector_paths(bench, names):
    """解析全部请求的检测器；缺一个就停止，避免无意改变测量配置。"""
    out, missing = [], []
    for n in names:
        d = DETECTOR_DIRS.get(n, n)
        p = d if os.path.isabs(d) else os.path.join(bench, "data/models", d)
        if os.path.isdir(p):
            out.append(p)
        else:
            missing.append((n, p))
    if missing:
        raise SystemExit(
            "请求的检测器不完整，已停止评分（不会静默改用更少模型）：\n" +
            "".join(f"  - {n}: {p}\n" for n, p in missing) +
            "可用 scripts/fetch_models.py 重新下载：\n"
            f"  cd {bench} && .venv/bin/python harness/fetch_models.py")
    if not out:
        raise SystemExit("未指定检测器；请至少提供一个可用模型。")
    return out


def generator_path(bench, key="qwen"):
    p = os.path.join(bench, "data/models", GENERATOR_DIRS.get(key, key))
    if not os.path.isdir(p):
        raise SystemExit(
            f"改写用的本地生成模型缺失：{p}\n"
            "它用于生成候选改写（数 GB，不在 skill 目录内）。\n"
            f"重新下载：cd {bench} && .venv/bin/python harness/fetch_robust.py Qwen/Qwen2.5-1.5B-Instruct")
    return p


def read_text(path):
    """读文本；.docx/.doc 走 textutil/pandoc。"""
    import subprocess
    if not os.path.exists(path):
        raise SystemExit(f"文件不存在：{path}")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md", ".markdown"):
        # 保留原文件的 CRLF/LF；自动候选按字符 offset 回填未选片段。
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    if ext in (".docx", ".doc"):
        for cmd in (["textutil", "-convert", "txt", "-stdout", path],
                    ["pandoc", "-t", "plain", path]):
            try:
                return subprocess.run(cmd, capture_output=True, check=True) \
                    .stdout.decode("utf-8", errors="replace")
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        raise SystemExit("无法解析 .docx/.doc：需要 macOS textutil 或 pandoc。"
                         "也可先另存为 .txt。")
    raise SystemExit(f"暂不支持该扩展名：{ext}（支持 .txt/.md/.docx/.doc）")
