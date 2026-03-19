<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { fetchMetricsRaw } from '@/api/metrics'
import AppHeader from '@/components/common/AppHeader.vue'

interface MetricSeries {
  rawName: string
  labels: Record<string, string>
  value: number
  subType?: string
}

interface ParsedMetric {
  name: string
  promName: string
  help: string
  type: string
  series: MetricSeries[]
}

type MetricsMap = Record<string, ParsedMetric>

const rawText = ref('')
const rawVisible = ref(false)
const metrics = ref<MetricsMap>({})
const statusOk = ref(false)
const statusText = ref('Loading...')
const lastUpdated = ref('')
const loading = ref(false)

let refreshTimer: ReturnType<typeof setInterval> | undefined
const REFRESH_INTERVAL = 10000

onMounted(async () => {
  await fetchData()
  refreshTimer = setInterval(fetchData, REFRESH_INTERVAL)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

async function fetchData() {
  loading.value = true
  try {
    const text = await fetchMetricsRaw()
    rawText.value = text
    if (!text.trim()) {
      metrics.value = {}
      statusOk.value = false
      statusText.value = 'No metrics data'
    } else {
      metrics.value = parsePrometheus(text)
      statusOk.value = true
      statusText.value = `${Object.keys(metrics.value).length} metrics`
    }
    lastUpdated.value = new Date().toLocaleTimeString()
  } catch (err) {
    statusOk.value = false
    statusText.value = `Error: ${(err as Error).message}`
  } finally {
    loading.value = false
  }
}

// ── Prometheus parser ──────────────────────────────
function parsePrometheus(text: string): MetricsMap {
  const result: MetricsMap = {}
  const helps: Record<string, string> = {}
  const types: Record<string, string> = {}

  for (const line of text.split('\n')) {
    if (line.startsWith('# HELP ')) {
      const rest = line.slice(7)
      const sp = rest.indexOf(' ')
      if (sp > 0) helps[rest.slice(0, sp)] = rest.slice(sp + 1)
    } else if (line.startsWith('# TYPE ')) {
      const rest = line.slice(7)
      const sp = rest.indexOf(' ')
      if (sp > 0) types[rest.slice(0, sp)] = rest.slice(sp + 1)
    } else if (line && !line.startsWith('#')) {
      const match = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)\{?(.*?)\}?\s+([0-9eE.+\-NnIiFf]+)/)
      if (!match) continue
      const [, rawName, labelsStr, valStr] = match
      const value = parseFloat(valStr)
      const labels: Record<string, string> = {}
      if (labelsStr) {
        for (const m of labelsStr.matchAll(/(\w+)="([^"]*)"/g)) {
          labels[m[1]] = m[2]
        }
      }

      // Determine base name and subType
      let baseName = rawName
      let subType: string | undefined
      for (const suffix of ['_bucket', '_sum', '_count', '_total', '_seconds']) {
        if (rawName.endsWith(suffix)) {
          baseName = rawName.slice(0, -suffix.length)
          subType = suffix.slice(1)
          break
        }
      }

      if (!result[baseName]) {
        const promName = rawName
        result[baseName] = {
          name: baseName,
          promName,
          help: helps[rawName] || helps[baseName] || helps[baseName + '_total'] || helps[baseName + '_seconds'] || '',
          type: types[rawName] || types[baseName] || types[baseName + '_total'] || types[baseName + '_seconds'] || 'untyped',
          series: [],
        }
      }
      result[baseName].series.push({ rawName, labels, value, subType })
    }
  }
  return result
}

// ── Stats computation ──────────────────────────────
function getTotalRequests(): { total: number; success: number; error: number } {
  const m = findMetric('request')
  if (!m) return { total: 0, success: 0, error: 0 }
  let success = 0, error = 0
  for (const s of m.series) {
    if (s.subType && s.subType !== 'total') continue
    const code = s.labels.code || s.labels.status_code || ''
    if (code.startsWith('2')) success += s.value
    else error += s.value
  }
  return { total: success + error, success, error }
}

