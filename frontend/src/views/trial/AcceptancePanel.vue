<template>
  <section class="trial-panel" data-testid="acceptance-panel">
    <header class="panel-head">
      <div>
        <h3>字段清洗验收</h3>
        <p class="panel-desc">
          验收只按试样报告里的原值和抽取规则逐页做类型转换，不改正式清洗规则。
          只有能回查候选规则与适用页面组、且没有转换问题的试样才能签为通过。
        </p>
      </div>
      <span class="badge" :class="state?.formal_unchanged ? 'badge-ok' : 'badge-bad'">
        {{ state?.formal_unchanged ? '正式清洗仍在使用原规则' : '正式数据已变化' }}
      </span>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="acceptance-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="acceptance-notice">{{ notice }}</p>

    <form class="trial-form" @submit.prevent="confirm">
      <label>
        <span>试跑批次</span>
        <select v-model="runId" data-testid="acceptance-run">
          <option value="" disabled>选择试跑批次</option>
          <option v-for="run in state?.runs ?? []" :key="run.run_id" :value="run.run_id">
            {{ run.run_id }} · {{ run.sample_group }}（{{ run.status }}）
          </option>
        </select>
      </label>
      <label>
        <span>确认说明</span>
        <input v-model="note" data-testid="acceptance-note" placeholder="例如 更正前先确认一次" />
      </label>
      <button class="btn primary" type="submit" data-testid="confirm-acceptance" :disabled="!runId || busy">
        {{ busy ? '确认中…' : '发起清洗验收' }}
      </button>
    </form>

    <template v-if="manifest">
      <div class="report-head">
        <h4>字段清洗清单 · {{ manifest.run_id }}</h4>
        <span class="muted">来源：{{ manifest.source }} · 同步于 {{ manifest.synced_at }}</span>
      </div>
      <p class="muted">
        清洗端按清单里的原值和抽取规则逐页转换；正式生效规则（只读）：
        <code v-for="rule in manifest.formal_clean_rules" :key="rule.规则编号">
          {{ rule.规则编号 }} {{ rule.目标字段 }}/{{ rule.清洗类型 }}
        </code>
        <span v-if="!manifest.formal_clean_rules.length">暂无生效规则</span>
      </p>
      <table class="data-table" data-testid="cleaning-manifest">
        <thead>
          <tr>
            <th>页面组</th>
            <th>页面</th>
            <th>候选</th>
            <th>字段</th>
            <th>XPath</th>
            <th>原值</th>
            <th>转换值</th>
            <th>值类型</th>
            <th>转换问题</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in manifest.items" :key="`${item.trace_id}-${item.field}`">
            <td>{{ item.page_group }}</td>
            <td>{{ item.page_id }} <span class="muted">v{{ item.page_revision }}</span></td>
            <td>{{ item.candidate_id }}</td>
            <td>{{ item.field }}</td>
            <td><code>{{ item.xpath }}</code></td>
            <td>{{ item.raw_value ?? '—' }}</td>
            <td>{{ item.converted_value ?? '—' }}</td>
            <td><span class="tag">{{ item.value_type }}</span></td>
            <td>
              <span v-for="problem in item.problems" :key="problem.code" class="tag tag-warn">
                {{ problem.code }}
              </span>
              <span v-if="!item.problems.length" class="muted">无</span>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <template v-if="latest">
      <div class="report-head">
        <h4>本次确认实际领到 · {{ latest.acceptance_id }}</h4>
        <span class="muted">
          领取 {{ latest.received_count }} 份试样 ·
          可交发布验收 {{ latest.eligible_tests.length }} 条 ·
          暂缓 {{ latest.deferred.length }} 份 ·
          确认时批次状态 {{ latest.run_state_at_confirm.status }}
          （{{ latest.run_state_at_confirm.produced_specimens }}/{{ latest.run_state_at_confirm.expected_specimens }}）
        </span>
      </div>

      <div class="trial-grid">
        <article class="trial-card">
          <h4>可交发布验收的测试条目</h4>
          <ul class="plain-list" data-testid="eligible-tests">
            <li v-for="test in latest.eligible_tests" :key="test.test_id">
              <code>{{ test.test_id }}</code>
              <span>{{ test.page_group }} / {{ test.page_id }} · 候选 {{ test.candidate_id }} · v{{ test.page_revision }}</span>
            </li>
            <li v-if="!latest.eligible_tests.length" class="muted">暂无可以签为通过的条目</li>
          </ul>
        </article>

        <article class="trial-card">
          <h4>页面组资格</h4>
          <ul class="plain-list" data-testid="page-groups">
            <li v-for="group in latest.page_groups" :key="group.page_group">
              <span class="tag" :class="group.eligible ? 'tag-ok' : 'tag-warn'">{{ group.status }}</span>
              <strong>{{ group.page_group }}</strong>
              <span class="muted">
                适用试样 {{ group.signed_count }}/{{ group.specimen_count }} 已签，
                暂缓 {{ group.deferred_count }}
                <template v-if="group.out_of_scope_count">，另有 {{ group.out_of_scope_count }} 份候选不适用</template>
              </span>
              <ul class="plain-list inner">
                <li v-for="page in group.pages" :key="page.page_id" class="muted">
                  {{ page.page_id }}：已签 {{ page.signed }}，暂缓 {{ page.deferred }}
                  <span v-for="reason in page.reasons" :key="reason" class="tag tag-warn">{{ reason }}</span>
                </li>
              </ul>
            </li>
          </ul>
        </article>
      </div>

      <div class="trial-grid">
        <article class="trial-card">
          <h4>暂缓的试样与原因</h4>
          <ul class="plain-list" data-testid="deferred-list">
            <li v-for="item in latest.deferred" :key="item.trace_id">
              <code>{{ item.trace_id }}</code>
              <span>{{ item.page_group }} / {{ item.page_id }} · 候选 {{ item.candidate_id }}</span>
              <span v-for="reason in item.reasons" :key="reason" class="tag tag-warn">{{ reason }}</span>
            </li>
            <li v-if="!latest.deferred.length" class="muted">本批没有暂缓试样</li>
          </ul>
        </article>

        <article class="trial-card">
          <h4>同批口径冲突</h4>
          <ul class="plain-list" data-testid="conflict-list">
            <li v-for="(item, index) in latest.conflicts" :key="`${item.code}-${index}`">
              <span class="tag tag-warn">{{ item.code }}</span>
              <span>{{ item.detail }}</span>
            </li>
            <li v-if="!latest.conflicts.length" class="muted">本批口径一致</li>
          </ul>
        </article>
      </div>

      <details v-if="latest.not_covered.length || latest.pending_retry.length" class="trial-card">
        <summary>仍需补测的页面</summary>
        <ul class="plain-list">
          <li v-for="item in latest.not_covered" :key="`nc-${item.candidate_id}-${item.page_id}`">
            候选 {{ item.candidate_id }} × {{ item.page_id }}：{{ item.reason }}
          </li>
          <li v-for="item in latest.pending_retry" :key="`pr-${item.candidate_id}-${item.page_id}`">
            候选 {{ item.candidate_id }} × {{ item.page_id }}：{{ item.reason }}
          </li>
        </ul>
      </details>
    </template>

    <template v-if="history && history.acceptances.length">
      <div class="report-head">
        <h4>验收历史（{{ runId }}）</h4>
      </div>
      <table class="data-table" data-testid="acceptance-history">
        <thead>
          <tr><th>验收号</th><th>确认时间</th><th>说明</th><th>领取</th><th>可交发布验收</th><th>暂缓</th></tr>
        </thead>
        <tbody>
          <tr v-for="record in history.acceptances" :key="record.acceptance_id">
            <td><code>{{ record.acceptance_id }}</code></td>
            <td>{{ record.confirmed_at }}</td>
            <td>{{ record.note || '—' }}</td>
            <td>{{ record.received_count }}</td>
            <td>{{ record.eligible_tests.length }}</td>
            <td>{{ record.deferred.length }}</td>
          </tr>
        </tbody>
      </table>
      <p class="muted">
        此前结果与暂缓原因可逐条回查；试跑重试替换掉的旧试样保留在各自试样的历史里。
      </p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  confirmTrialAcceptance,
  loadCleaningManifest,
  loadTrialHistory,
  loadTrialState,
} from '@/api/backend-service'
import type {
  CleaningManifest,
  TrialAcceptance,
  TrialHistory,
  TrialState,
} from '@/data/types'
import { selectedRunId, trialError, trialNotice } from './shared'

