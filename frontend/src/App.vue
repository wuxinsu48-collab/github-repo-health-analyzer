<script setup lang="ts">
import { BarChart, GaugeChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import {
  Activity,
  AlertTriangle,
  BookOpen,
  Bot,
  CheckCircle2,
  ExternalLink,
  Github,
  History,
  Loader2,
  Play,
  RefreshCw,
  ShieldCheck,
  Star,
  Trash2,
  X,
} from 'lucide-vue-next'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { deleteReport, getAnalyzeJob, getReport, listReports, startAnalyzeJob, testAi, testGitHub } from './api'
import type {
  AnalysisJobResponse,
  AnalysisJobEvent,
  AnalysisJobStep,
  ConnectionTestResponse,
  ExplorationNote,
  RecentReport,
  ReportResponse,
  ScoreDimension,
} from './types'

use([BarChart, GaugeChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

type DrawerEvent = AnalysisJobEvent & { stepLabel: string }

const repoUrl = ref('')
const githubToken = ref('')
const aiBaseUrl = ref('https://api.openai.com/v1')
const aiApiKey = ref('')
const aiModel = ref('')

const reports = ref<RecentReport[]>([])
const currentReport = ref<ReportResponse | null>(null)
const isAnalyzing = ref(false)
const isTestingGitHub = ref(false)
const isTestingAi = ref(false)
const githubStatus = ref<ConnectionTestResponse | null>(null)
const aiStatus = ref<ConnectionTestResponse | null>(null)
const errorMessage = ref('')
const showScoringGuide = ref(false)
const currentJob = ref<AnalysisJobResponse | null>(null)
const deletingReportId = ref<number | null>(null)
let jobPollTimer: number | null = null

const scoreChartEl = ref<HTMLDivElement | null>(null)
const languageChartEl = ref<HTMLDivElement | null>(null)
const dimensionChartEl = ref<HTMLDivElement | null>(null)
let scoreChart: ECharts | null = null
let languageChart: ECharts | null = null
let dimensionChart: ECharts | null = null

const repo = computed(() => currentReport.value?.payload.evidence.repo)
const coreScore = computed(() => currentReport.value?.payload.core_score)
const deepAiAssessment = computed(() => currentReport.value?.payload.deep_ai_assessment)
const localIndex = computed(() => currentReport.value?.payload.local_index)
const explorationNotes = computed<ExplorationNote[]>(() => currentReport.value?.payload.exploration_notes ?? [])
const suitability = computed(() => currentReport.value?.payload.suitability)

const dimensionOrder = ['architecture', 'engineering', 'testing', 'documentation', 'security', 'maintainability']
const dimensionLabels: Record<string, string> = {
  architecture: '架构清晰度',
  engineering: '工程完整度',
  testing: '测试质量',
  documentation: '文档质量',
  security: '安全风险',
  maintainability: '可维护性',
}

const aiFieldLabels: Record<string, string> = {
  base_url: 'base_url',
  api_key: 'api_key',
  model: 'model name',
}

const suitabilityItems = computed(() => {
  const scores = suitability.value
  if (!scores) return []
  return [
    { key: 'learning', label: '学习参考', value: scores.learning, note: scores.notes.learning },
    { key: 'secondary', label: '二开参考', value: scores.secondary_development, note: scores.notes.secondary_development },
    { key: 'production', label: '生产参考', value: scores.production, note: scores.notes.production },
  ]
})

const scoreTone = computed(() => {
  const score = currentReport.value?.final_score ?? 0
  if (score >= 80) return 'good'
  if (score >= 60) return 'watch'
  return 'risk'
})

const jobSteps = computed<AnalysisJobStep[]>(() => currentJob.value?.steps ?? [])
const completedStepCount = computed(() => jobSteps.value.filter((step) => step.status === 'completed' || step.status === 'skipped').length)
const drawerEvents = computed<DrawerEvent[]>(() =>
  jobSteps.value.flatMap((step) =>
    (step.events ?? []).map((event) => ({
      ...event,
      stepLabel: step.label,
    })),
  ),
)
const toolEvents = computed(() => drawerEvents.value.filter((event) => event.kind === 'tool'))
const activeAgentEvent = computed(() => {
  const runningEvent = [...drawerEvents.value].reverse().find((event) => event.status === 'running')
  if (runningEvent) return runningEvent
  return [...drawerEvents.value].reverse()[0] ?? null
})
const activeStep = computed(() => {
  const runningStep = jobSteps.value.find((step) => step.status === 'running')
  if (runningStep) return runningStep
  return [...jobSteps.value].reverse().find((step) => step.status === 'completed' || step.status === 'failed' || step.status === 'skipped') ?? null
})
const drawerEvidenceCount = computed(() => currentReport.value?.payload.evidence_pool?.length ?? 0)
const drawerExplorationCount = computed(() => currentReport.value?.payload.exploration_notes?.length ?? drawerEvents.value.length)

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '等待',
    running: '运行中',
    completed: '完成',
    failed: '失败',
    skipped: '跳过',
  }
  return labels[status] ?? status
}

function eventKindLabel(event: AnalysisJobEvent): string {
  return event.kind === 'tool' ? '工具' : '节点'
}

function formatNumber(value: number | undefined): string {
  return new Intl.NumberFormat('zh-CN').format(value ?? 0)
}

function formatDate(value: string | undefined | null): string {
  if (!value) return '-'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(value))
}

