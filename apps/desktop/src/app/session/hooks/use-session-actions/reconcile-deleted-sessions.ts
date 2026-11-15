import type { deleteSession } from '@/hermes'
import { clearQueuedPrompts } from '@/store/composer-queue'
import { setSessions } from '@/store/session'
import { tombstoneSessions } from '@/store/session-removal'
import { $archivedSessions } from '@/store/sidebar-archive'
import { dropTranscriptTailEverywhere } from '@/store/transcript-tail-cache'

export function reconcileDeletedSessions(
  deleteResult: Awaited<ReturnType<typeof deleteSession>>,
  removedIds: (string | null | undefined)[]
): string[] {
  const deletedIds = deleteResult.already_absent
    ? removedIds
    : deleteResult.deleted_ids ?? removedIds

  const confirmedDeletedIds = deletedIds.filter((id): id is string => Boolean(id))
  const deletedSet = new Set(confirmedDeletedIds)
  setSessions(prev => prev.filter(session =>
    !deletedSet.has(session.id) &&
    !(session._lineage_root_id && deletedSet.has(session._lineage_root_id))))
  $archivedSessions.set($archivedSessions.get().filter(session =>
    !deletedSet.has(session.id) &&
    !(session._lineage_root_id && deletedSet.has(session._lineage_root_id))))
  tombstoneSessions(confirmedDeletedIds)

  for (const deletedId of confirmedDeletedIds) {
    dropTranscriptTailEverywhere(deletedId)
    clearQueuedPrompts(deletedId)
  }

  return confirmedDeletedIds
}
