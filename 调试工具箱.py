#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试工具箱 (Debug Toolbox)
==========================
一个类似"图吧工具箱"的快捷启动器，把常用软件集中成按钮：
  * 本地程序 (.exe / .bat / .lnk 等) —— 一键启动
  * 网址 / 路由器后台 —— 用默认浏览器打开（可直接填 IP，如 192.168.1.1）
  * 文件夹 —— 直接在资源管理器中打开

按钮名称、路径、分类、颜色、图标都可以自定义。
支持扫描指定文件夹，把里面的程序自动生成按钮。

配置文件：与本程序同目录的 config.json
运行日志：与本程序同目录的 debug.log

用法：
  python 调试工具箱.py
  python 调试工具箱.py --selftest       # 自检（不打开界面）
  python 调试工具箱.py --snapshot x.png # 截图（测试用）
"""

import ctypes
import datetime
import json
import os
import re
import sys
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

from ip_scanner import IPScanDialog
from hex_translator import HexTranslateDialog

# ---------------------------------------------------------------- 常量与工具

APP_DIR = Path(sys.argv[0] if getattr(sys, "frozen", False) else __file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "debug.log"

APP_VERSION = "1.0.0"

CARD_W = 150
CARD_H = 138
GRID_PAD = 10

CATEGORY_COLORS = {
    "FTU": "#4a90d9",
    "路由器": "#e67e22",
    "其他": "#27ae60",
}
FALLBACK_COLORS = [
    "#4a90d9", "#e67e22", "#27ae60", "#9b59b6",
    "#e74c3c", "#16a085", "#f39c12", "#34495e",
]
FONT = "Microsoft YaHei UI"

TYPE_TEXT = {"exe": "程序", "url": "网页", "folder": "文件夹"}
TYPE_CHOICES = [("exe", "程序 / 文件 (.exe 等)"), ("url", "网址 / 路由器 (IP)"), ("folder", "文件夹")]
SCAN_EXTS = (".exe", ".bat", ".cmd", ".msi", ".lnk", ".com")


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def lighten(hex_color, amount=0.2):
    """把 #rrggbb 颜色向白色混合 amount(0~1)，返回新颜色。"""
    try:
        h = hex_color.lstrip("#")
        if len(h) != 6:
            return hex_color
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r = int(r + (255 - r) * amount)
        g = int(g + (255 - g) * amount)
        b = int(b + (255 - b) * amount)
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return hex_color


def normalize_url(text):
    text = (text or "").strip()
    if not text:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", text):
        text = "http://" + text
    return text


# ---------------------------------------------------------------- 启动逻辑

def shell_open(path, params="", verb="open"):
    """用 ShellExecute 打开文件/文件夹，自动把工作目录设为文件所在目录。"""
    directory = os.path.dirname(path) if os.path.isfile(path) else None
    result = ctypes.windll.shell32.ShellExecuteW(
        None, verb, str(path), params or None, directory or None, 1
    )
    if result <= 32:
        raise OSError("ShellExecute 返回错误代码 %s" % result)


def launch_button(btn, parent=None):
    typ = (btn.get("type") or "exe").lower()
    path = (btn.get("path") or "").strip()
    if not path:
        if parent is not None:
            messagebox.showwarning("提示", "该按钮还没有设置路径。\n请右键按钮 → 编辑 进行设置。", parent=parent)
        return False

    if typ == "url":
        url = normalize_url(path)
        log("打开网址: %s" % url)
        webbrowser.open(url)
        return True

    if typ == "folder":
        if not os.path.isdir(path):
            if parent is not None:
                messagebox.showerror("无法打开", "文件夹不存在：\n%s" % path, parent=parent)
            return False
        log("打开文件夹: %s" % path)
        shell_open(path)
        return True

    # 程序 / 文件
    if not os.path.exists(path):
        if parent is not None:
            messagebox.showerror("无法打开", "文件不存在：\n%s" % path, parent=parent)
        return False
    args = (btn.get("args") or "").strip()
    verb = "runas" if btn.get("run_as_admin") else "open"
    log("启动程序: %s %s (verb=%s)" % (path, args, verb))
    shell_open(path, args, verb)
    return True


# ---------------------------------------------------------------- 配置

class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = Path(path)
        self.data = {
            "app_name": "调试工具箱",
            "window": {"width": 1024, "height": 680},
            "scan_base_dir": "",
            "update": {"repo": "arockwell233/debug-toolbox", "auto_check": True},
            "categories": [
                {"name": "FTU", "color": "#4a90d9"},
                {"name": "路由器", "color": "#e67e22"},
                {"name": "其他", "color": "#27ae60"},
            ],
            "buttons": [],
        }
        self.load()

    # ---------- 读写 ----------
    def load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                for k in ("app_name", "window", "scan_base_dir", "update", "categories", "buttons"):
                    if k in raw:
                        self.data[k] = raw[k]
                if not isinstance(self.data.get("categories"), list):
                    self.data["categories"] = []
                if not isinstance(self.data.get("buttons"), list):
                    self.data["buttons"] = []
        except Exception as e:
            log("读取配置失败: %s" % e)

    def save(self):
        try:
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            log("保存配置失败: %s" % e)

    # ---------- 分类 ----------
    def category_names(self):
        return [c.get("name", "") for c in self.data.get("categories", []) if c.get("name")]

    def category_color(self, name):
        for c in self.data.get("categories", []):
            if c.get("name") == name and c.get("color"):
                return c["color"]
        return CATEGORY_COLORS.get(name, "")

    def ensure_category(self, name):
        names = self.category_names()
        if name and name not in names:
            idx = len(names)
            color = FALLBACK_COLORS[idx % len(FALLBACK_COLORS)]
            self.data["categories"].append({"name": name, "color": color})
            self.save()

    def set_category_color(self, name, color):
        for c in self.data.get("categories", []):
            if c.get("name") == name:
                c["color"] = color
                self.save()
                return
        self.data["categories"].append({"name": name, "color": color})
        self.save()

    def rename_category(self, old, new):
        new = (new or "").strip()
        if not new or old == new:
            return
        for c in self.data.get("categories", []):
            if c.get("name") == old:
                c["name"] = new
        for b in self.data.get("buttons", []):
            if b.get("category") == old:
                b["category"] = new
        self.save()

    def delete_category(self, name):
        if any(b.get("category") == name for b in self.data.get("buttons", [])):
            return False
        self.data["categories"] = [c for c in self.data.get("categories", []) if c.get("name") != name]
        self.save()
        return True

    # ---------- 按钮 ----------
    def buttons(self):
        return self.data.get("buttons", [])

    def button_color(self, btn):
        return ((btn.get("color") or "").strip()
                or self.category_color(btn.get("category", ""))
                or FALLBACK_COLORS[0])

    def add_button(self, btn):
        self.data["buttons"].append(btn)
        self.save()

    def update_button(self, index, btn):
        self.data["buttons"][index] = btn
        self.save()

    def delete_button(self, index):
        del self.data["buttons"][index]
        self.save()

