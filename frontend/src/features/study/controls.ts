/** Control words the voice coach (ChatGPT via MCP) reacts to. Keep in sync with
 * INSTRUCTIONS in backend/src/lifeos/mcp_server/server.py. The words are spoken in the
 * learner's language, so they stay German; the explanations are English UI text. */
export const CONTROL_WORDS: { word: string; meaning: string }[] = [
  { word: "fertig", meaning: "My turn is over – now you may answer." },
  { word: "bewerten", meaning: "Grade everything I said since the question and save it." },
  { word: "weiter", meaning: "Next card (an unsaved answer is graded first)." },
  { word: "kenn ich", meaning: "Too easy – move the card to the top box." },
  { word: "zählt als richtig", meaning: "Change the last grade (the coach does not argue)." },
  { word: "Schluss", meaning: "End the session; the coach reads the saved count aloud." },
  { word: "Hilfe", meaning: "The coach says these words again." },
];

export const CONTROL_HINTS = [
  "A control word at the end of a turn also ends it – no extra “fertig” needed.",
  "Questions before “bewerten” are fine, but the grade is then at most partial.",
  "After the grade you can keep talking (why, examples, web) until you say “weiter”.",
];

const OPEN_KEY = "lifeos.study.controlsOpen";

export function controlsOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) !== "0";
  } catch {
    return true;
  }
}

export function rememberControlsOpen(open: boolean): void {
  try {
    localStorage.setItem(OPEN_KEY, open ? "1" : "0");
  } catch {
    // storage blocked: the box just opens again next time
  }
}
