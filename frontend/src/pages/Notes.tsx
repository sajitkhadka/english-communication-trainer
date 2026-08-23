import { useState } from "react";

import { ApiError, api } from "../api";
import { ErrorBox, Markdown } from "../components/common";
import { useAsync, useDocumentTitle, useRefreshOnFocus } from "../hooks";

/** The learning-notes wiki (`data/learning-notes.md`): sentence patterns, phrases being
 *  moved from passive to active, recurring corrections.
 *
 *  This is the durable half of the feedback loop - a session's feedback is read once and
 *  never again, this is what carries forward. Both the user and `/process-session` write
 *  to it, which is why every save carries the version it started from. */
export default function Notes() {
  useDocumentTitle("Notes");
  const notes = useAsync(() => api.notes(), []);
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  const editing = draft !== null;

  // `/process-session` edits this file from a terminal, so returning to the tab is
  // exactly when the page is stale. Never while an editor is open, though - refetching
  // under someone's unsaved edit is the one thing worse than showing it stale.
  useRefreshOnFocus(() => {
    if (!editing) notes.reload();
  });

  const startEditing = () => {
    setError(null);
    setConflict(false);
    setDraft(notes.data?.markdown ?? "");
  };

  const cancel = () => {
    setDraft(null);
    setError(null);
    setConflict(false);
  };

  const save = async () => {
    if (draft === null || !notes.data) return;
    setSaving(true);
    setError(null);
    try {
      await api.saveNotes(draft, notes.data.version);
      setDraft(null);
      setConflict(false);
      notes.reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Pull the newer version in so it can be shown alongside the draft, and so a
        // second save goes through once the two have been merged by hand.
        setConflict(true);
        notes.reload();
      }
      setError(err instanceof Error ? err.message : "Could not save the notes.");
    } finally {
      setSaving(false);
    }
  };

  if (notes.loading && !notes.data) return <p className="muted">Loading notes…</p>;

  return (
    <section>
      <div className="card-head">
        <div>
          <h2>Learning notes</h2>
          <p className="card-sub">
            What has already been coached — the patterns, phrases and corrections worth
            carrying into the next session. <code>/process-session</code> reads this before
            coaching and folds new lessons back in afterwards.
          </p>
        </div>
        {!editing && (
          <button type="button" onClick={startEditing} disabled={!notes.data}>
            Edit
          </button>
        )}
      </div>

      <ErrorBox error={notes.error} />

      {editing ? (
        <div className="card">
          <div className="card-head">
            <h3>Editing</h3>
            <div className="btn-row">
              <button type="button" onClick={cancel} disabled={saving}>
                Cancel
              </button>
              <button type="button" className="primary" onClick={save} disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
          <textarea
            className="notes-editor"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            spellCheck
          />
          <p className="muted small">
            Markdown. Consolidate rather than append — merge duplicates and drop what you
            have genuinely internalised, or the file grows past the point anyone reads it.
          </p>
          <ErrorBox error={error} />
          {conflict && notes.data && (
            <>
              <p className="muted small">
                The version now on disk is below. Merge what you need into your draft above,
                then save again — your edit has not been lost.
              </p>
              <div className="card">
                <Markdown>{notes.data.markdown}</Markdown>
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="card">
          <div className="card-head">
            <h3>{notes.data?.path ?? "learning-notes.md"}</h3>
            <span className="muted small">Shared with Claude · not in git</span>
          </div>
          <Markdown>{notes.data?.markdown ?? ""}</Markdown>
        </div>
      )}
    </section>
  );
}
