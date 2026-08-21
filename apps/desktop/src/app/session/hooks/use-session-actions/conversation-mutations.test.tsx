import { act, cleanup, render, waitFor } from '@testing-library/react'
import type { MutableRefObject } from 'react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { deleteSession, type SessionInfo, setSessionArchived } from '@/hermes'
import { $removedSessionIds, $sessionMutationsInFlight } from '@/store/projects'
import { $sessions, setSessions } from '@/store/session'
import { $archivedSessions } from '@/store/sidebar-archive'

import type { ClientSessionState } from '../../../types'

import { useSessionActions } from './index'

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  deleteSession: vi.fn(),
  getSession: vi.fn(),
  getAllSessionMessages: vi.fn(),
  getLatestSessionMessages: vi.fn(),
  listAllProfileSessions: vi.fn(),
  setApiRequestProfile: vi.fn(),
  setSessionArchived: vi.fn()
}))

vi.mock('@/store/profile', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ensureGatewayProfile: vi.fn().mockResolvedValue(undefined)
}))

function appSession(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    _lineage_root_id: 'root-ios',
    archived: false,
    ended_at: null,
    id: 'tip-ios',
    input_tokens: 0,
    is_active: false,
    last_active: 1,
    message_count: 2,
    model: null,
    output_tokens: 0,
    preview: null,
    source: 'ios',
    started_at: 1,
    title: 'iPhone chat',
    tool_call_count: 0,
    ...overrides
  } as SessionInfo
}

type Handle = Pick<ReturnType<typeof useSessionActions>, 'archiveSession' | 'removeSession'>

function Harness({ onReady }: { onReady: (handle: Handle) => void }) {
  const ref = <T,>(value: T): MutableRefObject<T> => ({ current: value })
  const actions = useSessionActions({
    activeSessionId: null,
    activeSessionIdRef: ref<string | null>(null),
    busyRef: ref(false),
    creatingSessionRef: ref(false),
    ensureSessionState: () => ({}) as ClientSessionState,
    getRouteToken: () => 'token',
    getRoutedStoredSessionId: () => null,
    navigate: vi.fn() as never,
    requestGateway: vi.fn().mockResolvedValue(undefined),
    resetViewSync: vi.fn(),
    runtimeIdByStoredSessionIdRef: ref(new Map<string, string>()),
    selectedStoredSessionId: null,
    selectedStoredSessionIdRef: ref<string | null>(null),
    sessionStateByRuntimeIdRef: ref(new Map<string, ClientSessionState>()),
    syncSessionStateToView: vi.fn(),
    updateSessionState: () => ({}) as ClientSessionState
  })

  useEffect(() => {
    onReady({ archiveSession: actions.archiveSession, removeSession: actions.removeSession })
  }, [actions, onReady])

  return null
}

async function mountHarness(): Promise<Handle> {
  let handle: Handle | undefined
  render(<Harness onReady={value => (handle = value)} />)
  await waitFor(() => expect(handle).toBeDefined())
  return handle as Handle
}

describe('Desktop conversation mutations', () => {
  beforeEach(() => {
    setSessions([])
    $archivedSessions.set([])
    $removedSessionIds.set(new Set())
    $sessionMutationsInFlight.set(new Set())
    vi.mocked(deleteSession).mockReset()
    vi.mocked(setSessionArchived).mockReset()
  })

  afterEach(() => {
    cleanup()
    setSessions([])
    $archivedSessions.set([])
    $removedSessionIds.set(new Set())
    $sessionMutationsInFlight.set(new Set())
  })

  it('keeps an iOS-source conversation manageable and archives it normally', async () => {
    setSessions([appSession()])
    vi.mocked(setSessionArchived).mockResolvedValue({ ok: true })
    const handle = await mountHarness()

    await act(() => handle.archiveSession('root-ios'))

    expect(setSessionArchived).toHaveBeenCalledWith('root-ios', true, undefined)
    expect($sessions.get()).toEqual([])
  })

  it('tombstones every backend-expanded row after successful delete', async () => {
    setSessions([appSession(), appSession({ _lineage_root_id: undefined, id: 'branch', source: 'desktop' })])
    vi.mocked(deleteSession).mockResolvedValue({
      app_chat_id: 'root-ios',
      deleted_count: 3,
      deleted_ids: ['root-ios', 'tip-ios', 'delegate-ios'],
      ok: true
    })
    const handle = await mountHarness()

    await act(() => handle.removeSession('root-ios'))

    expect($sessions.get().map(session => session.id)).toEqual(['branch'])
    expect([...$removedSessionIds.get()].sort()).toEqual(['delegate-ios', 'root-ios', 'tip-ios'])
  })

  it('rolls back optimistic state when the backend reports a conflict', async () => {
    const original = appSession()
    setSessions([original])
    vi.mocked(deleteSession).mockRejectedValue(new Error('HTTP 409 conflict'))
    const handle = await mountHarness()

    await act(() => handle.removeSession('root-ios'))

    expect($sessions.get().map(session => session.id)).toEqual(['tip-ios'])
    expect([...$removedSessionIds.get()]).toEqual([])
  })
})
