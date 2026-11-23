<template>
  <section class="trial-panel" data-testid="trial-panel">
    <header class="panel-head">
      <div>
        <h3>候选规则批量试跑</h3>
        <p class="panel-desc">
          一次提交同组本地网页快照，对多条候选规则逐页试跑；每份试样保留原值、转换值和转换问题。
          试跑只写试样登记表，不会写正式抽取数据。
        </p>
      </div>
      <span class="badge" :class="state?.formal_unchanged ? 'badge-ok' : 'badge-bad'" data-testid="formal-guard">
        {{ state?.formal_unchanged ? '正式数据未被改写' : '正式数据已发生变化' }}
      </span>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="trial-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="trial-notice">{{ notice }}</p>

    <div class="trial-grid">
      <article class="trial-card">
        <h4>候选规则</h4>
        <ul class="plain-list">
          <li v-for="candidate in state?.candidates ?? []" :key="candidate.candidate_id" class="candidate-item">
            <label>
              <input
                v-model="selectedCandidates"
                type="checkbox"
                :value="candidate.candidate_id"
                :data-testid="`candidate-${candidate.candidate_id}`"
              />
              <strong>{{ candidate.candidate_id }}</strong>
              <span>{{ candidate.name }}</span>
            </label>
            <span class="muted">适用：{{ candidate.applicable_page_groups.join('、') || '未声明' }}</span>
            <span class="muted">
              字段：
              <code v-for="(xpath, field) in candidate.field_mapping" :key="field">
                {{ field }}（{{ candidate.field_types[field] ?? 'string' }}）← {{ xpath }}
              </code>
            </span>
            <span class="muted">{{ candidate.description }}</span>
          </li>
        </ul>
      </article>

      <article class="trial-card">
        <h4>本地网页采集快照</h4>
        <ul class="plain-list">
          <li v-for="snapshot in state?.snapshots ?? []" :key="snapshot.page_id" class="snapshot-item">
            <label>
              <input
                v-model="selectedPages"
                type="checkbox"
                :value="snapshot.page_id"
                :data-testid="`page-${snapshot.page_id}`"
              />
              <strong>{{ snapshot.page_id }}</strong>
              <span>{{ snapshot.page_group }} · {{ snapshot.title }}</span>
            </label>
            <span class="muted">第 {{ snapshot.revision }} 版</span>
            <code v-for="(value, xpath) in snapshot.nodes" :key="xpath">{{ xpath }} = {{ value }}</code>
            <button class="link" type="button" :data-testid="`fix-${snapshot.page_id}`" @click="openFix(snapshot)">
              更正快照
            </button>
          </li>
        </ul>
      </article>
    </div>

    <form class="trial-form" @submit.prevent="startRun">
      <label>
        <span>同组样本</span>
        <input v-model="sampleGroup" data-testid="sample-group" placeholder="例如 样本组-NEWS-COLUMN" required />
      </label>
      <label class="inline">
        <input v-model="interrupted" type="checkbox" data-testid="simulate-interrupt" />
        <span>模拟批量试跑中断（只跑前半段）</span>
      </label>
      <button class="btn primary" type="submit" data-testid="start-run" :disabled="busy">
        {{ busy ? '试跑中…' : '提交批量试跑' }}
      </button>
    </form>

    <div v-if="state?.runs.length" class="run-strip">
      <button
        v-for="run in state.runs"
        :key="run.run_id"
        class="run-chip"
        :class="{ active: run.run_id === activeRunId }"
        type="button"
        :data-testid="`run-${run.run_id}`"
        @click="selectRun(run.run_id)"
      >
        {{ run.run_id }} · {{ run.sample_group }}
        <small>{{ run.status }} {{ run.produced_specimens }}/{{ run.expected_specimens }}</small>
      </button>
    </div>

    <template v-if="report">
      <div class="report-head">
        <h4>试样报告 · {{ report.run.run_id }}</h4>
        <span class="muted">
          页面 {{ report.page_count }} 个 · 试样 {{ report.specimen_count }} 份 ·
          已签 {{ report.state_totals['已签'] ?? 0 }} 份 ·
          未确认 {{ report.state_totals['未确认'] ?? 0 }} 份
        </span>
        <button
          class="btn"
          type="button"
          data-testid="retry-run"
          :disabled="busy"
          title="只替换本次未确认的试样，已签来源保留"
          @click="retryRun"
        >
          重试未确认试样
        </button>
      </div>

      <p v-if="retrySummary" class="notice-text" data-testid="retry-summary">{{ retrySummary }}</p>

      <table class="data-table" data-testid="specimen-table">
        <thead>
          <tr>
            <th>页面</th>
            <th>页面组</th>
            <th>候选</th>
            <th>版本</th>
            <th>试样回查号</th>
            <th>字段原值 → 转换值（类型）</th>
            <th>转换问题</th>
            <th>签署状态</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="page in report.pages" :key="page.page_id">
            <tr class="page-row" :data-testid="`report-page-${page.page_id}`">
              <td colspan="8">
                {{ page.page_id }} · {{ page.title }}（{{ page.page_group }}，第 {{ page.revision }} 版）
              </td>
            </tr>
            <tr
              v-for="specimen in page.specimens"
              :key="specimen.specimen_id"
              :data-testid="`specimen-${specimen.trace_id}`"
              :data-state="specimen.state"
            >
              <td>{{ specimen.page_id }}</td>
              <td>
                {{ specimen.page_group }}
                <span v-if="!specimen.applicable" class="tag tag-warn">不适用</span>
              </td>
              <td>{{ specimen.candidate_id }}</td>
              <td>{{ specimen.page_revision }}</td>
              <td><code>{{ specimen.trace_id }}</code></td>
              <td>
                <div v-for="field in specimen.fields ?? {}" :key="field.field" class="field-line">
                  <strong>{{ field.field }}</strong>
                  <span class="muted">{{ field.raw_value ?? '—' }}</span>
                  →
                  <span>{{ field.converted_value ?? '—' }}</span>
                  <span class="tag">{{ field.value_type }}</span>
                </div>
              </td>
              <td>
                <span v-for="problem in specimen.problems" :key="problem.code" class="tag tag-warn">
                  {{ problem.code }}
                </span>
                <span v-if="!specimen.problems.length" class="muted">无</span>
              </td>
              <td>
                <span class="tag" :class="specimen.state === '已签' ? 'tag-ok' : 'tag-wait'">
                  {{ specimen.state }}
                </span>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>

    <div v-if="fixing" class="modal-backdrop" data-testid="fix-modal">
      <form class="modal" @submit.prevent="saveFix">
        <h3>更正本地快照 · {{ fixing.page_id }}</h3>
        <label v-for="(value, xpath) in fixNodes" :key="xpath" class="create-field">
          <span>{{ xpath }}</span>
          <input v-model="fixNodes[xpath]" :data-testid="`fix-node-${xpath}`" />
        </label>
        <label class="create-field">
          <span>更正说明</span>
          <input v-model="fixNote" data-testid="fix-note" placeholder="例如 坏日期更正为 2026/11/22" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="fixing = null">取消</button>
          <button class="btn primary" type="submit" data-testid="save-fix">保存更正</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createTrialRun,
  loadTrialReport,
  loadTrialState,
  retryTrialRun,
  updateTrialSnapshot,
} from '@/api/backend-service'
import type { TrialReport, TrialSnapshot, TrialState } from '@/data/types'
import { selectedRunId, trialError, trialNotice } from './shared'