function setChart(container: HTMLDivElement | null, chart: ECharts | null): ECharts | null {
  if (!container) return null
  chart?.dispose()
  return init(container)
}

function statusField(status: ConnectionTestResponse | null): string {
  const field = status?.details?.field
  return typeof field === 'string' ? field : ''
}

function aiInputClass(field: string): Record<string, boolean> {
  return { invalid: statusField(aiStatus.value) === field }
}

function buildAiConfig() {
  return {
    baseUrl: aiBaseUrl.value.trim(),
    apiKey: aiApiKey.value.trim(),
    model: aiModel.value.trim(),
  }
}

function getMissingAiFields(): string[] {
  const config = buildAiConfig()
  const missing: string[] = []
  if (!config.baseUrl) missing.push('base_url')
  if (!config.apiKey) missing.push('api_key')
  if (!config.model) missing.push('model')
  return missing
}

function formatAiStatusMessage(response: ConnectionTestResponse): string {
  const field = statusField(response)
  if (response.ok || !field) return response.message
  return `${aiFieldLabels[field] ?? field}：${response.message}`
}

function clearJobPolling() {
  if (jobPollTimer !== null) {
    window.clearInterval(jobPollTimer)
    jobPollTimer = null
  }
}

function dimensionEntries(dimensions: Record<string, ScoreDimension> | undefined) {
  if (!dimensions) return []
  return dimensionOrder
    .map((key) => ({ key, label: dimensionLabels[key], dimension: dimensions[key] }))
    .filter((item) => item.dimension)
}