# ---------------------------------------------------------------- 小部件

class Tooltip:
    """简单悬浮提示。"""

    def __init__(self, widget, text_func, delay=550):
        self.widget = widget
        self.text_func = text_func
        self.delay = delay
        self.after_id = None
        self.tip = None
        widget.bind("<Enter>", lambda e: self._schedule(), add="+")
        widget.bind("<Leave>", lambda e: self._hide(), add="+")
        widget.bind("<Button-1>", lambda e: self._hide(), add="+")

    def _schedule(self):
        self._cancel()
        self.after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        text = self.text_func()
        if not text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry("+%d+%d" % (x, y))
        lab = tk.Label(self.tip, text=text, justify="left", bg="#2b2f3a", fg="#ffffff",
                       font=(FONT, 9), padx=9, pady=6, wraplength=560)
        lab.pack()

    def _cancel(self):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class ScrollFrame(tk.Frame):
    """可滚动的容器。"""

    def __init__(self, master, bg="#f2f3f5"):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.bind("<Configure>", self._on_canvas_conf)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

    def _on_canvas_conf(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)


# ---------------------------------------------------------------- 主窗口

class ToolboxApp(tk.Tk):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.edit_mode = False
        self.current_category = "全部"
        self.search_var = tk.StringVar()
        self._search_job = None
        self._image_cache = {}
        self.current_buttons = []

        self.title("%s v1.0" % config.data.get("app_name", "调试工具箱"))
        w = int(config.data.get("window", {}).get("width", 1024))
        h = int(config.data.get("window", {}).get("height", 680))
        self.geometry("%dx%d" % (w, h))
        self.minsize(780, 540)
        self.configure(bg="#f2f3f5")

        self._build_topbar()
        self._build_body()
        self._build_statusbar()

        self.search_var.trace_add("write", self._on_search_changed)
        self._load_sidebar()
        self.refresh()
        if (config.data.get("update", {}) or {}).get("auto_check"):
            self.after(2500, self._auto_check_update)

    # ---------------- 界面搭建 ----------------
    def _build_topbar(self):
        top = tk.Frame(self, bg="#2b2f3a", height=56)
        top.pack(side="top", fill="x")
        top.pack_propagate(False)

        title = tk.Label(top, text=self.config.data.get("app_name", "调试工具箱"),
                         bg="#2b2f3a", fg="#ffffff", font=(FONT, 14, "bold"))
        title.pack(side="left", padx=(16, 6), pady=12)

        # 搜索框
        box = tk.Frame(top, bg="#3a4150")
        box.pack(side="left", padx=8, pady=10)
        self.search_entry = tk.Entry(box, textvariable=self.search_var, width=30,
                                     bg="#3a4150", fg="#ffffff", insertbackground="#ffffff",
                                     relief="flat", font=(FONT, 10))
        self.search_entry.pack(side="left", ipady=4, padx=(10, 4))
        hint = tk.Label(box, text="搜索", bg="#3a4150", fg="#9aa4b2", font=(FONT, 9))
        hint.pack(side="right", padx=(0, 10))

        # 右侧工具按钮
        btns = tk.Frame(top, bg="#2b2f3a")
        btns.pack(side="right", padx=12)
        self.edit_btn = self._tool_button(btns, "✎ 编辑模式", self._toggle_edit)
        self._tool_button(btns, "＋ 添加", self._add_button)
        self._tool_button(btns, "扫描文件夹", self._scan_button)
        self._tool_button(btns, "设置", self._settings_button)
        self._tool_button(btns, "IP扫描", self._ip_scan_button)
        self._tool_button(btns, "报文翻译", self._hex_button)

    def _tool_button(self, parent, text, command):
        b = tk.Button(parent, text=text, command=command, bg="#3a4150", fg="#ffffff",
                      activebackground="#4a90d9", activeforeground="#ffffff",
                      relief="flat", bd=0, padx=12, pady=6, cursor="hand2", font=(FONT, 9))
        b.pack(side="left", padx=4)
        return b

    def _build_body(self):
        body = tk.Frame(self, bg="#f2f3f5")
        body.pack(side="top", fill="both", expand=True)

        # 左侧分类栏
        side = tk.Frame(body, bg="#e8eaed", width=176)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        lab = tk.Label(side, text="分 类", bg="#d9dce1", fg="#555555",
                       font=(FONT, 10, "bold"), anchor="w", padx=12)
        lab.pack(fill="x", ipady=5)
        self.side_list = tk.Listbox(side, bg="#e8eaed", fg="#333333",
                                    selectbackground="#cfe3f7", selectforeground="#1f5fa8",
                                    highlightthickness=0, bd=0, activestyle="none",
                                    font=(FONT, 10), exportselection=False)
        self.side_list.pack(side="left", fill="both", expand=True, padx=(0, 2))
        self.side_list.bind("<<ListboxSelect>>", self._on_category_select)
        self.side_list.bind("<Button-3>", self._on_sidebar_menu)

        # 右侧按钮区
        self.main = ScrollFrame(body, bg="#f2f3f5")
        self.main.pack(side="left", fill="both", expand=True)
        self.bind_all("<MouseWheel>", self._on_mousewheel)

    def _build_statusbar(self):
        bar = tk.Frame(self, bg="#e6e8eb", height=26)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        self.status_label = tk.Label(bar, text="", bg="#e6e8eb", fg="#666666",
                                     font=(FONT, 9), anchor="w")
        self.status_label.pack(side="left", padx=12)
        self.status_tip = tk.Label(bar, text="左键单击：打开  ｜  右键：更多操作",
                                   bg="#e6e8eb", fg="#999999", font=(FONT, 9))
        self.status_tip.pack(side="right", padx=12)

    # ---------------- 数据与刷新 ----------------
    def _load_sidebar(self):
        self.side_list.delete(0, "end")
        cats = ["全部"] + self.config.category_names()
        counts = self._category_counts()
        for c in cats:
            self.side_list.insert("end", "%s (%d)" % (c, counts.get(c, 0)))
        try:
            idx = cats.index(self.current_category) if self.current_category in cats else 0
        except ValueError:
            idx = 0
        self.side_list.selection_clear(0, "end")
        self.side_list.selection_set(idx)
        self.side_list.activate(idx)

    def _category_counts(self):
        counts = {"全部": 0}
        for b in self.config.buttons():
            counts["全部"] += 1
            cat = b.get("category") or "未分类"
            counts[cat] = counts.get(cat, 0) + 1
        for c in self.config.category_names():
            counts.setdefault(c, 0)
        return counts

    def _filtered_buttons(self):
        kw = self.search_var.get().strip().lower()
        cat = self.current_category
        out = []
        for b in self.config.buttons():
            if cat != "全部" and (b.get("category") or "未分类") != cat:
                continue
            if kw:
                hay = " ".join([
                    str(b.get("name", "")), str(b.get("path", "")),
                    str(b.get("category", "")), str(b.get("note", "")),
                ]).lower()
                if kw not in hay:
                    continue
            out.append(b)
        return out

    def refresh(self):
        self.current_buttons = self._filtered_buttons()
        for child in self.main.inner.winfo_children():
            child.destroy()

        if not self.current_buttons:
            msg = ("没有找到按钮\n\n点击右上角「＋ 添加」手动添加，\n"
                   "或用「扫描文件夹」自动把里面的程序生成按钮。")
            tk.Label(self.main.inner, text=msg, bg="#f2f3f5", fg="#999999",
                     font=(FONT, 12)).pack(pady=90)
        else:
            width = self.main.canvas.winfo_width()
            cols = max(1, (width - 6) // (CARD_W + GRID_PAD)) if width > 50 else 4
            for i, btn in enumerate(self.current_buttons):
                card = self._make_card(btn)
                r, c = divmod(i, cols)
                card.grid(row=r, column=c, padx=GRID_PAD // 2, pady=GRID_PAD // 2, sticky="n")

        n = len(self.current_buttons)
        total = len(self.config.buttons())
        self.status_label.config(
            text="共 %d 个按钮（当前分类 %d 个）  |  分类：%s" % (total, n, self.current_category)
            + ("  ｜  编辑模式：单击按钮 = 编辑" if self.edit_mode else ""))

    # ---------------- 卡片 ----------------
    def _make_card(self, btn):
        color = self.config.button_color(btn)
        card = tk.Frame(self.main.inner, bg=color, width=CARD_W, height=CARD_H, cursor="hand2")
        card.pack_propagate(False)
        card.grid_propagate(False)
        card._orig_color = color

        icon = self._make_icon(card, btn, color)
        icon.pack(pady=(12, 6))

        name = tk.Label(card, text=btn.get("name", "未命名"), bg=color, fg="#ffffff",
                        font=(FONT, 10, "bold"), wraplength=CARD_W - 24, justify="center")
        name.pack(fill="x", padx=8)

        typ = (btn.get("type") or "exe").lower()
        badge = tk.Label(card, text=TYPE_TEXT.get(typ, "程序"), bg=lighten(color, 0.28),
                         fg="#ffffff", font=(FONT, 8), padx=8, pady=1)
        badge.pack(pady=(2, 0))

        if self.edit_mode:
            eb = tk.Label(card, text="✎ 编辑", bg="#000000", fg="#ffffff", font=(FONT, 8))
            eb.place(relx=1.0, rely=0.0, anchor="ne")

        for w in (card, icon, name, badge):
            w.bind("<Button-1>", lambda e, b=btn: self._on_card_click(b))
            w.bind("<Button-3>", lambda e, b=btn: self._on_card_menu(e, b))
            w.bind("<Enter>", lambda e, c=card: self._on_card_enter(c))
            w.bind("<Leave>", lambda e, c=card: self._on_card_leave(c))
        Tooltip(card, lambda b=btn: self._card_tooltip(b))
        return card

    def _make_icon(self, parent, btn, color):
        icon_path = (btn.get("icon") or "").strip()
        if icon_path and os.path.isfile(icon_path):
            img = self._load_icon_image(icon_path, 52)
            if img:
                lab = tk.Label(parent, image=img, bg=color)
                lab.image = img
                return lab
        # 无图标时显示首字头像
        ch = (btn.get("name") or "?").strip()[:1] or "?"
        c = tk.Canvas(parent, width=54, height=54, bg=color, highlightthickness=0)
        c.create_oval(1, 1, 53, 53, fill=lighten(color, 0.22), outline="")
        c.create_text(27, 27, text=ch, fill="#ffffff", font=(FONT, 22, "bold"))
        return c

    def _load_icon_image(self, path, size=52):
        key = (path.lower(), size)
        if key in self._image_cache:
            return self._image_cache[key]
        img = None
        try:
            from PIL import Image, ImageTk
            im = Image.open(path).convert("RGBA")
            im.thumbnail((size, size))
            img = ImageTk.PhotoImage(im)
        except Exception:
            try:
                img = tk.PhotoImage(file=path)
            except Exception:
                img = None
        self._image_cache[key] = img
        return img

    def _on_card_enter(self, card):
        card.configure(bg=lighten(card._orig_color, 0.15))
        for ch in card.winfo_children():
            try:
                ch.configure(bg=lighten(card._orig_color, 0.15))
            except Exception:
                pass

    def _on_card_leave(self, card):
        card.configure(bg=card._orig_color)
        for ch in card.winfo_children():
            try:
                ch.configure(bg=card._orig_color)
            except Exception:
                pass

    def _card_tooltip(self, btn):
        lines = [btn.get("name", "")]
        p = (btn.get("path") or "").strip()
        if p:
            lines.append(p)
        if btn.get("note"):
            lines.append("备注：" + btn["note"])
        if btn.get("run_as_admin"):
            lines.append("以管理员身份运行")
        return "\n".join(lines)

    # ---------------- 交互 ----------------
    def _on_mousewheel(self, event):
        if event.widget.winfo_toplevel() is self:
            self.main.canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _on_search_changed(self, *_):
        if self._search_job:
            try:
                self.after_cancel(self._search_job)
            except Exception:
                pass
        self._search_job = self.after(180, self.refresh)

    def _on_category_select(self, _e=None):
        sel = self.side_list.curselection()
        if not sel:
            return
        text = self.side_list.get(sel[0])
        self.current_category = text.rsplit(" (", 1)[0] if " (" in text else text
        self.refresh()

    def _on_card_click(self, btn):
        if self.edit_mode:
            self._edit_button(btn)
        else:
            self._launch(btn)

    def _launch(self, btn):
        if launch_button(btn, parent=self):
            self.status_label.config(text="已打开：%s" % btn.get("name", ""))

    def _on_card_menu(self, event, btn):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="打开", command=lambda: self._launch(btn))
        menu.add_command(label="编辑…", command=lambda: self._edit_button(btn))
        menu.add_command(label="删除…", command=lambda: self._delete_button(btn))
        menu.add_separator()
        menu.add_command(label="打开所在文件夹", command=lambda: self._open_containing(btn))
        menu.add_command(label="复制路径", command=lambda: self._copy_path(btn))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_containing(self, btn):
        path = (btn.get("path") or "").strip()
        if (btn.get("type") or "exe").lower() == "url" or not path:
            messagebox.showinfo("提示", "该按钮是网页，没有本地路径。", parent=self)
            return
        folder = os.path.dirname(path) if os.path.isfile(path) else path
        if folder and os.path.isdir(folder):
            shell_open(folder)
        else:
            messagebox.showwarning("提示", "找不到所在文件夹：\n%s" % path, parent=self)

    def _copy_path(self, btn):
        p = (btn.get("path") or "").strip()
        if p:
            self.clipboard_clear()
            self.clipboard_append(p)
            self.status_label.config(text="已复制路径：%s" % p)

    # ---------------- 编辑模式 / 增删改 ----------------
    def _toggle_edit(self):
        self.edit_mode = not self.edit_mode
        self.edit_btn.config(text="✔ 完成编辑" if self.edit_mode else "✎ 编辑模式")
        self.edit_btn.config(bg="#e67e22" if self.edit_mode else "#3a4150")
        self.refresh()

    def _add_button(self):
        ButtonDialog(self, self.config, button=None, on_save=self._on_added)

    def _on_added(self, btn):
        self.config.add_button(btn)
        self._after_change()
        self._select_category(btn.get("category", ""))

    def _edit_button(self, btn):
        try:
            index = self.config.buttons().index(btn)
        except ValueError:
            return

        def on_save(new_btn):
            self.config.update_button(index, new_btn)
            self._after_change()

        ButtonDialog(self, self.config, button=btn, on_save=on_save)

    def _delete_button(self, btn):
        if messagebox.askyesno("删除按钮", "确定删除按钮「%s」吗？" % btn.get("name", ""), parent=self):
            try:
                self.config.delete_button(self.config.buttons().index(btn))
            except ValueError:
                pass
            self._after_change()

    def _select_category(self, name):
        cats = ["全部"] + self.config.category_names()
        self.current_category = name if name in cats else "全部"
        self._load_sidebar()
        self.refresh()

    def _after_change(self):
        self._load_sidebar()
        self.refresh()

    # ---------------- 分类右键菜单 ----------------
    def _on_sidebar_menu(self, event):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="新建分类…", command=self._new_category)
        idx = self.side_list.nearest(event.y)
        if idx is not None and idx >= 0:
            text = self.side_list.get(idx)
            cat = text.rsplit(" (", 1)[0] if " (" in text else text
            if cat != "全部":
                menu.add_separator()
                menu.add_command(label="重命名「%s」…" % cat, command=lambda c=cat: self._rename_category(c))
                menu.add_command(label="设置「%s」颜色…" % cat, command=lambda c=cat: self._pick_category_color(c))
                menu.add_command(label="删除「%s」" % cat, command=lambda c=cat: self._delete_category(c))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _new_category(self):
        name = self._ask_text("新建分类", "分类名称：", "")
        if name:
            self.config.ensure_category(name)
            self._after_change()

    def _rename_category(self, cat):
        name = self._ask_text("重命名分类", "新名称：", cat)
        if name and name != cat:
            self.config.rename_category(cat, name)
            self._after_change()

    def _pick_category_color(self, cat):
        c = colorchooser.askcolor(color=self.config.category_color(cat) or "#4a90d9",
                                  parent=self, title="选择「%s」的颜色" % cat)[1]
        if c:
            self.config.set_category_color(cat, c)
            self.refresh()

    def _delete_category(self, cat):
        if self.config.delete_category(cat):
            if self.current_category == cat:
                self.current_category = "全部"
            self._after_change()
        else:
            messagebox.showwarning("提示", "该分类下还有按钮，无法删除。\n请先移动或删除这些按钮。", parent=self)

    def _ask_text(self, title, prompt, initial=""):
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.configure(bg="#f5f6f8")
        result = {"value": None}
        tk.Label(dlg, text=prompt, bg="#f5f6f8", font=(FONT, 10)).pack(padx=16, pady=(14, 4), anchor="w")
        var = tk.StringVar(value=initial)
        ent = tk.Entry(dlg, textvariable=var, width=34, font=(FONT, 10))
        ent.pack(padx=16, pady=(0, 10))
        btns = tk.Frame(dlg, bg="#f5f6f8")
        btns.pack(padx=16, pady=(0, 12))

        def ok():
            result["value"] = var.get().strip()
            dlg.destroy()

        tk.Button(btns, text="确定", width=8, command=ok, bg="#4a90d9", fg="#ffffff",
                  relief="flat", cursor="hand2").pack(side="left", padx=4)
        tk.Button(btns, text="取消", width=8, command=dlg.destroy, bg="#d9dce1", fg="#333333",
                  relief="flat", cursor="hand2").pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: ok())
        ent.focus_set()
        dlg.grab_set()
        self.wait_window(dlg)
        return result["value"]

    # ---------------- 扫描 / 设置 ----------------
    def _scan_button(self):
        ScanDialog(self, self.config, on_add=self._on_added)

    def _auto_check_update(self):
        def worker():
            try:
                from updater import fetch_remote_version, is_newer
                repo = (self.config.data.get("update", {}) or {}).get("repo", "arockwell233/debug-toolbox")
                remote = fetch_remote_version(repo)
                if remote and is_newer(APP_VERSION, remote):
                    self.after(0, lambda: self._prompt_update(remote))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _prompt_update(self, version):
        if messagebox.askyesno("发现新版本", "发现新版本 v%s，是否打开 GitHub 下载页？\n（下载新版后覆盖当前程序即可）" % version, parent=self):
            repo = (self.config.data.get("update", {}) or {}).get("repo", "arockwell233/debug-toolbox")
            webbrowser.open("https://github.com/%s" % repo)

    def _settings_button(self):
        SettingsDialog(self, self.config)

    def _ip_scan_button(self):
        IPScanDialog(self, self.config)

    def _hex_button(self):
        HexTranslateDialog(self, self.config)