const emit = defineEmits<{ (event: 'run-selected', runId: string): void }>()

const state = ref<TrialState | null>(null)
const report = ref<TrialReport | null>(null)
const sampleGroup = ref('样本组-NEWS-COLUMN')
const selectedCandidates = ref<string[]>(['P', 'Q'])
const selectedPages = ref<string[]>(['news-001', 'column-bad', 'column-good'])
const interrupted = ref(false)
const busy = ref(false)
const errorMessage = trialError
const notice = trialNotice
const retrySummary = ref('')

const activeRunId = computed(() => selectedRunId.value)

const fixing = ref<TrialSnapshot | null>(null)
const fixNodes = ref<Record<string, string>>({})
const fixNote = ref('')

onMounted(async () => {
  await refreshState()
  if (!selectedRunId.value && state.value?.runs.length) {
    selectRun(state.value.runs[state.value.runs.length - 1].run_id)
  } else if (selectedRunId.value) {
    await loadReport(selectedRunId.value)
  }
})

async function refreshState() {
  try {
    state.value = await loadTrialState()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试跑状态读取失败'
  }
}

async function loadReport(runId: string) {
  try {
    report.value = await loadTrialReport(runId)
  } catch (error) {
    report.value = null
    errorMessage.value = error instanceof Error ? error.message : '试样报告读取失败'
  }
}

function selectRun(runId: string) {
  selectedRunId.value = runId
  retrySummary.value = ''
  void loadReport(runId)
  emit('run-selected', runId)
}

async function startRun() {
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  retrySummary.value = ''
  try {
    const result = await createTrialRun({
      sample_group: sampleGroup.value,
      candidate_ids: selectedCandidates.value,
      page_ids: selectedPages.value,
      interrupted: interrupted.value,
      operator: '运营值班',
    })
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    await refreshState()
    if (result.entry?.run) selectRun(result.entry.run.run_id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量试跑失败'
  } finally {
    busy.value = false
  }
}

async function retryRun() {
  if (!report.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await retryTrialRun(report.value.run.run_id, { operator: '运营值班' })
    if (!result.ok) throw new Error(result.message)
    retrySummary.value = result.message
    await refreshState()
    await loadReport(report.value.run.run_id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重试失败'
  } finally {
    busy.value = false
  }
}

function openFix(snapshot: TrialSnapshot) {
  fixing.value = snapshot
  fixNodes.value = { ...snapshot.nodes }
  fixNote.value = ''
}

async function saveFix() {
  if (!fixing.value) return
  errorMessage.value = ''
  try {
    const result = await updateTrialSnapshot(fixing.value.page_id, {
      nodes: fixNodes.value,
      note: fixNote.value || '更正本地快照',
      operator: '值班人员',
    })
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    fixing.value = null
    await refreshState()
    if (selectedRunId.value) await loadReport(selectedRunId.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '快照更正失败'
  }
}

defineExpose({ refreshState, selectRun })
</script>
