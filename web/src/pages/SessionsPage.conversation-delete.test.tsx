// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '../lib/api'
import {
  conversationDeleteIds,
  conversationDeleteRowCount,
  conversationDeleteVisibleCount,
  evictDeletedSessions
} from '../lib/session-conversation-delete'

const apiMocks = vi.hoisted(() => ({
  deleteSession: vi.fn(),
  getEmptySessionsCount: vi.fn(),
  getSessions: vi.fn(),
  getSessionStats: vi.fn(),
  getStatus: vi.fn()
}))

vi.mock('@/lib/api', () => ({ api: apiMocks }))
vi.mock('@/contexts/usePageHeader', () => ({
  usePageHeader: () => ({ setAfterTitle: vi.fn(), setEnd: vi.fn() })
}))
vi.mock('@/contexts/useSystemActions', () => ({
  useSystemActions: () => ({
    activeAction: null,
    actionStatus: null,
    dismissLog: vi.fn()
  })
}))
vi.mock('@/plugins', () => ({ PluginSlot: () => null }))
vi.mock('@/lib/dashboard-flags', () => ({
  isDashboardEmbeddedChatEnabled: () => false
}))
vi.mock('@/i18n', () => {
  const section = new Proxy({}, { get: (_target, property) => String(property) })
  return {
    useI18n: () => ({
      t: new Proxy({}, { get: () => section })
    })
  }
})
vi.mock('@/components/DeleteConfirmDialog', () => ({
  DeleteConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => void }) =>
    open ? (
      <button data-testid="confirm-delete" onClick={onConfirm}>
        confirm
      </button>
    ) : null
}))

function session(id: string): SessionInfo {
  return { id } as SessionInfo
}

describe('SessionsPage conversation delete reconciliation', () => {
  it('evicts every backend-expanded technical row after one visible delete', () => {
    const response = {
      app_chat_id: 'root',
      deleted_count: 3,
      deleted_ids: ['root', 'tip', 'delegate'],
      ok: true
    }

    const ids = conversationDeleteIds(response, ['root'])

    expect(ids).toEqual(['root', 'tip', 'delegate'])
    expect(evictDeletedSessions([session('root'), session('tip'), session('branch')], ids).map(row => row.id)).toEqual([
      'branch'
    ])
    expect(conversationDeleteRowCount(response, ids)).toBe(3)
  })

  it('uses the legacy request-id and deleted-count fallback during staged rollout', () => {
    const legacyResponse = { deleted: 1, ok: true }

    const ids = conversationDeleteIds(legacyResponse, ['root'])

    expect(ids).toEqual(['root'])
    expect(conversationDeleteRowCount(legacyResponse, ids)).toBe(1)
  })

  it('evicts the requested ghost when an idempotent delete reports no rows', () => {
    expect(conversationDeleteIds({ deleted_count: 0, deleted_ids: [], ok: true }, ['ghost'])).toEqual(['ghost'])
  })

  it('uses deleted_rows only as the technical storage count', () => {
    const response = {
      deleted: 1,
      deleted_conversations: 1,
      deleted_ids: ['root', 'tip', 'delegate'],
      deleted_rows: 3,
      ok: true
    }

    expect(conversationDeleteRowCount(response, response.deleted_ids)).toBe(3)
    expect(conversationDeleteVisibleCount(response, ['root'])).toBe(1)
  })

  it('falls back to unique requested conversations for visible totals', () => {
    expect(
      conversationDeleteVisibleCount({ deleted: 3, deleted_ids: ['root', 'tip', 'delegate'], ok: true }, [
        'root',
        'root'
      ])
    ).toBe(1)
  })
})

describe('SessionsPage visible delete pagination', () => {
  beforeEach(() => {
    const rows = Array.from({ length: 20 }, (_, index) => ({
      id: index === 0 ? 'root' : `filler-${index}`,
      source: 'ios',
      title: index === 0 ? 'Root' : `Filler ${index}`,
      message_count: 1,
      tool_call_count: 0,
      started_at: 1,
      last_active: 1
    }))
    apiMocks.getSessions.mockImplementation((limit: number) =>
      Promise.resolve(limit === 20 ? { sessions: rows, total: 22 } : { sessions: [], total: 22 })
    )
    apiMocks.getEmptySessionsCount.mockResolvedValue({ count: 0 })
    apiMocks.getSessionStats.mockResolvedValue({
      total: 22,
      active_store: 22,
      archived: 0,
      messages: 22,
      by_source: {}
    })
    apiMocks.getStatus.mockResolvedValue({ gateway_platforms: {} })
    apiMocks.deleteSession.mockResolvedValue({
      ok: true,
      app_chat_id: 'root',
      deleted_count: 3,
      deleted_ids: ['root', 'tip', 'delegate']
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('decrements the total by one visible conversation, not three rows', async () => {
    const { default: SessionsPage } = await import('./SessionsPage')

    render(
      <MemoryRouter>
        <SessionsPage />
      </MemoryRouter>
    )

    await screen.findByText('1–20 of 22')
    fireEvent.click(screen.getAllByLabelText('deleteSession')[0])
    fireEvent.click(await screen.findByTestId('confirm-delete'))

    await waitFor(() => expect(screen.getByText('1–20 of 21')).toBeTruthy())
    expect(screen.queryByText('Root')).toBeNull()
    expect(screen.getByText('Filler 1')).toBeTruthy()
    expect(screen.getAllByLabelText('nextPage').every(button => !button.hasAttribute('disabled'))).toBe(true)
  })
})
