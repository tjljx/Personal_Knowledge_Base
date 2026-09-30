export const SESSION_IDLE_MS = 10 * 60 * 1000

const SESSION_KEY = 'personal-knowledge-session'

export type SavedSession = { token: string; lastActivityAt: number; renewedAt: number }

export function readSession(storage: Storage, now = Date.now()): SavedSession | null {
  try {
    const raw = storage.getItem(SESSION_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<SavedSession>
    if (typeof value.token !== 'string' || !value.token
      || typeof value.lastActivityAt !== 'number' || !Number.isFinite(value.lastActivityAt)
      || value.lastActivityAt > now || now - value.lastActivityAt >= SESSION_IDLE_MS) {
      storage.removeItem(SESSION_KEY)
      return null
    }
    const renewedAt = typeof value.renewedAt === 'number' && Number.isFinite(value.renewedAt)
      ? value.renewedAt : value.lastActivityAt
    return { token: value.token, lastActivityAt: value.lastActivityAt, renewedAt }
  } catch {
    try { storage.removeItem(SESSION_KEY) } catch { /* Storage may be unavailable. */ }
    return null
  }
}

export function saveSession(storage: Storage, token: string, lastActivityAt = Date.now(), renewedAt = lastActivityAt): void {
  storage.setItem(SESSION_KEY, JSON.stringify({ token, lastActivityAt, renewedAt }))
}

export function clearStoredSession(storage: Storage): void {
  storage.removeItem(SESSION_KEY)
}
