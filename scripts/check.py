# -*- coding: utf-8 -*-
"""
句式指纹检测器 —— 双层架构（统计核心 + 文体 profile）

适用范围：中文现代散文体（小说/散文/报告/说明文）。
    诗歌与文言/半文言不适用——句长恒定、对仗复现、重韵律是体裁形式本体，
    会被系统性误判（ONTOLOGY §一/§五-5）。

本体论与判据规格见仓库根 ONTOLOGY.md（宪章）。本脚本是其 §3「方法论」的实现：
  指标① 出现频率   L2 句法骨架倒排（章内） + --cross-chapter 结尾段 4-gram（跨章，定位型）
  指标② 标准化方差 L3 章级句长 CV = σ/mean（cv_thresh，方向自定：高 → 关注）
  指标③ 信息密度   —— 已退役（句对级操作化证伪，ONTOLOGY §3.4），本脚本无实现
  合取判据          ①+② 2/2 制（1/2 提示、2/2 关注；工具侧计数输出待实现）

辅助层（宪章 §3.3 定性，不进合取）：
  L0 句内结构   '的'链/顿号并列/词性堆叠/'是…的'（可读性层，与 AI 味弱相关）
  L0.5 模板标点 句式模板词对复现 + 标点密度
  L1 词层      新信息率、句首/句末词分布、高频 3-gram
  L1.5 规范清单 违禁词/情绪直写/对话标签/系统数据行（时敏层，仅提示，需 profile 开启）

用法：
    python scripts/check.py [--profile=通用|网文] [--out-dir=DIR] 文件 [文件 ...]
    python scripts/check.py --calibrate <基线文件...>     # 均值±2σ 校准建议阈值（不自动写回）
    python scripts/check.py --cross-chapter <文件...>     # 跨章装置报告（指标①，≥2 章）

支持格式：.md / .txt / 无扩展名文本 / .docx（Word）。
    纯文本编码按 utf-8 → gbk → latin-1 自动回退；docx 仅读正文（表格按行合并，
    页眉页脚/批注/脚注不计入统计）。

配置：
    profiles/<体裁名>.yaml（每体裁一个文件，存在则覆盖内置默认；自定义 profile 以
    "通用"为基底补齐缺省键）。旧版单文件 profiles.yaml 仍兼容（按 当前工作目录 →
    仓库根 → scripts/ 顺序查找）。可用 --calibrate 校准后手写，阈值不跨语料迁移。
    报告模板：report_template.md.j2（jinja2，默认在 assets/ 下，也可放脚本同目录）。

实现注记：
    - POS 标注经 TokCache 缓存，L0/L1/L2 共享，同一句子只标注一次。
    - 骨架清洗：去结构助词与停顿标点，保留 EOS/QT 结构锚点。
    - CV 公式与章内 n-gram 统计骨架参考 lmscan v0.6.1（Apache-2.0, github.com/stef41/lmscan），
      判据方向按本语料实测自定，未复制其词表与英文分词假设（详见 references/lmscan-feature-audit.md）。
"""

import sys
import os
import re
import collections

import jieba.posseg as pseg

try:
    import yaml
except ImportError:
    yaml = None

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    Environment = None

# ---------------------------------------------------------------- 常量

SENT_END = "。！？…"
CLAUSE_END = SENT_END + "，、：；"
QUOTES = "“”‘’「」『』"
PUNCT = set(QUOTES + "。！？…，、：；（）《》〈〉【】——·~～·—…\"'")

# 骨架清洗：功能词（结构助词）不参与骨架；标点仅保留 EOS/QT 作结构锚点
FUNC_POS = ("uj", "ul", "uv", "uz", "ug", "u")
KEEP_PUN = ("EOS", "QT")
PUN_DROP = ("COM", "STO", "PUN")

# jieba 词性前缀：实词
CONTENT_POS = ("n", "v", "a")  # 名词/动词/形容词系

# 句首统计时过滤的常见虚词/代词
SENT_START_STOP = set("他她它我你您那这这那其之而于以与及或被把将着为到向对在从由因所等若如即")
# 句末统计时保留的助词/语气词（中文句末指纹）
SENT_END_KEEP = "了着的过啊呢吧吗呀嘛罢哦嗯唉"

# 输出目录：由 --out-dir 指定（默认 cwd/reports），在 main() 中确定
OUT_DIR = None

# 文体配置：通用模式只做统计核心 + 文本内相对异常判断（不依赖文体先验）；
# 网文模式叠加网文创作规范的专属规则（黑名单/对话标签/系统数据行/绝对阈值）。
# 若存在 profiles.yaml 则加载覆盖（用户可手改或用 --calibrate 校准）。
DEFAULT_PROFILES = {
    "通用": {
        "banlist": False,      # 不判黑名单词（"不禁"在文学文本是正常表达）
        "dialog": False,       # 不判对话标签占比
        "dataline": False,     # 不判系统数据行
        "dash_thresh": None,   # 破折号不设绝对阈值，只报相对分布
        "de_long_flag": False, # "的"长链只展示不 flag（论文文体天然密度高）
        "tpl_thresh": 5,       # 模板重复阈值（通用：5 次才算显著）
        "rep_ratio": 0.10,     # 重复骨架句占全句 >10% 视为句式单调（相对自身规模）
        "de_sto_flag": False,  # 顿号并列"的"不判 flag（通用文体不关注）
    },
    "网文": {
        "banlist": True,
        "dialog": True,
        "dataline": True,
        "dash_thresh": 4.0,
        "de_long_flag": True,
        "de_sto_flag": True,
        "tpl_thresh": 3,
        "rep_ratio": 0.06,
        "cv_thresh": None,     # 指标②绝对阈值默认关闭：用 --calibrate 得基线 CV 后手填（宪章§七）
    },
}
DEFAULT_PROFILE = "通用"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
TEMPLATE = "report_template.md.j2"


