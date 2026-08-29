# -*- coding: utf-8 -*-
"""
平衡101规约 16进制报文翻译器
=============================
参考《新版平衡101总结》整理：
  * 固定帧：10 C AL AH CS 16
  * 长帧：  68 L L 68 C AL AH ASDU CS 16
  * ASDU：类型标识TI + 可变结构限定词VSQ + 传送原因COT(2字节) + 公共地址(2字节) + 信息对象
  * 校验和：固定帧 CS=(C+AL+AH)低8位；长帧 CS=(C+AL+AH+ASDU和)低8位

用途：粘贴一段16进制报文，直观显示每一帧是什么、各字段含义、以及哪里有问题。
"""

import re
import struct
import zlib

import tkinter as tk
from tkinter import ttk

FONT = "Microsoft YaHei UI"

# ---------------------------------------------------------------- 规约表

TI_TABLE = {
    0x01: "单点遥信(YX)，不带时标",
    0x03: "双点遥信(YX)，不带时标",
    0x09: "遥测(YC)归一化值（整型，2字节）",
    0x0b: "遥测(YC)标度化值（2字节）",
    0x0d: "遥测(YC)短浮点值（4字节）",
    0x1e: "单点遥信(YX)，带7字节时标",
    0x1f: "双点遥信(YX)，带7字节时标",
    0x2a: "故障事件信息",
    0x2d: "单点遥控(YK)命令",
    0x2e: "双点遥控(YK)命令",
    0x46: "初始化结束",
    0x64: "总召(全数据召唤)",
    0x65: "电能量召唤",
    0x67: "时钟同步",
    0x68: "测试命令",
    0x69: "复位进程命令（终端复位重启）",
    0xce: "累积量，短浮点数",
    0xcf: "带时标的累积量，短浮点数",
    0xd2: "文件传输（录波文件相关）",
    0xd3: "软件升级",
}

COT_TABLE = {
    0x01: "周期循环",
    0x02: "背景扫描（不用）",
    0x03: "突发/自发（如SOE主动上报）",
    0x04: "初始化完成",
    0x05: "请求/被请求",
    0x06: "激活（如遥控、总召）",
    0x07: "激活确认",
    0x08: "停止激活",
    0x09: "停止激活确认",
    0x0a: "激活终止",
    0x0d: "文件传输",
    0x14: "响应总召",
    0x25: "响应电能量召唤",
    0x2c: "未知的类型标识（否定）",
    0x2d: "未知的传送原因（否定）",
    0x2e: "未知的公共地址（否定）",
    0x2f: "未知的信息体地址（否定）",
}

# 固定帧按控制域C精确识别（参考文档中的例子）
FIXED_FC_NAMES = {
    0x00: "肯定确认(ACK)",
    0x01: "否定确认(NACK)",
    0x0b: "链路状态响应",
    0x40: "复位远方链路",
    0x42: "发送/确认(链路测试)",
    0x49: "请求链路状态",
    0x80: "肯定确认(ACK)",
    0x81: "否定确认(NACK)",
    0x8b: "链路状态响应",
    0xc0: "复位远方链路（终端发起）",
    0xc9: "请求链路状态（终端发起）",
}

FIXED_FC_GENERIC = {
    0: "复位远方链路 / 肯定确认",
    1: "否定确认",
    2: "发送/确认(链路测试)",
    3: "发送/确认用户数据",
    4: "发送/无回答用户数据",
    8: "肯定确认",
    9: "请求链路状态",
    10: "响应链路状态",
    11: "链路状态响应",
}

QOI_TABLE = {0x14: "总召唤", 0x00: "周期召唤", 0x01: "第1组", 0x02: "第2组"}
QCC_TABLE = {0x05: "总的请求电能量", 0x01: "单个电能量请求"}
COI_TABLE = {0x00: "当地电源合上", 0x01: "当地手动复位", 0x02: "远方复位"}
QRP_TABLE = {0x01: "复位进程命令"}

FILE_OP_TABLE = {
    0x01: "读目录(召唤目录)",
    0x02: "读目录(确认)",
    0x03: "读文件激活",
    0x04: "读文件激活确认",
    0x05: "读文件数据",
    0x06: "读文件数据响应",
}

# ---------------------------------------------------------------- 输入解析

def normalize_hex(text):
    """把各种书写形式的16进制文本转成字节列表；无法解析返回 None。"""
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"0[xX]", "", s)
    s = re.sub(r"[^0-9a-fA-F]", "", s)
    if len(s) % 2 != 0:
        return None
    try:
        return bytes.fromhex(s)
    except Exception:
        return None