function getErrorBreakdown(): { client: number; server: number } {
  const m = findMetric('request')
  if (!m) return { client: 0, server: 0 }
  let client = 0, server = 0
  for (const s of m.series) {
    if (s.subType && s.subType !== 'total') continue
    const code = s.labels.code || s.labels.status_code || ''
    if (code.startsWith('4')) client += s.value
    else if (code.startsWith('5')) server += s.value
  }
  return { client, server }
}

function getAvgDuration(keyword: string): number {
  const m = findMetric(keyword)
  if (!m) return 0
  let sum = 0, count = 0
  for (const s of m.series) {
    if (s.subType === 'sum') sum += s.value
    else if (s.subType === 'count') count += s.value
  }
  return count > 0 ? sum / count : 0
}

function getActiveStreams(): number {
  const m = findMetric('active_stream')
  if (!m) return 0
  return m.series.reduce((a, s) => a + (s.subType ? 0 : s.value), 0)
}

function findMetric(keyword: string): ParsedMetric | undefined {
  return Object.values(metrics.value).find((m) => m.name.includes(keyword))
}

function formatNum(n: number): string {
  return n.toLocaleString()
}

function formatDuration(s: number): string {
  if (s < 0.001) return `${(s * 1_000_000).toFixed(0)}µs`
  if (s < 1) return `${(s * 1000).toFixed(1)}ms`
  if (s < 60) return `${s.toFixed(2)}s`
  return `${(s / 60).toFixed(1)}min`
}

function formatBound(val: string): string {
  const n = parseFloat(val)
  if (!isFinite(n)) return '+Inf'
  if (n < 0.001) return `${(n * 1_000_000).toFixed(0)}µs`
  if (n < 1) return `${(n * 1000).toFixed(0)}ms`
  return `${n}s`
}

function formatLabels(labels: Record<string, string>, exclude: string[] = []): string {
  return Object.entries(labels)
    .filter(([k]) => !exclude.includes(k) && k !== 'service')
    .map(([k, v]) => `${k}="${v}"`)
    .join(', ')
}

function codeBadgeClass(code: string): string {
  if (code.startsWith('2')) return 'code-2xx'
  if (code.startsWith('4')) return 'code-4xx'
  if (code.startsWith('5')) return 'code-5xx'
  return ''
}

// ── Histogram rendering ────────────────────────────
function getHistogramGroups(m: ParsedMetric): { key: string; labels: Record<string, string>; buckets: { le: string; value: number }[]; count: number; sum: number }[] {
  const groups = new Map<string, { labels: Record<string, string>; buckets: { le: string; value: number }[]; count: number; sum: number }>()

  for (const s of m.series) {
    const key = formatLabels(s.labels, ['le'])
    if (!groups.has(key)) groups.set(key, { labels: { ...s.labels }, buckets: [], count: 0, sum: 0 })
    const g = groups.get(key)!
    if (s.subType === 'bucket') {
      g.buckets.push({ le: s.labels.le || '+Inf', value: s.value })
    } else if (s.subType === 'count') {
      g.count = s.value
    } else if (s.subType === 'sum') {
      g.sum = s.value
    }
  }

  // Sort buckets
  for (const g of groups.values()) {
    g.buckets.sort((a, b) => {
      const na = parseFloat(a.le), nb = parseFloat(b.le)
      if (!isFinite(na)) return 1
      if (!isFinite(nb)) return -1
      return na - nb
    })
  }

  return Array.from(groups.entries()).map(([key, g]) => ({ key, ...g }))
}

// Categorize metrics for display
function metricsByType(type: string): ParsedMetric[] {
  return Object.values(metrics.value).filter((m) => m.type === type)
}
</script>