# ---------------------------------------------------------------- 添加/编辑按钮对话框

class ButtonDialog(tk.Toplevel):
    def __init__(self, master, config, button=None, on_save=None):
        super().__init__(master)
        self.config = config
        self.button = button or {}
        self.on_save = on_save
        self._color = (self.button.get("color") or "").strip()

        self._type_var = tk.StringVar(value=(self.button.get("type") or "exe"))
        self._name_var = tk.StringVar(value=self.button.get("name", ""))
        self._path_var = tk.StringVar(value=self.button.get("path", ""))
        self._args_var = tk.StringVar(value=self.button.get("args", ""))
        self._admin_var = tk.BooleanVar(value=bool(self.button.get("run_as_admin")))
        self._cat_var = tk.StringVar(value=self.button.get("category", ""))
        self._icon_var = tk.StringVar(value=self.button.get("icon", ""))
        self._note_var = tk.StringVar(value=self.button.get("note", ""))
        self._use_cat_color = tk.BooleanVar(value=not self._color)

        self.title("编辑按钮" if button else "添加按钮")
        self.resizable(False, False)
        self.transient(master)
        self.configure(bg="#f5f6f8")
        self._build()
        self._update_type_ui()
        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    # ---------- 界面 ----------
    def _row(self, parent, label):
        lab = tk.Label(parent, text=label, bg="#f5f6f8", fg="#333333",
                       font=(FONT, 10), anchor="w", width=11)
        lab.pack(side="left", padx=(4, 8), pady=4)
        box = tk.Frame(parent, bg="#f5f6f8")
        box.pack(side="left", fill="x", expand=True, pady=4)
        return box

    def _build(self):
        body = tk.Frame(self, bg="#f5f6f8")
        body.pack(fill="both", expand=True, padx=18, pady=14)

        # 名称
        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x")
        box = self._row(r, "按钮名称 *")
        self.name_entry = tk.Entry(box, textvariable=self._name_var, width=44, font=(FONT, 10))
        self.name_entry.pack(fill="x")
        self.name_entry.focus_set()

        # 类型
        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x")
        box = self._row(r, "类型")
        display = [label for _, label in TYPE_CHOICES]
        self.type_cb = ttk.Combobox(box, values=display, state="readonly", width=40, font=(FONT, 10))
        self.type_cb.current(self._type_index())
        self.type_cb.pack(side="left")
        self.type_cb.bind("<<ComboboxSelected>>", lambda e: self._update_type_ui())

        # 路径
        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x")
        box = self._row(r, "路径 / 网址 *")
        self.path_entry = tk.Entry(box, textvariable=self._path_var, width=36, font=(FONT, 10))
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.browse_btn = tk.Button(box, text="浏览…", command=self._browse, bg="#d9dce1",
                                    fg="#333333", relief="flat", cursor="hand2", padx=10)
        self.browse_btn.pack(side="left")

        # 参数 / 管理员（仅程序）
        self.args_row = tk.Frame(body, bg="#f5f6f8")
        self.args_row.pack(fill="x")
        box = self._row(self.args_row, "启动参数")
        tk.Entry(box, textvariable=self._args_var, width=44, font=(FONT, 10)).pack(fill="x")

        self.admin_row = tk.Frame(body, bg="#f5f6f8")
        self.admin_row.pack(fill="x")
        box = self._row(self.admin_row, "")
        tk.Checkbutton(box, text="以管理员身份运行（部分软件需要）", variable=self._admin_var,
                       bg="#f5f6f8", font=(FONT, 10), anchor="w").pack(fill="x")

        # 分类
        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x")
        box = self._row(r, "分类")
        self.cat_cb = ttk.Combobox(box, values=self.config.category_names(), width=40, font=(FONT, 10))
        self.cat_cb.pack(side="left", fill="x", expand=True)
        tip = tk.Label(box, text="（可输入新分类名）", bg="#f5f6f8", fg="#999999", font=(FONT, 9))
        tip.pack(side="left", padx=6)

        # 颜色
        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x")
        box = self._row(r, "按钮颜色")
        self.color_chk = tk.Checkbutton(box, text="使用分类颜色", variable=self._use_cat_color,
                                        bg="#f5f6f8", font=(FONT, 10),
                                        command=self._refresh_swatch)
        self.color_chk.pack(side="left")
        self.swatch = tk.Label(box, text="   ", bg="#4a90d9", width=3, cursor="hand2")
        self.swatch.pack(side="left", padx=6)
        self.swatch.bind("<Button-1>", lambda e: self._pick_color())

        # 图标
        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x")
        box = self._row(r, "图标(可选)")
        self.icon_entry = tk.Entry(box, textvariable=self._icon_var, width=36, font=(FONT, 10))
        self.icon_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(box, text="浏览…", command=self._browse_icon, bg="#d9dce1", fg="#333333",
                  relief="flat", cursor="hand2", padx=10).pack(side="left")
        tip = tk.Label(box, text="支持 png/jpg/ico，不填则显示首字图标",
                       bg="#f5f6f8", fg="#999999", font=(FONT, 9))
        tip.pack(side="left", padx=6)

        # 备注
        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x")
        box = self._row(r, "备注(可选)")
        tk.Entry(box, textvariable=self._note_var, width=44, font=(FONT, 10)).pack(fill="x")

        # 底部按钮
        btns = tk.Frame(self, bg="#f5f6f8")
        btns.pack(fill="x", padx=18, pady=(4, 14))
        tk.Button(btns, text="测试打开", command=self._test, bg="#16a085", fg="#ffffff",
                  relief="flat", cursor="hand2", padx=14, pady=5, font=(FONT, 9)).pack(side="left")
        tk.Button(btns, text="保存", command=self._save, bg="#4a90d9", fg="#ffffff",
                  relief="flat", cursor="hand2", padx=20, pady=5, font=(FONT, 9)).pack(side="right", padx=(6, 0))
        tk.Button(btns, text="取消", command=self.destroy, bg="#d9dce1", fg="#333333",
                  relief="flat", cursor="hand2", padx=14, pady=5, font=(FONT, 9)).pack(side="right")

    def _type_index(self):
        typ = (self.button.get("type") or "exe").lower()
        for i, (v, _) in enumerate(TYPE_CHOICES):
            if v == typ:
                return i
        return 0

    # ---------- 行为 ----------
    def _update_type_ui(self):
        typ = TYPE_CHOICES[self.type_cb.current()][0]
        self._type_var.set(typ)
        if typ == "url":
            self.browse_btn.config(state="disabled")
            self.path_entry.config(fg="#888888")
            self.args_row.pack_forget()
            self.admin_row.pack_forget()
        else:
            self.browse_btn.config(state="normal")
            self.path_entry.config(fg="#000000")
            if typ == "exe":
                self.args_row.pack(fill="x")
                self.admin_row.pack(fill="x")
            else:
                self.args_row.pack_forget()
                self.admin_row.pack_forget()
        self._refresh_swatch()

    def _browse(self):
        typ = self._type_var.get()
        if typ == "url":
            return
        if typ == "folder":
            p = filedialog.askdirectory(parent=self, title="选择文件夹",
                                        initialdir=self._path_var.get() or None)
        else:
            p = filedialog.askopenfilename(
                parent=self, title="选择程序或文件",
                initialdir=os.path.dirname(self._path_var.get()) if self._path_var.get() else None,
                filetypes=[("程序/文件", "*.exe;*.bat;*.cmd;*.lnk;*.msi;*.com"), ("所有文件", "*.*")])
        if p:
            self._path_var.set(p)
            if not self._name_var.get().strip():
                self._name_var.set(os.path.splitext(os.path.basename(p))[0])

    def _browse_icon(self):
        p = filedialog.askopenfilename(parent=self, title="选择图标",
                                       filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.ico;*.gif"),
                                                  ("所有文件", "*.*")])
        if p:
            self._icon_var.set(p)

    def _pick_color(self):
        c = colorchooser.askcolor(color=self._color or "#4a90d9", parent=self,
                                  title="选择按钮颜色")[1]
        if c:
            self._color = c
            self._use_cat_color.set(False)
            self._refresh_swatch()

    def _refresh_swatch(self):
        if self._use_cat_color.get():
            cat = self._cat_var.get().strip()
            color = self.config.button_color({"category": cat, "color": ""}) or "#4a90d9"
        else:
            color = self._color or "#4a90d9"
        self.swatch.config(bg=color)

    def _build_btn(self):
        typ = self._type_var.get()
        path = self._path_var.get().strip()
        if typ == "url":
            path = normalize_url(path)
        cat = self._cat_var.get().strip() or "未分类"
        return {
            "name": self._name_var.get().strip(),
            "type": typ,
            "path": path,
            "category": cat,
            "color": "" if self._use_cat_color.get() else self._color,
            "icon": self._icon_var.get().strip(),
            "args": self._args_var.get().strip(),
            "run_as_admin": bool(self._admin_var.get()),
            "note": self._note_var.get().strip(),
        }

    def _test(self):
        btn = self._build_btn()
        if not btn["name"]:
            messagebox.showwarning("提示", "请先填写按钮名称。", parent=self)
            return
        if not btn["path"]:
            messagebox.showwarning("提示", "请先填写路径或网址。", parent=self)
            return
        launch_button(btn, parent=self)

    def _save(self):
        btn = self._build_btn()
        if not btn["name"]:
            messagebox.showwarning("提示", "请填写按钮名称。", parent=self)
            return
        if not btn["path"]:
            messagebox.showwarning("提示", "请填写路径或网址。", parent=self)
            return
        self.config.ensure_category(btn["category"])
        if self.on_save:
            self.on_save(btn)
        self.destroy()

