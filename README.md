# ✂️ FastCut

[![English](https://img.shields.io/badge/Read-English-1f6feb?style=for-the-badge)](#english)
[![中文](https://img.shields.io/badge/阅读-中文-0f766e?style=for-the-badge)](#中文)

---

## English

FastCut is a transcript-first video and audio editor.

Upload a file, generate a transcript, select words in the transcript, cut them out, preview the edited result, and export a clean final file.

FastCut is designed for spoken content workflows such as:
- removing filler words
- trimming repeated takes
- cleaning up pauses and thinking sounds
- cutting directly from transcript text instead of a traditional timeline

### Default Setup

FastCut now defaults to a **local-only transcription setup**.

For the standard setup, you do **not** need:
- an OpenAI API key
- any other external AI API key

By default FastCut uses:
- `qwen3_asr_local`
- `Qwen/Qwen3-ASR-0.6B`
- `Qwen/Qwen3-ForcedAligner-0.6B`

You only need an API key if you intentionally switch the provider to `openai_whisper_chunked`.

### Language Focus

FastCut is currently tuned for **Chinese spoken-content editing**.

There are already many mature options for English transcript editing. This project was built around Chinese creator workflows, especially:
- spoken Mandarin video
- repeated takes and restarts
- filler words and thinking sounds
- pause cleanup
- word-level editing directly from the transcript

The default local setup uses Qwen ASR because, among the models tested in this build process, it gave the best balance for Chinese transcript quality and editable word-level timing.

### What FastCut Does

- Generates editable transcript tokens with timestamps
- Lets you select transcript text and cut or restore it with keyboard shortcuts
- Supports drag selection across multiple words
- Detects filler words, long pauses, short pauses, and thinking sounds
- Generates an **accurate edited preview** using the same backend render path as final export
- Exports edited video or audio with ffmpeg

### Current Editing Model

FastCut no longer uses per-token `CUT / KEEP` buttons as the main workflow.

The current editing model is:
- Click a word to move the playhead
- Drag across words to select a range
- Press `D` to cut the selected range
- Press `F` to restore the selected range
- Press `Space` to play or pause

This makes the editor behave more like editing text than clicking a control panel.

### Supported Media

Input:
- Video: `MP4`, `MOV`, `AVI`, `MKV`
- Audio: `M4A`, `MP3`, `WAV`

Output:
- Edited video export for video inputs
- Edited audio export for audio-only inputs

### Transcription Providers

FastCut supports multiple transcription backends through `TRANSCRIPTION_PROVIDER`.

Implemented providers:
- `qwen3_asr_local`
- `local_whisper`
- `openai_whisper_chunked`
- `funasr`

The current codebase also supports local Qwen ASR + forced alignment settings.

Example provider-related settings in [`.env.example`](/Users/simona/Downloads/video cutter/.env.example):

```env
TRANSCRIPTION_PROVIDER=qwen3_asr_local

LOCAL_WHISPER_MODEL=base
LOCAL_WHISPER_LANGUAGE=zh
WHISPER_DEVICE=cpu

QWEN_ASR_MODEL=Qwen/Qwen3-ASR-0.6B
QWEN_ASR_ALIGNER_MODEL=Qwen/Qwen3-ForcedAligner-0.6B
QWEN_ASR_LANGUAGE=Chinese
QWEN_ASR_DEVICE=auto
QWEN_ASR_DTYPE=auto
QWEN_ASR_MAX_BATCH_SIZE=8
QWEN_ASR_MAX_NEW_TOKENS=256
QWEN_ASR_ENABLE_ALIGNER=true

# Optional: only needed if you switch to the OpenAI provider
# OPENAI_API_KEY=sk-your-real-openai-api-key
```

Notes:
- `qwen3_asr_local` is the default local-only setup and does not require an API key
- `openai_whisper_chunked` remains available if you want to use the OpenAI API
- local Qwen / local Whisper may download models on first run
- ffmpeg must be installed and available in `PATH`

#### Models tried during development

FastCut did not jump straight to the current default. These are the main transcription paths tested during development:

##### 1. OpenAI `whisper-1` via `openai_whisper_chunked`
- Strengths:
  - strong general transcription quality
  - reliable word-level timestamps
  - straightforward chunk-and-merge workflow
- Weaknesses in this project:
  - often cleaned up or merged repeated Chinese speech
  - could smooth over false starts and duplicated phrases
  - less faithful for spoken Mandarin cleanup workflows

##### 2. Local Whisper via `local_whisper`
- Models tested included local Whisper setups such as `large-v3`
- Strengths:
  - local control
  - no API dependency
  - more decoding options than hosted API use
- Weaknesses in this project:
  - still tended to normalize repeated spoken phrases
  - not consistent enough for the Chinese edit-from-transcript workflow we wanted

##### 3. FunASR via `funasr`
- Current backend model option: `paraformer-zh`
- Strengths:
  - Chinese-focused transcription path
  - sometimes preserved spoken Chinese structure better than Whisper
- Weaknesses in this project:
  - timestamp completeness could be inconsistent
  - some tail words appeared in text without equally reliable timestamps
  - that made precise transcript-based editing harder

##### 4. Local Qwen ASR via `qwen3_asr_local`
- Current default:
  - `Qwen/Qwen3-ASR-0.6B`
  - `Qwen/Qwen3-ForcedAligner-0.6B`
- Why this became the default:
  - best tested balance for Chinese spoken content
  - better fit for repeated phrases, pauses, and edit-driven transcript use
  - local-only workflow, so no API key is required
  - the forced aligner gives the timestamp structure needed for transcript editing

If you are building primarily for English, you may prefer a different default. This repository is optimized for Chinese-first editing.

### Prerequisites

- Python 3.10+
- Node.js 18+
- `ffmpeg` installed and available in `PATH`
- enough local disk / memory to run local ASR models
- an OpenAI API key only if you explicitly switch to `openai_whisper_chunked`

### Setup

#### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

#### 2. Install frontend dependencies

```bash
npm install
```

#### 3. Create your local env file

```bash
cp .env.example .env
```

You can use the copied [`.env`](/Users/simona/Downloads/video cutter/.env) as-is. The default setup already uses local Qwen transcription and does not require an API key.

Only change [`.env`](/Users/simona/Downloads/video cutter/.env) if you want to switch providers or tune the local model settings.

The default local setup is:

```env
TRANSCRIPTION_PROVIDER=qwen3_asr_local
QWEN_ASR_MODEL=Qwen/Qwen3-ASR-0.6B
QWEN_ASR_ALIGNER_MODEL=Qwen/Qwen3-ForcedAligner-0.6B
```

### Run the App

#### Backend

```bash
uvicorn main:app --reload --port 8000
```

#### Frontend

```bash
npm run dev -- --port 4190
```

Then open:
- Frontend: [http://127.0.0.1:4190](http://127.0.0.1:4190)
- Backend health check: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

On first run with the default local setup, FastCut may spend extra time downloading the Qwen ASR and aligner weights before the first transcription completes.

### How to Use

1. Upload a video or audio file
2. Generate the transcript
3. Edit the transcript:
   - click a word to move the playhead
   - drag across words to select a range
   - press `D` to cut
   - press `F` to restore
   - press `Space` to play or pause
4. Use **Play edited cut** to generate and preview the edited version
5. Export the final cut

### Editing Features

#### Transcript-based cutting
- Edit by selecting transcript text instead of scrubbing a timeline
- Deleted words stay visible with strike-through, so you can review your edits

#### Batch selection
- Drag across multiple words to select them in one gesture
- Works left-to-right and right-to-left

#### Keyboard shortcuts
- `D` → cut selection
- `F` → restore selection
- `Space` → play / pause

#### Auto-mark tools
- Mark filler words
- Mark thinking sounds
- Mark pauses

#### Search-based cutting
- Find matching words
- Cut all matches in one action

#### Manual time cuts
- Add cuts directly by time range
- Useful when transcript text is not enough for a specific boundary

### Preview vs Export

FastCut now uses two distinct ideas:

#### Transcript editing preview
- You edit through the transcript UI
- Selection and cut state are shown in the editor

#### Accurate edited preview
- `Play edited cut` calls the backend `/preview`
- The backend renders a temporary edited media file using the same ffmpeg concat path as final export
- This makes preview playback much closer to the final export than simple browser skip-seeking

#### Final export
- `/export` renders the final edited file
- Video and audio are cut using ffmpeg based on transcript deletions and manual cuts

### Pause / Filler / Annotation Behavior

FastCut can display:
- filler words
- thinking sounds
- long pauses
- short pauses

These appear inline in the transcript so they can be selected and cut like normal text.

Examples:
- `〈停顿 1.7s〉`
- `〈短停顿 0.8s〉`
- `〈思考音 1.5s〉`

### Project Structure

```text
video cutter/
├── App.jsx
├── App.css
├── TranscriptEditor.jsx
├── main.py
├── requirements.txt
├── package.json
├── index.html
├── vite.config.js
├── .env.example
├── uploads/          # transcripts, uploaded media, raw debug outputs
└── outputs/          # preview and exported media
```

Key files:
- [App.jsx](/Users/simona/Downloads/video cutter/App.jsx) — upload, loading, editing, export, done states
- [TranscriptEditor.jsx](/Users/simona/Downloads/video cutter/TranscriptEditor.jsx) — transcript editing UI
- [App.css](/Users/simona/Downloads/video cutter/App.css) — styling
- [main.py](/Users/simona/Downloads/video cutter/main.py) — FastAPI backend, transcription, preview, export

### API Endpoints

Implemented backend endpoints:
- `POST /transcribe`
- `POST /preview`
- `POST /export`
- `GET /health`

### Notes

- Local model providers may be slow on first run because they download weights
- Export speed depends on media length, edit density, and available hardware acceleration
- Accurate preview and final export are intentionally aligned so playback is closer to the exported result

### GitHub

Repository:
- [https://github.com/bluebluegrass/FastCut](https://github.com/bluebluegrass/FastCut)

---

## 中文

FastCut 是一个基于 transcript 的视频 / 音频编辑器。

你上传文件、生成 transcript、直接在文字上选择和删除内容、试听剪辑结果，然后导出最终文件。

它主要适合这类口播工作流：
- 删除语气词
- 删掉重复重说
- 清理停顿和思考音
- 不用传统时间轴，直接通过 transcript 剪辑

### 默认配置

FastCut 现在默认使用 **纯本地转录方案**。

正常使用时，你**不需要**：
- OpenAI API key
- 任何其他外部 AI API key

默认使用的是：
- `qwen3_asr_local`
- `Qwen/Qwen3-ASR-0.6B`
- `Qwen/Qwen3-ForcedAligner-0.6B`

只有当你主动把 provider 切到 `openai_whisper_chunked` 时，才需要 API key。

### 语言定位

FastCut 目前主要面向 **中文口播内容编辑**。

英文 transcript 编辑工具已经很多了。这个项目之所以做成现在这样，是因为它主要针对中文创作者的真实需求，尤其是：
- 普通话口播视频
- 重复重说和卡壳重启
- 语气词和思考音
- 停顿清理
- 直接按词级 transcript 做编辑

默认使用 Qwen ASR，是因为在这次整个构建和测试过程中，它在中文 transcript 质量和可编辑时间戳之间给出了最好的平衡。

### 它能做什么

- 生成带时间戳、可编辑的 transcript token
- 直接在 transcript 上选择文字并删除 / 恢复
- 支持拖拽跨多个词批量选择
- 检测语气词、长停顿、短停顿、思考音
- 生成与最终导出路径对齐的 **准确预览**
- 通过 ffmpeg 导出最终剪辑结果

### 当前编辑方式

FastCut 现在不再把每个 token 的 `CUT / KEEP` 按钮当成主交互。

当前主要编辑方式是：
- 点击一个词，把播放头跳到对应位置
- 拖拽多个词，形成一个选择范围
- 按 `D` 删除选区
- 按 `F` 恢复选区
- 按 `Space` 播放 / 暂停

整个体验更像在编辑文字，而不是点一堆控制按钮。

### 支持的媒体类型

输入：
- 视频：`MP4`, `MOV`, `AVI`, `MKV`
- 音频：`M4A`, `MP3`, `WAV`

输出：
- 视频输入会导出编辑后的视频
- 纯音频输入会导出编辑后的音频

### 转录 Provider

FastCut 通过 `TRANSCRIPTION_PROVIDER` 支持多种转录后端。

当前代码里支持：
- `qwen3_asr_local`
- `local_whisper`
- `openai_whisper_chunked`
- `funasr`

当前也支持本地 Qwen ASR + forced alignment 的配置。

相关示例配置见 [`.env.example`](/Users/simona/Downloads/video cutter/.env.example)。

说明：
- `qwen3_asr_local` 是当前默认、本地优先、无需 API key 的方案
- `openai_whisper_chunked` 仍然保留，如果你想使用 OpenAI API 也可以切换
- 本地 Qwen / 本地 Whisper 第一次运行时可能会先下载模型
- 需要先安装 `ffmpeg`

#### 开发过程中实际试过的模型

FastCut 不是一开始就直接用现在这套默认配置。开发过程中主要试过这几条路线：

##### 1. OpenAI `whisper-1` + `openai_whisper_chunked`
- 优点：
  - 通用转录质量强
  - 词级时间戳比较稳定
  - chunk + merge 工作流清楚
- 在这个项目里的问题：
  - 对中文重复话会经常“整理得太干净”
  - false starts、重复短语容易被合并
  - 不够忠实于中文口播剪辑场景

##### 2. 本地 Whisper + `local_whisper`
- 实测里包括像 `large-v3` 这样的本地 Whisper 路线
- 优点：
  - 本地可控
  - 不依赖 API
  - 解码参数可调
- 在这个项目里的问题：
  - 对重复表达仍然有明显正规化倾向
  - 对我们想要的中文 transcript 剪辑工作流还不够稳

##### 3. FunASR + `funasr`
- 当前后端里的对应模型选项是 `paraformer-zh`
- 优点：
  - 更偏中文场景
  - 某些中文表达保留得比 Whisper 更自然
- 在这个项目里的问题：
  - 时间戳完整性不够稳定
  - 有时文本里有词，但时间戳没有同样可靠地跟上
  - 这会影响精细的 transcript 剪辑

##### 4. 本地 Qwen ASR + `qwen3_asr_local`
- 当前默认：
  - `Qwen/Qwen3-ASR-0.6B`
  - `Qwen/Qwen3-ForcedAligner-0.6B`
- 为什么最后选它：
  - 在中文口播场景下整体最平衡
  - 对重复话、停顿、以剪辑为目的的 transcript 使用更合适
  - 完全本地，不需要 API key
  - forced aligner 能提供 transcript 编辑所需的时间戳结构

如果你的核心场景是英文内容，默认路线可能会不同。这个仓库当前是按中文优先来优化的。

### 环境要求

- Python 3.10+
- Node.js 18+
- `ffmpeg` 已安装并可在 `PATH` 中找到
- 本地要有足够的磁盘空间和内存运行 ASR 模型
- 只有在你主动切到 `openai_whisper_chunked` 时才需要 OpenAI API key

### 安装

#### 1. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

#### 2. 安装前端依赖

```bash
npm install
```

#### 3. 创建本地 env 文件

```bash
cp .env.example .env
```

复制完 [`.env`](/Users/simona/Downloads/video cutter/.env) 之后，默认就可以直接用，不需要再额外填 API key。

只有当你想切 provider，或者想调整本地模型参数时，才需要修改 [`.env`](/Users/simona/Downloads/video cutter/.env)。

默认本地配置是：

```env
TRANSCRIPTION_PROVIDER=qwen3_asr_local
QWEN_ASR_MODEL=Qwen/Qwen3-ASR-0.6B
QWEN_ASR_ALIGNER_MODEL=Qwen/Qwen3-ForcedAligner-0.6B
```

### 运行方式

#### 后端

```bash
uvicorn main:app --reload --port 8000
```

#### 前端

```bash
npm run dev -- --port 4190
```

然后打开：
- 前端：[http://127.0.0.1:4190](http://127.0.0.1:4190)
- 后端健康检查：[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

第一次跑默认本地配置时，FastCut 可能会先下载 Qwen ASR 和 aligner 模型，所以第一次转录会更慢一些。

### 如何使用

1. 上传视频或音频文件
2. 生成 transcript
3. 编辑 transcript：
   - 点击一个词，把播放头跳到对应位置
   - 拖拽多个词，形成选区
   - 按 `D` 删除
   - 按 `F` 恢复
   - 按 `Space` 播放 / 暂停
4. 使用 **Play edited cut** 生成并试听剪辑预览
5. 导出最终文件

### 编辑功能

#### 基于 transcript 的剪辑
- 不用拖传统时间轴，而是直接编辑文字
- 已删除的词会保留并显示删除线，方便复查

#### 批量选择
- 可以拖过多个词，一次性形成选区
- 支持从左往右和从右往左

#### 键盘快捷键
- `D` → 删除选区
- `F` → 恢复选区
- `Space` → 播放 / 暂停

#### 自动标记工具
- 标记 filler words
- 标记 thinking sounds
- 标记 pauses

#### 搜索批量删除
- 查找匹配词
- 一次性删除所有匹配结果

#### 手动时间切割
- 直接按时间范围添加 cut
- 适合 transcript 不足以表达某个精细边界的情况

### Preview 和 Export 的区别

FastCut 里这两个概念是分开的：

#### Transcript 编辑预览
- 你在 transcript UI 里完成选择和删除
- 编辑状态会直接显示在文字里

#### 准确剪辑预览
- `Play edited cut` 会调用后端 `/preview`
- 后端会用和最终导出相同的 ffmpeg 拼接路径生成一个临时预览文件
- 所以它比浏览器里简单跳播更接近最终导出结果

#### 最终导出
- `/export` 会渲染最终文件
- 视频和音频都会根据 transcript 删除结果和 manual cuts 一起导出

### 停顿 / 语气词 / 注释行为

FastCut 目前可以在 transcript 里显示：
- filler words
- thinking sounds
- long pauses
- short pauses

这些都会以内联标记的形式出现在 transcript 里，可以像普通文字一样被选中和删除。

例如：
- `〈停顿 1.7s〉`
- `〈短停顿 0.8s〉`
- `〈思考音 1.5s〉`

### 项目结构

```text
video cutter/
├── App.jsx
├── App.css
├── TranscriptEditor.jsx
├── main.py
├── requirements.txt
├── package.json
├── index.html
├── vite.config.js
├── .env.example
├── uploads/          # transcripts, uploaded media, raw debug outputs
└── outputs/          # preview and exported media
```

关键文件：
- [App.jsx](/Users/simona/Downloads/video cutter/App.jsx) — 上传、loading、编辑、导出、完成页状态
- [TranscriptEditor.jsx](/Users/simona/Downloads/video cutter/TranscriptEditor.jsx) — transcript 编辑界面
- [App.css](/Users/simona/Downloads/video cutter/App.css) — 样式
- [main.py](/Users/simona/Downloads/video cutter/main.py) — FastAPI 后端、转录、preview、export

### API 接口

当前后端实现的接口：
- `POST /transcribe`
- `POST /preview`
- `POST /export`
- `GET /health`

### 备注

- 本地模型第一次运行时会先下载权重，所以会更慢
- 导出速度会受到媒体长度、剪辑密度和硬件加速条件影响
- accurate preview 和 final export 故意走得更接近，目的是让试听更接近最终结果

### GitHub

仓库地址：
- [https://github.com/bluebluegrass/FastCut](https://github.com/bluebluegrass/FastCut)