def split_frames(data):
    """把字节流切成一帧一帧，返回 [(类型, 帧字节), ...]。"""
    frames = []
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b == 0x10:
            if i + 5 < n and data[i + 5] == 0x16:
                frames.append(("fixed", data[i:i + 6]))
                i += 6
            else:
                frames.append(("fixed_bad", data[i:i + min(6, n - i)]))
                i += min(6, n - i)
        elif b == 0x68 and i + 4 < n and data[i + 3] == 0x68:
            L = data[i + 1]
            total = L + 6
            if i + total - 1 < n and data[i + total - 1] == 0x16:
                frames.append(("long", data[i:i + total]))
                i += total
            else:
                # L 可能写错：向后找下一个 16 作为帧尾，交给解码器提示长度问题
                end = data.find(0x16, i + 4, min(n, i + 300))
                if end != -1 and end - i >= 8:
                    frames.append(("long", data[i:end + 1]))
                    i = end + 1
                else:
                    frames.append(("long_bad", data[i:i + min(8, n - i)]))
                    i += min(4, n - i)
        elif b == 0xEB and i + 4 < n and data[i + 3] == 0xEB:
            sec_len = (data[i + 1] << 8) | data[i + 2]
            total = sec_len + 9
            if i + total - 1 < n and data[i + total - 1] == 0xD7:
                frames.append(("sec", data[i:i + total]))
                i += total
            else:
                i += 1
        else:
            i += 1
    return frames

# ---------------------------------------------------------------- 解码函数