# ---------------------------------------------------------------- 扫描对话框

class ScanDialog(tk.Toplevel):
    def __init__(self, master, config, on_add=None):
        super().__init__(master)
        self.config = config
        self.on_add = on_add
        self._root_var = tk.StringVar(value=config.data.get("scan_base_dir", ""))
        self._recurse_var = tk.BooleanVar(value=True)
        self._by_folder_var = tk.BooleanVar(value=True)
        self._items = []

        self.title("扫描文件夹，自动生成按钮")
        self.transient(master)
        self.configure(bg="#f5f6f8")
        self.geometry("680x500")
        self.minsize(560, 380)
        self._build()
        self.grab_set()

    def _build(self):
        body = tk.Frame(self, bg="#f5f6f8")
        body.pack(fill="both", expand=True, padx=16, pady=12)

        # 文件夹选择
        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x")
        tk.Label(r, text="要扫描的文件夹：", bg="#f5f6f8", font=(FONT, 10)).pack(side="left")
        tk.Entry(r, textvariable=self._root_var, width=42, font=(FONT, 10)).pack(side="left", fill="x", expand=True, padx=6)
        tk.Button(r, text="浏览…", command=self._browse_root, bg="#d9dce1", fg="#333333",
                  relief="flat", cursor="hand2", padx=10).pack(side="left")

        # 选项
        opts = tk.Frame(body, bg="#f5f6f8")
        opts.pack(fill="x", pady=8)
        tk.Checkbutton(opts, text="包含子文件夹（每个厂家一个子文件夹时建议勾选）",
                       variable=self._recurse_var, bg="#f5f6f8", font=(FONT, 9)).pack(side="left", padx=(0, 16))
        tk.Checkbutton(opts, text="用子文件夹名作为分类", variable=self._by_folder_var,
                       bg="#f5f6f8", font=(FONT, 9)).pack(side="left")

        # 预览
        tk.Label(body, text="扫描结果（可多选）：", bg="#f5f6f8", font=(FONT, 10)).pack(anchor="w")
        frame = tk.Frame(body, bg="#f5f6f8")
        frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(frame, selectmode="extended", font=("Consolas", 9),
                                  exportselection=False)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=vsb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # 底部按钮
        btns = tk.Frame(self, bg="#f5f6f8")
        btns.pack(fill="x", padx=16, pady=(0, 12))
        tk.Button(btns, text="重新扫描", command=self._scan, bg="#3a4150", fg="#ffffff",
                  relief="flat", cursor="hand2", padx=12, pady=5, font=(FONT, 9)).pack(side="left")
        tk.Button(btns, text="全选", command=self._select_all, bg="#d9dce1", fg="#333333",
                  relief="flat", cursor="hand2", padx=10, pady=5, font=(FONT, 9)).pack(side="left", padx=6)
        tk.Button(btns, text="全不选", command=lambda: self.listbox.selection_clear(0, "end"),
                  bg="#d9dce1", fg="#333333", relief="flat", cursor="hand2", padx=10, pady=5,
                  font=(FONT, 9)).pack(side="left")
        self.add_btn = tk.Button(btns, text="添加选中 (0)", command=self._add_selected, bg="#4a90d9",
                                 fg="#ffffff", relief="flat", cursor="hand2", padx=16, pady=5,
                                 font=(FONT, 9), state="disabled")
        self.add_btn.pack(side="right")
        tk.Button(btns, text="取消", command=self.destroy, bg="#d9dce1", fg="#333333",
                  relief="flat", cursor="hand2", padx=12, pady=5, font=(FONT, 9)).pack(side="right", padx=6)

        self.listbox.bind("<<ListboxSelect>>", self._update_add_label)
        if self._root_var.get():
            self.after(150, self._scan)

    def _browse_root(self):
        p = filedialog.askdirectory(parent=self, title="选择要扫描的文件夹",
                                    initialdir=self._root_var.get() or None)
        if p:
            self._root_var.set(p)
            self.config.data["scan_base_dir"] = p
            self.config.save()
            self._scan()

    @staticmethod
    def _top_walker(root):
        yield root, [], os.listdir(root)
        for name in sorted(os.listdir(root)):
            sub = os.path.join(root, name)
            if os.path.isdir(sub):
                yield sub, [], os.listdir(sub)

    def _scan(self):
        root = self._root_var.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showwarning("提示", "请先选择有效的文件夹。", parent=self)
            return
        self._items = []
        self.listbox.delete(0, "end")
        existing = {b.get("path", "").lower() for b in self.config.buttons()}
        walker = os.walk(root) if self._recurse_var.get() else self._top_walker(root)
        for folder, _dirs, files in walker:
            rel = os.path.relpath(folder, root)
            cat = (rel.split(os.sep)[0] if rel != "." else "") if self._by_folder_var.get() else ""
            for f in sorted(files):
                if os.path.splitext(f)[1].lower() not in SCAN_EXTS:
                    continue
                p = os.path.join(folder, f)
                stem = os.path.splitext(f)[0]
                name = ("%s · %s" % (cat, stem)) if cat else stem
                item = {"name": name, "path": p, "category": cat or "其他",
                        "exists": p.lower() in existing}
                self._items.append(item)
                mark = "[已存在] " if item["exists"] else ""
                self.listbox.insert("end", "%s%s | %s | %s" % (mark, item["category"], item["name"], p))
        if not self._items:
            messagebox.showinfo("提示", "没有找到可添加的程序文件（.exe/.bat/.cmd/.msi 等）。", parent=self)
        self._update_add_label()

    def _update_add_label(self, _e=None):
        n = len(self.listbox.curselection())
        self.add_btn.config(text="添加选中 (%d)" % n,
                            state="normal" if n else "disabled")

    def _select_all(self):
        self.listbox.selection_set(0, "end")
        self._update_add_label()

    def _add_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        added = 0
        for i in sel:
            item = self._items[i]
            if item["exists"]:
                continue
            if self.on_add:
                self.on_add({"name": item["name"], "type": "exe", "path": item["path"],
                             "category": item["category"], "color": "", "icon": "",
                             "args": "", "run_as_admin": False, "note": ""})
            added += 1
        messagebox.showinfo("完成", "已添加 %d 个按钮。\n可在主界面右键按钮进行编辑改名。" % added, parent=self)
        self.destroy()


