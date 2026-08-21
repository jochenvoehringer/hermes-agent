import type { BulkSessionDeleteResponse, SessionDeleteResponse, SessionInfo } from './api'

type ConversationDeleteResponse = BulkSessionDeleteResponse | SessionDeleteResponse

export function conversationDeleteIds(response: ConversationDeleteResponse, requestedIds: string[]): string[] {
  const returnedIds = response.deleted_ids
  const source = Array.isArray(returnedIds) && returnedIds.length > 0 ? returnedIds : requestedIds
  return [...new Set(source.filter(id => typeof id === 'string' && id))]
}

export function conversationDeleteRowCount(response: ConversationDeleteResponse, deletedIds: string[]): number {
  if ('deleted_rows' in response && typeof response.deleted_rows === 'number') {
    return response.deleted_rows
  }
  if ('deleted_count' in response && typeof response.deleted_count === 'number') {
    return response.deleted_count
  }
  if ('deleted' in response && typeof response.deleted === 'number') {
    return response.deleted
  }
  return deletedIds.length
}

export function conversationDeleteVisibleCount(response: ConversationDeleteResponse, requestedIds: string[]): number {
  if (
    'deleted_conversations' in response &&
    typeof response.deleted_conversations === 'number' &&
    response.deleted_conversations >= 0
  ) {
    return response.deleted_conversations
  }
  return new Set(requestedIds.filter(id => typeof id === 'string' && id)).size
}

export function evictDeletedSessions(sessions: SessionInfo[], deletedIds: string[]): SessionInfo[] {
  const deleted = new Set(deletedIds)
  return sessions.filter(session => !deleted.has(session.id))
}
