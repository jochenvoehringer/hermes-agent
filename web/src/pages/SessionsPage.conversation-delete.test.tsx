import { describe, expect, it } from "vitest";

import type { SessionInfo } from "../lib/api";
import {
  conversationDeleteIds,
  conversationDeleteRowCount,
  evictDeletedSessions,
} from "./SessionsPage";

function session(id: string): SessionInfo {
  return { id } as SessionInfo;
}

describe("SessionsPage conversation delete reconciliation", () => {
  it("evicts every backend-expanded technical row after one visible delete", () => {
    const response = {
      app_chat_id: "root",
      deleted_count: 3,
      deleted_ids: ["root", "tip", "delegate"],
      ok: true,
    };

    const ids = conversationDeleteIds(response, ["root"]);

    expect(ids).toEqual(["root", "tip", "delegate"]);
    expect(
      evictDeletedSessions(
        [session("root"), session("tip"), session("branch")],
        ids,
      ).map((row) => row.id),
    ).toEqual(["branch"]);
    expect(conversationDeleteRowCount(response, ids)).toBe(3);
  });

  it("uses the legacy request-id and deleted-count fallback during staged rollout", () => {
    const legacyResponse = { deleted: 1, ok: true };

    const ids = conversationDeleteIds(legacyResponse, ["root"]);

    expect(ids).toEqual(["root"]);
    expect(conversationDeleteRowCount(legacyResponse, ids)).toBe(1);
  });

  it("evicts the requested ghost when an idempotent delete reports no rows", () => {
    expect(
      conversationDeleteIds({ deleted_count: 0, deleted_ids: [], ok: true }, [
        "ghost",
      ]),
    ).toEqual(["ghost"]);
  });

  it("prefers deleted_rows for bulk totals", () => {
    const response = {
      deleted: 1,
      deleted_conversations: 1,
      deleted_ids: ["root", "tip", "delegate"],
      deleted_rows: 3,
      ok: true,
    };

    expect(conversationDeleteRowCount(response, response.deleted_ids)).toBe(3);
  });
});