# ---------------------------------------------------------------- 设置对话框

class SettingsDialog(tk.Toplevel):
    def __init__(self, master, config):
        super().__init__(master)
        self.config = config
        self.master_app = master
        self._name_var = tk.StringVar(value=config.data.get("app_name", "调试工具箱"))
        self._scan_var = tk.StringVar(value=config.data.get("scan_base_dir", ""))
        update_cfg = config.data.get("update", {}) or {}
        self._update_repo_var = tk.StringVar(value=update_cfg.get("repo", "arockwell233/debug-toolbox"))
        self._auto_check_var = tk.BooleanVar(value=bool(update_cfg.get("auto_check", False)))
        self._found_version = None

        self.title("设置")
        self.resizable(False, False)
        self.transient(master)
        self.configure(bg="#f5f6f8")
        self._build()
        self.grab_set()

    def _build(self):
        body = tk.Frame(self, bg="#f5f6f8")
        body.pack(fill="both", expand=True, padx=18, pady=14)

        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x", pady=4)
        tk.Label(r, text="应用名称", bg="#f5f6f8", font=(FONT, 10), width=11, anchor="w").pack(side="left")
        tk.Entry(r, textvariable=self._name_var, width=36, font=(FONT, 10)).pack(side="left")

        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x", pady=4)
        tk.Label(r, text="默认扫描文件夹", bg="#f5f6f8", font=(FONT, 10), width=11, anchor="w").pack(side="left")
        tk.Entry(r, textvariable=self._scan_var, width=28, font=(FONT, 10)).pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(r, text="浏览…", command=self._browse, bg="#d9dce1", fg="#333333",
                  relief="flat", cursor="hand2", padx=10).pack(side="left")

        info = tk.Label(body, text="配置文件位置：\n%s" % self.config.path,
                        bg="#f5f6f8", fg="#888888", font=(FONT, 9), justify="left", anchor="w")
        info.pack(fill="x", pady=(14, 4))

        # ---------- 在线更新 ----------
        tk.Label(body, text="— 在线更新 —", bg="#f5f6f8", fg="#888888",
                 font=(FONT, 9)).pack(fill="x", pady=(10, 2))
        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x", pady=4)
        tk.Label(r, text="更新源", bg="#f5f6f8", font=(FONT, 10), width=11, anchor="w").pack(side="left")
        tk.Entry(r, textvariable=self._update_repo_var, width=28, font=(FONT, 10)).pack(side="left")
        tk.Label(r, text="（GitHub 用户名/仓库名）", bg="#f5f6f8", fg="#999999",
                 font=(FONT, 9)).pack(side="left", padx=6)

        r = tk.Frame(body, bg="#f5f6f8")
        r.pack(fill="x", pady=4)
        tk.Label(r, text="当前版本", bg="#f5f6f8", font=(FONT, 10), width=11, anchor="w").pack(side="left")
        tk.Label(r, text="v%s" % APP_VERSION, bg="#f5f6f8", fg="#4a90d9",
                 font=(FONT, 10, "bold")).pack(side="left")
        self.update_btn = tk.Button(r, text="检查更新", command=self._check_update, bg="#4a90d9",
                                    fg="#ffffff", relief="flat", cursor="hand2", padx=12, pady=3,
                                    font=(FONT, 9))
        self.update_btn.pack(side="left", padx=10)
        self.do_update_btn = tk.Button(r, text="下载新版", command=self._do_update, bg="#e67e22",
                                       fg="#ffffff", relief="flat", cursor="hand2", padx=12, pady=3,
                                       font=(FONT, 9), state="disabled")
        self.do_update_btn.pack(side="left")
        tk.Checkbutton(body, text="启动时自动检查更新", variable=self._auto_check_var,
                       bg="#f5f6f8", font=(FONT, 9)).pack(anchor="w", padx=130)
        self.update_status = tk.Label(body, text="", bg="#f5f6f8", fg="#666666",
                                      font=(FONT, 9), anchor="w", wraplength=480)
        self.update_status.pack(fill="x", padx=130)

        btns = tk.Frame(self, bg="#f5f6f8")
        btns.pack(fill="x", padx=18, pady=(0, 14))
        tk.Button(btns, text="保存", command=self._save, bg="#4a90d9", fg="#ffffff",
                  relief="flat", cursor="hand2", padx=20, pady=5, font=(FONT, 9)).pack(side="right")
        tk.Button(btns, text="取消", command=self.destroy, bg="#d9dce1", fg="#333333",
                  relief="flat", cursor="hand2", padx=14, pady=5, font=(FONT, 9)).pack(side="right", padx=6)

    def _browse(self):
        p = filedialog.askdirectory(parent=self, title="选择默认扫描文件夹",
                                    initialdir=self._scan_var.get() or None)
        if p:
            self._scan_var.set(p)

    # ---------- 在线更新 ----------
    def _check_update(self):
        repo = self._update_repo_var.get().strip() or "arockwell233/debug-toolbox"
        self.update_btn.config(state="disabled")
        self.update_status.config(text="正在检查更新…")
        threading.Thread(target=self._check_worker, args=(repo,), daemon=True).start()

    def _check_worker(self, repo):
        try:
            from updater import fetch_remote_version, is_newer
            remote = fetch_remote_version(repo)
            if remote is None:
                self.after(0, lambda: self._show_check_result(
                    None, "检查失败：无法连接更新源，请检查网络或更新源是否正确。"))
            elif is_newer(APP_VERSION, remote):
                self._found_version = remote
                self.after(0, lambda: self._show_check_result(
                    remote, "发现新版本 v%s，可点击「立即更新」。" % remote))
            else:
                self._found_version = None
                self.after(0, lambda: self._show_check_result(None, "已是最新版（v%s）。" % APP_VERSION))
        except Exception as e:
            self.after(0, lambda: self._show_check_result(None, "检查失败：%s" % e))

    def _show_check_result(self, version, msg):
        self.update_btn.config(state="normal")
        self.update_status.config(text=msg)
        self.do_update_btn.config(state="normal" if version else "disabled")

    def _do_update(self):
        if not self._found_version:
            return
        repo = self._update_repo_var.get().strip() or "arockwell233/debug-toolbox"
        webbrowser.open("https://github.com/%s" % repo)
        self.update_status.config(text="已在浏览器打开下载页，下载新版后覆盖当前程序即可。")

    def _save(self):
        name = self._name_var.get().strip() or "调试工具箱"
        self.config.data["app_name"] = name
        self.config.data["scan_base_dir"] = self._scan_var.get().strip()
        self.config.data["update"] = {
            "repo": self._update_repo_var.get().strip() or "arockwell233/debug-toolbox",
            "auto_check": bool(self._auto_check_var.get()),
        }
        self.config.save()
        self.master_app.title("%s v1.0" % name)
        self.destroy()


