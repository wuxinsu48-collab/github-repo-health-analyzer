<script setup lang="ts">
import { BarChart, GaugeChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import {
  Activity,
  AlertTriangle,
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
} from 'lucide-vue-next'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { analyzeRepo, getReport, listReports, testAi, testGitHub } from './api'
import type { ConnectionTestResponse, RecentReport, ReportResponse } from './types'

use([BarChart, GaugeChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

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

const scoreChartEl = ref<HTMLDivElement | null>(null)
const languageChartEl = ref<HTMLDivElement | null>(null)
const dimensionChartEl = ref<HTMLDivElement | null>(null)
let scoreChart: ECharts | null = null
let languageChart: ECharts | null = null
let dimensionChart: ECharts | null = null

const repo = computed(() => currentReport.value?.payload.evidence.repo)
const ruleScore = computed(() => currentReport.value?.payload.rule_score)
const aiAssessment = computed(() => currentReport.value?.payload.ai_assessment)
const configSummary = computed(() => currentReport.value?.payload.evidence.config_summary)

const scoreTone = computed(() => {
  const score = currentReport.value?.final_score ?? 0
  if (score >= 80) return 'good'
  if (score >= 60) return 'watch'
  return 'risk'
})

function formatNumber(value: number | undefined): string {
  return new Intl.NumberFormat('zh-CN').format(value ?? 0)
}

function formatDate(value: string | undefined): string {
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

    const dimensions = report.payload.rule_score.dimension_scores
    const labels: Record<string, string> = {
      popularity: '热度',
      activity: '活跃',
      community: '社区',
      engineering: '工程',
      risk: '风险',
    }
    dimensionChart?.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 40, right: 18, top: 22, bottom: 34 },
      xAxis: {
        type: 'category',
        data: Object.keys(dimensions).map((key) => labels[key] ?? key),
        axisLabel: { color: '#596273' },
      },
      yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: '#596273' } },
      series: [
        {
          type: 'bar',
          data: Object.values(dimensions),
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
  try {
    aiStatus.value = await testAi(aiBaseUrl.value, aiApiKey.value, aiModel.value)
  } catch (error) {
    aiStatus.value = { ok: false, message: error instanceof Error ? error.message : 'AI 测试失败', details: {} }
  } finally {
    isTestingAi.value = false
  }
}

async function onAnalyze() {
  isAnalyzing.value = true
  errorMessage.value = ''
  try {
    const aiReady = aiBaseUrl.value.trim() && aiApiKey.value.trim() && aiModel.value.trim()
    const report = await analyzeRepo({
      repo_url: repoUrl.value,
      github_token: githubToken.value || undefined,
      ai: aiReady
        ? {
            base_url: aiBaseUrl.value,
            api_key: aiApiKey.value,
            model: aiModel.value,
          }
        : undefined,
    })
    currentReport.value = report
    await refreshReports()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分析失败'
  } finally {
    isAnalyzing.value = false
  }
}

async function openReport(id: number) {
  errorMessage.value = ''
  currentReport.value = await getReport(id)
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
          <p>规则评分 + AI 解释评分</p>
        </div>
      </section>

      <form class="analysis-form" @submit.prevent="onAnalyze">
        <label>
          仓库 URL
          <input v-model="repoUrl" placeholder="https://github.com/owner/repo" required />
        </label>

        <div class="field-with-action">
          <label>
            GitHub Token
            <input v-model="githubToken" type="password" placeholder="可选" />
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
            <input v-model="aiBaseUrl" placeholder="https://api.openai.com/v1" />
          </label>
          <label>
            api_key
            <input v-model="aiApiKey" type="password" placeholder="本次请求使用，不入库" />
          </label>
          <label>
            model name
            <input v-model="aiModel" placeholder="gpt-4.1-mini / deepseek-chat / qwen-plus" />
          </label>
          <button class="secondary-button" type="button" :disabled="isTestingAi || !aiBaseUrl || !aiApiKey || !aiModel" @click="onTestAi">
            <Loader2 v-if="isTestingAi" class="spin" :size="17" />
            <Play v-else :size="17" />
            测试 AI
          </button>
        </div>

        <div v-if="aiStatus" class="status-line" :class="{ ok: aiStatus.ok, fail: !aiStatus.ok }">
          <CheckCircle2 v-if="aiStatus.ok" :size="16" />
          <AlertTriangle v-else :size="16" />
          <span>{{ aiStatus.message }}</span>
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
        <button
          v-for="report in reports"
          :key="report.id"
          class="history-item"
          :class="{ active: currentReport?.id === report.id }"
          type="button"
          @click="openReport(report.id)"
        >
          <span>{{ report.repo_full_name }}</span>
          <strong>{{ report.final_score }}</strong>
        </button>
        <p v-if="!reports.length" class="empty-note">暂无历史报告</p>
      </section>
    </aside>

    <section class="dashboard">
      <div v-if="!currentReport" class="empty-state">
        <Github :size="42" />
        <h2>输入公开仓库 URL 后开始体检</h2>
        <p>报告会展示 Star、Fork、语言分布、社区文件、工程结构、规则分和 AI 中文建议。</p>
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
          </div>
          <div class="score-badge" :class="scoreTone">
            <span>综合分</span>
            <strong>{{ currentReport.final_score }}</strong>
            <div class="score-breakdown">
              <div>
                <span>规则分</span>
                <b>{{ currentReport.rule_score }}</b>
              </div>
              <div>
                <span>AI 分</span>
                <b>{{ currentReport.ai_score ?? '未生成' }}</b>
              </div>
              <p v-if="currentReport.ai_score !== null">综合 = 规则分 * 70% + AI 分 * 30%</p>
              <p v-else>AI 未生成时，综合分 = 规则分</p>
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

        <section class="chart-grid">
          <div class="chart-panel">
            <div class="panel-heading">
              <h3>综合评分</h3>
              <span>规则 {{ currentReport.rule_score }} / AI {{ currentReport.ai_score ?? '-' }}</span>
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
              <h3>维度评分</h3>
              <span>热度 / 活跃 / 社区 / 工程 / 风险</span>
            </div>
            <div ref="dimensionChartEl" class="chart chart-wide"></div>
          </div>
        </section>

        <section class="details-grid">
          <div class="info-panel">
            <h3>仓库结构信号</h3>
            <div class="signal-list">
              <span :class="{ active: configSummary?.has_ci }">CI</span>
              <span :class="{ active: configSummary?.has_tests }">Tests</span>
              <span :class="{ active: configSummary?.has_docker }">Docker</span>
              <span :class="{ active: !!repo?.license }">License</span>
              <span :class="{ active: currentReport.payload.evidence.community.security }">Security</span>
            </div>
            <div class="tree-preview">
              <code v-for="path in currentReport.payload.evidence.tree.slice(0, 12)" :key="path">{{ path }}</code>
            </div>
          </div>

          <div class="info-panel">
            <h3>风险标记</h3>
            <ul v-if="ruleScore?.risk_flags.length" class="plain-list risk-list">
              <li v-for="item in ruleScore.risk_flags" :key="item">{{ item }}</li>
            </ul>
            <p v-else class="positive-note">规则评分没有发现明显风险标记。</p>
          </div>
        </section>

        <section class="ai-panel">
          <div class="panel-heading">
            <h3>AI 体检报告</h3>
            <span v-if="aiAssessment">置信度 {{ aiAssessment.confidence }}</span>
            <span v-else>未生成</span>
          </div>

          <p v-if="currentReport.payload.ai_error" class="error-message">{{ currentReport.payload.ai_error }}</p>

          <template v-if="aiAssessment">
            <p class="ai-summary">{{ aiAssessment.summary }}</p>
            <div class="ai-columns">
              <div>
                <h4>优势</h4>
                <ul class="plain-list">
                  <li v-for="item in aiAssessment.strengths" :key="item">{{ item }}</li>
                </ul>
              </div>
              <div>
                <h4>风险</h4>
                <ul class="plain-list">
                  <li v-for="item in aiAssessment.risks" :key="item">{{ item }}</li>
                </ul>
              </div>
              <div>
                <h4>建议</h4>
                <ul class="plain-list">
                  <li v-for="item in aiAssessment.recommendations" :key="item">{{ item }}</li>
                </ul>
              </div>
            </div>
          </template>
          <p v-else class="empty-note">填写 AI 配置后，分析时会生成中文评分解释。</p>
        </section>
      </template>
    </section>
  </main>
</template>