function renderCharts() {
  const report = currentReport.value
  if (!report) return

  nextTick(() => {
    scoreChart = setChart(scoreChartEl.value, scoreChart)
    languageChart = setChart(languageChartEl.value, languageChart)
    dimensionChart = setChart(dimensionChartEl.value, dimensionChart)

    scoreChart?.setOption({
      series: [
        {
          type: 'gauge',
          min: 0,
          max: 100,
          radius: '88%',
          progress: { show: true, width: 14, itemStyle: { color: '#1f8a70' } },
          axisLine: { lineStyle: { width: 14, color: [[0.55, '#d95d39'], [0.8, '#f2a541'], [1, '#1f8a70']] } },
          axisTick: { show: false },
          splitLine: { length: 8, lineStyle: { color: '#667085', width: 1 } },
          axisLabel: { color: '#667085', distance: 18 },
          pointer: { width: 4, itemStyle: { color: '#384252' } },
          detail: {
            valueAnimation: true,
            formatter: '{value}',
            color: '#202733',
            fontSize: 34,
            fontWeight: 700,
          },
          data: [{ value: report.final_score }],
        },
      ],
    })

    const languages = report.payload.evidence.languages
    const languageData = Object.entries(languages).map(([name, value]) => ({ name, value }))
    languageChart?.setOption({
      tooltip: { trigger: 'item' },
      legend: { bottom: 0, type: 'scroll' },
      series: [
        {
          type: 'pie',
          radius: ['44%', '72%'],
          center: ['50%', '45%'],
          avoidLabelOverlap: true,
          label: { formatter: '{b}\n{d}%', color: '#384252' },
          data: languageData.length ? languageData : [{ name: '暂无语言数据', value: 1 }],
        },
      ],
      color: ['#1f8a70', '#2f80ed', '#f2a541', '#d95d39', '#7b61ff', '#6a994e', '#8d6e63'],
    })

    const dimensions = dimensionEntries(report.payload.core_score?.dimensions)
    dimensionChart?.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: (items: unknown) => {
          const item = Array.isArray(items) ? items[0] : null
          const index = item && typeof item === 'object' && 'dataIndex' in item ? Number(item.dataIndex) : 0
          const current = dimensions[index]
          if (!current) return ''
          return `${current.label}<br/>${current.dimension.score} / ${current.dimension.max_score}<br/>${current.dimension.reason}`
        },
      },
      grid: { left: 40, right: 18, top: 22, bottom: 44 },
      xAxis: {
        type: 'category',
        data: dimensions.map((item) => item.label),
        axisLabel: { color: '#596273', interval: 0 },
      },
      yAxis: { type: 'value', min: 0, max: 20, axisLabel: { color: '#596273' } },
      series: [
        {
          type: 'bar',
          data: dimensions.map((item) => item.dimension.score),
          barWidth: 28,
          itemStyle: {
            color: '#2f80ed',
            borderRadius: [4, 4, 0, 0],
          },
        },
      ],
    })
  })
}

async function refreshReports() {
  reports.value = await listReports()
}

async function onTestGitHub() {
  isTestingGitHub.value = true
  githubStatus.value = null
  errorMessage.value = ''
  try {
    githubStatus.value = await testGitHub(githubToken.value)
  } catch (error) {
    githubStatus.value = { ok: false, message: error instanceof Error ? error.message : 'GitHub 测试失败', details: {} }
  } finally {
    isTestingGitHub.value = false
  }
}

async function onTestAi() {
  isTestingAi.value = true
  aiStatus.value = null
  errorMessage.value = ''
  const missing = getMissingAiFields()
  if (missing.length) {
    aiStatus.value = {
      ok: false,
      message: `请先填写 ${missing.map((field) => aiFieldLabels[field] ?? field).join('、')}`,
      details: { field: missing[0] },
    }
    isTestingAi.value = false
    return
  }

  const config = buildAiConfig()
  try {
    aiStatus.value = await testAi(config.baseUrl, config.apiKey, config.model)
  } catch (error) {
    aiStatus.value = { ok: false, message: error instanceof Error ? error.message : 'AI 测试失败', details: {} }
  } finally {
    isTestingAi.value = false
  }
}

async function onAnalyze() {
  isAnalyzing.value = true
  errorMessage.value = ''
  clearJobPolling()
  currentJob.value = null
  try {
    const aiConfig = buildAiConfig()
    const aiReady = aiConfig.baseUrl && aiConfig.apiKey && aiConfig.model
    const hasPartialAiConfig = aiConfig.apiKey || aiConfig.model
    if (hasPartialAiConfig && !aiReady) {
      const missing = getMissingAiFields()
      throw new Error(`AI 配置不完整：请补充 ${missing.map((field) => aiFieldLabels[field] ?? field).join('、')}`)
    }

    const job = await startAnalyzeJob({
      repo_url: repoUrl.value,
      github_token: githubToken.value || undefined,
      ai: aiReady
        ? {
            base_url: aiConfig.baseUrl,
            api_key: aiConfig.apiKey,
            model: aiConfig.model,
          }
        : undefined,
    })
    currentJob.value = job
    jobPollTimer = window.setInterval(pollCurrentJob, 1000)
    await pollCurrentJob()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分析失败'
    isAnalyzing.value = false
    clearJobPolling()
  }
}

