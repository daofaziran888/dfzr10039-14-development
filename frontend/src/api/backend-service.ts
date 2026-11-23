import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  CleaningManifest,
  EntryRow,
  OverviewResult,
  PageResult,
  TrialAcceptance,
  TrialHistory,
  TrialReport,
  TrialRetryResult,
  TrialRun,
  TrialSnapshot,
  TrialState,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---------------------------------------------------------------------------
// 内容抽取试跑 / 试样报告 / 字段清洗验收
// 这些接口只读写试跑登记表，不会写正式抽取与清洗数据。
// ---------------------------------------------------------------------------

async function trialJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  if (!response.ok) {
    let detail = `接口返回 ${response.status}`
    try {
      const payload = await response.json()
      if (payload?.detail) detail = String(payload.detail)
      if (payload?.message) detail = String(payload.message)
    } catch {
      // 保持通用错误说明
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

function jsonBody(payload: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }
}

export async function loadTrialState(): Promise<TrialState> {
  return trialJson<TrialState>('/api/trial/state')
}

export async function createTrialRun(payload: {
  sample_group: string
  candidate_ids: string[]
  page_ids: string[]
  interrupted?: boolean
  operator?: string
}): Promise<ActionResult & { entry: { run: TrialRun } }> {
  return trialJson('/api/trial/runs', jsonBody(payload))
}

export async function loadTrialReport(runId: string): Promise<TrialReport> {
  return trialJson<TrialReport>(`/api/trial/runs/${encodeURIComponent(runId)}/report`)
}

export async function retryTrialRun(runId: string, payload: { operator?: string } = {}) {
  return trialJson<ActionResult & { entry: TrialRetryResult }>(
    `/api/trial/runs/${encodeURIComponent(runId)}/retry`,
    jsonBody(payload),
  )
}

export async function loadCleaningManifest(runId: string): Promise<CleaningManifest> {
  return trialJson<CleaningManifest>(
    `/api/trial/runs/${encodeURIComponent(runId)}/cleaning-manifest`,
  )
}

export async function loadTrialHistory(runId: string): Promise<TrialHistory> {
  return trialJson<TrialHistory>(`/api/trial/runs/${encodeURIComponent(runId)}/history`)
}

export async function confirmTrialAcceptance(
  runId: string,
  payload: { note?: string; operator?: string; specimen_ids?: string[] },
) {
  return trialJson<ActionResult & { entry: TrialAcceptance }>(
    `/api/trial/runs/${encodeURIComponent(runId)}/acceptances`,
    jsonBody(payload),
  )
}

export async function updateTrialSnapshot(
  pageId: string,
  payload: { page_group?: string; title?: string; nodes: Record<string, string>; note?: string; operator?: string },
): Promise<ActionResult & { entry: TrialSnapshot }> {
  const response = await request(`/api/trial/snapshots/${encodeURIComponent(pageId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const payloadJson = await response.json()
  if (!response.ok) throw new Error(payloadJson?.detail ?? '快照更新失败')
  return payloadJson
}

export async function replayTrialScenario(): Promise<Record<string, unknown>> {
  const response = await request('/api/trial/scenario', { method: 'POST' })
  if (!response.ok) throw new Error('示例重放失败')
  return (await response.json()) as Record<string, unknown>
}
