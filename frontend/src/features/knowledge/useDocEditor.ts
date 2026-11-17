import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../lib/api";
import { docQuery, saveDoc } from "./api";

type Options = {
  /** Ctrl/Cmd+S saves while true (only one editor on the page should listen). */
  shortcuts?: boolean;
  /** Called after a successful save, e.g. to refresh views built from the file. */
  onSaved?: () => Promise<unknown> | void;
};

/**
 * Loading, editing and saving one knowledge file with the revision check:
 * a save of a file changed elsewhere fails with 409 and sets ``conflict`` to the
 * current revision, so the user can load theirs or overwrite with theirs.
 */
export function useDocEditor(path: string, { shortcuts = true, onSaved }: Options = {}) {
  const queryClient = useQueryClient();
  const doc = useQuery(docQuery(path));
  const [draft, setDraft] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const dirty = draft !== null && doc.data !== undefined && draft !== doc.data.content;

  const save = useMutation({
    mutationFn: (revision: string | null) => saveDoc(path, draft ?? "", revision),
    onSuccess: async ({ revision }) => {
      queryClient.setQueryData(docQuery(path).queryKey, { path, content: draft ?? "", revision });
      setConflict(null);
      await queryClient.invalidateQueries({ queryKey: ["knowledge", "backlinks"] });
      await onSaved?.();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        setConflict((e.body as { currentRevision: string | null }).currentRevision);
      }
    },
  });

  const doSave = useCallback(() => {
    if (doc.data && dirty && !save.isPending) save.mutate(doc.data.revision);
  }, [doc.data, dirty, save]);

  useEffect(() => {
    if (!shortcuts) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        doSave();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doSave, shortcuts]);

  // Warn before leaving with unsaved changes.
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  return {
    doc,
    content: draft ?? doc.data?.content ?? "",
    setDraft,
    dirty,
    saving: save.isPending,
    saveError: save.isError && !(save.error instanceof ApiError && save.error.status === 409) ? save.error : null,
    doSave,
    conflict,
    /** Throw away local changes. */
    discard: () => setDraft(null),
    /** Resolve a conflict by reloading the file (local changes are lost). */
    loadTheirs: async () => {
      setDraft(null);
      setConflict(null);
      await doc.refetch();
    },
    /** Resolve a conflict by saving the local version over the other one. */
    overwrite: () => {
      if (conflict !== null) save.mutate(conflict);
    },
  };
}

export type DocEditorState = ReturnType<typeof useDocEditor>;
