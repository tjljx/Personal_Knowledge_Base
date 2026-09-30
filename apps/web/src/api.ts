export interface User {
  id: string
  username: string
  role: 'ADMIN' | 'MEMBER'
  status: string
}

export interface KnowledgeBase {
  id: string
  name: string
  description: string
  owner_id: string
  status: string
  cloud_enabled: boolean
  created_at: string
}

export interface Document {
  id: string
  knowledge_base_id: string
  title: string
  description: string
  source: string
  file_type: string
  created_by: string
  tags: string[]
  current_version: number
  status: string
  created_at: string
  updated_at: string
}

export interface DocumentVersion {
  id: string
  document_id: string
  version_no: number
  original_filename: string
  sha256: string
  size_bytes: number
  status: string
  parse_error: string | null
  job_status: string | null
  created_at: string
}

export interface DocumentParseIssue {
  document_id: string
  title: string
  version_no: number
  original_filename: string
  status: string
  job_status: string | null
  attempts: number
  parse_error: string | null
  supported: boolean
}

export interface DocumentParseOverview {
  total_documents: number
  parsed_documents: number
  issues: DocumentParseIssue[]
}

export interface ParsedDocument {
  document_version_id: string
  parser_name: string
  parser_version: string
  markdown: string
  structure: { parser_config?: { ocr?: boolean; ocr_pages?: number }; pages: Array<{ number: number; blocks: Array<{ type: string; text: string }> }> }
  created_at: string
}

export interface ProvenanceChunk {
  id: string
  ordinal: number
  page_no: number | null
  block_type: string
  excerpt: string
}

export interface ProvenanceVersion {
  version_no: number
  status: string
  parser_name: string | null
  page_count: number
  chunk_count: number
  chunks: ProvenanceChunk[]
}

export interface Provenance {
  document_id: string
  title: string
  current_version: number
  versions: ProvenanceVersion[]
  more_versions: boolean
}

export interface KnowledgeGraphData {
  nodes: Array<{ id: string; name: string; type: string }>
  edges: Array<{ id: string; source: string; target: string; relation: string; evidence: Array<{ document_id: string; document_title: string; version_no: number; page_no: number | null; chunk_id: string; quote: string; model: string }> }>
  processed_chunks: number
  failed_chunks: number
}

export interface KnowledgeGraphProgress {
  total_chunks: number
  processed_chunks: number
  remaining_chunks: number
  running_chunks: number
  failed_chunks: number
  waiting_budget_chunks: number
  total_documents: number
  parsed_documents: number
}

export interface DocumentSearchResult {
  document: Document
  matched_in: string[]
  snippet: string
  locations: Array<{ page: number | null; block_type: string | null }>
}

export interface AskResult {
  answer: string
  citations: Array<{
    number: number
    document_id: string
    document_title: string
    version_no: number
    page_no: number | null
    chunk_id: string
    quote: string
    graph_evidence?: Array<{ id: string; source: string; relation: string; target: string; quote: string }>
  }>
}

export type CitationSource = AskResult['citations'][number] & {
  content: string
  file_type: string
  original_filename: string
}

export interface ChatSession {
  id: string
  knowledge_base_id: string
  user_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant'
  content: string
  citations: AskResult['citations']
  model: string | null
  created_at: string
}

export interface CloudBudgetSummary {
  month: string
  budget_cny: number
  charged_cny: number
  reserved_cny: number
  remaining_cny: number
  alert_level: 0 | 70 | 90 | 100
}

let accessToken = ''

export function setToken(token: string): void {
  accessToken = token
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init.headers,
    },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `请求失败 (${response.status})`)
  }
  return response
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await request(path, init)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export async function apiPage<T>(path: string): Promise<{ items: T[]; total: number }> {
  const response = await request(path)
  return { items: (await response.json()) as T[], total: Number(response.headers.get('X-Total-Count') ?? 0) }
}

export async function apiStream(
  path: string,
  body: object,
  onEvent: (name: string, payload: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body), signal })
  if (!response.body) throw new Error('浏览器不支持流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = buffer.replace(/\r\n/g, '\n')
    let separator = buffer.indexOf('\n\n')
    while (separator >= 0) {
      const frame = buffer.slice(0, separator)
      buffer = buffer.slice(separator + 2)
      const event = frame.split('\n').find(line => line.startsWith('event:'))?.slice(6).trim() ?? 'message'
      const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n')
      if (raw) onEvent(event, JSON.parse(raw) as Record<string, unknown>)
      separator = buffer.indexOf('\n\n')
    }
  }
}

export async function download(path: string, filename: string): Promise<void> {
  const response = await fetch(`/api/v1${path}`, {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  })
  if (!response.ok) throw new Error(`下载失败 (${response.status})`)
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export async function openPdf(path: string, pageNo: number | null): Promise<void> {
  const tab = window.open('about:blank', '_blank')
  if (!tab) throw new Error('浏览器阻止了新窗口，请允许弹出窗口后重试')
  tab.opener = null
  try {
    const response = await fetch(`/api/v1${path}`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    })
    if (!response.ok) throw new Error(`打开原文件失败 (${response.status})`)
    const url = URL.createObjectURL(new Blob([await response.blob()], { type: 'application/pdf' }))
    tab.location.href = `${url}${pageNo ? `#page=${pageNo}` : ''}`
    window.setTimeout(() => URL.revokeObjectURL(url), 300_000)
  } catch (error) {
    tab.close()
    throw error
  }
}