async function pollCurrentJob() {
  const jobId = currentJob.value?.job_id
  if (!jobId) return

  try {
    const job = await getAnalyzeJob(jobId)
    currentJob.value = job
    if (job.status === 'completed') {
      clearJobPolling()
      isAnalyzing.value = false
      if (job.report) {
        currentReport.value = job.report
        await refreshReports()
      }
    }
    if (job.status === 'failed') {
      clearJobPolling()
      isAnalyzing.value = false
      errorMessage.value = job.error || '分析任务失败'
    }
  } catch (error) {
    clearJobPolling()
    isAnalyzing.value = false
    errorMessage.value = error instanceof Error ? error.message : '读取分析进度失败'
  }
}

async function openReport(id: number) {
  errorMessage.value = ''
  currentReport.value = await getReport(id)
}

async function onDeleteReport(report: RecentReport) {
  const ok = window.confirm(`删除报告 ${report.repo_full_name}？`)
  if (!ok) return

  deletingReportId.value = report.id
  errorMessage.value = ''
  try {
    await deleteReport(report.id)
    if (currentReport.value?.id === report.id) {
      currentReport.value = null
    }
    await refreshReports()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '删除报告失败'
  } finally {
    deletingReportId.value = null
  }
}

function resizeCharts() {
  scoreChart?.resize()
  languageChart?.resize()
  dimensionChart?.resize()
}

watch(() => currentReport.value?.id, renderCharts)

onMounted(async () => {
  await refreshReports()
  window.addEventListener('resize', resizeCharts)
})

onUnmounted(() => {
  clearJobPolling()
  window.removeEventListener('resize', resizeCharts)
  scoreChart?.dispose()
  languageChart?.dispose()
  dimensionChart?.dispose()
})
</script>