def find_template():
    """定位报告模板：assets/ → 脚本目录 → cwd，返回首个存在的目录。"""
    for d in (os.path.join(SKILL_DIR, "assets"), SCRIPT_DIR, os.getcwd()):
        if os.path.exists(os.path.join(d, TEMPLATE)):
            return d
    raise FileNotFoundError(
        f"找不到 {TEMPLATE}（查找过 assets/、scripts/、当前目录）。"
        f"请把模板放到 assets/ 下或脚本同目录。"
    )


def find_profiles_yaml():
    """按 当前工作目录 → skill 根目录 → scripts/ 顺序查找 profiles.yaml。"""
    for p in (os.path.join(os.getcwd(), "profiles.yaml"),
              os.path.join(SKILL_DIR, "profiles.yaml"),
              os.path.join(SCRIPT_DIR, "profiles.yaml")):
        if os.path.exists(p):
            return p
    return None


def load_profiles():
    """加载文体参数：内置默认 ← profiles/*.yaml（每体裁一个文件）← profiles.yaml（旧版单文件，兼容）。

    profiles/ 目录下每个 *.yaml 为一份体裁参数（文件内可含一个或多个 profile 名）；
    自定义 profile 以"通用"为基底补齐缺省键，只写差异项即可。
    新体裁建议先 --calibrate 基线文件，再按"建议阈值"（均值±2σ）填写。
    """
    profiles = {k: dict(v) for k, v in DEFAULT_PROFILES.items()}
    if yaml is None:
        return profiles
    pdir = os.path.join(SKILL_DIR, "profiles")
    if os.path.isdir(pdir):
        paths = sorted(os.path.join(pdir, f) for f in os.listdir(pdir)
                       if f.endswith((".yaml", ".yml")))
    else:
        legacy = find_profiles_yaml()
        paths = [legacy] if legacy else []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                ext = yaml.safe_load(f) or {}
            for name, cfg in ext.items():
                if name in profiles:
                    profiles[name].update(cfg)
                else:
                    # 自定义 profile 以"通用"为基底补齐缺省键，只写差异项即可
                    base = {k: v for k, v in DEFAULT_PROFILES[DEFAULT_PROFILE].items()}
                    base.update(cfg or {})
                    profiles[name] = base
        except Exception as e:
            print(f"⚠ {path} 加载失败（跳过）：{e}")
    return profiles

# ---------------------------------------------------------------- 文本解析


