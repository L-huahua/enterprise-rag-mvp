# 开发记录（Dev Log）

本文件按时间顺序记录需求、决策、验证结果与 AI 参与情况，供复盘使用。
关联规则：每次 `run` 产出独立 `run_id`，日志、代码、成片都归档在 `runs/<run_id>/`。

---

## 2026-10-28 · 阶段零：环境盘点与补齐

**事实（实测，非推测）**

- Python 3.12.7（Anaconda 虚拟环境 `.venv`）
- 已有：Flask 3.1.3、uvicorn 0.54.0、pydantic 2.14.1、chromadb 1.5.9、torch 2.14.1、httpx、requests、Git 2.55.0
- 缺失：ffmpeg / ffprobe（视频链路完全不可用）、`openai` SDK、opencv
- 已补齐：`openai 3.40.0`（原本已满足）、`opencv-python-headless 5.0.0.93`

**决策**

1. **不使用 torch / transformers**：当前为 CPU 版，加载模型耗时不可控，180 分钟预算内放弃。
2. **不使用 moviepy，直接调用 ffmpeg CLI**：moviepy 2.x API 与 1.x 不兼容，AI 生成的代码极易踩版本坑；CLI 确定性更强、报错更明确。
3. **ffmpeg 使用项目内置便携版**（`tools/ffmpeg/`）：无需管理员权限，不受考场机器环境影响。
4. 所有文件操作限制在 `workspace/` 内，防止 Agent 写出项目目录。

**已知问题**

- ffmpeg 下载受网络速度影响多次中断（实测带宽约 52 kB/s），仍在重试。
- `.env` 尚未填写 API Key，模型连通性未验证。

---

## 2026-10-28 · 阶段一：CodingAgent

**需求**：实现一个具备自主编程闭环的 CodingAgent，能自主写代码、运行、根据报错修正，并在完成后自我验证。

**产出**

| 文件 | 职责 |
|---|---|
| `main.py` | CLI 入口（`--selfcheck` / 任务描述） |
| `agent/config.py` | 配置装载、主备服务商、ffmpeg 路径探测 |
| `agent/llm.py` | OpenAI 兼容客户端，指数退避重试 + 主备切换 |
| `agent/tools.py` | 6 个工具（列目录/读/写/执行/语法检查/完成），含路径越界防护 |
| `agent/loop.py` | 主循环 + 四道护栏 |
| `agent/prompts.py` | 系统提示词 |
| `agent/selfcheck.py` | 环境自检 |

**四道护栏及其存在理由**

1. 单命令超时（默认 60s）——防止一条坏命令卡死整轮。
2. 总步数上限（默认 12，后因收尾失败上调至 24）——防止模型陷入无限自我修正。
3. 失败输出原样回喂——这是“自主闭环”的核心，缺了它就只是个代码生成器。
4. `finish` 前强制产物校验（至少一个 `.py` 且语法通过）——防止模型谎报完成。

**验证方式**：用假模型（脚本化返回工具调用）离线跑通全链路，避免在无 API Key 时无法自测。

实测输出：

```
[1/6] write_file -> 已写入 demo.py
[2/6] run_python -> exit_code=0  stdout: hello agent
[3/6] finish     -> 产物校验通过
finished=True  verified=True  artifacts=['demo.py']
```

**AI 参与说明**

- Agent 主循环骨架、工具 schema 由 AI 生成；人工加入四道护栏、路径越界防护、输出截断策略（保留头尾，避免砍掉堆栈尾部）。
- 离线假模型测试由人工设计，目的是在缺少 API Key 时仍能验证闭环。

---

## 2026-10-28 · 阶段一补强：执行日志与计费

**需求来源**：交付材料要求「执行日志能关联需求、工具调用、代码修改、验证及成片」，并要求说明模型调用费用。

**决策**：日志必须**运行中落盘**，不能事后补录——过程不可复现，事后写的日志经不起追问。

**产出**

