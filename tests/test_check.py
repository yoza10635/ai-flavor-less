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
        assert {"通用", "网文", "论文"} <= set(ps)
        assert ps["网文"]["cv_thresh"] == 0.864
        # 论文 只写差异项，其余键从"通用"基底继承
        assert ps["论文"]["dialog"] == check.DEFAULT_PROFILES["通用"]["dialog"]

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