<template>
  <div class="page">
    <AppHeader title="Astron Claw" subtitle="Metrics">
      <router-link to="/" class="icon-btn" title="Chat">&#8962;</router-link>
      <router-link to="/admin" class="icon-btn" title="Admin">&#9881;</router-link>
      <button class="btn btn-sm" :class="rawVisible ? 'btn-primary' : 'btn-secondary'" @click="rawVisible = !rawVisible">Raw</button>
      <button class="btn btn-sm btn-primary" @click="fetchData">Refresh</button>
    </AppHeader>

    <!-- Status bar -->
    <div class="status-bar">
      <span class="status-dot" :class="{ error: !statusOk }"></span>
      <span>{{ statusText }}</span>
      <span style="margin-left:auto">{{ lastUpdated }}</span>
    </div>

    <!-- Stats -->
    <div class="stats">
      <div class="stat-card">
        <div class="label">TOTAL REQUESTS</div>
        <div class="value accent">{{ formatNum(getTotalRequests().total) }}</div>
        <div class="sub" v-if="getTotalRequests().error">{{ formatNum(getTotalRequests().error) }} errors</div>
      </div>
      <div class="stat-card error-card">
        <div class="label">ERROR REQUESTS</div>
        <div class="value error-value">{{ formatNum(getTotalRequests().error) }}</div>
        <div class="sub" v-if="getTotalRequests().error">{{ formatNum(getErrorBreakdown().client) }} 4xx, {{ formatNum(getErrorBreakdown().server) }} 5xx</div>
      </div>
      <div class="stat-card">
        <div class="label">ACTIVE STREAMS</div>
        <div class="value success">{{ getActiveStreams() }}</div>
      </div>
      <div class="stat-card">
        <div class="label">AVG REQUEST DURATION</div>
        <div class="value">{{ formatDuration(getAvgDuration('request_duration')) }}</div>
      </div>
      <div class="stat-card">
        <div class="label">AVG STREAM DURATION</div>
        <div class="value">{{ formatDuration(getAvgDuration('stream_duration')) }}</div>
      </div>
    </div>

    <!-- Raw text -->
    <div v-if="rawVisible" class="raw-block">
      <pre>{{ rawText }}</pre>
    </div>

    <!-- Metric cards by type -->
    <template v-for="type in ['counter', 'histogram', 'gauge']" :key="type">
      <div v-if="metricsByType(type).length" class="section-title">
        {{ type.charAt(0).toUpperCase() + type.slice(1) }}s
        <span class="badge">{{ metricsByType(type).length }}</span>
      </div>

      <div v-for="m in metricsByType(type)" :key="m.name" class="metric-card">
        <div class="metric-header">
          <span class="metric-name">{{ m.name }}</span>
          <span class="metric-type" :class="`type-${m.type}`">{{ m.type }}</span>
        </div>
        <div v-if="m.help" class="metric-help">{{ m.help }}</div>

        <!-- Counter / Gauge table -->
        <template v-if="m.type === 'counter' || m.type === 'gauge'">
          <table class="metric-table">
            <thead><tr><th>Labels</th><th>Value</th></tr></thead>
            <tbody>
              <tr v-for="(s, i) in m.series.filter(s => !s.subType || s.subType === 'total')" :key="i">
                <td class="labels">
                  <template v-for="(v, k) in s.labels" :key="k">
                    <span v-if="k !== 'service'">
                      <span v-if="k === 'code' || k === 'status_code'" :class="['code-badge', codeBadgeClass(v)]">{{ v }}</span>
                      <span v-else class="label-chip">{{ k }}={{ v }}</span>
                    </span>
                  </template>
                </td>
                <td class="val">{{ formatNum(s.value) }}</td>
              </tr>
            </tbody>
          </table>
        </template>

        <!-- Histogram -->
        <template v-if="m.type === 'histogram'">
          <div v-for="(group, gi) in getHistogramGroups(m)" :key="group.key" class="hist-group">
            <div v-if="group.key" class="hist-labels">{{ group.key }}</div>
            <div class="hist-summary">
              Count: <strong>{{ formatNum(group.count) }}</strong> &middot;
              Sum: <strong>{{ formatDuration(group.sum) }}</strong> &middot;
              Avg: <strong>{{ formatDuration(group.count > 0 ? group.sum / group.count : 0) }}</strong>
            </div>
            <div class="hist-chart">
              <div
                v-for="(b, bi) in group.buckets"
                :key="bi"
                class="hist-bar-row"
                :class="`hist-series-${gi % 5}`"
              >
                <span class="hist-bar-label">{{ formatBound(b.le) }}</span>
                <div class="hist-bar-track">
                  <div
                    class="hist-bar-fill"
                    :style="{ width: (group.buckets.length ? (b.value / Math.max(...group.buckets.map(x => x.value), 1)) * 100 : 0) + '%' }"
                  ></div>
                </div>
                <span class="hist-bar-count">{{ formatNum(b.value) }}</span>
              </div>
            </div>
          </div>
        </template>
      </div>
    </template>

    <!-- Empty state -->
    <div v-if="!Object.keys(metrics).length && !loading" class="empty-state">
      <div class="icon">&#128202;</div>
      <div class="title">No metrics data</div>
      <div class="desc">Metrics will appear once OTLP telemetry is enabled and traffic is flowing.</div>
    </div>
  </div>
