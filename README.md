# GitHub 仓库体检工具

一个本地运行的 GitHub 仓库分析 Web 应用。输入公开 GitHub 仓库地址后，系统会读取 GitHub 元数据、clone 仓库到本地临时目录、执行只读代码分析，并用可视化方式展示基础规则评分和 AI Agent 深度评分。

![Vue](https://img.shields.io/badge/Vue-3-42b883)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![SQLite](https://img.shields.io/badge/SQLite-local-336791)
![LangGraph](https://img.shields.io/badge/LangGraph-Agent-1f6feb)

## 功能概览

- 公开 GitHub 仓库 URL 分析
- 可选 GitHub Token，提高 GitHub API 限额
- GitHub Token 连通性测试
- AI 配置连通性测试：`base_url`、`api_key`、`model`
- GitHub Star、Fork、Issue、语言分布等指标展示
- 基础规则评分：本地只读扫描 + 规则证据链
- AI Agent 深度评分：模型自主调用只读工具探索仓库
- Agent 执行观察台：展示当前节点、工具调用和分析进度
- 最近分析列表和报告删除
- SQLite 本地报告记录

## 技术栈

前端：

- Vue 3
- Vite
- TypeScript
- ECharts
- lucide-vue-next

后端：

- FastAPI
- HTTPX
- Pydantic
- LangGraph
- SQLite

数据源：

- GitHub REST API
- 本地 `git clone` 后的只读仓库扫描
- OpenAI Chat Completions 兼容 AI 接口

## 评分说明

### 基础规则评分

基础规则评分是核心 100 分，不直接使用 Star、Fork 等社区热度。系统会 clone 仓库到本地临时目录，并通过只读工具收集证据：

- `list_dir`：查看目录结构
- `read_file`：读取关键文件片段
- `grep`：搜索测试、安全、维护性等模式
- `find_files`：查找 Dockerfile、环境示例、测试文件等

核心维度：

| 维度 | 分值 | 关注点 |
| --- | ---: | --- |
| 架构清晰度 | 20 | 入口文件、目录组织、模块边界、分层结构 |
| 工程完整度 | 20 | 依赖声明、构建脚本、CI、容器化、环境示例 |
| 测试质量 | 15 | 测试文件、测试语法、测试脚本、CI 测试链路 |
| 文档质量 | 15 | README、docs、安装运行说明、许可证、贡献说明 |
| 安全风险 | 15 | 疑似密钥、危险执行调用、敏感文件、安全文档 |
| 可维护性 | 15 | 文件规模、TODO/FIXME、分层证据、测试和文档支撑 |

### AI Agent 深度评分

AI Agent 深度评分是独立参考分，不参与核心综合分。它会先自主探索仓库，再参考基础规则评分提供的“侦查地图”补充证据。侦查地图只包含证据线索、候选路径和缺口提示，不包含基础规则分数，避免 AI 被规则分锚定。

AI Agent 的流程大致是：

1. `repo_indexer`：读取本地仓库索引
2. `project_classifier`：识别项目类型和技术栈
3. `rubric_selector`：选择评分 Rubric
4. `evidence_explorer_loop`：自主调用只读工具收集证据
5. `evidence_curator`：整理证据
6. `dimension_judges`：多个维度评委打分
7. `critic_review`：复核证据不足和过高评分
8. `score_calibrator`：校准分数
9. `final_report`：生成最终 AI 审阅报告

AI Agent 具备节点级 fallback、JSON 修复、瞬时 HTTP 重试、最大探索步数限制和重复工具调用拦截。

## 安全边界

- 不执行被分析仓库中的任何代码
- clone 后只做本地只读分析
- 分析完成后清理临时 clone 目录
- GitHub Token 不写入 SQLite
- AI API Key 不写入 SQLite
- `backend/data/`、`backend/workspaces/`、`.env`、`node_modules`、`dist` 已在 `.gitignore` 中忽略

## 环境要求

请先安装：

- Git
- Python 3.11 或更高版本
- Node.js 20 或更高版本

推荐使用 PowerShell、Windows Terminal、macOS Terminal 或 Linux shell。

## 快速开始

### 1. Clone 仓库

```bash
git clone https://github.com/wuxinsu48-collab/github-repo-health-analyzer.git
cd github-repo-health-analyzer
```

### 2. 启动后端

Windows PowerShell：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

macOS / Linux：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

后端默认地址：

```text
http://127.0.0.1:8000
```

健康检查：

```text
http://127.0.0.1:8000/api/health
```

### 3. 启动前端

另开一个终端：

```bash
cd frontend
npm install
npm run dev
```

前端默认地址：

```text
http://127.0.0.1:5173
```

如果你的后端不是运行在 `http://127.0.0.1:8000`，可以在前端目录创建 `.env.local`：

```env
VITE_API_BASE=http://127.0.0.1:8000
```

## 页面使用方式

1. 在“仓库 URL”中输入公开 GitHub 仓库地址，例如：

   ```text
   https://github.com/openai/codex
   ```

2. 可选填写 GitHub Token，并点击测试按钮确认可用。

3. 如果需要 AI Agent 深度评分，填写：

   - `base_url`
   - `api_key`
   - `model name`

   示例：

   ```text
   base_url: https://api.openai.com/v1
   model: gpt-4.1-mini
   ```

   或使用其他 OpenAI Chat Completions 兼容服务。

4. 点击“开始体检”。

5. 右侧 Agent 观察台会展示 GitHub 元数据读取、clone、本地索引、基础规则评分、AI Agent 深度评分等执行状态。

6. 报告生成后，可以在“基础规则评分”和“AI Agent 深度评分”两个标签页中查看结果。

## 常见问题

### 一定要填 GitHub Token 吗？

不一定。公开仓库可以不填。但 GitHub 未认证请求限额较低，分析较多仓库时建议填写 Token。

### 一定要填 AI 配置吗？

不一定。不填 AI 配置时，系统仍会生成基础规则评分。填写 AI 配置后，会额外生成 AI Agent 深度评分。

### AI 接口需要兼容什么格式？

当前后端按 OpenAI Chat Completions 兼容接口调用：

```text
POST {base_url}/chat/completions
```

返回结构需要包含：

```json
{
  "choices": [
    {
      "message": {
        "content": "..."
      }
    }
  ]
}
```

### 为什么 AI Agent 有时失败？

常见原因：

- `base_url` 不正确
- `api_key` 无效
- `model name` 不存在
- 服务不支持 JSON object 输出
- 网络超时或服务限流

项目已经加入 JSON 修复、瞬时重试和节点级 fallback，但如果服务长期不可用，AI Agent 深度评分会失败，基础规则评分仍然可用。

### 分析仓库会不会留下 clone 文件？

默认不会。后端会 clone 到 `backend/workspaces/` 下的临时目录，分析完成后清理。

## 本地验证

后端测试：

```bash
cd backend
python -m pytest
```

前端构建：

```bash
cd frontend
npm run build
```

## 项目结构

```text
.
├── backend
│   ├── app
│   │   ├── main.py
│   │   ├── models.py
│   │   └── services
│   │       ├── agent_scoring.py
│   │       ├── deep_analysis.py
│   │       ├── github.py
│   │       ├── repo_indexer.py
│   │       ├── repo_tools.py
│   │       └── repo_workspace.py
│   ├── requirements.txt
│   └── tests
├── frontend
│   ├── src
│   │   ├── App.vue
│   │   ├── api.ts
│   │   ├── style.css
│   │   └── types.ts
│   └── package.json
└── README.md
```

## 开发备注

- 核心分来自基础规则评分。
- AI Agent 深度评分是独立参考，不参与核心综合分。
- Star、Fork、Issue 等社区数据只作为背景信息，不直接影响核心分。
- SQLite 数据库在本地自动创建，默认位于 `backend/data/`。

