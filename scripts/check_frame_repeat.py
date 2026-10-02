#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""句法框架重复检测（check_frame_repeat.py）

【为什么需要这个脚本】

check.py 的 L2 句法层比对的是**实词序列**（骨架清洗会去掉结构助词与停顿标点，
只保留实词 + EOS/QT 锚点，见 scripts/check.py:67、:566）。因此有一类重复它测不到：

    「短判断句 + 提示下文标点（：/——） + 展开说明」

这个跨句骨架的实词每次都不同（"融资/投入/智谱" vs "原神/梭哈/多开"），
句中只有标点与虚词相同，所以 L2 永远报"无重复骨架"。
同时 check.py 的聚合列只统计「破折号每千字」，**不统计冒号**——
而中文里冒号与破折号在"提示下文/解释"这一功能上等价。
结果：把破折号替换成冒号，可以让"破折号"一栏归零，句法框架却一处未变。
这就是"标点迁移"型伪修正能骗过工具的原因。

本脚本补这几处缺口：
  指标 A —— 提示下文标点密度（冒号 + 破折号 / 千字）
  指标 B —— 判断句先行：B1「短判断句 + 提示下文标点」、B2「短判断句独占句群首」
  指标 C —— 分句 POS 粗骨架的重复簇（去实词、留词性，与 L2 互补）
  指标 D —— 对话感标记（问号/第二人称/第一人称/祈使/口语连接/元叙述/条件式对话）

指标 B2 说明（第二版新增）：上一版只认带提示标点（：/——）的判断句先行，
于是「轻的那条路可以直接排除。」这类以**句号**收尾的判断句先行成了漏网——
病灶相同（先抛判断、再补注解），只是末尾标点不同。B2 改为按语义句群切分，
取每个句群的首个子分句，若 ≤N 字且不含数字/引号，即判为判断句先行。

指标 D 说明：设问、反问、第二人称是"作者在对读者说话"的标记，与"判断+注解"
同属把陈述体写成对话体的毛病。正式文稿（知乎长文、报告、公文）应清空。
注意：直接引语中的人物问句不计入（引号内会被排除）。

【性质声明 / 局限（务必连同结论一起引用）】

- 这是**结构性自查工具**，不是判据。指标 A/B 的阈值来自一篇稿子的前后对照，
  **没有真实语料基线**（skill 的 CSV 只提供了破折号基线：知乎 0.31/千字，
  且 5 篇中 4 篇为 0；冒号与"判断句+提示"这两项无基线可依）。
- 因此正确用法是**同一篇稿子改前改后对照**，或与同体裁文本横向比，
  不可当作跨体裁的绝对门槛。若需真实基线，按 skill 宪章 §六先 --calibrate。
- 指标 C 的 POS 粗骨架区分度有限：2–3 字的短骨架（[NV]/[VN]）天然容易撞车，
  建议只看 ≥4 字的骨架簇。
- 指标 B2 有一类**已知假阳性**：判据按第一个逗号切分，不判断语法完整性，
  所以"它得先把自己从用模型的人，变成造模型的人"这种长句的主语从句部分
  会被当成独立短判断句。B2 输出的是候选清单，务必人工判读。

【人工判读项（脚本测不到，必须逐句看）】

以下几类是本工具覆盖不到、但在实际改稿中反复出现的病灶，需人工过：

1. **断言跨维度并列**——连续几个短断言若分属不同维度（方向／选择／约束／
   程度／时间），读者要跨多次才能连上，读感是"跳"。典型：「它得先把自己从
   用模型的人，变成造模型的人。／最有把握的路它没选。／它选的是唯一装得下
   1000 亿的那条。」（方向、选择、约束）。改法：找真实推导关系改成"结论+
   因为"，或删掉没有锚点的那句。
2. **比较级必须有锚点**——"最有把握的路它没选""更贵""更早"这类比较级，
   若前文从没出现过比较的基准，就是凭空插入，读者会卡住。查法：把每个
   比较级所在句往回读两段，找不到基准就删。
