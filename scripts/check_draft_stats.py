#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐稿体量与节奏统计（check_draft_stats.py）

【为什么需要这个脚本】

`check.py` 给的是**章节级**指纹报告，`check_frame_repeat.py` 给的是**句法框架/对话感**。
但逐稿改稿时还有一组数要反复算，两者都不直接给：

  ① 汉字数 / 含标点数  —— 字数上限（如知乎千字稿）靠它判；
  ② 段内 CV 均值        —— 「研报味」的直接指标（`check.py` 只在 L3 给"段内均值 CV"，
                          且用的是**句群**粒度）；
  ③ pooled CV           —— 全篇句长离散度（`check.py` 不输出）；
  ④ 分句粒度下的同一组数 —— 改稿是逐句改的，**分句**粒度的 CV 才能反映改动效果。

【踩过的坑（务必读）】

**'的' 密度有多个分母，混用会得出相反结论。**
① **段落拼接口径**（`的` 数 / **逐段拼接后的长度**：剔除段落间的空行分隔符，段内
字符含标点原样保留）——**这是 `check.py` 的报值口径**（`l_intra` 的 `n_chars`），
**横向比较只认它**；② 含空白口径（把段落间空行也计入）——比 ① 略低约 0.05pp；
③ 汉字口径（`的` 数 / 汉字数）——比 ① 高一截，同一稿实测约 **4.5%**，
看起来像"远超基线"，实际只是**分母换了**。
本脚本三栏都打，判读时先看①。

> **基线 3.22% 的口径**：它来自 `references/external-corpus-summary.csv` 的 `的密度_%` 列
> （知乎 5 篇均值 3.2172%），与 ① 同源口径。逐篇明细可核验；
> 换用②或③，同一批语料会整体平移，故比较时必须同分母。

用法：
    python scripts/check_draft_stats.py <文件...> [--markers 正文]

默认按 `【正文开始】` / `【正文结束】` 提取正文；无标记则整篇统计。
`--markers` 可换成其它标记前缀（如 `正文` → `【正文开始】`）。
"""
import argparse
import io
import re
import statistics
import sys

BREAK = "，、：；。！？…"
GROUP = "。！？…"


def split_by(text, marks):
    parts = re.split(r"([" + marks + r"])", text)
    out, buf = [], ""
    for p in parts:
        if not p:
            continue
        buf += p
        if p in marks:
            out.append(buf)
            buf = ""
    if buf.strip():
        out.append(buf)
    return [s.strip() for s in out if s.strip()]


def cv_of(lens):
    if len(lens) < 2:
        return 0.0
    mean = sum(lens) / len(lens)
    return statistics.pstdev(lens) / mean if mean else 0.0


def extract(text, tag):
    if not tag:
        return text.strip()
    a, b = f"【{tag}开始】", f"【{tag}结束】"
    if a in text and b in text:
        return text.split(a)[1].split(b)[0].strip()
    return text.strip()


def analyze(path, tag):
    text = io.open(path, encoding="utf-8").read()
    body = extract(text, tag)
    paras = [p.strip() for p in body.split("\n") if p.strip()]

    han = len(re.findall(r"[\u4e00-\u9fff]", body))
    nonspace = len(re.sub(r"\s", "", body))
    print(f"\n=== {path} ===")
    print(f"段数 {len(paras)} | 汉字 {han} | 含标点(非空白) {nonspace}")

    per_para, clause_all, group_all = [], [], []
    for i, p in enumerate(paras, 1):
        cl = split_by(p, BREAK)
        gr = split_by(p, GROUP)
        clause_all += [len(s) for s in cl]
        group_all += [len(s) for s in gr]
        c = cv_of([len(s) for s in cl])
        per_para.append(c)
        print(f"  段{i:>2} 分句{len(cl):>2} 均长{sum(len(s) for s in cl)/len(cl):>5.1f} "
              f"CV{c:.3f} min{min(len(s) for s in cl)} max{max(len(s) for s in cl)}")

    print(f"分句粒度：pooled CV {cv_of(clause_all):.3f} | 段内 CV 均值 {sum(per_para)/len(per_para):.3f} | "
          f"全局均句长 {sum(clause_all)/len(clause_all):.1f}")
    print(f"句群粒度：pooled CV {cv_of(group_all):.3f}（check.py 的「段内均值 CV」用这一档，勿与上栏并列）")

    de = body.count("的")
    # ① 段落拼接：与 check.py 的 n_chars 同构造——逐段拼接（剔除段落间空行/缩进），
    #    段内字符含标点与段内空格原样保留。横向比较只认这一栏。
    joined = "".join(paras)
    print(f"'的' {de} 处 | ①段落拼接 {de/len(joined)*100:.2f}%"
          f"（**check.py 报值 & 基线 3.22% 同此口径，横向比较只认这一栏**）"
          f" | ②含空白 {de/len(body)*100:.2f}% | ③汉字 {de/han*100:.2f}%（③不可与基线比较）")
    for ch, name in [("—", "破折号"), ("…", "省略号"), ("？", "问号"), ("?", "半角问号"),
                     ("！", "感叹号"), ("!", "半角叹号"), ("；", "分号"), ("：", "冒号")]:
        print(f"  {name} {body.count(ch)}")
    print(f"  冒号(去书名号内) {len(re.findall('：', re.sub(r'《[^》]*》', '', body)))}")


def main():
    ap = argparse.ArgumentParser(description="逐稿体量与节奏统计")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--markers", default="正文", help="正文标记前缀，默认 正文 → 【正文开始】/【正文结束】；传空串则整篇统计")
    args = ap.parse_args()
    for path in args.files:
        try:
            analyze(path, args.markers)
        except FileNotFoundError:
            print(f"找不到文件：{path}", file=sys.stderr)


if __name__ == "__main__":
    main()