</template>

<style scoped>
.page { max-width: 1100px; margin: 0 auto; padding: 20px; }

.icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: var(--radius-sm); background: var(--bg-tertiary);
  border: 1px solid var(--border); color: var(--text-secondary); cursor: pointer;
  font-size: 1.1rem; transition: all var(--transition); text-decoration: none;
}
.icon-btn:hover { background: var(--accent-dim); color: var(--accent); border-color: var(--accent); }

.btn {
  display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px;
  border-radius: var(--radius-sm); font-size: .85rem; font-weight: 500;
  cursor: pointer; border: 1px solid transparent; transition: all var(--transition); font-family: var(--font);
}
.btn-sm { padding: 4px 10px; font-size: .78rem; }
.btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-primary:hover { background: var(--accent-hover); }
.btn-secondary { background: transparent; color: var(--text-secondary); border-color: var(--border); }
.btn-secondary:hover { background: var(--accent-dim); color: var(--accent); border-color: var(--accent); }

.status-bar {
  display: flex; align-items: center; gap: 12px; margin-bottom: 20px;
  font-size: .82rem; color: var(--text-muted);
}
.status-dot {
  width: 8px; height: 8px; border-radius: 50%; background: var(--success);
  display: inline-block; animation: pulse 2s infinite;
}
.status-dot.error { background: var(--error); animation: none; }

.stats {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px; margin-bottom: 24px;
}
.stat-card {
  background: var(--bg-secondary); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 18px 20px; animation: fadeUp .3s ease;
}
.stat-card .label {
  font-size: .78rem; color: var(--text-muted); margin-bottom: 6px;
  text-transform: uppercase; letter-spacing: .5px;
}
.stat-card .value { font-size: 1.6rem; font-weight: 700; font-family: var(--font-mono); }
.stat-card .sub { font-size: .75rem; color: var(--text-muted); margin-top: 4px; }
.accent { color: var(--accent); }
.success { color: var(--success); }
.error-card { border-color: var(--error); }
.error-card .label { color: var(--error); }
.error-value { color: var(--error); }

.raw-block {
  background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 16px; margin-bottom: 16px; overflow-x: auto; max-height: 500px; overflow-y: auto;
}
.raw-block pre { font-family: var(--font-mono); font-size: .78rem; color: var(--text-secondary); white-space: pre; line-height: 1.5; }

.section-title {
  font-size: .9rem; font-weight: 600; color: var(--text-secondary); margin: 28px 0 14px;
  text-transform: uppercase; letter-spacing: .5px; display: flex; align-items: center; gap: 8px;
}
.badge {
  font-size: .72rem; padding: 2px 8px; border-radius: 10px; background: var(--accent-dim);
  color: var(--accent); font-weight: 500; text-transform: none; letter-spacing: 0;
}

