# references 索引

全部实证结论基于同一内部语料：**单作者中文网文 76 章**（ch001–ch076），
其中 **A 组 ch001–010 已人工去味**、**C 组 ch067–076 未处理**，B 组为中间未处理章。
文中「A/C 分离」「漂移 r（指标×章号）」「命中率 AUC」等口径均针对该语料，阈值请勿跨语料搬运。

## 规范（与代码同步维护）

| 文件 | 内容 |
|---|---|
| [detection-layers.md](detection-layers.md) | 六层检测的指标细节、报告各节数据结构、阈值语义与归因互斥规则 |
| [cross-chapter-phrase-cloud.md](cross-chapter-phrase-cloud.md) | 指标①（跨章装置）的方法规格、实测依据与复现脚本 |
| [profiles.example.yaml](profiles.example.yaml) | profiles.yaml 配置示例（阈值键与校准方法） |

## 实验与证伪档案（负面清单的证据链，只增不改）

| 文件 | 结论 |
|---|---|
| [indicator-3-falsification.md](indicator-3-falsification.md) | 指标③句对级操作化证伪：40 对盲判精确率 7.5%；根因是粒度错——相邻句对不存在车轱辘话签名 |
| [vector-density-feasibility-test.md](vector-density-feasibility-test.md) | 向量/语义密度方案证伪：命中率 AUC 0.43–0.46（<0.5），全被创作时间漂移解释 |
| [lmscan-feature-audit.md](lmscan-feature-audit.md) | 同路线开源竞品 lmscan（Apache-2.0）24 特征逐条审计：中文双层失效；借算法不借管线 |
| [storyscope-evolution-assessment.md](storyscope-evolution-assessment.md) | StoryScope 论文（arXiv 2604.03136）可迁移性评估：方向指导性充足、数值零迁移 |
| [storyscope-dimension-transfer-test.md](storyscope-dimension-transfer-test.md) | 论文 30 特征→10 语义场维度迁移实测：6/10 方向相反，阈值必须自定 |
| [competitive-analysis.md](competitive-analysis.md) | 开源前竞品尽调：四层竞品地图、真实差异化资产、两阶段开源策略 |

档案文档按「只增不改」维护：结论被后续实验推翻时在文内追加修订注，不重写历史段落。