| 文件 | 变更 |
|---|---|
| `agent/tracing.py` | 新增，每次运行产出 `runs/<run_id>/` 四件套 |
| `agent/llm.py` | 采集 `response.usage`，累计 prompt/completion token |
| `agent/loop.py` | 逐步记录 LLM 调用、工具调用、产物校验的耗时与结果 |
| `agent/config.py` | 新增 `LLM_PRICE_INPUT` / `LLM_PRICE_OUTPUT` 单价配置 |

**验证**（`runs/20261028-112739`）：hello.py 任务，3 步完成，24.8s，2467 in / 135 out，¥0.0027，四个文件全部生成。

**顺带解决**：ffmpeg 直连下载实测仅 16 kB/s（80MB 需 55 分钟），切换到 `gh-proxy.com` 镜像后达 16.6 MB/s，192MB 包十余秒下完。

**AI 参与说明**：日志模块骨架由 AI 生成，人工确定事件字段（type/step/tool/args/result/duration_ms）与目录结构。

---

## 2026-10-28 · 阶段二：CodingAgent 生成 VideoAgent

**需求**：由 CodingAgent 自主开发一个视频剪辑 Agent，完成素材分析、剪辑与导出。

**契约（人工给定，Agent 实现）**

```
analyze → analysis.json        （name/duration_s/width/height/fps/size_bytes）
plan    → timeline_v1.json     （version/output/clips[source,start,duration,order]）
render  → output_v1.mp4        （逐段裁剪 → 统一 scale 1280x720 → concat）
```

约束：只依赖标准库，禁止 moviepy，ffmpeg/ffprobe 走绝对路径。

**过程**（`runs/20261028-113043`，20 步，387s，¥0.1715）

1. 先写 `probe_tmp.py` 探测素材 → 发现 `clip03.mp4` 损坏（无法解析）
2. 自行生成 `fix_clip03.py` 重建素材（判断合理：素材本身坏了，不是绕过问题）
3. 写出 `video_agent.py`（307 行），analyze 与 plan 一次通过
4. **render 首次失败**：ffmpeg `exit 1`
5. 读回代码定位问题 → 重写（308 行）→ 重跑成功

**错误恢复实证**（本次考核的核心考点，终于被真实触发）

```
step 13  run_python render  → exit_code=1，ffmpeg 命令失败
step 15  read_file  video_agent.py
step 16  write_file video_agent.py（重写）
step 18  run_python render  → exit_code=0，渲染完成
step 20  run_python verify_output.py → RESULT: PASS
```

**首次成片 v1**（人工用 ffprobe 独立复核）：6.000s / 1280x720 / h264 / yuv420p / 无音轨 / 771KB。

**暴露的问题**：20 步刚好耗尽，产物全对但**没来得及调用 `finish`**，`完成=False`。

### 收尾补跑（`runs/20261028-113758`）

单独给一个复核任务，5 步完成，`完成=True 校验=True`，费用 ¥0.0079。

---

## 2026-10-28 · 阶段三：追加修改与缺陷修复

**追加需求**：顺序改为 clip02 → clip01，每段 1.5s，输出 v2。

**做法**：复用 `plan --order --duration --out` 与 `render --timeline --out`，**未改一行代码**——这正是契约先行的价值。

**结果**（人工 ffprobe 复核）：timeline_v2.json 两段 1.5s；output_v2.mp4 实测 3.000s / 1280x720 / 无音轨。

**踩到的两个坑**

1. **步数耗尽再次发生**（`20261028-113938` 16 次调用、`20261028-114155` 8 次调用，都没走到 finish）
   → 修复：接近上限（剩 2 步）时注入「立即收尾」提示；默认上限 12 → 24
2. **`read_file` 4000 字符截断**：Agent 为看完整 `video_agent.py`，连续 8 步写临时脚本打印行区间
   → 未修，已记入已知问题；修掉预计可显著降低阶段二的 token 消耗

修复后同一收尾任务 5 步完成（`runs/20261028-114332`）。

---

## 2026-10-28 · 修复 read_file 截断