# ---------------------------------------------------------------- 自检 / 主入口

def run_selftest(config):
    print("配置文件 :", config.path)
    print("应用名称 :", config.data.get("app_name"))
    print("分类     :", ", ".join(config.category_names()))
    print("按钮数量 :", len(config.buttons()))
    print("-" * 60)
    problems = 0
    for i, b in enumerate(config.buttons()):
        typ = (b.get("type") or "exe").lower()
        path = (b.get("path") or "").strip()
        name = b.get("name") or "(未命名)"
        flags = []
        if not path:
            flags.append("缺少路径")
        elif typ == "exe" and not os.path.exists(path):
            flags.append("文件不存在")
        elif typ == "folder" and not os.path.isdir(path):
            flags.append("文件夹不存在")
        if flags:
            problems += 1
        status = ("⚠ " + "、".join(flags)) if flags else "OK"
        print("[%2d] %-20s | %-6s | %s %s" % (i + 1, name[:20], typ, path or "(空)", status))
    print("-" * 60)
    print("自检完成：" + ("发现 %d 个问题，请检查上面 ⚠ 标记的按钮。" % problems if problems else "一切正常。"))


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    config = Config()

    if "--selftest" in sys.argv:
        run_selftest(config)
        return

    if "--snapshot" in sys.argv:
        idx = sys.argv.index("--snapshot")
        out = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "snapshot.png"
        app = ToolboxApp(config)
        app.after(2000, lambda: _snapshot(app, out))
        app.mainloop()
        return

    app = ToolboxApp(config)
    app.mainloop()


def _snapshot(app, out):
    try:
        from PIL import ImageGrab
        app.lift()
        app.attributes("-topmost", True)
        app.update_idletasks()
        app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        img.save(out)
        log("截图已保存: %s" % out)
    except Exception as e:
        log("截图失败: %s" % e)
    app.destroy()


if __name__ == "__main__":
    main()
