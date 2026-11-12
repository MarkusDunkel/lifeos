import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { languages } from "@codemirror/language-data";
import { EditorView } from "@codemirror/view";
import CodeMirror from "@uiw/react-codemirror";
import { useEffect, useState } from "react";

const extensions = [markdown({ base: markdownLanguage, codeLanguages: languages }), EditorView.lineWrapping];

function usePrefersDark() {
  const query = "(prefers-color-scheme: dark)";
  const [dark, setDark] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setDark(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return dark;
}

export function MarkdownEditor({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const dark = usePrefersDark();
  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      extensions={extensions}
      theme={dark ? "dark" : "light"}
      basicSetup={{ lineNumbers: false, foldGutter: false, highlightActiveLine: false }}
      className="h-full overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800"
      autoFocus
    />
  );
}
