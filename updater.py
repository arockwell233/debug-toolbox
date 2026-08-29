# -*- coding: utf-8 -*-
"""
在线更新模块
============
更新源：GitHub 仓库（通过 api.github.com 读取，国内网络更稳定）
  * 版本信息：仓库根目录 version.json（{"version":"1.0.0"}）
  * 程序文件：仓库根目录 调试工具箱.exe（通过 contents API 下载）
更新方式：下载新版到临时目录 → 生成替换脚本 → 退出当前程序 → 脚本替换并重启
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


def get_app_dir():
    """当前程序所在目录（exe 或脚本目录）。"""
    return Path(sys.argv[0] if getattr(sys, "frozen", False) else __file__).resolve().parent


def parse_version(v):
    """把 '1.2.3' 转成可比较的元组 (1,2,3)。"""
    nums = re.findall(r"\d+", str(v))
    return tuple(int(x) for x in nums[:3]) or (0,)


def _api_get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "debug-toolbox",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _contents_url(repo, path):
    quoted = urllib.parse.quote(path)
    return "https://api.github.com/repos/%s/contents/%s" % (repo, quoted)


def fetch_remote_version(repo, timeout=30):
    """读取远程 version.json，返回版本字符串；失败返回 None。"""
    try:
        data = _api_get(_contents_url(repo, "version.json"), timeout)
        content = base64.b64decode(data["content"].replace("\n", "")).decode("utf-8")
        info = json.loads(content)
        return str(info.get("version", "")).strip()
    except Exception:
        return None


def fetch_remote_notes(repo, timeout=30):
    """读取 README/更新说明（可选），失败返回空串。"""
    return ""


def download_exe(repo, dest, timeout=120):
    """通过 GitHub contents API 下载仓库根目录的 调试工具箱.exe。"""
    data = _api_get(_contents_url(repo, "调试工具箱.exe"), timeout)
    content = base64.b64decode(data["content"].replace("\n", ""))
    with open(dest, "wb") as f:
        f.write(content)
    return len(content)


def is_newer(local_version, remote_version):
    if not remote_version:
        return False
    return parse_version(remote_version) > parse_version(local_version)


def self_update(new_exe_path):
    """把下载好的新版 exe 替换当前运行的 exe 并重启。返回说明文字。"""
    if not getattr(sys, "frozen", False):
        # 源码版：无法自动替换自身，提示用户
        return "当前是源码版，无法自动替换。请手动用新版文件覆盖，或使用 exe 版。"
    current = sys.executable
    app_dir = Path(current).parent
    bat_path = app_dir / "_update_toolbox.bat"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "timeout /t 2 /nobreak >nul",
        'del /f /q "%s"' % current,
        'move /y "%s" "%s"' % (new_exe_path, current),
        'start "" "%s"' % current,
        'del "%~f0"',
    ]
    try:
        bat_path.write_text("\r\n".join(lines), encoding="gbk")
    except Exception:
        # 如果程序目录不可写（如Program Files），回退到临时目录
        tmp = Path(tempfile.gettempdir()) / "_update_toolbox.bat"
        tmp.write_text("\r\n".join(lines), encoding="gbk")
        bat_path = tmp
    try:
        subprocess.Popen(["cmd", "/c", str(bat_path)],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return "无法启动更新脚本"
    return "OK"

def download_and_apply(repo, timeout=120):
    """下载新版 exe 并应用替换，返回 (是否成功, 说明)。"""
    if not getattr(sys, "frozen", False):
        return False, "当前是源码版，请手动替换文件或改用 exe 版。"
    dest = os.path.join(tempfile.gettempdir(), "调试工具箱_new.exe")
    try:
        n = download_exe(repo, dest, timeout)
        if n < 1000000:
            return False, "下载的文件异常（过小），请稍后重试。"
        msg = self_update(dest)
        if msg == "OK":
            return True, "OK"
        return False, msg
    except Exception as e:
        return False, "更新失败：%s" % e
