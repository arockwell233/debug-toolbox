# -*- coding: utf-8 -*-
"""
IP 扫描器（类似 Angry IP Scanner 的核心功能）
===============================================
  * 支持多种写法：192.168.1.1-254、192.168.1.0/24、192.168.1.*、单个IP
  * 并发 Ping 探测在线主机，显示响应时间和主机名
  * 可选检测常用端口（80/443/8080/22/23/3389 等），方便发现路由器/设备的Web管理页
  * 右键结果可直接复制IP、用浏览器打开、一键添加为工具箱按钮
"""

import ipaddress
import queue
import re
import socket
import subprocess
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
from tkinter import ttk, messagebox

FONT = "Microsoft YaHei UI"


# ---------------------------------------------------------------- 解析与探测

def parse_range(text):
    """把 IP 范围文本解析成 IP 字符串列表。"""
    text = (text or "").strip()
    if not text:
        return []
    ips = []

    if text.endswith(".*"):
        base = text[:-2]
        if not re.match(r"^\d{1,3}(\.\d{1,3}){2}$", base):
            return []
        for i in range(1, 255):
            ips.append("%s.%d" % (base, i))
        return ips

    if "/" in text:
        try:
            net = ipaddress.ip_network(text, strict=False)
            return [str(ip) for ip in net.hosts()][:1024]
        except Exception:
            return []

    m = re.match(r"^(\d{1,3}(?:\.\d{1,3}){3})\s*-\s*(\d{1,3})$", text)
    if m:
        start_ip = m.group(1)
        end = int(m.group(2))
        prefix = start_ip.rsplit(".", 1)[0]
        start_oct = int(start_ip.rsplit(".", 1)[1])
        if end > 255:
            return []
        for i in range(start_oct, end + 1):
            ips.append("%s.%d" % (prefix, i))
        return ips

    m = re.match(r"^(\d{1,3}(?:\.\d{1,3}){3})\s*-\s*(\d{1,3}(?:\.\d{1,3}){3})$", text)
    if m:
        try:
            s = int(ipaddress.ip_address(m.group(1)))
            e = int(ipaddress.ip_address(m.group(2)))
            if e >= s and e - s <= 2048:
                return [str(ipaddress.ip_address(x)) for x in range(s, e + 1)]
        except Exception:
            pass
        return []

    try:
        ipaddress.ip_address(text)
        return [text]
    except Exception:
        return []


def parse_ports(text):
    """解析端口列表：80,443,8080 或 20-25。"""
    text = (text or "").strip()
    if not text:
        return []
    ports = []
    for part in re.split(r"[,\s;，；]+", text):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                for p in range(int(a), int(b) + 1):
                    if 0 < p < 65536:
                        ports.append(p)
            except Exception:
                pass
        else:
            try:
                p = int(part)
                if 0 < p < 65536:
                    ports.append(p)
            except Exception:
                pass
    return sorted(set(ports))


def ping_check(ip, timeout_ms=400):
    """用系统 ping 探测，返回 (是否在线, 响应毫秒)。"""
    try:
        out = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout_ms), ip],
            capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            m = re.search(r"(\d+)\s*ms", out.stdout + out.stderr, re.I)
            return True, (int(m.group(1)) if m else None)
    except Exception:
        pass
    return False, None