3. **比例口径要先换算绝对量**——"烧完募资的九成多"这类比例，先换成绝对量，
   再问"这个量放在本段量级里，读者会往哪个方向推"。比例唬人、绝对量小时
   （几十亿 vs 千亿），读者会反推相反结论。改用同口径的结构性数字。

另：**微调句子时容易打破已清理过的指标**（本工具自身也测不到这一点）——
例如在句中加一个"而"，会让句首/分句首虚词计数回涨。每次改动后都要复测。

用法：
    python scripts/check_frame_repeat.py <文件...> [--max-pred 18] [--min-frame 4]
"""
import argparse
import collections
import io
import re
import sys

import jieba.posseg as pseg

PROMPT_MARKS = "：—"          # 提示下文型标点（语义等价，合并统计）
SENT_BREAK = "，、：；。！？…"  # 与 check.py split_semantic_sents 保持一致
GROUP_BREAK = "。！？…"        # 语义句群边界（句号级）
CLAUSE_BREAK = "，,、：；"      # 句群内子分句边界
STRIP_CHARS = "，、：；。！？…“”\"'（）()《》—%"

_COARSE = {
    'n': 'N', 'nr': 'N', 'ns': 'N', 'nt': 'N', 'nz': 'N',
    'v': 'V', 'vn': 'V', 'vd': 'V',
    'a': 'A', 'ad': 'A', 'an': 'A',
    'd': 'D', 'r': 'R', 'm': 'M', 'q': 'Q', 'p': 'P',
    'c': 'C', 'u': 'U', 'f': 'F', 't': 'T', 'x': 'X',
    'eng': 'X', 'w': 'W',
}
# 与 check.py 同源：虚词/标点不进骨架
_SKIP = {'C', 'U', 'P', 'W', 'X'}


def split_semantic(text):
    parts = re.split(r"([" + SENT_BREAK + r"])", text)
    out, buf = [], ""
    for p in parts:
        if not p:
            continue
        buf += p
        if p in SENT_BREAK:
            out.append(buf)
            buf = ""
    if buf.strip():
        out.append(buf)
    return [s.strip() for s in out if s.strip()]


def pos_skeleton(sent):
    core = re.sub(r"[" + STRIP_CHARS + r"]", "", sent)
    core = re.sub(r"[0-9]+(?:\.[0-9]+)?", "M", core)
    tags = []
    for word, flag in pseg.cut(core):
        if not word.strip():
            continue
        coarse = _COARSE.get(flag, 'X')
        if coarse in _SKIP:
            continue
        tags.append(coarse)
    return "".join(tags)


DIALOGUE_PATTERNS = [
    ("问号（设问/反问）", r"[？?]"),
    ("第二人称", r"[你您]|大家|咱们|各位"),
    ("第一人称", r"[我咱]"),
    ("祈使起句", r"(?m)^\s*(先|请|试|看|想|记住|别|不要|不妨)"),
    ("感叹号", r"[！!]"),
    ("元叙述", r"值得注意的是|需要指出的是|不难看出|本文|我们来看|下面来看"),
    ("条件式对话", r"如果说|假如|试想|不妨想|要知道"),
]

# 指标 E：语域与垫词 —— **不是对话感**，是另一类病灶，勿与指标 D 混判。
#
# 分类依据（2026-09 修正）：原先把下面两组统一叫"口语连接"并放进指标 D，
# 但它装了三类异质的东西——
#   ① "你看" 属第二人称对话感（已被 D 的「第二人称」覆盖，重复）
#   ② "说白了/顺便/话说" 属**语域**（口语化，与对话姿态正交）
#   ③ "其实/那么/总之/也就是说/换句话说" 属**书面议论体垫词**（不是口语！）
# 三者改写策略完全不同：①要删（伪对话）；②可留可加（松弛来源）；
# ③要按"是否多余"判断，与口语无关。混为一类会导致错误改写。
REGISTER_PATTERNS = [
    ("口语插入语（语域下沉，非病灶）", r"说白了|顺便|话说|讲真|老实说|讲道理"),
    ("议论垫词（书面议论体，看是否多余）", r"其实|那么|总之|也就是说|换句话说|换言之"),
]


def strip_quotes(text):
    """去掉引号内内容，避免把人物原话里的问句误判为作者的设问。"""
    text = re.sub(r"“[^”]*”", "〓", text)
    text = re.sub(r'"[^"]*"', "〓", text)
    return text


def strip_titles(text):
    """去掉书名号内内容，避免把《崩坏：星穹铁道》这类固有名词里的冒号当成提示标点。

    注意必须对**整篇**调用：若先按标点切分再替换，《崩坏： 处的书名号不成对，
    正则匹配不上，冒号仍会被计入（这是第一版的误报原因）。
    """
    text = re.sub(r"《[^》]*》", "〓", text)
    return re.sub(r"《[^》]*$", "〓", text)


# 判断句必须含"系词 / 情态 / 判断标记"，否则只是名词短语或时间状语
PRED_MARKS = re.compile(r"是|为|没|不|等于|必须|只有|唯一|需要|得|才|该|算|叫|缺|够|抵|撑")
# 含以下特征 → 视为具体事实而非抽象判断（数字、英文、专名）
SPECIFIC_MARKS = re.compile(r"[A-Za-z〓]|[0-9０-９]")


def check_dialogue(text):
    body = strip_quotes(text)
    print("\n[指标 D] 对话感标记（引号内人物原话已排除）")
    total = 0
    for name, pat in DIALOGUE_PATTERNS:
        hits = []
        for m in re.finditer(pat, body):
            lo = max(0, m.start() - 12)
            hi = min(len(body), m.end() + 12)
            hits.append(body[lo:hi].replace("\n", " "))
        if hits:
            total += len(hits)
            print(f"  ⚠ {name} ×{len(hits)}")
            for h in hits:
                print(f"      …{h}…")
        else:
            print(f"  ✔ {name}：0")
    print(f"  合计 {total} 处"
          + ("（陈述体，无对话感标记）" if total == 0 else ""))

    print("\n[指标 E] 语域与垫词（**不是对话感**：D 管修辞姿态，E 管语域/垫词，两者正交）")
    for name, pat in REGISTER_PATTERNS:
        hits = []
        for m in re.finditer(pat, body):
            lo = max(0, m.start() - 12)
            hi = min(len(body), m.end() + 12)
            hits.append(body[lo:hi].replace("\n", " "))
        if hits:
            print(f"  · {name} ×{len(hits)}")
            for h in hits:
                print(f"      …{h}…")
        else:
            print(f"  · {name}：0")


def split_groups(text):
    """按句号级标点切成语义句群。"""
    parts = re.split(r"([" + GROUP_BREAK + r"])", text)
    out, buf = [], ""
    for p in parts:
        if not p:
            continue
        buf += p
        if p in GROUP_BREAK:
            out.append(buf)
            buf = ""
    if buf.strip():
        out.append(buf)
    return [g.strip() for g in out if g.strip()]


def check_pred_first(sents, groups, max_pred):
    """指标 B：判断句先行（候选清单，需人工判读）。

    B1 —— 短判断句 + 提示下文标点（：/——），最明确的形态
    B2 —— 短判断句独占句群首（以句号收尾的判断句先行，B1 的漏网）

    注意 B2 输出的是**候选**，不是判决：中文里有些判断句先行是合理修辞
    （首句摘要位、对仗收口、事实之后的落点句）。工具只负责把它们列出来，
    改不改由人判读——判据本身做不到零误报。
    """
    b1 = []
    for s in sents:
        for mark in PROMPT_MARKS:
            if mark in s:
                left = s.split(mark)[0].rstrip("，,、 ")
                if 0 < len(left) <= max_pred:
                    b1.append(left)
                break

    b2 = []
    for idx, g in enumerate(groups):
        first = re.split("[" + CLAUSE_BREAK + r"]", g, maxsplit=1)[0].strip()
        if not (6 <= len(first) <= max_pred):       # 过短 → 多为状语片段
            continue
        if not PRED_MARKS.search(first):            # 无判断标记 → 事实陈述
            continue
        if SPECIFIC_MARKS.search(first):            # 含数字/专名 → 具体事实
            continue
        b2.append((idx == 0, first))

    print(f"\n[指标 B] 判断句先行（候选清单）")
    print(f"  B1 短判断句（≤{max_pred} 字）+ 提示标点：{len(b1)} 处")
    for left in b1:
        print(f"     L={len(left):>2} | {left}")
    lead = [x for x in b2 if not x[0]]
    print(f"  B2 短判断句独占句群首：{len(lead)} 处（首句 1 处属摘要位设计，单列不计）")
    for is_first, t in b2:
        tag = " [首句·摘要位]" if is_first else ""
        print(f"     L={len(t):>2} | {t}{tag}")
    print(f"  合计需处理：B1 {len(b1)} + B2 {len(lead)} = {len(b1) + len(lead)} 处")


def analyze(path, max_pred, min_frame):
    text = io.open(path, encoding="utf-8").read()
    sents = split_semantic(text)
    # B1 用去掉书名号后的分句：避免《崩坏：星穹铁道》里的冒号被当成提示标点
    sents_b1 = split_semantic(strip_titles(text))
    groups = split_groups(strip_quotes(text))
    total = len(text)

    print(f"\n=== {path} ===")
    print(f"字符数 {total} | 分句数 {len(sents)} | 句群数 {len(groups)}")

    # 指标 A：提示下文标点密度
    print("\n[指标 A] 提示下文标点（冒号与破折号语义等价，合并计）")
    for mark, name in (("：", "冒号"), ("—", "破折号")):
        c = strip_titles(text).count(mark)
        print(f"  {name}: {c:>3} 处  ({c / total * 1000:.1f}/千字)")

    # 指标 B：判断句先行
    check_pred_first(sents_b1, groups, max_pred)

    # 指标 C：POS 粗骨架重复簇
    frames = [pos_skeleton(s) for s in sents]
    pair = [(f, s) for f, s in zip(frames, sents) if len(f) >= min_frame]
    counter = collections.Counter(f for f, _ in pair)
    dup = {k: v for k, v in counter.items() if v >= 2}
    print(f"\n[指标 C] 分句 {len(sents)} | 唯一骨架 {len(counter)} | "
          f"重复簇(≥2 次, 骨架≥{min_frame} 字) {len(dup)}")
    for frame, n in sorted(dup.items(), key=lambda kv: -kv[1]):
        ex = [s.strip()[:24] for f, s in pair if f == frame][:3]
        print(f"   ×{n}  [{frame}]  e.g. {' / '.join(ex)}")

    # 指标 D：对话感标记
    check_dialogue(text)


def main():
    ap = argparse.ArgumentParser(description="句法框架重复检测（补 check.py 的 L2 盲区）")
    ap.add_argument("files", nargs="+", help="待检文本文件")
    ap.add_argument("--max-pred", type=int, default=18,
                    help="判定为'短判断句'的最大字数（默认 18）")
    ap.add_argument("--min-frame", type=int, default=4,
                    help="参与重复统计的最小骨架长度，默认 4（低于 4 区分度不足）")
    args = ap.parse_args()
    for path in args.files:
        try:
            analyze(path, args.max_pred, args.min_frame)
        except FileNotFoundError:
            print(f"找不到文件：{path}", file=sys.stderr)


if __name__ == "__main__":
    main()
