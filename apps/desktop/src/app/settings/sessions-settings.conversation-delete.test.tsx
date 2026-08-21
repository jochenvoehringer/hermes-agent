import { describe, expect, it } from 'vitest'

import { sessionDeleteIds } from '@/hermes'
import type { SessionInfo } from '@/types/hermes'

import { evictDeletedConversationRows } from './sessions-settings'

function session(id: string, root?: string): SessionInfo {
  return { _lineage_root_id: root, id } as SessionInfo
}

describe('archived settings conversation deletion', () => {
  it('evicts rows by returned technical ID or stable lineage root', () => {
    const rows = [session('tip', 'root'), session('delegate'), session('branch')]

    expect(
      evictDeletedConversationRows(rows, ['root', 'tip', 'delegate']).map(row => row.id)
    ).toEqual(['branch'])
  })

  it('leaves unrelated archived rows untouched', () => {
    const branch = session('branch')

    expect(evictDeletedConversationRows([branch], ['root'])).toEqual([branch])
  })

  it('falls back to the requested ghost ID after an idempotent delete', () => {
    expect(sessionDeleteIds({ deleted_count: 0, deleted_ids: [], ok: true }, ['ghost'])).toEqual(['ghost'])
  })
})
