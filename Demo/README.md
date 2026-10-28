# 自主 Coding Agent + 视频剪辑 Agent

一个两阶段的 Agent 工程：阶段一用手写的 **CodingAgent** 接收自然语言需求，自主写代码、执行、读报错、改代码；阶段二由它**自己生成**一个 **VideoAgent**，完成素材分析、剪辑方案生成与成片导出。

## 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.12 | 项目使用 `.venv` |
| ffmpeg / ffprobe | 任意近期版本 | 视频处理必需 |
| 模型 API | OpenAI 兼容接口 | 默认智谱 `glm-5.3-flash`，可切 DeepSeek |

Python 依赖见 `requirements.txt`，核心只有 `openai`、`python-dotenv`、`httpx`。

## 快速开始

```powershell
# 1. 安装依赖
.\.venv\Scripts\pip.exe install -r requirements.txt

# 2. 配置模型
Copy-Item .env.example .env
notepad .env          # 填入 LLM_API_KEY

# 3. 环境自检（含模型连通性）
.\.venv\Scripts\python.exe main.py --selfcheck --api
```

自检会逐项检查 Python、openai、ffmpeg、ffprobe、`.env` 配置和模型连通性。

### ffmpeg 获取方式

项目优先查找仓库根目录的便携版 `../tools/ffmpeg/bin/`（即 `MXTest/tools/ffmpeg/bin/`），找不到才回退到系统 PATH。便携版体积较大，未纳入 Git，二选一：

```powershell
# 方式一：系统安装
winget install Gyan.FFmpeg

# 方式二：放便携版到仓库根目录（推荐，换机器可整体搬走）
# 下载后解压，把 ffmpeg.exe / ffprobe.exe 放到 MXTest\tools\ffmpeg\bin\
```

## 运行阶段一：CodingAgent

```powershell
# 让 Agent 自主完成一个任务
.\.venv\Scripts\python.exe main.py "创建 hello.py，打印 hello agent，运行验证通过后调用 finish。"

# 调整步数预算（默认 24）
.\.venv\Scripts\python.exe main.py "任务描述" --max-steps 16
```

Agent 可用工具：`list_files`、`read_file`、`write_file`、`run_python`、`check_python`、`finish`。所有文件操作被限制在 `../artifacts/workspace/` 内（仓库根 artifacts 下）。

### 四道执行护栏

| 护栏 | 作用 |
|---|---|
| 单次命令超时 | 默认 60s，防止脚本卡死 |
| 总步数上限 | 默认 24 步，剩余 2 步时强制收尾 |
| 报错原样回喂 | 形成「执行 → 观察 → 修正」闭环 |
| finish 前产物校验 | 产物不合格会打回，不允许谎报完成 |

## 运行阶段二：VideoAgent

阶段二的 `video_agent.py` 由 CodingAgent 自主生成，只依赖标准库 + ffmpeg CLI。

**前置准备**：工作区在 `../artifacts/workspace/`（不入库），需先把阶段二产物复制进去：

```powershell
Copy-Item ..\artifacts\deliverables\stage2\video_agent.py ..\artifacts\workspace\ -Force
Copy-Item ..\artifacts\deliverables\stage2\materials ..\artifacts\workspace\materials -Recurse -Force
```

脚本用相对路径引用 `materials/`，因此**必须在 `../artifacts/workspace/` 目录内运行**。

```powershell
cd ..\artifacts\workspace

# 1. 分析素材（输出 analysis.json）
..\..\Demo\.venv\Scripts\python.exe video_agent.py analyze

# 2. 生成时间线（默认 clip01→clip03→clip02，每段 2s）
..\..\Demo\.venv\Scripts\python.exe video_agent.py plan

# 3. 渲染成片
..\..\Demo\.venv\Scripts\python.exe video_agent.py render

# 追加修改：改顺序、改时长、换输出
..\..\Demo\.venv\Scripts\python.exe video_agent.py plan --order clip02,clip01 --duration 1.5 --out timeline_v2.json
..\..\Demo\.venv\Scripts\python.exe video_agent.py render --timeline timeline_v2.json --out output_v2.mp4
```

时间线格式固定，便于二次修改：

```json
{
  "version": 1,
  "output": { "width": 1280, "height": 720, "fps": 30 },
  "clips": [
    { "source": "materials/clip01.mp4", "start": 0.0, "duration": 2.0, "order": 1 }
  ]
}
```

## 输出与执行日志

每次运行自动生成 `../artifacts/runs/<run_id>/`（仓库根 artifacts 下）：

| 文件 | 内容 |
|---|---|
| `task.md` | 需求原文 |
| `events.jsonl` | 结构化事件流（LLM 调用 / 工具调用 / 校验） |
| `transcript.md` | 人类可读执行记录 |
| `meta.json` | 耗时、token、费用、产物、校验结果 |

费用按 `.env` 中 `LLM_PRICE_INPUT` / `LLM_PRICE_OUTPUT`（元 / 百万 token）估算。

## 目录结构

```
MXTest/                        仓库根
├── Demo/                      项目本体（只放代码和配置）
│   ├── main.py                CLI 入口
│   ├── agent/                 阶段一 CodingAgent
│   │   ├── loop.py            主循环 + 四道护栏
│   │   ├── llm.py             模型调用（重试 + 主备切换 + 用量统计）
│   │   ├── tools.py           工具集（workspace 沙箱）
│   │   ├── tracing.py         执行日志落盘
│   │   ├── prompts.py         系统提示词
│   │   ├── config.py          配置读取
│   │   └── selfcheck.py       环境自检
│   ├── docs/                  开发记录与说明
│   └── requirements.txt       Python 依赖
├── tools/ffmpeg/bin/          便携版 ffmpeg（不入库）
└── artifacts/                 产出物（不入库）
    ├── workspace/             Agent 工作区（materials、脚本、成片）
    ├── deliverables/stage2/   阶段二交付物
    │   ├── video_agent.py     由 CodingAgent 生成的源码
    │   ├── materials/         三段源素材
    │   ├── analysis.json      素材分析结果
    │   ├── timeline_v1/v2.json 两次剪辑时间线
    │   └── output_v1/v2.mp4   两次成片
    └── runs/<run_id>/         每次运行的完整证据
```

## 配置项

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_API_KEY` | — | 必填 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容地址 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `LLM_FALLBACK_*` | 空 | 主模型失败时自动切换 |
| `AGENT_MAX_STEPS` | `24` | 单次任务步数上限 |
| `AGENT_RUN_TIMEOUT` | `60` | 单条命令超时（秒） |
| `LLM_TIMEOUT` | `120` | 单次请求超时（秒） |
| `LLM_PRICE_INPUT` / `LLM_PRICE_OUTPUT` | `1.0` / `2.0` | 计费单价（元 / 百万 token） |

## 已知限制

1. **超大文件仍受分页上限约束**（单次约 12000 字符 / 400 行）：`read_file` 已支持 `start_line` / `max_lines`，返回首行会标注总行数与续读位置
2. **代码直接在本机执行**，没有容器/沙箱隔离，仅做了 workspace 路径越界防护
3. **素材理解只到元数据层**（时长/分辨率/帧率），未接入多模态内容理解
4. **时间线由规则+参数驱动**，自然语言到时间线的模型翻译尚未接入
5. **依赖外部模型质量**，弱模型容易过度探索，步数预算可能被探索吃掉