def _decode_time7(b):
    """CP56Time2a 时标（7字节）：毫秒2 + 分 + 时 + 日 + 月 + 年。"""
    if len(b) < 7:
        return None
    ms = b[0] | (b[1] << 8)
    minute = b[2] & 0x3F
    hour = b[3] & 0x1F
    day = b[4] & 0x1F
    month = b[5] & 0x0F
    year = 2000 + b[6]
    return "%d-%02d-%02d %02d:%02d:%02d.%03d" % (year, month, day, hour, minute, ms // 1000, ms % 1000)


def _qds_text(v):
    """QDS 品质描述词。"""
    if v == 0:
        return "QDS=0(正常)"
    flags = []
    if v & 0x01: flags.append("溢出OV")
    if v & 0x02: flags.append("被封锁BL")
    if v & 0x04: flags.append("被取代SB")
    if v & 0x08: flags.append("非当前值NT")
    if v & 0x10: flags.append("无效IV")
    return "QDS=%02X(%s)" % (v, "、".join(flags) if flags else "?")


def _status_single(b):
    """单点遥信状态字节。"""
    s = "合" if (b & 0x01) else "分"
    flags = []
    if b & 0x10: flags.append("阻塞")
    if b & 0x20: flags.append("取代")
    if b & 0x40: flags.append("不刷新")
    if b & 0x80: flags.append("无效")
    return "状态=%s%s" % (s, ("(%s)" % "、".join(flags)) if flags else "")


def _status_double(b):
    d = b & 0x03
    names = {0: "中间/不确定", 1: "分", 2: "合", 3: "故障/不确定"}
    return "状态=%s" % names.get(d, "?")


def _command_text(ti, v):
    """遥控 SCO/DCO。"""
    se = "选择" if (v & 0x80) else "执行"
    if ti == 0x2D:
        val = "合" if (v & 0x01) else "分"
        return "SCO=%02X：%s%s" % (v, se, val)
    d = v & 0x03
    names = {0: "无效", 1: "分", 2: "合", 3: "不允许"}
    return "DCO=%02X：%s%s" % (v, se, names.get(d, "?"))


class _Reader:
    """顺序读取字节的小工具，越界时给出警告。"""

    def __init__(self, data, lines):
        self.data = data
        self.pos = 0
        self.lines = lines

    def take(self, n, what=""):
        if self.pos + n > len(self.data):
            self.lines.append(("  ⚠ 报文不完整：缺少%s（还差 %d 字节）" % (what or "数据", self.pos + n - len(self.data)), "warn"))
            return None
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def rest(self):
        return self.data[self.pos:]

    def remaining(self):
        return len(self.data) - self.pos


def _decode_info_objects(ti, sq, count, info, lines):
    r = _Reader(info, lines)
    if ti in (0x64, 0x65, 0x67, 0x69, 0x46, 0x68):
        # 固定信息体：信息体地址 + 限定词/数据
        addr = r.take(2, "信息体地址")
        if addr is None:
            return
        addr_val = addr[0] | (addr[1] << 8)
        lines.append(("  ── 信息对象 ──", "head"))
        lines.append(("    信息体地址：%04X (十进制 %d)" % (addr_val, addr_val), "info"))
        if ti == 0x64:
            q = r.take(1, "QOI")
            if q is not None:
                lines.append(("    召唤限定词 QOI=%02X：%s" % (q[0], QOI_TABLE.get(q[0], "未知")), "info"))
        elif ti == 0x65:
            q = r.take(1, "QCC")
            if q is not None:
                lines.append(("    电能量限定词 QCC=%02X：%s" % (q[0], QCC_TABLE.get(q[0], "未知")), "info"))
        elif ti == 0x67:
            t = r.take(7, "CP56Time2a时标")
            if t is not None:
                lines.append(("    时标 CP56Time2a：%s" % _decode_time7(t), "info"))
        elif ti == 0x69:
            q = r.take(1, "QRP")
            if q is not None:
                lines.append(("    复位限定词 QRP=%02X：%s" % (q[0], QRP_TABLE.get(q[0], "未知")), "info"))
        elif ti == 0x46:
            q = r.take(1, "COI")
            if q is not None:
                lines.append(("    初始化原因 COI=%02X：%s" % (q[0], COI_TABLE.get(q[0], "未知")), "info"))
        elif ti == 0x68:
            t = r.take(2, "FBP测试图像")
            if t is not None:
                v = t[0] | (t[1] << 8)
                lines.append(("    测试图像 FBP=%04X（%s）" % (v, "AA55 正常" if v == 0x55AA else "非标准！"), "info"))
    elif ti in (0x01, 0x03, 0x1e, 0x1f):
        lines.append(("  ── 信息对象（%d 个）──" % count, "head"))
        with_time = ti in (0x1e, 0x1f)
        for i in range(count):
            addr = r.take(2, "信息体地址")
            st = r.take(1, "状态")
            if addr is None or st is None:
                break
            addr_val = addr[0] | (addr[1] << 8)
            if ti in (0x01, 0x1e):
                txt = _status_single(st[0])
            else:
                txt = _status_double(st[0])
            line = "    对象%d：点号 %04X(%d) | %s" % (i + 1, addr_val, addr_val, txt)
            if with_time:
                t = r.take(7, "时标")
                if t is None:
                    break
                line += " | 时标 %s" % _decode_time7(t)
            lines.append((line, "info"))
    elif ti in (0x09, 0x0b, 0x0d, 0xce, 0xcf):
        lines.append(("  ── 信息对象（%d 个）──" % count, "head"))
        is_float = ti in (0x0d, 0xce, 0xcf)
        with_time = ti == 0xcf
        size = 4 if is_float else 2
        for i in range(count):
            addr = r.take(2, "信息体地址")
            val = r.take(size, "遥测数据")
            if addr is None or val is None:
                break
            addr_val = addr[0] | (addr[1] << 8)
            if is_float:
                f = struct.unpack("<f", val)[0]
                vtxt = "%.6f" % f
            else:
                iv = val[0] | (val[1] << 8)
                if iv >= 0x8000:
                    iv -= 0x10000
                if ti == 0x09:
                    vtxt = "%.6f" % (iv / 32768.0)
                else:
                    vtxt = str(iv)
            q = r.take(1, "QDS")
            if q is None:
                break
            line = "    对象%d：点号 %04X(%d) | 值=%s | %s" % (i + 1, addr_val, addr_val, vtxt, _qds_text(q[0]))
            if with_time:
                t = r.take(7, "时标")
                if t is None:
                    break
                line += " | 时标 %s" % _decode_time7(t)
            lines.append((line, "info"))
    elif ti in (0x2d, 0x2e):
        lines.append(("  ── 信息对象（%d 个）──" % count, "head"))
        for i in range(count):
            addr = r.take(2, "信息体地址")
            cmd = r.take(1, "遥控命令")
            if addr is None or cmd is None:
                break
            addr_val = addr[0] | (addr[1] << 8)
            lines.append(("    对象%d：点号 %04X(%d) | %s" % (i + 1, addr_val, addr_val, _command_text(ti, cmd[0])), "info"))
    elif ti == 0x2a:
        lines.append(("  ── 故障事件信息（TI=2A）──", "head"))
        lines.append(("    说明：故障事件含遥信+故障时刻遥测，格式以装置说明书为准；此处显示原始数据：", "dim"))
        rest = r.rest()
        if rest:
            lines.append(("    %s" % " ".join("%02X" % x for x in rest), "dim"))
    elif ti == 0xd2:
        lines.append(("  ── 文件传输（TI=D2）──", "head"))
        op = r.take(1, "操作标识")
        if op is not None:
            lines.append(("    操作标识=%02X：%s" % (op[0], FILE_OP_TABLE.get(op[0], "未知")), "info"))
        rest = r.rest()
        if rest:
            lines.append(("    后续数据：%s" % " ".join("%02X" % x for x in rest), "dim"))
    else:
        lines.append(("  ── 未知类型标识，无法详细解析，原始数据：", "warn"))
        rest = r.rest()
        if rest:
            lines.append(("    %s" % " ".join("%02X" % x for x in rest), "dim"))

    if r.remaining() > 0:
        lines.append(("  ⚠ 有 %d 字节数据未解析，报文结构可能异常" % r.remaining(), "warn"))


def decode_asdu(a, lines):
    if len(a) < 6:
        lines.append(("  ⚠ ASDU长度不足（%d字节），无法解析数据单元标识符" % len(a), "warn"))
        return
    ti, vsq, cotl, coth, a_al, a_ah = a[0], a[1], a[2], a[3], a[4], a[5]
    sq = (vsq >> 7) & 1
    count = vsq & 0x7F
    cot = cotl | (coth << 8)
    info = a[6:]

    lines.append(("  ── ASDU 数据单元标识符 ──", "head"))
    ti_known = ti in TI_TABLE
    cot_known = cot in COT_TABLE
    lines.append(("    类型标识 TI=%02X：%s" % (ti, TI_TABLE.get(ti, "未知类型标识！")), "info" if ti_known else "warn"))
    lines.append(("    可变结构 VSQ=%02X：%s，%d 个对象" % (vsq, "顺序(SQ=1)" if sq else "非顺序(SQ=0)", count), "info"))
    lines.append(("    传送原因 COT=%04X：%s" % (cot, COT_TABLE.get(cot, "未知传送原因！")), "info" if cot_known else "warn"))
    if cot in (0x2c, 0x2d, 0x2e, 0x2f):
        lines.append(("    ⚠ 注意：这是对方回复的否定/错误信息：" + COT_TABLE[cot], "warn"))
    lines.append(("    公共地址：%02X%02X (十进制 %d)" % (a_ah, a_al, a_ah * 256 + a_al), "info"))
    _decode_info_objects(ti, sq, count, info, lines)

# ---------------------------------------------------------------- 帧级解析

def decode_fixed(f):
    lines = []
    lines.append(("【固定帧】%s" % " ".join("%02X" % x for x in f), "head"))
    c, al, ah, cs = f[1], f[2], f[3], f[4]
    # 校验和：文档公式为 10+C+AL+AH，实际报文按 C+AL+AH
    calc = (c + al + ah) & 0xFF
    if calc == cs:
        lines.append(("  校验和 CS=%02X：正确" % cs, "ok"))
    else:
        alt = (0x10 + c + al + ah) & 0xFF
        if alt == cs:
            lines.append(("  ⚠ 校验和：按 C+AL+AH 应为 %02X（按含起始符 10 计算则匹配 %02X）" % (calc, alt), "warn"))
        else:
            lines.append(("  ⚠ 校验和错误：帧内 CS=%02X，按 C+AL+AH 计算应为 %02X" % (cs, calc), "warn"))

    dir_bit = (c >> 7) & 1
    prm = (c >> 6) & 1
    fc = c & 0x0F
    dir_txt = "终端→主站(上行)" if dir_bit else "主站→终端(下行)"
    lines.append(("  控制域 C=%02X (%s)" % (c, format(c, "08b")), "head"))
    lines.append(("    方向：%s | PRM=%d(%s)" % (dir_txt, prm, "启动站" if prm else "从动站"), "info"))
    if prm:
        fcb = (c >> 5) & 1
        fcv = (c >> 4) & 1
        lines.append(("    FCB=%d(帧计数位) | FCV=%d(%s) | 功能码=%d" % (fcb, fcv, "有效" if fcv else "无效", fc), "info"))
    else:
        dfc = (c >> 4) & 1
        lines.append(("    DFC=%d(%s) | 功能码=%d" % (dfc, "忙，不能再接收后续报文" if dfc else "可以接收后续报文", fc), "info"))
    name = FIXED_FC_NAMES.get(c) or FIXED_FC_GENERIC.get(fc, "功能码%d" % fc)
    lines.append(("    功能：%s" % name, "info"))
    lines.append(("  链路地址：%02X%02X (十进制 %d)" % (ah, al, ah * 256 + al), "info"))
    return lines


def decode_long(f):
    lines = []
    lines.append(("【长帧】%s" % " ".join("%02X" % x for x in f), "head"))
    l_byte, c, al, ah = f[1], f[4], f[5], f[6]
    asdu = f[7:-2]
    cs = f[-2]

    actual_l = len(f) - 6
    if l_byte == actual_l:
        lines.append(("  长度 L=%02X：正确（整帧 %d 字节）" % (l_byte, len(f)), "ok"))
    else:
        lines.append(("  ⚠ 长度不符：L=%02X，按整帧长度计算应为 %02X" % (l_byte, actual_l), "warn"))

    calc = (c + al + ah + sum(asdu)) & 0xFF
    if calc == cs:
        lines.append(("  校验和 CS=%02X：正确" % cs, "ok"))
    else:
        lines.append(("  ⚠ 校验和错误：帧内 CS=%02X，按 C+AL+AH+ASDU 计算应为 %02X" % (cs, calc), "warn"))

    dir_bit = (c >> 7) & 1
    fcb = (c >> 5) & 1
    fcv = (c >> 4) & 1
    fc = c & 0x0F
    dir_txt = "终端→主站(上行)" if dir_bit else "主站→终端(下行)"
    lines.append(("  控制域 C=%02X (%s)" % (c, format(c, "08b")), "head"))
    lines.append(("    方向：%s | PRM=1(启动站) | FCB=%d | FCV=%d(%s) | 功能码=%d" % (
        dir_txt, fcb, fcv, "有效" if fcv else "无效", fc), "info"))
    if fc == 3:
        lines.append(("    功能：发送/确认用户数据（长帧数据）", "info"))
    elif fc == 4:
        lines.append(("    功能：发送/无回答用户数据", "info"))
    else:
        lines.append(("    ⚠ 长帧控制域功能码应通常为 3（发送/确认用户数据），当前=%d" % fc, "warn"))
    lines.append(("  链路地址：%02X%02X (十进制 %d)" % (ah, al, ah * 256 + al), "info"))

    decode_asdu(asdu, lines)
    return lines


def decode_bad_frame(ftype, f):
    lines = [("【无法识别的数据】%s" % " ".join("%02X" % x for x in f), "warn")]
    lines.append(("  ⚠ 该段不是完整的固定帧/长帧（起始符与结束符 16 不匹配或长度异常）", "warn"))
    return lines


def parse_input(text):
    """解析用户输入的16进制文本，返回 [{lines:[(文本,标签)], warnings:int}, ...]"""
    data = normalize_hex(text)
    if data is None:
        return [{"lines": [("无法解析输入：请输入16进制报文。", "warn"),
                           ("支持格式示例：68 0C 0C 68 53 01 00 64 01 06 00 01 00 00 00 14 D4 16（可含空格、逗号、0x，也可连写）", "dim")],
                "warnings": 1}]
    if not data:
        return [{"lines": [("输入为空。", "warn")], "warnings": 1}]
    frames = split_frames(data)
    if not frames:
        return [{"lines": [("没有找到完整报文帧（应以 10 或 68 开头、16 结尾）。", "warn"),
                           ("请检查报文是否完整，或是否选错了协议。", "dim")], "warnings": 1}]

    results = []
    for ftype, f in frames:
        if ftype == "fixed":
            lines = decode_fixed(f)
        elif ftype == "long":
            lines = decode_long(f)
        elif ftype == "sec":
            lines = decode_sec(f)
        else:
            lines = decode_bad_frame(ftype, f)
        warns = sum(1 for _t, tag in lines if tag == "warn")
        results.append({"lines": lines, "warnings": warns, "type": ftype})
    return results


# ---------------------------------------------------------------- 国网安全报文
# 参考《国网配电终端安全报文定义 V1.2.2》：在 101/104 报文外再封装一层安全帧
#   EB | 长度(2,高位在前) | EB | 报文类型(2) | 封装数据域(密文/明文) | 校验和(4) | D7
#   长度/校验和覆盖范围：报文类型..校验和之前

SEC_APP_TABLE = {
    0x00: "业务报文（101/104）",
    0x01: "业务报文 + 签名",
    0x02: "业务报文 + 随机数",
    0x03: "业务报文 + 随机数 + 签名",
    0x04: "业务报文 + 时间",
    0x05: "业务报文 + 时间 + 签名",
    0x06: "业务报文 + 时间 + 随机数",
    0x07: "业务报文 + 时间 + 随机数 + 签名",
    0x08: "升级包验证（时间+随机数+签名，无业务报文）",
    0x1f: "业务安全处理结果返回",
    0x20: "网关认证请求",
    0x21: "终端认证确认并请求网关认证",
    0x22: "网关对终端认证请求的响应",
    0x23: "终端向网关返回认证结果",
    0x24: "网关获取终端设备序列号",
    0x25: "终端返回设备序列号和芯片序列号",
    0x50: "主站认证请求",
    0x51: "终端认证确认并请求主站认证",
    0x52: "主站对终端认证请求的响应",
    0x53: "终端向主站返回认证结果",
    0x54: "主站获取终端芯片序列号",
    0x55: "终端返回芯片序列号",
    0x56: "主站获取终端设备特征码",
    0x57: "终端返回业务通道特征码",
    0x58: "主站获取管理通道特征码",
    0x59: "终端返回管理通道特征码",
    0x60: "主站获取终端密钥版本",
    0x61: "终端返回密钥版本",
    0x62: "主站远程密钥更新",
    0x63: "终端返回密钥更新结果",
    0x64: "主站远程密钥恢复",
    0x65: "终端返回密钥恢复结果",
    0x70: "证书远程更新",
    0x71: "证书远程更新结果",
    0x72: "远程下载终端证书",
    0x73: "终端证书下载结果",
    0x74: "主站提取终端证书",
    0x75: "终端返回证书",
    0x76: "证书提取结果",
}

SEC_ERR_BIZ = {
    0x9101: "业务应用类型错误",
    0x9102: "报文验签失败",
    0x9103: "报文解密失败",
    0x9104: "随机数验证失败",
    0x9105: "时间校验失败",
    0x9106: "业务安全要求不合规（如该加密未加密）",
    0x9107: "业务安全流程非法（如未先认证后业务）",
    0x9108: "权限不够",
    0x9109: "未知错误",
    0x9110: "报文长度有误或解析失败（CRC/MAC错误）",
}
SEC_ERR_SEC = {
    0x9000: "成功",
    0x9090: "认证失败",
    0x9091: "密钥更新失败",
    0x9092: "密钥恢复失败",
    0x9093: "证书导入失败",
    0x9094: "证书导出失败",
    0x9095: "证书提取失败",
    0x9096: "分帧数据接收失败",
    0x9097: "证书远程更新/下载失败",
}
SEC_ERR_ALL = dict(SEC_ERR_BIZ)
SEC_ERR_ALL.update(SEC_ERR_SEC)


def _decode_time6(b):
    """yymmddhhmmss 6字节时间。"""
    if len(b) < 6:
        return None
    yy, mm, dd, hh, mi, ss = b[0], b[1], b[2], b[3], b[4], b[5]
    return "20%02d-%02d-%02d %02d:%02d:%02d" % (yy, mm, dd, hh, mi, ss)


def _hex_str(b):
    return " ".join("%02X" % x for x in b)


def decode_sec_ext(app, ext, lines):
    """按应用类型解析信息安全扩展区内容。"""
    if not ext:
        lines.append(("    空", "dim"))
        return
    r = _Reader(ext, lines)
    has_time = app in (0x04, 0x05, 0x06, 0x07, 0x08)
    has_rnd = app in (0x02, 0x03, 0x06, 0x07, 0x08, 0x20, 0x21, 0x50, 0x51)
    has_sig = app in (0x01, 0x03, 0x05, 0x07, 0x08, 0x21, 0x22, 0x51, 0x52, 0x62, 0x64)
    if has_time:
        t = r.take(6, "时间")
        if t is not None:
            lines.append(("    时间：%s" % _decode_time6(t), "info"))
    if has_rnd:
        rn = r.take(8, "随机数")
        if rn is not None:
            lines.append(("    随机数：%s" % _hex_str(rn), "dim"))
    if has_sig:
        s = r.take(64, "签名结果")
        if s is not None:
            lines.append(("    签名结果：%s…" % _hex_str(s[:16]), "dim"))
        k = r.take(1, "签名密钥标识")
        if k is not None:
            lines.append(("    签名密钥标识：%02X" % k[0], "info"))

    if app in (0x1f, 0x23, 0x53, 0x63, 0x65, 0x71, 0x73, 0x76):
        res = r.take(2, "处理结果")
        if res is not None:
            code = (res[0] << 8) | res[1]
            name = SEC_ERR_ALL.get(code, "未知错误码")
            lines.append(("    处理结果：%04X %s" % (code, name),
                          "info" if code in SEC_ERR_ALL else "warn"))
    elif app in (0x24, 0x54, 0x56, 0x58, 0x60, 0x74):
        lines.append(("    无内容", "dim"))
    elif app == 0x25:
        sn = r.take(24, "终端序列号")
        chip = r.take(8, "芯片序列号")
        if sn is not None:
            lines.append(("    终端序列号：%s" % _hex_str(sn), "dim"))
        if chip is not None:
            lines.append(("    芯片序列号：%s" % _hex_str(chip), "dim"))
    elif app == 0x55:
        chip = r.take(8, "芯片序列号")
        if chip is not None:
            lines.append(("    芯片序列号：%s" % _hex_str(chip), "dim"))
    elif app in (0x57, 0x59):
        dev = r.take(24, "设备ID")
        if dev is not None:
            lines.append(("    终端设备ID：%s" % _hex_str(dev), "dim"))
        if app == 0x59:
            esn = r.take(28, "华为ESN")
            if esn is not None:
                lines.append(("    华为ESN：%s" % _hex_str(esn), "dim"))
        chip = r.take(8, "芯片序列号")
        if chip is not None:
            lines.append(("    芯片序列号：%s" % _hex_str(chip), "dim"))
    elif app == 0x61:
        kv = r.take(1, "密钥版本号")
        rn = r.take(8, "终端随机数")
        if kv is not None:
            lines.append(("    密钥版本号：%02X" % kv[0], "info"))
        if rn is not None:
            lines.append(("    终端随机数：%s" % _hex_str(rn), "dim"))
    elif app in (0x62, 0x64):
        pkg = r.take(181, "密钥更新/恢复包")
        if pkg is not None:
            lines.append(("    密钥包(%d字节)：%s…" % (len(pkg), _hex_str(pkg[:12])), "dim"))
    elif app in (0x70, 0x72, 0x75):
        cert = r.take(1, "证书标识")
        total = r.take(1, "总帧数")
        cur = r.take(1, "当前帧序号")
        if cert is not None:
            cert_names = {0: "CA证书", 1: "主站证书", 2: "主站证书", 3: "主站证书",
                          4: "主站证书", 5: "网关证书", 6: "终端证书"}
            lines.append(("    证书标识：%02X（%s）" % (cert[0], cert_names.get(cert[0], "未知")), "info"))
        if total is not None:
            lines.append(("    总帧数：%d  当前帧序号：%d" % (total[0], cur[0] if cur else 0), "info"))
        rest = r.rest()
        if rest:
            lines.append(("    数据(%d字节)：%s…" % (len(rest), _hex_str(rest[:12])), "dim"))
    else:
        lines.append(("    %s" % _hex_str(ext), "dim"))

    if r.remaining() > 0:
        lines.append(("    ⚠ 扩展区有 %d 字节未解析" % r.remaining(), "warn"))


def parse_plain(payload, lines, is_mgmt=False):
    """解析安全帧明文的封装数据域：应用类型 + 应用数据区 + 信息安全扩展区。"""
    r = _Reader(payload, lines)
    app_b = r.take(1, "应用类型")
    if app_b is None:
        return
    app = app_b[0]
    known = app in SEC_APP_TABLE
    lines.append(("  应用类型 %02X：%s" % (app, SEC_APP_TABLE.get(app, "未知应用类型！")),
                  "info" if known else "warn"))
    if app == 0x1f:
        lines.append(("  ⚠ 这是安全处理失败后返回的结果帧（见下方处理结果）", "warn"))

    # 应用数据区：业务1字节长度，管理2字节长度
    if is_mgmt:
        ln = r.take(2, "应用数据长度")
        dlen = (ln[0] << 8) | ln[1] if ln else 0
    else:
        ln = r.take(1, "应用数据长度")
        dlen = ln[0] if ln else 0
    if dlen > r.remaining():
        lines.append(("  ⚠ 应用数据长度异常：声明 %d 字节，实际剩余 %d 字节" % (dlen, r.remaining()), "warn"))
        dlen = r.remaining()
    appdata = r.take(dlen, "应用数据") if dlen else b""
    if appdata:
        if app in (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07) and not is_mgmt:
            inner = split_frames(appdata)
            if inner:
                lines.append(("  ┌─ 内嵌业务报文（101/104）────────", "head"))
                for itype, iframe in inner:
                    if itype == "fixed":
                        sub = decode_fixed(iframe)
                    elif itype == "long":
                        sub = decode_long(iframe)
                    else:
                        sub = decode_bad_frame(itype, iframe)
                    for t2, tag in sub:
                        lines.append(("  " + t2, tag))
                lines.append(("  └───────────────────────────────", "head"))
            else:
                lines.append(("  应用数据(%d字节)：%s" % (dlen, _hex_str(appdata)), "dim"))
        else:
            lines.append(("  应用数据(%d字节)：%s" % (dlen, _hex_str(appdata)), "dim"))

    # 信息安全扩展区：2字节长度 + 内容
    eln = r.take(2, "安全扩展区长度")
    elen = (eln[0] << 8) | eln[1] if eln else 0
    ext = r.take(elen, "安全扩展区") if elen else b""
    has_ext_meaning = app in (0x1f, 0x23, 0x53, 0x63, 0x65, 0x71, 0x73, 0x76,
                              0x20, 0x21, 0x22, 0x24, 0x25,
                              0x50, 0x51, 0x52, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
                              0x60, 0x61, 0x62, 0x64, 0x70, 0x72, 0x74, 0x75)
    if ext or has_ext_meaning:
        lines.append(("  信息安全扩展区(%d字节)：" % elen, "head"))
        decode_sec_ext(app, ext, lines)

    if r.remaining() > 0:
        lines.append(("  ⚠ 封装数据域有 %d 字节未解析" % r.remaining(), "warn"))


def decode_sec(f):
    """解析国网安全帧。f = EB LEN(2) EB TYPE(2) 封装数据域 CRC(4) D7"""
    lines = []
    lines.append(("【安全报文(国网)】%s" % _hex_str(f), "head"))
    sec_len = (f[1] << 8) | f[2]
    actual = len(f) - 9
    if sec_len == actual:
        lines.append(("  长度 LEN=%04X：正确（整帧 %d 字节）" % (sec_len, len(f)), "ok"))
    else:
        lines.append(("  ⚠ 长度不符：LEN=%04X，按整帧计算应为 %04X" % (sec_len, actual), "warn"))

    type_field = (f[4] << 8) | f[5]
    low = type_field & 0xFF
    conn = (low >> 6) & 0x03
    enc = (low >> 2) & 0x01
    key_id = low & 0x03
    conn_names = {0: "主站(业务报文)", 1: "现场运维工具", 2: "网关", 3: "主站(管理报文)"}
    lines.append(("  报文类型 %04X：连接对象=%s | %s | 对称密钥标识=%d" % (
        type_field, conn_names.get(conn, "?"), "加密" if enc else "明文", key_id), "info"))
    if (type_field >> 8) & 0xFF:
        lines.append(("  ⚠ 报文类型高8位应为0，当前=%02X" % ((type_field >> 8) & 0xFF), "warn"))

    # 校验和：覆盖 报文类型..校验和之前；按 CRC32 校验（大小端都试）
    covered = f[4:-5]
    calc = zlib.crc32(covered) & 0xFFFFFFFF
    crc_b = f[-5:-1]
    crc_be = (crc_b[0] << 24) | (crc_b[1] << 16) | (crc_b[2] << 8) | crc_b[3]
    crc_le = (crc_b[3] << 24) | (crc_b[2] << 16) | (crc_b[1] << 8) | crc_b[0]
    if calc == crc_be or calc == crc_le:
        lines.append(("  校验和 %s：与CRC32一致" % _hex_str(crc_b), "ok"))
    else:
        lines.append(("  校验和 %s：按CRC32计算应为 %08X（如现场采用其它算法可忽略）"
                      % (_hex_str(crc_b), calc), "dim"))

    payload = f[6:-5]
    if enc:
        if len(payload) >= 4:
            mac = payload[-4:]
            lines.append(("  封装数据域为密文（%d字节），MAC=%s" % (len(payload) - 4, _hex_str(mac)), "warn"))
            lines.append(("  ⚠ 密文需国网安全芯片解密后才能看到内部报文", "warn"))
        else:
            lines.append(("  ⚠ 密文数据异常（不足4字节）", "warn"))
        return lines
    parse_plain(payload, lines, is_mgmt=(conn == 3))
    return lines


def _build_sec_sample():
    """构造一个安全报文示例：明文业务帧包裹 101 总召报文。"""
    inner = bytes.fromhex("68 0C 0C 68 53 01 00 64 01 06 00 01 00 00 00 14 D4 16")
    payload = bytes([0x00, 0x12]) + inner + bytes([0x00, 0x00])  # 应用类型0x00, 数据长0x12, 扩展区0000
    covered = bytes([0x00, 0x00]) + payload
    crc = zlib.crc32(covered) & 0xFFFFFFFF
    crc_b = bytes([(crc >> 24) & 0xFF, (crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF])
    frame = bytes([0xEB, 0x00, len(covered)]) + bytes([0xEB]) + covered + crc_b + bytes([0xD7])
    return " ".join("%02X" % x for x in frame)


SEC_SAMPLE = _build_sec_sample()


# ---------------------------------------------------------------- 示例

SAMPLE_TEXT = (
    "68 0C 0C 68 53 01 00 64 01 06 00 01 00 00 00 14 D4 16\n"
    "10 40 01 00 41 16\n"
    "10 80 01 00 81 16\n"
    "68 13 13 68 D3 01 00 1E 01 03 00 01 00 13 00 01 0F 4E 1D 0F 0D 0B 11 BD 16\n"
    "68 0C 0C 68 53 01 00 68 01 06 00 01 00 00 00 AA 55 C3 16\n"
    "10 49 01 00 4A 16\n"
    + SEC_SAMPLE + "\n"
)

# ---------------------------------------------------------------- 界面

class HexTranslateDialog(tk.Toplevel):
    def __init__(self, master, config=None):
        super().__init__(master)
        self.config = config
        self.title("16进制报文翻译器（平衡101规约）")
        self.geometry("900x660")
        self.minsize(680, 480)
        self.transient(master)
        self.configure(bg="#f5f6f8")
        self._build()
        self.grab_set()

    def _build(self):
        # 输入区
        top = tk.Frame(self, bg="#f5f6f8")
        top.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(top, text="粘贴16进制报文（可多帧，支持空格/逗号/0x/连写）：",
                 bg="#f5f6f8", fg="#333333", font=(FONT, 10)).pack(anchor="w")
        self.input_text = tk.Text(top, height=5, font=("Consolas", 10), wrap="word",
                                  relief="solid", bd=1)
        self.input_text.pack(fill="x", pady=(4, 6))

        btns = tk.Frame(top, bg="#f5f6f8")
        btns.pack(fill="x")
        tk.Button(btns, text="解 析", command=self._parse, bg="#4a90d9", fg="#ffffff",
                  relief="flat", cursor="hand2", padx=18, pady=5, font=(FONT, 10, "bold")).pack(side="left")
        tk.Button(btns, text="载入示例", command=self._load_sample, bg="#d9dce1", fg="#333333",
                  relief="flat", cursor="hand2", padx=10, pady=5, font=(FONT, 9)).pack(side="left", padx=6)
        tk.Button(btns, text="清空", command=self._clear, bg="#d9dce1", fg="#333333",
                  relief="flat", cursor="hand2", padx=10, pady=5, font=(FONT, 9)).pack(side="left")
        tk.Button(btns, text="复制结果", command=self._copy_result, bg="#16a085", fg="#ffffff",
                  relief="flat", cursor="hand2", padx=10, pady=5, font=(FONT, 9)).pack(side="right")

        # 结果区
        mid = tk.Frame(self, bg="#f5f6f8")
        mid.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.result_text = tk.Text(mid, font=("Consolas", 10), wrap="word",
                                   relief="solid", bd=1, state="disabled", bg="#ffffff")
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=vsb.set)
        self.result_text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.result_text.tag_configure("head", foreground="#1f5fa8", font=("Microsoft YaHei UI", 10, "bold"))
        self.result_text.tag_configure("info", foreground="#333333")
        self.result_text.tag_configure("ok", foreground="#2e7d32")
        self.result_text.tag_configure("warn", foreground="#c62828", font=("Microsoft YaHei UI", 10, "bold"))
        self.result_text.tag_configure("dim", foreground="#888888")
        self.result_text.tag_configure("sep", foreground="#bbbbbb")

        # 状态栏
        bar = tk.Frame(self, bg="#e6e8eb", height=26)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        self.status = tk.Label(bar, text="提示：红色为问题项；可多帧一起粘贴解析。",
                               bg="#e6e8eb", fg="#666666", font=(FONT, 9), anchor="w")
        self.status.pack(side="left", padx=12)

        self.bind("<Control-Return>", lambda e: self._parse())

    def _parse(self):
        text = self.input_text.get("1.0", "end")
        results = parse_input(text)
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", "end")
        total_warns = 0
        total_frames = 0
        first = True
        for res in results:
            if not first:
                self.result_text.insert("end", "\n" + "-" * 80 + "\n", "sep")
            first = False
            for t, tag in res["lines"]:
                self.result_text.insert("end", t + "\n", tag)
            total_warns += res["warnings"]
            total_frames += 1
        self.result_text.config(state="disabled")
        if total_warns:
            self.status.config(text="共解析 %d 帧，发现 %d 个问题（红色项）。" % (total_frames, total_warns),
                               fg="#c62828")
        else:
            self.status.config(text="共解析 %d 帧，全部正常。" % total_frames, fg="#2e7d32")

    def _load_sample(self):
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", SAMPLE_TEXT)
        self._parse()

    def _clear(self):
        self.input_text.delete("1.0", "end")
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.config(state="disabled")
        self.status.config(text="提示：红色为问题项；可多帧一起粘贴解析。", fg="#666666")

    def _copy_result(self):
        self.clipboard_clear()
        self.clipboard_append(self.result_text.get("1.0", "end"))
        self.status.config(text="结果已复制到剪贴板。", fg="#2e7d32")


if __name__ == "__main__":
    # 命令行自测：python hex_translator.py "68 0C 0C 68 53 01 00 64 01 06 00 01 00 00 00 14 D4 16"
    import sys
    text = sys.argv[1] if len(sys.argv) > 1 else SAMPLE_TEXT
    results = parse_input(text)
    for res in results:
        for t, tag in res["lines"]:
            print(t)
        print("-" * 60)
