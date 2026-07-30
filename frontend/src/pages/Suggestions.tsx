import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { Empty, ErrorBox, MODE_LABEL } from "../components/common";
import { useAsync, useDocumentTitle } from "../hooks";
import type { Mode } from "../types";

const MODES: Mode[] = ["recommended", "interview", "freeform"];

/** Empty until Claude writes something here (PRD 11). The request form is the other
 *  half of the queue contract: you ask for a category, the skill fulfils it. */
export default function Suggestions() {
  useDocumentTitle("Suggestions");
  const navigate = useNavigate();
  const suggestions = useAsync(() => api.suggestions(), []);
  const [mode, setMode] = useState<Mode>("recommended");
  const [category, setCategory] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const requestTopics = async () => {
    setError(null);
    try {
      const result = await api.requestSuggestions({ mode, category: category.trim() || null });
      setMessage(result.hint);
      setCategory("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not raise the request.");
    }
  };

  const startFrom = async (suggestionId: number) => {
    const suggestion = suggestions.data?.find((item) => item.id === suggestionId);
    if (!suggestion) return;
    setError(null);
    try {
      const session = await api.createSession({
        mode: suggestion.mode,
        topic: suggestion.topic,
        category: suggestion.category,
        target_words: suggestion.target_words,
      });
      await api.useSuggestion(session.id, suggestion.id);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start a session.");
    }
  };

  const rows = suggestions.data ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Suggestions</h1>
          <p>Topics Claude proposed for next time. Ask for a category and it will fill it.</p>
        </div>
      </div>

      <ErrorBox error={error} />
      {message && <p className="notice">{message}</p>}

      <div className="card">
        <div className="card-head">
          <h2>Request a category</h2>
        </div>
        <div className="btn-row">
          <div className="mode-tabs">
            {MODES.map((option) => (
              <button
                key={option}
                className={mode === option ? "on" : ""}
                onClick={() => setMode(option)}
              >
                {MODE_LABEL[option]}
              </button>
            ))}
          </div>
          <input
            style={{ flex: 1, minWidth: "14rem" }}
            value={category}
            placeholder="e.g. system-design interview, incident review, stakeholder update"
            onChange={(event) => setCategory(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void requestTopics();
            }}
          />
          <button className="primary" onClick={requestTopics}>
            Request
          </button>
        </div>
        <p className="card-sub" style={{ marginTop: "0.85rem", marginBottom: 0 }}>
          The request waits until you run <code>/generate-topic</code> in the console.
        </p>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Proposed topics</h2>
          <span className="muted small">{rows.length}</span>
        </div>
        {rows.length === 0 ? (
          <Empty title="Nothing suggested yet">
            <p>
              Claude adds suggestions here as it processes sessions. Run{" "}
              <code>/process-session</code> after a few recordings.
            </p>
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Topic</th>
                  <th>Mode</th>
                  <th>Target words</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((suggestion) => (
                  <tr key={suggestion.id}>
                    <td>
                      <div className="session-row-topic">{suggestion.topic}</div>
                      {suggestion.rationale && (
                        <div className="session-row-meta">{suggestion.rationale}</div>
                      )}
                    </td>
                    <td className="small">
                      {MODE_LABEL[suggestion.mode]}
                      {suggestion.category && (
                        <div className="session-row-meta">{suggestion.category}</div>
                      )}
                    </td>
                    <td>
                      {suggestion.target_words.length === 0 ? (
                        <span className="muted small">—</span>
                      ) : (
                        suggestion.target_words.map((term) => (
                          <span className="term" key={term}>
                            {term}
                          </span>
                        ))
                      )}
                    </td>
                    <td>
                      <button className="primary" onClick={() => startFrom(suggestion.id)}>
                        Use it
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