<template>
  <main class="app-shell">
    <aside class="workspace-panel">
      <section class="brand">
        <div class="brand-mark">
          <Github :size="24" />
        </div>
        <div>
          <h1>GitHub 仓库体检</h1>
          <p>只读深度分析 + AI 独立审阅</p>
        </div>
      </section>

      <button class="guide-button" type="button" @click="showScoringGuide = true">
        <BookOpen :size="17" />
        评分说明
      </button>

      <form class="analysis-form" @submit.prevent="onAnalyze">
        <label>
          仓库 URL
          <input v-model="repoUrl" placeholder="https://github.com/owner/repo" required />
        </label>

        <div class="field-with-action">
          <label>
            GitHub Token
            <input v-model="githubToken" type="password" placeholder="可选，提高 GitHub API 限额" />
          </label>
          <button class="icon-button" type="button" title="测试 GitHub 连接" :disabled="isTestingGitHub" @click="onTestGitHub">
            <Loader2 v-if="isTestingGitHub" class="spin" :size="18" />
            <ShieldCheck v-else :size="18" />
          </button>
        </div>

        <div v-if="githubStatus" class="status-line" :class="{ ok: githubStatus.ok, fail: !githubStatus.ok }">
          <CheckCircle2 v-if="githubStatus.ok" :size="16" />
          <AlertTriangle v-else :size="16" />
          <span>{{ githubStatus.message }}</span>
        </div>

        <div class="ai-config">
          <div class="section-label">
            <Bot :size="16" />
            AI 配置
          </div>
          <label>
            base_url
            <input v-model="aiBaseUrl" :class="aiInputClass('base_url')" placeholder="https://api.openai.com/v1" />
          </label>
          <label>
            api_key
            <input v-model="aiApiKey" :class="aiInputClass('api_key')" type="password" placeholder="本次请求使用，不入库" />
          </label>
          <label>
            model name
            <input v-model="aiModel" :class="aiInputClass('model')" placeholder="gpt-4.1-mini / deepseek-chat / qwen-plus" />
          </label>
          <button class="secondary-button" type="button" :disabled="isTestingAi" @click="onTestAi">
            <Loader2 v-if="isTestingAi" class="spin" :size="17" />
            <Play v-else :size="17" />
            测试 AI
          </button>
        </div>

        <div v-if="aiStatus" class="status-line" :class="{ ok: aiStatus.ok, fail: !aiStatus.ok }">
          <CheckCircle2 v-if="aiStatus.ok" :size="16" />
          <AlertTriangle v-else :size="16" />
          <span>{{ formatAiStatusMessage(aiStatus) }}</span>
        </div>

        <button class="primary-button" type="submit" :disabled="isAnalyzing || !repoUrl">
          <Loader2 v-if="isAnalyzing" class="spin" :size="18" />
          <Activity v-else :size="18" />
          开始体检
        </button>

        <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
      </form>

      <section class="history-panel">
        <div class="section-title">
          <History :size="17" />
          最近分析
          <button class="ghost-button" type="button" title="刷新历史" @click="refreshReports">
            <RefreshCw :size="15" />
          </button>
        </div>
        <div
          v-for="report in reports"
          :key="report.id"
          class="history-row"
        >
          <button
            class="history-item"
            :class="{ active: currentReport?.id === report.id }"
            type="button"
            @click="openReport(report.id)"
          >
            <span>{{ report.repo_full_name }}</span>
            <strong>{{ report.final_score }}</strong>
          </button>
          <button
            class="icon-button delete-history-button"
            type="button"
            title="删除报告"
            :disabled="deletingReportId === report.id"
            @click="onDeleteReport(report)"
          >
            <Loader2 v-if="deletingReportId === report.id" class="spin" :size="15" />
            <Trash2 v-else :size="15" />
          </button>
        </div>
        <p v-if="!reports.length" class="empty-note">暂无历史报告</p>
      </section>
    </aside>

    <section class="dashboard">
      <div v-if="!currentReport" class="empty-state">
        <Github :size="42" />
        <h2>输入公开仓库 URL 后开始体检</h2>
        <p>后端会 clone 仓库到本地工作区，由 LangGraph 编排只读工具按需探索配置、文档、测试、源码和安全线索，再生成核心 100 分报告。</p>
      </div>

      <template v-else>
        <header class="repo-header">
          <div>
            <div class="repo-kicker">报告 #{{ currentReport.id }}</div>
            <h2>{{ currentReport.repo_full_name }}</h2>
            <p>{{ repo?.description || '这个仓库没有填写描述。' }}</p>
            <div class="repo-links">
              <a :href="currentReport.repo_url" target="_blank" rel="noreferrer">
                GitHub
                <ExternalLink :size="14" />
              </a>
              <span>生成于 {{ formatDate(currentReport.created_at) }}</span>
            </div>
            <p v-if="currentReport.payload.github_warning" class="warning-message">
              {{ currentReport.payload.github_warning }}
            </p>
          </div>
          <div class="score-badge" :class="scoreTone">
            <span>核心综合分</span>
            <strong>{{ currentReport.final_score }}</strong>
            <div class="score-breakdown">
              <div>
                <span>核心分</span>
                <b>{{ coreScore?.score ?? currentReport.final_score }}</b>
              </div>
              <div>
                <span>AI 独立分</span>
                <b>{{ deepAiAssessment?.score ?? '未生成' }}</b>
              </div>
              <p>AI 独立分按同一六维标准审阅证据包，不参与核心分公式。</p>
            </div>
          </div>
        </header>

        <section class="metric-grid">
          <div class="metric-tile">
            <Star :size="18" />
            <span>Stars</span>
            <strong>{{ formatNumber(repo?.stars) }}</strong>
          </div>
          <div class="metric-tile">
            <Github :size="18" />
            <span>Forks</span>
            <strong>{{ formatNumber(repo?.forks) }}</strong>
          </div>
          <div class="metric-tile">
            <AlertTriangle :size="18" />
            <span>Open Issues</span>
            <strong>{{ formatNumber(repo?.open_issues) }}</strong>
          </div>
          <div class="metric-tile">
            <Activity :size="18" />
            <span>最近 Push</span>
            <strong>{{ formatDate(repo?.pushed_at) }}</strong>
          </div>
        </section>

        <section class="suitability-grid">
          <div v-for="item in suitabilityItems" :key="item.key" class="suitability-tile">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
            <p>{{ item.note }}</p>
          </div>
        </section>

        <section class="chart-grid">
          <div class="chart-panel">
            <div class="panel-heading">
              <h3>核心评分</h3>
              <span>总分 100，不含社区热度</span>
            </div>
            <div ref="scoreChartEl" class="chart chart-score"></div>
          </div>

          <div class="chart-panel">
            <div class="panel-heading">
              <h3>语言分布</h3>
              <span>{{ Object.keys(currentReport.payload.evidence.languages).length }} 种语言</span>
            </div>
            <div ref="languageChartEl" class="chart"></div>
          </div>

          <div class="chart-panel wide">
            <div class="panel-heading">
              <h3>六维核心得分</h3>
              <span>20 / 20 / 15 / 15 / 15 / 15</span>
            </div>
            <div ref="dimensionChartEl" class="chart chart-wide"></div>
          </div>
        </section>

        <section class="details-grid">
          <div class="info-panel">
            <h3>本地结构信号</h3>
            <div class="signal-list">
              <span :class="{ active: !!localIndex?.ci_files.length }">CI</span>
              <span :class="{ active: !!localIndex?.test_files.length }">Tests</span>
              <span :class="{ active: !!localIndex?.documentation_files.length }">Docs</span>
              <span :class="{ active: !!repo?.license }">License</span>
              <span :class="{ active: !!localIndex?.security_files.length }">Security</span>
            </div>
            <div class="tree-preview">
              <code v-for="path in localIndex?.tree.slice(0, 14)" :key="path">{{ path }}</code>
            </div>
          </div>

          <div class="info-panel">
            <h3>风险与建议</h3>
            <ul v-if="coreScore?.risk_flags.length" class="plain-list risk-list">
              <li v-for="item in coreScore.risk_flags" :key="item">{{ item }}</li>
            </ul>
            <p v-else class="positive-note">核心评分没有发现明显高风险标记。</p>
            <ul class="plain-list">
              <li v-for="item in currentReport.payload.recommendations" :key="item">{{ item }}</li>
            </ul>
          </div>
        </section>

        <section class="dimension-evidence">
          <div class="panel-heading">
            <h3>评分证据</h3>
            <span>每个维度保留可追溯片段</span>
          </div>
          <div class="evidence-grid">
            <article v-for="item in dimensionEntries(coreScore?.dimensions)" :key="item.key" class="evidence-card">
              <div class="evidence-card-head">
                <h4>{{ item.label }}</h4>
                <strong>{{ item.dimension.score }} / {{ item.dimension.max_score }}</strong>
              </div>
              <p>{{ item.dimension.reason }}</p>
              <code v-for="evidence in item.dimension.evidence" :key="`${item.key}-${evidence.path}-${evidence.label}`">
                {{ evidence.path || evidence.label }}{{ evidence.excerpt ? `：${evidence.excerpt}` : '' }}
              </code>
            </article>
          </div>
        </section>

        <section class="agent-exploration-panel">
          <div class="panel-heading">
            <h3>Agent 探索过程</h3>
            <span>{{ explorationNotes.length }} 步本地只读探索</span>
          </div>
          <ol v-if="explorationNotes.length" class="exploration-list">
            <li v-for="note in explorationNotes" :key="`${note.step}-${note.action}-${note.target}`" class="exploration-item">
              <div class="exploration-step">
                <span>{{ note.step }}</span>
              </div>
              <div class="exploration-body">
                <div class="exploration-meta">
                  <strong>{{ note.action }}</strong>
                  <code>{{ note.target }}</code>
                </div>
                <p>{{ note.thought }}</p>
                <p class="exploration-result">{{ note.result_summary }}</p>
              </div>
            </li>
          </ol>
          <p v-else class="empty-note">这份历史报告还没有 Agent 探索过程。</p>
        </section>

        <section class="ai-panel">
          <div class="panel-heading">
            <h3>AI 独立审阅</h3>
            <span v-if="deepAiAssessment">AI {{ deepAiAssessment.score }} / 100，置信度 {{ deepAiAssessment.confidence }}</span>
            <span v-else>未生成</span>
          </div>

          <p class="ai-score-note">
            AI 独立分由模型读取本地证据包后给出：目录树、LangGraph 探索过程、manifest/CI/测试/文档/安全信号、少量脱敏源码片段，以及核心评分摘要。它按架构 20、工程 20、测试 15、文档 15、安全 15、可维护性 15 的同一套标准独立判断。
          </p>

          <p v-if="currentReport.payload.ai_error" class="error-message">{{ currentReport.payload.ai_error }}</p>

          <template v-if="deepAiAssessment">
            <p class="ai-summary">{{ deepAiAssessment.summary }}</p>
            <div class="ai-columns">
              <div>
                <h4>优势</h4>
                <ul class="plain-list">
                  <li v-for="item in deepAiAssessment.strengths" :key="item">{{ item }}</li>
                </ul>
              </div>
              <div>
                <h4>风险</h4>
                <ul class="plain-list">
                  <li v-for="item in deepAiAssessment.risks" :key="item">{{ item }}</li>
                </ul>
              </div>
              <div>
                <h4>建议</h4>
                <ul class="plain-list">
                  <li v-for="item in deepAiAssessment.recommendations" :key="item">{{ item }}</li>
                </ul>
              </div>
            </div>
            <div class="ai-review-list">
              <div v-for="(review, key) in deepAiAssessment.dimension_reviews" :key="key">
                <strong>{{ dimensionLabels[key] ?? key }}</strong>
                <p>{{ review }}</p>
              </div>
            </div>
          </template>
          <p v-else class="empty-note">填写 AI 配置后，分析时会让 AI 基于本地证据包做独立中文审阅。</p>
        </section>
      </template>
    </section>

    <aside class="agent-drawer">
      <div class="agent-console-head">
        <div>
          <h2>Agent 执行观察台</h2>
          <p v-if="currentJob">任务 {{ currentJob.job_id.slice(0, 8) }} · {{ statusLabel(currentJob.status) }}</p>
          <p v-else>等待开始体检</p>
        </div>
        <span class="agent-status-pill" :class="currentJob?.status ?? 'idle'">
          <Loader2 v-if="isAnalyzing" class="spin" :size="15" />
          {{ currentJob ? statusLabel(currentJob.status) : '空闲' }}
        </span>
      </div>

      <div v-if="currentJob" class="agent-console">
        <section class="agent-overview">
          <div>
            <span>阶段</span>
            <strong>{{ completedStepCount }} / {{ jobSteps.length }}</strong>
          </div>
          <div>
            <span>工具调用</span>
            <strong>{{ toolEvents.length }}</strong>
          </div>
          <div>
            <span>证据</span>
            <strong>{{ drawerEvidenceCount }}</strong>
          </div>
        </section>

        <section class="agent-focus">
          <div class="agent-focus-label">当前活动</div>
          <strong>{{ activeAgentEvent ? `${activeAgentEvent.stepLabel} · ${eventKindLabel(activeAgentEvent)} · ${activeAgentEvent.label}` : activeStep?.label }}</strong>
          <code v-if="activeAgentEvent?.target">{{ activeAgentEvent.target }}</code>
          <p>{{ activeAgentEvent?.detail || activeStep?.detail || '等待下一步执行' }}</p>
        </section>

        <section class="agent-section">
          <div class="agent-section-title">
            <span>阶段时间线</span>
            <b>{{ statusLabel(activeStep?.status ?? 'pending') }}</b>
          </div>
          <ol class="agent-stage-list">
            <li v-for="step in jobSteps" :key="step.id" :class="['agent-stage', step.status]">
              <span class="agent-stage-dot"></span>
              <div class="agent-stage-main">
                <div class="agent-stage-title">
                  <strong>{{ step.label }}</strong>
                  <span>{{ statusLabel(step.status) }}</span>
                </div>
                <p>{{ step.detail || statusLabel(step.status) }}</p>
              </div>
            </li>
          </ol>
        </section>

        <section class="agent-section">
          <div class="agent-section-title">
            <span>内部执行事件</span>
            <b>{{ drawerEvents.length }} 条</b>
          </div>
          <ol v-if="drawerEvents.length" class="agent-event-list">
            <li v-for="event in drawerEvents" :key="`${event.stepLabel}-${event.id}`" :class="['agent-event', event.status, event.kind]">
              <span class="agent-event-dot"></span>
              <div class="agent-event-body">
                <div class="agent-event-title">
                  <strong>{{ event.stepLabel }} · {{ eventKindLabel(event) }} · {{ event.label }}</strong>
                  <span>{{ statusLabel(event.status) }}</span>
                </div>
                <code v-if="event.target">{{ event.target }}</code>
                <p>{{ event.detail || statusLabel(event.status) }}</p>
              </div>
            </li>
          </ol>
          <p v-else class="agent-muted">任务运行后，这里会显示 GitHub REST、clone、本地探索和 LangGraph 工具调用等内部动作。</p>
        </section>

        <section class="agent-section agent-evidence-link">
          <span>最终报告会复用同一批探索记录</span>
          <strong>{{ drawerExplorationCount }} 步探索</strong>
        </section>
      </div>
      <div v-else class="agent-empty">
        <Activity :size="28" />
        <p>开始体检后，这里会显示阶段时间线、LangGraph 内部节点、只读工具调用和当前正在查看的目标文件。</p>
      </div>
    </aside>

    <div v-if="showScoringGuide" class="modal-backdrop" @click="showScoringGuide = false">
      <section class="guide-modal" role="dialog" aria-modal="true" aria-label="评分说明" @click.stop>
        <div class="guide-modal-header">
          <div>
            <h2>评分说明</h2>
            <p>核心分来自 LangGraph 本地只读探索证据；AI 分是独立审阅，不再使用“规则分 + 修正值”。</p>
          </div>
          <button class="icon-button" type="button" title="关闭评分说明" @click="showScoringGuide = false">
            <X :size="18" />
          </button>
        </div>

        <div class="guide-content">
          <section>
            <h3>核心 100 分</h3>
            <p>系统 clone 公开仓库后，由 LangGraph 先规划探索，再调用 list_dir/read_file/grep/find_files 只读查看配置、README、测试、CI、源码层次和安全信号；索引摘要只做兜底，不安装依赖，不运行仓库代码。</p>
          </section>

          <section>
            <h3>六个维度</h3>
            <ul>
              <li>架构清晰度 20 分：源码目录、模块边界、配置和文档组织。</li>
              <li>工程完整度 20 分：依赖声明、构建配置、CI、锁文件、容器或工具配置。</li>
              <li>测试质量 15 分：测试文件、测试框架线索、CI 测试链路和测试/源码比例。</li>
              <li>文档质量 15 分：README、docs、LICENSE、贡献指南和安全说明。</li>
              <li>安全风险 15 分：疑似密钥、敏感文件、许可证和 SECURITY.md。</li>
              <li>可维护性 15 分：文件规模、源码数量、大文件、测试和文档支撑。</li>
            </ul>
          </section>

          <section>
            <h3>参考分</h3>
            <p>学习、二开、生产使用三个适用性分不改变核心 100 分，只帮助用户判断这个仓库适合拿来做什么。</p>
          </section>

          <section>
            <h3>AI 独立分</h3>
            <p>AI 独立分不是“规则分 + 修正值”。系统会把本地只读证据包压缩给 AI，包括目录树、Agent 探索过程、关键配置、测试/CI/文档/安全信号、少量脱敏源码片段和核心评分摘要。AI 再按同样的六个维度独立给出 0-100 分、置信度和中文理由。这个分数只作为对照参考，不改变核心综合分。</p>
          </section>

          <section>
            <h3>社区元数据</h3>
            <p>Star、Fork、Issue、最近提交和 Release 只作为背景参考，不直接进入核心分，避免热门但工程质量一般的仓库被高估。</p>
          </section>
        </div>
      </section>
    </div>
  </main>
</template>
