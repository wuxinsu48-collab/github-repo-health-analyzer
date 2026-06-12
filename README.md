# GitHub 仓库体检工具

一个本地运行的 GitHub 仓库分析 Web 应用。用户输入公开 GitHub 仓库 URL 后，后端通过 GitHub REST API 采集数据，计算规则基础分，可选调用 OpenAI 兼容接口生成中文 AI 评分解释，并把每次分析保存到 SQLite。

## 技术栈

- 前端：Vue 3 + Vite + TypeScript + ECharts
- 后端：FastAPI + HTTPX + Pydantic
- 数据库：SQLite
- 数据源：GitHub REST API
- AI：页面填写 `base_url`、`api_key`、`model name`

## 启动后端

```powershell
cd C:\Users\31753\Desktop\github_analysis\backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## 启动前端

```powershell
cd C:\Users\31753\Desktop\github_analysis\frontend
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173
```

## 已实现功能

- 公开 GitHub 仓库 URL 分析
- 可选 GitHub Token
- GitHub 连接测试按钮
- AI `base_url`、`api_key`、`model name`
- AI 连接测试按钮
- 规则基础分
- AI 解释评分
- 综合分：`规则分 * 0.7 + AI 分 * 0.3`
- Star、Fork、Issue、最近 Push 等指标展示
- 编程语言分布图
- 评分维度图
- 仓库结构信号
- 风险标记
- 简单历史报告列表
- SQLite 报告记录

## 安全约定

- GitHub Token 不写入 SQLite。
- AI API Key 不写入 SQLite。
- SQLite 只保存仓库指标、规则分、AI 评分结果和报告内容。

## 验证命令

```powershell
cd C:\Users\31753\Desktop\github_analysis\backend
python -m pytest -q
```

```powershell
cd C:\Users\31753\Desktop\github_analysis\frontend
npm run build
```