def hostname_of(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def check_port(ip, port, timeout=0.3):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass

# ---------------------------------------------------------------- 扫描窗口

class IPScanDialog(tk.Toplevel):
    def __init__(self, master, config=None):
        super().__init__(master)
        self.config = config
        self._range_var = tk.StringVar(value="192.168.1.1-254")
        self._ports_var = tk.StringVar(value="80,443,8080,22,23,3389")
        self._port_scan = tk.BooleanVar(value=True)
        self._stop = threading.Event()
        self._running = False
        self._queue = queue.Queue()
        self._results = []
        self._item_map = {}

        self.title("IP 扫描（类 Angry IP Scanner）")
        self.geometry("780x580")
        self.minsize(640, 420)
        self.transient(master)
        self.configure(bg="#f5f6f8")
        self._build()
        self.grab_set()

    def _build(self):
        top = tk.Frame(self, bg="#f5f6f8")
        top.pack(fill="x", padx=12, pady=(12, 6))

        r = tk.Frame(top, bg="#f5f6f8")
        r.pack(fill="x")
        tk.Label(r, text="IP 范围：", bg="#f5f6f8", font=(FONT, 10)).pack(side="left")
        self.range_entry = tk.Entry(r, textvariable=self._range_var, width=24, font=("Consolas", 10))
        self.range_entry.pack(side="left", padx=4)
        self.start_btn = tk.Button(r, text="开始扫描", command=self._start, bg="#4a90d9",
                                   fg="#ffffff", relief="flat", cursor="hand2", padx=16,
                                   pady=4, font=(FONT, 10, "bold"))
        self.start_btn.pack(side="left", padx=8)
        self.stop_btn = tk.Button(r, text="停止", command=self._stop_scan, bg="#e74c3c",
                                  fg="#ffffff", relief="flat", cursor="hand2", padx=12,
                                  pady=4, font=(FONT, 10), state="disabled")
        self.stop_btn.pack(side="left")
        hint = tk.Label(r, text="支持：192.168.1.1-254 / 192.168.1.0/24 / 192.168.1.* / 单个IP",
                        bg="#f5f6f8", fg="#999999", font=(FONT, 9))
        hint.pack(side="left", padx=8)

        opts = tk.Frame(top, bg="#f5f6f8")
        opts.pack(fill="x", pady=(6, 2))
        tk.Checkbutton(opts, text="检测常用端口", variable=self._port_scan,
                       bg="#f5f6f8", font=(FONT, 9)).pack(side="left")
        tk.Entry(opts, textvariable=self._ports_var, width=26, font=("Consolas", 9)).pack(side="left", padx=6)
        tk.Label(opts, text="（可改，如 80,443,22,23,3389,8080）",
                 bg="#f5f6f8", fg="#999999", font=(FONT, 9)).pack(side="left")

        # 结果表格
        mid = tk.Frame(self, bg="#f5f6f8")
        mid.pack(fill="both", expand=True, padx=12, pady=(4, 6))
        cols = ("ip", "status", "ms", "host", "ports")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("ip", text="IP 地址")
        self.tree.heading("status", text="状态")
        self.tree.heading("ms", text="响应")
        self.tree.heading("host", text="主机名")
        self.tree.heading("ports", text="开放端口")
        self.tree.column("ip", width=130, anchor="w")
        self.tree.column("status", width=60, anchor="center")
        self.tree.column("ms", width=70, anchor="center")
        self.tree.column("host", width=180, anchor="w")
        self.tree.column("ports", width=180, anchor="w")
        self.tree.tag_configure("alive", background="#e8f5e9")
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Button-3>", self._on_menu)

        # 底部
        bar = tk.Frame(self, bg="#e6e8eb", height=28)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        self.progress = tk.Label(bar, text="输入 IP 范围后点「开始扫描」。",
                                 bg="#e6e8eb", fg="#666666", font=(FONT, 9), anchor="w")
        self.progress.pack(side="left", padx=12)
        tk.Label(bar, text="右键结果：复制 / 浏览器打开 / 添加为按钮", bg="#e6e8eb",
                 fg="#999999", font=(FONT, 9)).pack(side="right", padx=12)

    # ---------------- 扫描 ----------------
    def _parse_ports(self):
        if not self._port_scan.get():
            return []
        return parse_ports(self._ports_var.get())

    def _set_running(self, running):
        self._running = running
        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")

    def _start(self):
        ips = parse_range(self._range_var.get())
        if not ips:
            messagebox.showwarning("提示", "无法解析 IP 范围。\n支持：192.168.1.1-254、192.168.1.0/24、192.168.1.*、单个IP",
                                   parent=self)
            return
        if len(ips) > 4096:
            messagebox.showwarning("提示", "范围过大（%d 个IP），最多扫描 4096 个。" % len(ips), parent=self)
            return
        self._stop.clear()
        self._queue = queue.Queue()
        self._results = []
        self._item_map = {}
        for child in self.tree.get_children():
            self.tree.delete(child)
        self._set_running(True)
        self.progress.config(text="正在扫描 %d 个 IP…" % len(ips))
        ports = self._parse_ports()
        threading.Thread(target=self._worker, args=(ips, ports), daemon=True).start()
        self.after(120, self._poll_queue)

    def _stop_scan(self):
        self._stop.set()
        self.progress.config(text="正在停止…")

    def _worker(self, ips, ports):
        alive = []
        total = len(ips)
        done = 0
        with ThreadPoolExecutor(max_workers=60) as ex:
            futs = {ex.submit(ping_check, ip): ip for ip in ips}
            for fut in as_completed(futs):
                if self._stop.is_set():
                    break
                ip = futs[fut]
                try:
                    ok, ms = fut.result()
                except Exception:
                    ok, ms = False, None
                done += 1
                if ok:
                    alive.append(ip)
                    host = hostname_of(ip)
                    self._queue.put(("row", {"ip": ip, "ms": ms, "host": host, "ports": []}))
                self._queue.put(("progress", (done, total)))

        if ports and alive and not self._stop.is_set():
            with ThreadPoolExecutor(max_workers=30) as ex:
                futs = {ex.submit(self._port_scan, ip, ports): ip for ip in alive}
                for fut in as_completed(futs):
                    if self._stop.is_set():
                        break
                    ip = futs[fut]
                    try:
                        open_ports = fut.result()
                    except Exception:
                        open_ports = []
                    self._queue.put(("ports", (ip, open_ports)))
        self._queue.put(("done", None))

    def _port_scan(self, ip, ports):
        out = []
        for p in ports:
            if self._stop.is_set():
                break
            if check_port(ip, p):
                out.append(p)
        return out

    def _poll_queue(self):
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "row":
                    row = item[1]
                    self._results.append(row)
                    item_id = self.tree.insert(
                        "", "end", values=(row["ip"], "在线",
                                           ("%d ms" % row["ms"]) if row["ms"] is not None else "-",
                                           row["host"], ""), tags=("alive",))
                    self._item_map[row["ip"]] = item_id
                elif kind == "ports":
                    ip, ports = item[1]
                    item_id = self._item_map.get(ip)
                    if item_id:
                        vals = list(self.tree.item(item_id, "values"))
                        vals[4] = ", ".join(str(p) for p in ports) if ports else "-"
                        self.tree.item(item_id, values=vals)
                elif kind == "progress":
                    done, total = item[1]
                    self.progress.config(text="扫描中：%d / %d" % (done, total))
                elif kind == "done":
                    self._set_running(False)
                    self.progress.config(text="扫描完成：发现 %d 台在线设备" % len(self._results))
                    return
        except queue.Empty:
            pass
        if self._running:
            self.after(120, self._poll_queue)

    # ---------------- 右键菜单 ----------------
    def _selected_ip(self):
        sel = self.tree.selection()
        if not sel:
            return None
        vals = self.tree.item(sel[0], "values")
        return vals[0] if vals else None

    def _on_menu(self, event):
        ip = self._selected_ip()
        menu = tk.Menu(self, tearoff=0)
        if ip:
            menu.add_command(label="复制 IP：%s" % ip, command=lambda: self._copy(ip))
            menu.add_command(label="用浏览器打开 http://%s" % ip,
                             command=lambda: self._open_browser(ip))
            menu.add_command(label="添加为工具箱按钮（网页）",
                             command=lambda: self._add_button(ip))
            menu.add_separator()
        menu.add_command(label="清空结果", command=self._clear_results)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy(self, ip):
        self.clipboard_clear()
        self.clipboard_append(ip)
        self.progress.config(text="已复制：%s" % ip)

    def _open_browser(self, ip):
        webbrowser.open("http://" + ip)

    def _add_button(self, ip):
        if not self.config:
            return
        name = "Web管理 %s" % ip
        btn = {"name": name, "type": "url", "path": "http://" + ip,
               "category": "路由器", "color": "", "icon": "", "args": "",
               "run_as_admin": False, "note": "由IP扫描添加"}
        self.config.add_button(btn)
        if hasattr(self.master, "_after_change"):
            self.master._after_change()
        self.progress.config(text="已添加按钮：%s" % name)

    def _clear_results(self):
        self._results = []
        self._item_map = {}
        for child in self.tree.get_children():
            self.tree.delete(child)
        self.progress.config(text="结果已清空。")


if __name__ == "__main__":
    # 命令行自测：python ip_scanner.py "192.168.1.1-10"
    import sys
    text = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.1-254"
    ips = parse_range(text)
    print("解析 %s → %d 个IP（前5个：%s）" % (text, len(ips), ips[:5]))
    ports = parse_ports("80,443,22-23")
    print("端口解析:", ports)
