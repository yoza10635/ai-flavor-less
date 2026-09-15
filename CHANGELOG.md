# Changelog

本文件记录对外可见的版本变化。方法论层面的修订史（含判据变更依据）以
[ONTOLOGY.md](ONTOLOGY.md) 为准——宪章是唯一权威修订记录，本文件只做版本索引。

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### Added
- 跨体裁外部语料测试档案（`references/cross-genre-external-test.md` +
  `references/external-corpus-summary.csv`）：新闻/知乎/学术各 5 篇的聚合指标；
  合取判据下学术 0/5 升级；含真值未核验声明与选择偏差局限。

## [0.1.0] - 2026-09-16

首个公开版本。方法论在 2026-09-15 定稿（宪章确立 + 合取制收敛为 2/2），此后进入对外维护阶段。

### Added
- 六层统计检测：L0 句内结构 / L0.5 模板标点 / L1 词层 / L1.5 规范清单（时敏层）/
  L2 句法骨架 / L3 节奏（章级 CV）；
- 指标① `--cross-chapter`：跨章结尾段字符 4-gram 复用报告（DF≥3 提示 / ≥5 flag）；
- 指标② 章级句长 CV 判据（`cv_thresh`，方向自定：高 → 关注）；
- `--calibrate`：作者级基线校准（均值±2σ），输出建议阈值供人工确认；
- 体裁参数外部化：`profiles/` 目录每体裁一个 yaml，旧版单文件 `profiles.yaml` 兼容；
- 支持 `.md` / `.txt` / 无扩展名文本 / `.docx` 输入；
- 单元测试（CV / 4-gram DF / profile 加载 / CLI 冒烟）与 GitHub Actions；
- `docs/` 输出样例：口语体（干净通过）与政务通稿（公文排比触发，体裁惯例非 AI 味）。

### 判据状态（对应宪章 §3.1/§3.2）
- 合取制 = **① 出现频率 + ② 标准化方差的 2/2**（1/2 提示、2/2 关注）；
- ③ 信息密度：**退役**（2026-09-15，40 对盲判精确率 7.5%，句对级不存在车轱辘话签名；
  现象降级为开放项，证据链见 `references/indicator-3-falsification.md`）。

### 适用于
- 中文现代散文体（小说/散文/报告/说明文）。诗歌与文言/半文言不适用（宪章 §一/§五-5）。
- 定位为**作者自查校准器**，请勿用于评判他人作品。

[0.1.0]: https://github.com/yoza10635/ai-flavor-less/releases/tag/v0.1.0
