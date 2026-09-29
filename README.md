# prediction-market-radar

预测市场数据源与分析框架，内置原创的 **CGAD（Calibration-Gated Adaptive-Depth
Agent，校准门控自适应深度智能体）** 判断层。基于 Polymarket (gamma) 与 Kalshi (v2)
公开 API，端到端做四件事：**取数 → 规则信号 → 多智能体判断 → 可审计的输出与回测**。

> **免责声明**：仅供研究与学习，处理预测市场公开数据，**不构成投资建议**；不提供交易执行。

## 核心设计

预测市场是天然带环境反馈的推理场景：市场收盘后会揭晓真实结果。全程 point-in-time
（不碰未来数据）。端到端数据流：

```text
  Polymarket (gamma) ──┐
  Kalshi (v2)      ────┴──▶ 统一模型 ──▶ 信号引擎(6规则) ──▶ Router(Tier 0/1/2)
                           (asof_ts)     跨平台偏离·主题归一     <1ms · HIGH 硬兜底
                                                                   │
                              Tier 0 ─ 规则直出                    │ Tier 1/2 ─ 进 LLM
                                                                   ▼
                             Bull×3 + Bear×3 ─▶ SOD 聚合 ─▶ 分歧>10pp → 至多1轮辩论
                                                                   │
                                               Judge ─▶ 双模型弃权(分歧>15pp)
                                                                   │
                                                          Selective Gate
                                                                   │
                                                ┌──────────────────┴──────────────────┐
                                                ▼                                     ▼
                                          decisions 落库                      predictions.log
                                          (snapshot_hash)                   (hash链 · 事前Brier)
                                                │
                                                ▼
                           calibrate: 回放Brier · 可靠性图 · 记忆注入仪表
```

CGAD 四个组件各有明确的设计动机：

| 组件 | 做什么 | 关键取舍 |
|---|---|---|
| 深度路由器 | 确定性规则打分定 Tier 0/1/2，`<1ms` 零成本 | 用规则替代 LLM 复杂度分类器（MDAgents） |
| 采样聚合 + 条件辩论 | Bull/Bear 各采样 K=3，SOD 语义离群降权；分歧 >10pp 才辩论，至多 1 轮 | 聚合优先，辩论降级为分歧解决器（ARMOR-MAD / L-MAD） |
| 环境反馈反思 | 结算后产 lesson + 按主题再校准 P(YES) 偏移 | 免训练借鉴 RefGRPO，不等价训练 |
| 学习型弃权阈值 | 历史扫描最小化 selective Brier；双模型分歧 >15pp 强弃权 | 数据不足退回默认阈值，不伪装成已校准 |

有前瞻性的工程决策：

- **point-in-time 数据契约**：落库行带 `asof_ts`，裁决存 `snapshot_hash`，消融显式剔除收敛区泄漏。
- **前向预测台账**（`predictions.log`）：append-only hash 链，`calibrate` 输出「事前 Brier」与回放 Brier 分列——可审计的 track record，而非一次性回测报表。
- **双模型弃权锚点**：同一输入跑第二模型，`|p_yes₁ − p_yes₂| > 15pp` 强制弃权（DeepFund「交易人格」差异）。
- **阈值单一权威来源**：所有【假设】阈值收敛在 `data/config.py::ASSUMPTIONS`，README 与测试同源读取，防止「未校准」被悄悄当成「已校准」。

完整设计见 **[docs/DESIGN.md](docs/DESIGN.md)**。

## 快速开始

```bash
pip install -r requirements.txt
python main.py run --mock        # 三分钟输出完整日报（无需网络 / key）
python main.py calibrate --mock  # 演示校准回测 + 可靠性图 + 台账关联
```

`--mock` 用固定种子合成数据，输出除时间戳外可复现。填真实 key 后跑真实链路：

```bash
cp .env.example .env            # 填 LLM_API_KEY（DeepSeek）
KALSHI_BASE_URL=https://demo-api.kalshi.co/trade-api/v2 \
  python main.py run --db demo.db --ledger-source demo
```

## 项目状态

- 全链路可跑：`--mock` 三分钟产出日报；`pytest` 118 passed / 1 xfailed。
- 真实链路已通 demo 沙盒（Kalshi sandbox 直连 + DeepSeek 真实 LLM 端到端）；生产数据待境外代理。
- 诚实结论：沙盒数据上 **CGAD 深度无增益**（T0/T1/T2 无分离），数字只验证管线、不验证真实校准。

## 目录

```
data/       数据接入：Polymarket / Kalshi 客户端 · 统一模型 · 存储 · 台账
analysis/   分析：Scanner 过滤 · 信号引擎 · 跨平台偏离 · 主题归一
agents/     CGAD：路由器 · 聚合/辩论 · Judge · 反思 · 校准 · 调研 agent
report/     渲染：Markdown 日报
tests/      118 个离线测试 + 真实 fixtures
```

## 文档导航

- [docs/DESIGN.md](docs/DESIGN.md) — 完整设计（架构、数据模型、信号规则、消融、文献核实、已知坑）
- [examples/ablation.md](examples/ablation.md) — R13 分层消融（真实 LLM · demo 沙盒）
- [examples/reliability.md](examples/reliability.md) — 可靠性图 + 记忆注入仪表
- [examples/sample_output.md](examples/sample_output.md) — mock 日报样例

## License

[MIT](LICENSE)