def read_docx(path):
    """从 .docx 抽取段落文本：解压 word/document.xml，按 <w:p> 段取 <w:t> 文本。

    只依赖标准库（zipfile + 正则）；表格内文本（<w:tc>）按行合并为段。
    """
    import zipfile
    import xml.etree.ElementTree as ET
    NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as z:
        xml_data = z.read("word/document.xml")
    root = ET.fromstring(xml_data)
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("非标准 docx：找不到 word/document.xml 的 body")
    paras = []
    for p in body.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        text = "".join(t.text or "" for t in p.iter(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        if text.strip():
            paras.append(text.strip())
    return paras


def read_text(path):
    """读纯文本（md/txt/无扩展名）：utf-8-sig → gbk → latin-1 依次回退。"""
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")  # 理论不可达


def read_chapter(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        paras = read_docx(path)
        return os.path.splitext(os.path.basename(path))[0], paras
    raw = read_text(path)
    # 半角标点归一化：ch001 等章节混用半角逗号，会让切分/断链失灵
    raw = raw.replace(",", "，").replace("!", "！").replace("?", "？")
    raw = raw.replace(";", "；").replace(":", "：").replace("(", "（").replace(")", "）")
    lines = raw.splitlines()
    paras, title = [], ""
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            title = s.lstrip("# ").strip()
            continue
        if s.startswith(">"):  # 引用块/作者注
            continue
        if re.fullmatch(r"[—\-*=~]{2,}", s):  # markdown 分隔线
            continue
        paras.append(s)
    return title or os.path.splitext(os.path.basename(path))[0], paras


def split_semantic_sents(text, sto_break=True):
    """按语义句切分：逗号/句末符切分；顿号默认也切分（供句长/骨架层用）。

    sto_break=False 时顿号不切分——用于"的"链检测：顿号连接的是并列修饰语
    （"穿红衣服的、在雨中站着的"），属于同一修饰链，切开会漏掉排比定语。
    """
    chars = "，、：；。！？…" if sto_break else "，：；。！？…"
    parts = re.split(r"([" + chars + r"])", text)
    sents, buf = [], ""
    for p in parts:
        if not p:
            continue
        buf += p
        if p in chars:
            sents.append(buf)
            buf = ""
    if buf.strip():
        sents.append(buf)
    return [s.strip() for s in sents if s.strip()]


def split_final_sents(text):
    """按句末符切分（用于 L3 的标点句统计）。"""
    parts = re.split(r"([。！？…])", text)
    sents, buf = [], ""
    for p in parts:
        if not p:
            continue
        buf += p
        if p in SENT_END:
            sents.append(buf)
            buf = ""
    if buf.strip():
        sents.append(buf)
    return [s.strip() for s in sents if s.strip()]


def pos_tag(sent, normalize_pn=False):
    """返回 (词, 词性) 列表；标点归一化为类别标记。"""
    out = []
    for w, p in pseg.cut(sent):
        if p == "x" and w in PUNCT:
            if w in SENT_END:
                tag = "EOS"          # 句末
            elif w == "、":
                tag = "STO"          # 顿号：并列修饰语分隔，不断"的"链
            elif w in "，：；":
                tag = "COM"          # 停顿
            elif w in QUOTES or w in "\"'":
                tag = "QT"           # 引号
            else:
                tag = "PUN"
            out.append((w, tag))
        else:
            if normalize_pn and p in ("nr", "nrfg", "nr1", "nr2", "nrt"):
                out.append((w, "PN"))  # 人名归一化，让不同人名的同构动作句式合并
            else:
                out.append((w, p))
    return out


# 句式模板词对（半同构句式）：骨架指纹要求完全同构会漏掉"不是X而是Y"这类
# 标记词固定、填充任意的模板重复。检测基于标点句（按句号切，避免逗号劈开模板）。
TPL_PAIRS = [
    ("不是", "而是"), ("并非", "而是"), ("不再", "而是"),
    ("不仅", "而且"), ("不仅", "还"), ("不仅", "更"),
    ("既", "又"), ("虽然", "但是"), ("虽然", "但"),
    ("一边", "一边"), ("与其", "不如"), ("宁可", "也不"),
    ("要么", "要么"), ("无论", "都"), ("不管", "都"),
    ("只要", "就"), ("只有", "才"), ("因为", "所以"),
    ("如果", "那么"), ("否则", "就"), ("于是", "便"),
]


def l_tpl_punct(paras):
    """句式模板重复 + 标点密度指纹。

    模板：标记词对在同一个标点句内按顺序出现即命中（填充任意）。
    标点：破折号/省略号按"组"计，其余按字符计，输出次/千字。
    """
    tpl_hits = {}
    punct = collections.Counter()
    total_chars = sum(len(p) for p in paras)
    # 预编译：re.search 从所有起始位置尝试，m1 多次出现时只要其后存在 m2 即命中
    tpl_re = [(re.compile(re.escape(m1) + r".*?" + re.escape(m2)), f"{m1}…{m2}") for m1, m2 in TPL_PAIRS]
    for para in paras:
        for f in split_final_sents(para):
            for pat, name in tpl_re:
                if pat.search(f):
                    tpl_hits.setdefault(name, []).append(f)
        # 破折号按"连续—串"计（——算一次，单个—也算）；半角 -- 并入
        punct["破折号"] += len(re.findall(r"—+", para)) + len(re.findall(r"-{2,}", para))
        punct["省略号"] += len(re.findall(r"…{2,}", para))
        punct["问号"] += para.count("？")
        punct["感叹号"] += para.count("！")
        punct["分号"] += para.count("；")
        punct["冒号"] += para.count("：")
    tpl_rows = [
        (t, len(sents), sents) for t, sents in tpl_hits.items() if len(sents) >= 2
    ]
    tpl_rows.sort(key=lambda x: -x[1])
    per_k = {k: v / total_chars * 1000 for k, v in punct.items()}
    return {"tpl_rows": tpl_rows, "punct": per_k, "chars": total_chars}


# ---------------------------------------------------------------- L1.5 规范清单

# L1.5 规范清单（时敏层，见 ONTOLOGY §3.3）：网文创作规范中可自动化的项。
# ⚠️ 词表为特定时期的模型套话，公开即失效——仅作提示，不进合取判据；更新走语料自动诱导。
BAN_WORDS = [
    "不禁", "忍不住", "心中暗道", "眼中闪过一抹精光", "涌上心头",
    "在当今社会", "随着时代的发展", "众所周知", "总而言之", "心中一动",
]
EMO_WORDS = [
    "愤怒", "悲伤", "恐惧", "害怕", "激动", "兴奋", "烦躁", "不安", "失落",
    "绝望", "委屈", "羞耻", "后悔", "惭愧", "得意", "紧张", "焦虑", "苦涩",
    "酸楚", "慌乱", "心悸", "窃喜",
]
EMO_PAT = re.compile(
    r"(感到|觉得|心中|心里|心底|一股|涌上|生出|泛起|满心)(?:了)?(?:一阵|一丝|一股|莫名的|的)?("
    + "|".join(EMO_WORDS) + ")"
)
DIALOG_TAG = re.compile(
    r"[“\"'][^”\"']{1,80}[”\"']\s*(?:[，。！？；])?\s*[^，。！？\n]{0,8}?"
    r"(?:说|道|问|喊|答|接|应|笑|叹|嘀咕|解释|提醒|警告|沉声道|淡淡道|轻声道|低声道|开口|出声|嘱咐|追问|嘟囔)"
)
DIALOG_TAG_PRE = re.compile(
    r"(?:说|道|问|喊|答|笑道|淡淡道|沉声道|轻声道|努嘴|皱眉|抬眼|抬头|点头|摇头|摆手|摊手|冷笑|开口|出声|压低声音)[：:,，]?[“\"']"
)
DATA_LINE = re.compile(r"^[^\n：:]{1,12}[：:][^\n：:]{1,24}$")


def l_banlist(paras):
    """创作规范自动项：AI 高频词 / 情绪直写 / 对话标签占比 / 引号占比 / 段落句数 / 数据行。"""
    ban_hits, emo_hits = [], []
    n_quotes = n_tagged = 0
    quoted_chars = total_chars = 0
    para_sent_counts = []
    data_runs = []

    for pi, para in enumerate(paras):
        total_chars += len(para)
        for w in BAN_WORDS:
            if w in para:
                ban_hits.append((pi + 1, w, para[:60]))
        m = EMO_PAT.search(para)
        if m:
            emo_hits.append((pi + 1, m.group(0), para[:60]))
        qs = re.findall(r"[“\"'][^”\"']*[”\"']", para)
        n_quotes += len(qs)
        quoted_chars += sum(len(q) for q in qs)
        n_tagged += len(DIALOG_TAG.findall(para)) + len(DIALOG_TAG_PRE.findall(para))
        para_sent_counts.append(len(split_final_sents(para)))
        if DATA_LINE.match(para) and not re.search(r"[“\"'”]", para):  # 排除对话引导冒号（如"萧嫣抬眼："）
            if data_runs and data_runs[-1][-1] == pi:  # 与前一段连续（段号直接追加）
                data_runs[-1].append(pi + 1)
            else:
                data_runs.append([pi + 1])

    tag_ratio = n_tagged / n_quotes if n_quotes else 0
    quote_ratio = quoted_chars / total_chars if total_chars else 0
    para_counts = collections.Counter(para_sent_counts)
    total_paras = len(para_sent_counts)
    mid_ratio = (para_counts.get(3, 0) + para_counts.get(4, 0)) / total_paras if total_paras else 0
    return {
        "ban_hits": ban_hits, "emo_hits": emo_hits,
        "tag_ratio": tag_ratio, "quote_ratio": quote_ratio,
        "n_quotes": n_quotes, "para_counts": para_counts,
        "mid_ratio": mid_ratio,
        "data_runs": [pids for pids in data_runs if len(pids) >= 2],
    }


# ---------------------------------------------------------------- L0 句内结构


def de_chain_len(toks):
    """句中'的'的最长连续链长度：'的'之间只要没有标点打断就视为同一修饰链。

    'A的B的C'（链长2）= 正常定语，可接受；'A的B的C的D'（链长≥3）= 长定语堆叠，AI 味。
    """
    max_chain = cur = 0
    for _, p in toks:
        if p == "uj":
            cur += 1
            max_chain = max(max_chain, cur)
        elif p in ("COM", "EOS", "QT", "PUN"):
            cur = 0
    return max_chain


def l_intra(paras, tok_cache=None):
    """句内结构检测：'的'字连续链（长定语）、'地'状语堆叠、连续同词性堆叠、'是…的'句。

    与骨架重复互补：重复度看句间，这里看单句内的修饰密度（AI 长定语堆叠的特征）。
    '的'≥3 连续链是强信号；'的'=2 属可接受，仅作参考展示。
    """
    de_long = []     # '的'连续链 ≥3（长定语堆叠，AI 味）
    de_two = []      # '的'连续链 =2（可接受，参考）
    de_sto = []      # '的'字结构顿号并列：规范宜用逗号（GB/T 15834），非 AI 味
    uv_rows = []
    stack_rows = []
    shi_rows = []
    total_de = total_chars = 0
    for pi, para in enumerate(paras):
        for s in split_semantic_sents(para, sto_break=False):  # 顿号不断链：并列修饰语属同一链
            toks = tok_cache.get(s) if tok_cache else pos_tag(s)
            words = [w for w, _ in toks]
            pos = [p for _, p in toks]
            chain = de_chain_len(toks)
            n_de = sum(1 for w, p in toks if p == "uj" and w == "的")
            total_de += n_de
            total_chars += len(s)
            # "的"字结构顿号并列：STO 前一词必须是"的"（字面），且句内 ≥3 处才报
            # （"A的、B的、C的" 2 处顿号属紧凑并列，4 项以上才是明显堆叠）
            n_sto_de = sum(
                1 for i, (w, p) in enumerate(toks)
                if p == "STO" and i >= 1 and toks[i - 1][1] == "uj" and toks[i - 1][0] == "的"
            )
            # 归因互斥：含"的"字顿号并列的句子是标点形态（顿号误用），不是无顿号长定语 AI 味
            if chain >= 3 and n_sto_de == 0:
                de_long.append((pi + 1, chain, n_de, s))
            elif chain == 2:
                de_two.append((pi + 1, chain, n_de, s))
            if n_sto_de >= 3:
                de_sto.append((pi + 1, n_sto_de, s))
            n_uv = sum(1 for _, p in toks if p == "uv")
            if n_uv >= 2:
                uv_rows.append((pi + 1, n_uv, s))
            # 连续同词性堆叠：直接在原始词性序列（含标点标记）上数连续段，
            # 标点/助词天然打断，避免"删虚词后假连击"的误报
            BREAKERS = ("EOS", "COM", "QT", "PUN", "uj", "ul", "uv", "uz", "ug", "u")
            sig = [p if p not in BREAKERS else "|" for p in pos]
            run, best = 1, (0, "")
            for i in range(1, len(sig)):
                if sig[i] != "|" and sig[i] == sig[i - 1]:
                    run += 1
                else:
                    if run > best[0]:
                        best = (run, sig[i - 1] if i - 1 < len(sig) else "")
                    run = 1
            if sig and run > best[0]:
                best = (run, sig[-1])
            if best[0] >= 3 and best[1] in ("v", "vn", "a", "an", "ad", "n", "z", "l", "i"):
                stack_rows.append((pi + 1, best[0], best[1], s))
            # "是……的"强调句：真正的"是…的"要求最后一个"的"位于分句末尾
            # （后面只剩标点/引号/语气词），且"是"与"的"之间有动词短语。
            # 仅"是"与"的"同句不算（如"这是硬科幻给AI的第一副面孔"——"的"后还有名词）。
            if "是" in words:
                de_idx = [i for i, w in enumerate(words) if w == "的"]
                if de_idx:
                    j = de_idx[-1]  # 句末"的"候选
                    tail = pos[j + 1:]
                    if all(p in ("EOS", "QT", "PUN", "y", "uj") for p in tail):
                        mid = pos[:j]
                        if any(p.startswith("v") for p in mid):
                            shi_rows.append((pi + 1, s))
    density = total_de / total_chars * 100 if total_chars else 0
    return {
        "density": density, "de_long": de_long, "de_two": de_two, "de_sto": de_sto,
        "uv_rows": uv_rows, "stack_rows": stack_rows, "shi_rows": shi_rows,
        "n_de": total_de, "n_chars": total_chars,
    }


# ---------------------------------------------------------------- L1 词层


def l1_stats(sents_all, paras, tok_cache=None):
    """sents_all: 全文语义句; paras: 原文段落。"""
    rep = {}

    # 1) 新信息率：滚动窗口（约 600 字）为记忆，段落后新实词比例
    window_words = collections.Counter()
    window_deque = collections.deque()
    WINDOW_WORDS = 400  # 约 600 字
    low_info = []
    for pi, para in enumerate(paras):
        words = [(w, p) for w, p in pseg.cut(para) if p.startswith(CONTENT_POS) and len(w) >= 2]
        total = len(words)
        if total == 0:
            continue
        new_cnt = sum(1 for w, _ in words if window_words[w] == 0)
        ratio = new_cnt / total
        if total >= 5 and ratio < 0.12:  # 实词过少的对话碎片段不判；其余新词比例过低 → 疑似原地打转
            low_info.append((pi + 1, ratio, new_cnt, total))
        for w, _ in words:
            window_words[w] += 1
            window_deque.append(w)
        while len(window_deque) > WINDOW_WORDS:
            old = window_deque.popleft()
            window_words[old] -= 1
            if window_words[old] <= 0:
                del window_words[old]
    rep["low_info_paras"] = low_info

    # 2) 句首/句末词分布
    starts, ends = collections.Counter(), collections.Counter()
    for s in sents_all:
        toks = tok_cache.get(s) if tok_cache else pos_tag(s)
        if not toks:
            continue
        # 句首词：跳过引号/停顿符
        for w, p in toks:
            if p in ("QT", "COM", "STO", "EOS", "PUN"):
                continue
            starts[w] += 1
            break
        # 句末词：最后一个非标点词
        for w, p in reversed(toks):
            if p in ("QT", "COM", "STO", "EOS", "PUN"):
                continue
            ends[w] += 1
            break
    rep["starts_top"] = starts.most_common(12)
    rep["ends_top"] = ends.most_common(12)
    n_sents = len(sents_all)
    rep["end_aux_ratio"] = {w: c / n_sents for w, c in ends.most_common(6) if w in SENT_END_KEEP}

    # 3) 高频 3-gram 复现（词级，过滤含纯标点/虚词的）
    tokens = []
    for s in sents_all:
        toks = tok_cache.get(s) if tok_cache else pos_tag(s)
        tokens.extend(w for w, _ in toks if w not in PUNCT and len(w) >= 1)
    grams = collections.Counter(zip(tokens, tokens[1:], tokens[2:]))
    rep["grams_top"] = [(g, c) for g, c in grams.most_common(40) if c >= 3]
    return rep


# ---------------------------------------------------------------- L2 句法层


def clean_skel(toks):
    """骨架清洗：去功能词（的/了/地…）与停顿标点，保留实词 + EOS/QT 结构锚点。

    去功能词后同构句更容易聚类（"他走了"/"她来了"→ r|v|EOS）；
    保留 EOS/QT 区分完成句/对话语体。
    """
    return "|".join(
        p for _, p in toks
        if p not in FUNC_POS and p not in PUN_DROP
    )


def l2_skeletons(sents_all, tok_cache=None):
    """为每句生成骨架指纹；输出重复簇（含跨段）+ 排比簇。"""
    records = []  # (句号, 原文, 骨架串)
    for si, s in enumerate(sents_all):
        toks = tok_cache.get(s, normalize_pn=True) if tok_cache else pos_tag(s, normalize_pn=True)
        skel = clean_skel(toks)
        tok_n = skel.count("|") + 1
        if tok_n < 3:  # 清洗后实词过少（碎片）
            continue
        records.append((si, s, skel))

    clusters = collections.defaultdict(list)
    for si, s, skel in records:
        clusters[skel].append((si, s))

    n = len(records)
    reps = []
    for skel, items in clusters.items():
        if len(items) < 2:
            continue
        freq = len(items)
        if freq / n > 0.25:  # 泛骨架：占比过高 → 粒度噪声
            continue
        tok_n = skel.count("|") + 1
        if tok_n < 3:
            continue  # 清洗后实词过少（碎片）
        if tok_n == 3 and freq < 4:
            continue  # 3-token 骨架要求 ≥4 次，过滤偶发
        if tok_n == 4 and freq < 3:
            continue  # 4-token 骨架要求 ≥3 次，过滤偶发
        reps.append({"skel": skel, "n": freq, "items": items[:10], "total": len(items)})
    reps.sort(key=lambda x: -x["n"])

    # 排比簇：相邻 >=3 句同骨架
    seq = []
    for si, s, skel in records:
        seq.append((si, s, skel))
    runs = []
    i = 0
    while i < len(seq):
        j = i + 1
        while j < len(seq) and seq[j][2] == seq[i][2] and seq[j][0] == seq[j - 1][0] + 1:
            j += 1
        if j - i >= 3:
            runs.append(seq[i:j])
            i = j
        else:
            i += 1
    return reps, runs


# ---------------------------------------------------------------- L3 节奏层
# 宪章指标② =「标准化方差」：判据用 CV = σ/mean，不用原始 σ（σ 与句长均值强相关，
# 篇幅变化会污染判据；CV 消除之）。公式与 lmscan sentence_length_variance 同型
# （参考实现 lmscan v0.6.1, Apache-2.0, github.com/stef41/lmscan）。
# **方向按本语料实测自定：高 CV → 关注**（不继承 lmscan 的 low_is_ai——那依赖英文说明文节奏假设；
# 中英文探测差异与阈值自定教训见 references/lmscan-feature-audit.md）。


def sent_cv(lens):
    """变异系数 CV = 标准差/均值。lens 为空或均值为 0 时返回 0。"""
    n = len(lens)
    if n < 2:
        return 0.0
    mean = sum(lens) / n
    if mean <= 0:
        return 0.0
    var = sum((x - mean) ** 2 for x in lens) / n
    return (var ** 0.5) / mean


def chapter_cv(paras):
    """章级句长 CV（全书实测口径：全章标点句字长，与 stat_layer_test 一致）。"""
    lens = [len(s) for p in paras for s in split_final_sents(p)]
    return sent_cv(lens)


def l3_rhythm(paras):
    rows = []
    for pi, para in enumerate(paras):
        fin = split_final_sents(para)
        if len(fin) < 3:
            continue
        lens = [len(s) for s in fin]
        mean = sum(lens) / len(lens)
        var = sum((x - mean) ** 2 for x in lens) / len(lens)
        std = var ** 0.5
        mssd = sum(abs(lens[k] - lens[k - 1]) for k in range(1, len(lens))) / (len(lens) - 1)
        short_ratio = sum(1 for x in lens if x <= 8) / len(lens)
        rows.append({
            "para": pi + 1, "n": len(fin), "mean": mean, "std": std,
            "cv": std / mean if mean else 0.0,
            "mssd": mssd, "short": short_ratio,
        })
    return rows


# ---------------------------------------------------------------- 报告



_JINJA = None


def render_chapter_report(title, paras, l0, tpl, l1, bl, reps, runs, l3, profile, cfg=None):
    """用 jinja2 模板渲染章节报告（report_template.md.j2）。"""
    global _JINJA
    if Environment is None:
        raise RuntimeError("jinja2 未安装：pip install jinja2")
    if _JINJA is None:
        _JINJA = Environment(loader=FileSystemLoader(find_template()), autoescape=False)
    data = {
        "title": title, "profile": profile,
        "paras_count": len(paras),
        "sent_count": sum(len(split_semantic_sents(p)) for p in paras),
        "l0": l0, "tpl": tpl, "l1": l1, "bl": bl,
        "reps": reps, "runs": runs, "l3": l3,
        "cfg": cfg or {},
    }
    return _JINJA.get_template("report_template.md.j2").render(**data)


def calibrate(files):
    """--calibrate：对基线（已评审通过）章节统计各数值指标均值±2σ，输出建议阈值。

    低好型指标（越少越好）阈值 = mean + 2σ；高好型（节奏类，越大越好）= mean - 2σ。
    不自动写回 profiles.yaml，仅供人工参考后手动调整。
    """
    import statistics
    rows = []
    for fp in files:
        if not os.path.exists(fp):
            print(f"跳过：{fp} 不存在")
            continue
        title, paras = read_chapter(fp)
        sents_all = [s for p in paras for s in split_semantic_sents(p)]
        l0 = l_intra(paras)
        tpl = l_tpl_punct(paras)
        l1 = l1_stats(sents_all, paras)
        bl = l_banlist(paras)
        reps, runs = l2_skeletons(sents_all)
        l3 = l3_rhythm(paras)
        sents = len(sents_all)
        avg_std = sum(r["std"] for r in l3) / len(l3) if l3 else 0
        avg_mssd = sum(r["mssd"] for r in l3) / len(l3) if l3 else 0
        cv = chapter_cv(paras)
        rows.append({
            "章": os.path.basename(fp),
            "重复骨架簇": len(reps),
            "重复句占比": sum(r["n"] for r in reps) / sents if sents else 0,
            "排比簇": len(runs),
            "信息停滞": len(l1["low_info_paras"]),
            "堆叠": len(l0["stack_rows"]),
            "'的'密度": l0["density"],
            "破折号": tpl["punct"].get("破折号", 0),
            "章CV": cv,
            "平均σ": avg_std,
            "平均MSSD": avg_mssd,
            "对话标签": bl["tag_ratio"],
            "3-4句段占比": bl["mid_ratio"],
            "模板命中": sum(n for _, n, _ in tpl["tpl_rows"]),
        })
    if not rows:
        return
    keys = [k for k in rows[0] if k != "章"]
    print("=== 基线校准（均值±2σ）===")
    print("章 | " + " | ".join(keys))
    for r in rows:
        print(r["章"] + " | " + " | ".join(
            f"{r[k]:.3f}" if isinstance(r[k], float) else str(r[k]) for k in keys))
    print()
    print("指标 | 均值 | σ | 建议阈值 | 方向")
    high_good = {"平均σ", "平均MSSD"}  # 越大越好，低于阈值报警
    for k in keys:
        vals = [r[k] for r in rows]
        mean = statistics.mean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0
        if k in high_good:
            thresh = max(0.0, mean - 2 * sd)
            direct = "高好：低于此报警"
        else:
            thresh = mean + 2 * sd
            direct = "低好：超过此报警"
        if k == "章CV":
            direct += "（指标②；注意本语料方向=高→关注，勿继承英文工具的低→AI 假设）"
        print(f"{k} | {mean:.3f} | {sd:.3f} | {thresh:.3f} | {direct}")
    print()
    print("提示：校准结果供人工参考，确认后写入 profiles.yaml 生效。")

def summarize(title, paras, l0, tpl, l1, bl, reps, runs, l3, profile, cfg):
    sents = sum(len(split_semantic_sents(p)) for p in paras)
    flat = l3
    avg_mssd = sum(r["mssd"] for r in flat) / len(flat) if flat else 0
    avg_std = sum(r["std"] for r in flat) / len(flat) if flat else 0
    cv = chapter_cv(paras)  # 宪章指标②：章级 CV（σ 仅展示，不再作为判据）
    n_reps = sum(1 for r in reps if r["n"] >= 2)
    rep_sent_ratio = sum(r["n"] for r in reps) / sents if sents else 0
    flags = []
    # ---- 通用层（跨文体）：统计核心 + 文本内相对异常 ----
    if n_reps and rep_sent_ratio > cfg["rep_ratio"]:
        flags.append(f"重复骨架×{n_reps}（占句{rep_sent_ratio:.0%}）")
    if runs:
        flags.append(f"排比簇×{len(runs)}")
    if len(l1["low_info_paras"]) >= 3:
        flags.append(f"信息停滞×{len(l1['low_info_paras'])}")
    tpl_total = sum(n for _, n, _ in tpl["tpl_rows"])
    if tpl_total >= cfg["tpl_thresh"]:
        flags.append(f"模板×{tpl_total}")
    if len(l0["stack_rows"]) > cfg.get("stack_thresh", 0):
        flags.append(f"词性堆叠×{len(l0['stack_rows'])}")
    if cfg["de_long_flag"] and len(l0["de_long"]) >= 3:
        flags.append(f"'的'长链×{len(l0['de_long'])}")
    if cfg.get("de_sto_flag") and len(l0["de_sto"]) >= 1:
        flags.append(f"顿号并列'的'×{len(l0['de_sto'])}")
    # ---- 文体层（banlist 开启的 profile）：项目规范规则 ----
    if cfg.get("banlist"):
        # 宪章指标②：判据用章级 CV（高 → 关注；本语料实测方向，见 L3 段注释）。
        # 原始 σ 不作判据（实测漂移主导，ONTOLOGY §3.1），仅作平均σ统计值展示。
        if cfg.get("cv_thresh") and cv > cfg["cv_thresh"]:
            flags.append(f"句长方差漂移(CV {cv:.2f})")
        if cfg.get("dash_thresh") and tpl["punct"].get("破折号", 0) > cfg["dash_thresh"]:
            flags.append(f"破折号{tpl['punct']['破折号']:.1f}")
        if l0["density"] > cfg.get("de_density_thresh", 6):
            flags.append(f"'的'密度{l0['density']:.1f}")
        if bl["ban_hits"]:
            flags.append(f"违禁词×{len(bl['ban_hits'])}")
        if bl["emo_hits"]:
            flags.append(f"情绪直写×{len(bl['emo_hits'])}")
        if bl["tag_ratio"] > cfg.get("tag_thresh", 0.30):
            flags.append(f"对话标签{bl['tag_ratio']:.0%}")
        if bl["mid_ratio"] > cfg.get("mid_ratio_thresh", 0.55):
            flags.append("段落3-4句偏多")
        if bl["data_runs"]:
            flags.append(f"数据行×{len(bl['data_runs'])}")
    return {
        "章": title, "语义句": sents, "重复骨架簇": n_reps, "排比簇": len(runs),
        "章CV": round(cv, 3),
        "平均σ": round(avg_std, 1), "平均MSSD": round(avg_mssd, 1),
        "'的'密度": round(l0["density"], 1),
        "flags": "；".join(flags) if flags else "—",
    }


class TokCache:
    """POS 标注缓存：同一句子（按切分模式区分 normalize_pn）只标注一次，L0/L1/L2 共享。"""

    def __init__(self):
        self._c = {}

    def get(self, s, normalize_pn=False):
        key = (s, normalize_pn)
        if key not in self._c:
            self._c[key] = pos_tag(s, normalize_pn)
        return self._c[key]


# ---------------------------------------------------------------- 跨章装置（指标①）
# 宪章三指标之①「出现频率」= 跨章 n-gram 复用。方法规格与实测依据：
# references/cross-chapter-phrase-cloud.md（结尾段 + 字符 4-gram + DF≥3 章；
# 结尾段 4-gram Jaccard 已知装置命中率 AUC 0.718★，全指标最高）。
# Counter 统计骨架与 lmscan long_ngram_repetition 同族（参考实现 lmscan v0.6.1,
# Apache-2.0, github.com/stef41/lmscan），两点关键差异：
#   1) 作用域升为**跨章**（lmscan 仅章内）；2) 抽取范围限**结尾段**（装置集中在收尾）。
# 不复制其词表与英文分词假设——字符级 n-gram 天然免分词。

CLOUD_N = 4          # 字符 n-gram 长度（5-gram 过稀、3-gram 混入常用搭配噪声）
CLOUD_TAIL = 3       # 每章取最后 N 段
CLOUD_DF_HINT = 3    # DF≥3 章 → 提示
CLOUD_DF_FLAG = 5    # DF≥5 章 → flag（76 章量级下 ≥3 章已是 4% 低概率事件）


def chapter_tail_grams(paras, n=CLOUD_N, tail=CLOUD_TAIL):
    """取章节结尾段的字符 n-gram 集合（去非汉字后滑动窗口）。返回 set。"""
    if not paras:
        return set()
    tail_text = "".join(paras[-tail:])
    han = re.sub(r"[^\u4e00-\u9fff]", "", tail_text)
    return {han[i:i + n] for i in range(len(han) - n + 1)}


def cross_chapter(files, out_dir):
    """--cross-chapter：扫描多章，输出跨章复用装置报告（指标①）。"""
    from collections import defaultdict
    gram_df = defaultdict(set)             # gram -> {章序数}
    chap_names = []
    chap_grams = {}
    for fp in files:
        if not os.path.exists(fp):
            print(f"跳过：{fp} 不存在")
            continue
        title, paras = read_chapter(fp)
        idx = len(chap_names)
        chap_names.append(title)
        grams = chapter_tail_grams(paras)
        chap_grams[idx] = grams
        for g in grams:
            gram_df[g].add(idx)
    # 聚簇：按章号相邻性给装置归段（"装置有任期"，见 references §3）
    clouds = sorted(gram_df.items(), key=lambda kv: -len(kv[1]))
    strong = [(g, chs) for g, chs in clouds if len(chs) >= CLOUD_DF_HINT]
    lines = []
    lines.append("# 跨章装置报告（指标①：出现频率）\n")
    lines.append(f"- 语料：{len(chap_names)} 章　·　方法：结尾 {CLOUD_TAIL} 段 → 字符 {CLOUD_N}-gram → 跨章 DF≥{CLOUD_DF_HINT}")
    lines.append(f"- 判级：DF≥{CLOUD_DF_HINT} 提示；DF≥{CLOUD_DF_FLAG} flag（宪章：定位型指标，指向具体可改句）\n")
    if not strong:
        lines.append("**未发现跨章复用装置（本批章节结尾段无 DF≥%d 片段）。**" % CLOUD_DF_HINT)
    else:
        lines.append("| 装置片段 | 章数(DF) | 级别 | 章节 |")
        lines.append("| --- | --- | --- | --- |")
        for g, chs in strong:
            chs_sorted = sorted(chs)
            level = "🚩 flag" if len(chs) >= CLOUD_DF_FLAG else "⚠ 提示"
            names = "、".join(chap_names[i] for i in chs_sorted)
            lines.append(f"| {g} | ×{len(chs)} | {level} | {names} |")
        # 每章被牵连度（评分用）：该章结尾段中命中装置片段的个数
        lines.append("\n## 每章牵连度（结尾段命中装置片段数，定位改哪章优先）\n")
        lines.append("| 章节 | 命中装置数 | 其中 flag 级 |")
        lines.append("| --- | --- | --- |")
        flaggy = {g for g, chs in strong if len(chs) >= CLOUD_DF_FLAG}
        cloud = {g for g, _ in strong}
        for i, name in enumerate(chap_names):
            hit = chap_grams.get(i, set()) & cloud
            hit_flag = chap_grams.get(i, set()) & flaggy
            lines.append(f"| {name} | {len(hit)} | {len(hit_flag)} |")
    report = "\n".join(lines) + "\n"
    out_path = os.path.join(out_dir, "跨章装置报告.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    # stdout 摘要
    print(f"=== 跨章装置（指标①）：{len(strong)} 个 DF≥{CLOUD_DF_HINT} 片段，"
          f"其中 flag 级 {sum(1 for _, c in strong if len(c) >= CLOUD_DF_FLAG)} 个 ===")
    for g, chs in strong[:10]:
        print(f"  ×{len(chs):2d}  {g}")
    print(f"✔ 完整报告 → {out_path}")


def main():
    global OUT_DIR
    args = sys.argv[1:]
    profile = DEFAULT_PROFILE
    out_dir = None
    for a in args:
        if a.startswith("--profile="):
            profile = a.split("=", 1)[1]
        elif a.startswith("--out-dir="):
            out_dir = a.split("=", 1)[1]
    OUT_DIR = out_dir or os.path.join(os.getcwd(), "reports")
    os.makedirs(OUT_DIR, exist_ok=True)
    files = [a for a in args if not a.startswith("--")]
    PROFILES = load_profiles()
    if "--cross-chapter" in args:
        cross_chapter(files, OUT_DIR)
        return
    if "--calibrate" in args:
        calibrate(files)
        return
    if profile not in PROFILES:
        print(f"未知 profile：{profile}，可选 {list(PROFILES)}")
        return
    if not files:
        print(__doc__)
        print(f"可用 profile：{' / '.join(PROFILES)}（默认 {DEFAULT_PROFILE}），例：--profile=网文")
        print("校准模式：--calibrate 基线文件.md [更多基线文件.md]")
        print("跨章装置：--cross-chapter 文件...（指标①，输出<结尾段 4-gram 跨章复用>报告，需 ≥2 章）")
        return
    summary_rows = []
    for fp in files:
        if not os.path.exists(fp):
            print(f"跳过：{fp} 不存在")
            continue
        title, paras = read_chapter(fp)
        sents_all = [s for p in paras for s in split_semantic_sents(p)]
        cache = TokCache()
        l0 = l_intra(paras, cache)
        tpl = l_tpl_punct(paras)
        l1 = l1_stats(sents_all, paras, cache)
        bl = l_banlist(paras) if PROFILES[profile]["banlist"] else {
            "ban_hits": [], "emo_hits": [], "tag_ratio": 0, "quote_ratio": 0,
            "n_quotes": 0, "para_counts": {}, "mid_ratio": 0, "data_runs": [],
        }
        reps, runs = l2_skeletons(sents_all, cache)
        l3 = l3_rhythm(paras)
        report = render_chapter_report(title, paras, l0, tpl, l1, bl, reps, runs, l3, profile, PROFILES[profile])
        out_name = os.path.splitext(os.path.basename(fp))[0] + f"_指纹报告_{profile}.md"
        out_path = os.path.join(OUT_DIR, out_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
        summary_rows.append(summarize(title, paras, l0, tpl, l1, bl, reps, runs, l3, profile, PROFILES[profile]))
        print(f"✔ {os.path.basename(fp)} → {os.path.basename(out_path)}")
    if len(summary_rows) > 1:
        print("\n=== 汇总对比 ===")
        header = "章 | 语义句 | 重复骨架簇 | 排比簇 | 章CV | 平均σ | 平均MSSD | 的密度 | flags"
        print(header)
        print("-" * len(header))
        for r in summary_rows:
            de_density = r["'的'密度"]
            print(f"{r['章']} | {r['语义句']} | {r['重复骨架簇']} | {r['排比簇']} | {r['章CV']} | {r['平均σ']} | {r['平均MSSD']} | {de_density} | {r['flags']}")


if __name__ == "__main__":
    main()
