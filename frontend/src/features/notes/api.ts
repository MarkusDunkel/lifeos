import { queryOptions } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { Cells, Field, Note } from "./grid";

export type NotesData = { fields: Field[]; notes: Note[] };

export const notesQuery = queryOptions({
  queryKey: ["notes"],
  queryFn: () => api<NotesData>("/api/notes"),
});

export const createNote = (cells: Cells) => api<Note>("/api/notes", { method: "POST", json: { cells } });

export const updateNote = (id: string, cells: Cells, revision: string) =>
  api<Note>(`/api/notes/${encodeURIComponent(id)}`, { method: "PUT", json: { cells, revision } });

export const deleteNote = (id: string, revision: string) =>
  api(`/api/notes/${encodeURIComponent(id)}/delete`, { method: "POST", json: { revision } });