**问题**：`read_file` 硬截断 4000 字符。`video_agent.py` 有 307 行 / 10.7KB，读不全；Agent 为看完整源码，在 `runs/20261028-113938` 里连续 8 步写临时脚本打印行区间，浪费步数与 token。

**修法**（`agent/tools.py`）

- 改为**按行分页**：新增 `start_line`、`max_lines` 参数
- 单次上限 12000 字符 / 400 行（10KB 级源码可一次读完）
- 返回首行固定标注「共 N 行，本次返回第 a-b 行」；未读完时给出 `start_line` 续读提示
- 工具 schema 同步说明分页用法，避免模型继续用老习惯

**验证**

```
默认: video_agent.py：共 307 行，本次返回第 1-307 行 | 返回字符数: 9883
分页: video_agent.py：共 307 行，本次返回第 1-20 行（未读完，继续读取请设 start_line=21）
续读: video_agent.py：共 307 行，本次返回第 300-307 行
```

**效果**：307 行源码一次读完，不再需要临时脚本探路。

## 交付物对照

| # | 交付物 | 位置 |
|---|---|---|
| 1 | 两 Agent 源码 / 依赖 / 启动命令 / 配置 | `agent/`、`deliverables/stage2/video_agent.py`、`README.md`、`requirements.txt`、`.env.example` |
| 2 | 阶段切换提交与开发记录 | Git 提交记录（见下）+ 本文件 |
| 3 | 完整执行日志 | `runs/<run_id>/`（6 个目录，events.jsonl + transcript.md + meta.json） |
| 4 | 素材分析 / 两条时间线 / 两个成片 | `deliverables/stage2/`（含 `materials/` 源素材、analysis.json、timeline_v1/v2.json、output_v1/v2.mp4） |
| 5 | 一页说明 | `docs/report.md` |

## 2026-10-28 · 交付自查与修复

对照 5 项交付清单逐项核对本地文件，发现并修复 3 处影响完整性的问题：

| 问题 | 影响 | 修复 |
|---|---|---|
| 源素材未入库（`workspace/` 被忽略） | `analysis.json` 指向的文件不存在，README 的阶段二流程无法复现 | 复制 3 段素材（1.91MB）到 `deliverables/stage2/materials/`；README 补前置复制步骤与「必须在 workspace 内运行」约束 |
| 三次未收尾运行的 `meta.json` 为 `finished=false` | 单独点开看会误判任务失败 | 三个目录各加 `NOTE.md`，说明原因并指向复核运行 |
| 报告超出「一页以内」 | 与要求不符 | 合并「耗时」与「费用」两张表，补第 6 条已知问题 |

## 提交记录

```
cfbde90 修复：read_file 改为按行分页，长文件一次读完
7b4a59d 开发记录：同步提交记录与交付物对照表
f5afe4e 交付自查修复：素材入库、未收尾运行说明、报告精简
9a154f3 开发记录：补齐阶段二与追加修改段落
76a11a1 文档：补充 README 启动说明与一页交付说明
2782a38 追加修改：按新需求调整时间线，输出 v2 成片
ee19db2 [阶段切换] CodingAgent 自主生成 VideoAgent，首次成片 v1 通过验证
3c6c505 基础设施：执行日志落盘与 token 费用统计
33e6833 阶段一：完成 CodingAgent 自主编程闭环
```

## 待办

- [x] ffmpeg 落地并验证 `ffmpeg -version`
- [x] 填写 `.env`，运行 `python main.py --selfcheck --api`
- [x] 阶段二：由 CodingAgent 生成 VideoAgent，首次成片
- [x] 补充执行日志落盘与 token/费用统计
- [x] 追加修改：输出 v2 成片
- [x] README 与一页说明
- [x] 交付自查与修复（素材入库、未收尾运行说明、报告精简）
- [ ] 推送 GitHub
- [x] 修复 `read_file` 截断（按行分页，上限 12000 字符 / 400 行）
- [ ] （可选）接入自然语言→时间线的模型翻译