const state = ref<TrialState | null>(null)
const manifest = ref<CleaningManifest | null>(null)
const history = ref<TrialHistory | null>(null)
const latest = ref<TrialAcceptance | null>(null)
const note = ref('清洗验收确认')
const busy = ref(false)
let loading = false
const errorMessage = trialError
const notice = trialNotice

const runId = computed({
  get: () => selectedRunId.value,
  set: (value: string) => {
    selectedRunId.value = value
  },
})

onMounted(async () => {
  await refreshState()
  if (!selectedRunId.value && state.value?.runs.length) {
    // 默认接着看最近一次试跑，之后再交给 watch 统一加载。
    selectedRunId.value = state.value.runs[state.value.runs.length - 1].run_id
    return
  }
  await loadRunDetail()
})

watch(() => selectedRunId.value, (value, previous) => {
  if (value && value !== previous && state.value) void loadRunDetail()
})

async function refreshState() {
  errorMessage.value = ''
  try {
    state.value = await loadTrialState()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清洗验收数据读取失败'
  }
}

async function loadRunDetail() {
  if (loading) return
  loading = true
  errorMessage.value = ''
  latest.value = null
  try {
    const current = selectedRunId.value
    if (!current) {
      manifest.value = null
      history.value = null
      return
    }
    ;[manifest.value, history.value] = await Promise.all([
      loadCleaningManifest(current),
      loadTrialHistory(current),
    ])
    if (history.value?.acceptances.length) {
      latest.value = history.value.acceptances[history.value.acceptances.length - 1]
    }
  } catch (error) {
    manifest.value = null
    history.value = null
    errorMessage.value = error instanceof Error ? error.message : '清洗验收数据读取失败'
  } finally {
    loading = false
  }
}

async function loadAll() {
  await refreshState()
  await loadRunDetail()
}

async function confirm() {
  if (!selectedRunId.value) return
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await confirmTrialAcceptance(selectedRunId.value, {
      note: note.value,
      operator: '维护者',
    })
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    await refreshState()
    await loadRunDetail()
    latest.value = result.entry
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清洗验收失败'
  } finally {
    busy.value = false
  }
}

defineExpose({ loadAll })
</script>
