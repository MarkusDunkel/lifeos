import { describe, expect, it } from "vitest";
import server from "../../../../backend/src/lifeos/mcp_server/server.py?raw";
import { CONTROL_WORDS } from "./controls";

describe("voice coach control words", () => {
  it("match the coach instructions of the MCP server", () => {
    for (const { word } of CONTROL_WORDS) expect(server).toContain(`"${word}"`);
  });

  it("are unique and explained", () => {
    const words = CONTROL_WORDS.map((c) => c.word);
    expect(new Set(words).size).toBe(words.length);
    for (const c of CONTROL_WORDS) expect(c.meaning.length).toBeGreaterThan(10);
  });
});