.metric-card {
  background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 20px; margin-bottom: 14px; animation: fadeUp .3s ease;
}
.metric-header { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.metric-name { font-family: var(--font-mono); font-size: .88rem; font-weight: 600; color: var(--text-primary); }
.metric-type { font-size: .7rem; padding: 2px 8px; border-radius: 10px; font-weight: 500; text-transform: uppercase; }
.type-counter { background: rgba(79,143,247,.15); color: var(--chart-1); }
.type-gauge { background: rgba(34,197,94,.15); color: var(--chart-2); }
.type-histogram { background: rgba(245,158,11,.15); color: var(--chart-3); }
.metric-help { font-size: .78rem; color: var(--text-muted); margin-bottom: 14px; }

.metric-table { width: 100%; border-collapse: collapse; font-size: .82rem; }
.metric-table th {
  text-align: left; padding: 8px 12px; font-weight: 600; color: var(--text-muted);
  border-bottom: 1px solid var(--border); font-size: .75rem; text-transform: uppercase; letter-spacing: .3px;
}
.metric-table td { padding: 8px 12px; border-bottom: 1px solid var(--border); color: var(--text-secondary); }
.metric-table tr:last-child td { border-bottom: none; }
.val { font-family: var(--font-mono); font-weight: 600; color: var(--text-primary); }
.labels { font-family: var(--font-mono); font-size: .76rem; color: var(--text-muted); }
.label-chip {
  display: inline-block; padding: 1px 6px; background: var(--bg-tertiary);
  border-radius: 4px; margin: 1px 2px;
}
.code-badge { display: inline-block; padding: 1px 7px; border-radius: 4px; font-family: var(--font-mono); font-size: .74rem; font-weight: 600; }
.code-2xx { background: rgba(34,197,94,.15); color: var(--success); }
.code-4xx { background: rgba(245,158,11,.15); color: var(--warning); }
.code-5xx { background: rgba(239,68,68,.15); color: var(--error); }

.hist-group { margin-top: 12px; }
.hist-labels { font-family: var(--font-mono); font-size: .76rem; color: var(--text-muted); margin-bottom: 4px; }
.hist-summary { font-size: .8rem; color: var(--text-secondary); margin-bottom: 8px; }
.hist-chart { margin-top: 6px; }
.hist-bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: .76rem; }
.hist-bar-label { width: 80px; text-align: right; font-family: var(--font-mono); color: var(--text-muted); flex-shrink: 0; }
.hist-bar-track { flex: 1; height: 20px; background: var(--bg-tertiary); border-radius: 4px; overflow: hidden; }
.hist-bar-fill { height: 100%; border-radius: 4px; background: var(--chart-1); transition: width .5s ease; }
.hist-bar-count { width: 60px; font-family: var(--font-mono); color: var(--text-secondary); font-size: .74rem; flex-shrink: 0; }
.hist-series-0 .hist-bar-fill { background: var(--chart-1); }
.hist-series-1 .hist-bar-fill { background: var(--chart-2); }
.hist-series-2 .hist-bar-fill { background: var(--chart-3); }
.hist-series-3 .hist-bar-fill { background: var(--chart-4); }
.hist-series-4 .hist-bar-fill { background: var(--chart-5); }

.empty-state { text-align: center; padding: 60px 20px; color: var(--text-muted); }
.empty-state .icon { font-size: 3rem; margin-bottom: 12px; opacity: .4; }
.empty-state .title { font-size: 1.1rem; margin-bottom: 6px; color: var(--text-secondary); }
.empty-state .desc { font-size: .85rem; }

@media (max-width: 600px) {
  .stats { grid-template-columns: 1fr 1fr; }
  .hist-bar-label { width: 60px; font-size: .7rem; }
}
</style>
