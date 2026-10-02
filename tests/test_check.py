"""ai-flavor-less 核心函数单元测试。

覆盖三块核心数学与一处加载契约：
- L3 节奏层：章级 CV = σ/mean（指标②）的计算与短段跳过；
- 指标①：chapter_tail_grams 的汉字 n-gram 切窗与跨章 DF 聚簇；
- 体裁参数外部化：profiles/*.yaml 加载与"通用"基底继承；
- CLI 冒烟：--calibrate 端到端。

运行：pytest tests/
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import check  # noqa: E402


# ---------------------------------------------------------------- L3 CV

class TestL3Rhythm:
    def test_equal_length_cv_zero(self):
        # 三句等长 → CV = 0（节奏完全均匀）
        paras = ["一二三四五。一二三四五。一二三四五。"]
        rows = check.l3_rhythm(paras)
        assert len(rows) == 1
        assert rows[0]["cv"] == 0.0
        assert rows[0]["n"] == 3

    def test_cv_value_matches_manual(self):
        # split_final_sents 保留句尾标点：句长 [6,11,11]
        # mean=28/3，σ=√(50/9)（方差不变，均值+1），CV≈0.2525
        paras = ["一二三四五。一二三四五六七八九十。一二三四五六七八九十。"]
        rows = check.l3_rhythm(paras)
        assert abs(rows[0]["cv"] - 0.2525) < 0.01

    def test_short_para_skipped(self):
        # 少于 3 句的段不进节奏统计
        assert check.l3_rhythm(["一。二三。"]) == []
        assert check.l3_rhythm([]) == []


# ---------------------------------------------------------------- 指标① 4-gram

class TestChapterTailGrams:
    def test_han_only_and_window(self):
        # 非汉字被剥离后才切窗；4-gram 齐全
        paras = ["第一段随意内容。", "甲乙丙丁戊"]
        grams = check.chapter_tail_grams(paras, n=4, tail=2)
        assert "甲乙丙丁" in grams
        assert "乙丙丁戊" in grams
        # 标点与数字不产生含杂字符的 gram
        assert all(g.isascii() is False for g in grams)

    def test_empty(self):
        assert check.chapter_tail_grams([]) == set()


class TestCrossChapterDF:
    RARE = "玄铁重剑无锋"  # 6 字罕用串 → 3 个 4-gram 全部命中

    def _make(self, tmp: Path, i: int, tail: str) -> Path:
        # 导语恰 4 字且含唯一序数字 → 其唯一 4-gram 不跨篇共享
        name = f"ch{i}.md"
        fp = tmp / name
        fp.write_text(
            f"# {name}\n\n{'甲乙丙'[i]}篇{'子丑寅'[i]}言。\n\n{tail}\n",
            encoding="utf-8",
        )
        return fp

    def test_shared_tail_flagged(self, tmp_path):
        files = [str(self._make(tmp_path, i, self.RARE)) for i in range(3)]
        check.cross_chapter(files, str(tmp_path))
        report = (tmp_path / "跨章装置报告.md").read_text(encoding="utf-8")
        assert "×3" in report  # 3 章共享 → DF=3 提示级

    def test_distinct_tail_clean(self, tmp_path):
        tails = ["花开两朵各表一枝", "山高水远来日方长", "雾散云收月出东山"]
        files = [str(self._make(tmp_path, i, t)) for i, t in enumerate(tails)]
        check.cross_chapter(files, str(tmp_path))
        report = (tmp_path / "跨章装置报告.md").read_text(encoding="utf-8")
        assert "未发现跨章复用装置" in report


# ---------------------------------------------------------------- profiles 外部化

class TestLoadProfiles:
    def test_bundled_profiles(self):
        ps = check.load_profiles()
        assert {"通用", "网文", "论文", "新闻", "知乎", "学术"} <= set(ps)
        assert ps["网文"]["cv_thresh"] == 0.864
        # 论文 只写差异项，其余键从"通用"基底继承
        assert ps["论文"]["dialog"] == check.DEFAULT_PROFILES["通用"]["dialog"]

    def test_external_profiles_carry_numeric_thresholds(self):
        """外部语料基线 profile 的数值阈值必须真的加载进来（否则 profile 是空壳）。"""
        ps = check.load_profiles()
        assert ps["知乎"]["cv_thresh"] == 0.6717
        assert ps["知乎"]["de_density_thresh"] == 4.1413
        assert ps["新闻"]["cv_thresh"] == 0.9100
        # 学术体：描述层故意留 null（体裁惯例非 AI 味，见 ONTOLOGY §五）
        assert ps["学术"]["cv_thresh"] == 0.7402
        assert ps["学术"]["tpl_thresh"] is None
        assert ps["学术"]["lowinfo_thresh"] is None

    def test_custom_profile_dir(self, tmp_path, monkeypatch):
        pdir = tmp_path / "profiles"
        pdir.mkdir()
        (pdir / "散文.yaml").write_text(
            "散文:\n  tpl_thresh: 7\n", encoding="utf-8"
        )
        monkeypatch.setattr(check, "SKILL_DIR", str(tmp_path))
        ps = check.load_profiles()
        assert "散文" in ps
        assert ps["散文"]["tpl_thresh"] == 7
        assert ps["散文"]["banlist"] == check.DEFAULT_PROFILES["通用"]["banlist"]


# ------------------------------------------------- 数值阈值层与 banlist 解耦

class TestNumericThresholdLayer:
    """数值阈值层（CV/破折号/'的'密度/对话标签/3-4句段）不再由 banlist 罩着。

    这是外部语料基线 profile 能生效的前提：它们 banlist=false，
    但要靠这一层判"是否偏离该体裁的统计形态"。
    """

    @staticmethod
    def _run(paras, cfg):
        sents = [s for p in paras for s in check.split_semantic_sents(p)]
        cache = check.TokCache()
        l0 = check.l_intra(paras, cache)
        tpl = check.l_tpl_punct(paras)
        l1 = check.l1_stats(sents, paras, cache)
        bl = check.l_banlist(paras)
        reps, runs = check.l2_skeletons(sents, cache)
        l3 = check.l3_rhythm(paras)
        return check.summarize("t", paras, l0, tpl, l1, bl, reps, runs, l3, "测试", cfg)

    def test_threshold_fires_with_banlist_off(self):
        """banlist=false 时数值阈值仍生效（外部 profile 的核心契约）。"""
        paras = ["这是一个非常非常长的句子用来把句子的平均长度显著抬高。"
                 "短。短。短。短。短。短。短。短。短。短。"]
        cfg = {**check.DEFAULT_PROFILES["通用"], "banlist": False, "cv_thresh": 0.01}
        r = self._run(paras, cfg)
        assert "句长方差漂移" in r["flags"]

    def test_null_threshold_off(self):
        """阈值 null = 不判该项（学术 profile 关描述层的机制）。"""
        paras = ["这是很长的第一句用来拉开方差差距的句子。短。短。短。短。短。"]
        cfg = {**check.DEFAULT_PROFILES["通用"], "banlist": False, "cv_thresh": None}
        r = self._run(paras, cfg)
        assert "句长方差漂移" not in r["flags"]

    def test_banlist_still_gates_wordlist(self):
        """banlist 仍只管词层清单：关掉后违禁词/情绪直写不再 flag。"""
        paras = ["他心中不禁涌上一阵愤怒。"] + ["这一句是普通句子用来凑数。"] * 6
        base = {**check.DEFAULT_PROFILES["通用"], "cv_thresh": None}
        off = self._run(paras, {**base, "banlist": False})
        assert "违禁词" not in off["flags"] and "情绪直写" not in off["flags"]
        on = self._run(paras, {**base, "banlist": True, "tpl_thresh": 99, "rep_ratio": 1})
        assert "违禁词" in on["flags"] or "情绪直写" in on["flags"]

    def test_lowinfo_semantics(self):
        """lowinfo_thresh：null 不判；数值 n = 超过 n 段才报。"""
        paras = ["散。走。停。看。坐。" for _ in range(4)]
        base = {**check.DEFAULT_PROFILES["通用"], "banlist": False}
        cfg_off = {**base, "lowinfo_thresh": None}
        assert "信息停滞" not in self._run(paras, cfg_off)["flags"]
        cfg_hi = {**base, "lowinfo_thresh": 99}
        assert "信息停滞" not in self._run(paras, cfg_hi)["flags"]


# ------------------------------------------------- 报告渲染

class TestReportRender:
    def test_threshold_line_and_chapter_cv(self, tmp_path):
        """报告头部要打印数值阈值，且 L3 的判据对象是章级 CV（原模板错比段内均值）。"""
        fp = tmp_path / "a.md"
        fp.write_text("# t\n\n" + "\n\n".join(
            "这是第一句比较长一些的句子。短。这是第三句也不短的句子。短。短。" for _ in range(2)
        ), encoding="utf-8")
        title, paras = check.read_chapter(str(fp))
        sents = [s for p in paras for s in check.split_semantic_sents(p)]
        cache = check.TokCache()
        cfg = {**check.DEFAULT_PROFILES["通用"], "cv_thresh": 0.30,
               "de_density_thresh": 9.9}
        report = check.render_chapter_report(
            title, paras, check.l_intra(paras, cache), check.l_tpl_punct(paras),
            check.l1_stats(sents, paras, cache), check.l_banlist(paras),
            *check.l2_skeletons(sents, cache), check.l3_rhythm(paras), "测试", cfg)
        assert "章CV 0.3" in report
        assert "'的'密度 9.9" in report
        assert "章级 CV" in report
        # 判据对象是章级 CV（原模板拿"段内均值 CV"比阈值，指错了对象）
        assert "高于阈值 0.3" in report


# ---------------------------------------------------------------- CLI 冒烟

class TestCLI:
    def test_calibrate_smoke(self, tmp_path):
        files = [str(self._mk(tmp_path, i)) for i in range(3)]
        r = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "check.py"), "--calibrate", *files],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
        assert r.returncode == 0
        assert "建议阈值" in r.stdout

    @staticmethod
    def _mk(tmp: Path, i: int) -> Path:
        fp = tmp / f"base{i}.md"
        # 每篇 3 段 × 4 句，句长刻意错落，保证 CV/σ 可统计
        sents = ["这一句有五个字。", "这一句稍微长一点共有十个字。", "短句。", "再来一个长度中等的句子吧。"]
        fp.write_text(f"# 基线{i}\n\n" + "\n\n".join("".join(sents) for _ in range(3)), encoding="utf-8")
        return fp
