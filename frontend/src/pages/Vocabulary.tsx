import { useState } from "react";

import { api } from "../api";
import { Empty, ErrorBox, Tile, formatDate } from "../components/common";
import { useAsync, useDocumentTitle } from "../hooks";

const SORTS = [
  { key: "recency", label: "Recent" },
  { key: "mastery", label: "Weakest" },
  { key: "frequency", label: "Most practised" },
  { key: "due", label: "Due first" },
  { key: "alpha", label: "A–Z" },
];

export default function Vocabulary() {
  useDocumentTitle("Vocabulary");
  const [sort, setSort] = useState("recency");
  const words = useAsync(() => api.words(sort), [sort]);
  const stats = useAsync(() => api.wordStats(), []);

  const rows = words.data ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Vocabulary</h1>
          <p>
            Everything learned so far, with spaced-repetition state. Scheduling changes
            only when a session is processed.
          </p>
        </div>
        <div className="mode-tabs">
          {SORTS.map((option) => (
            <button
              key={option.key}
              className={sort === option.key ? "on" : ""}
              onClick={() => setSort(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <ErrorBox error={words.error ?? stats.error} />

      {stats.data && (
        <div className="tiles" style={{ marginBottom: "1rem" }}>
          <Tile label="Words" value={stats.data.total} sub="in the corpus" />
          <Tile label="Due" value={stats.data.due} sub="ready to resurface" />
          <Tile label="Mastered" value={stats.data.mastered} sub="mastery ≥ 0.8" />
          <Tile
            label="Avg mastery"
            value={stats.data.avg_mastery.toFixed(2)}
            sub={`${stats.data.never_practiced} never practised`}
          />
        </div>
      )}

      <div className="card">
        {rows.length === 0 ? (
          <Empty title="No vocabulary yet">
            <p>
              Run <code>/generate-topic</code> to get your first set of target words, or{" "}
              <code>/vocab-review</code> once you have a few sessions in.
            </p>
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Term</th>
                  <th>Kind</th>
                  <th>Meaning</th>
                  <th>Mastery</th>
                  <th className="num">Seen</th>
                  <th>Due</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((word) => (
                  <tr key={word.id}>
                    <td>
                      <span className="term">{word.term}</span>
                      {word.notes && (
                        <div className="session-row-meta" title="Recorded misuse">
                          {word.notes}
                        </div>
                      )}
                    </td>
                    <td className="small muted">{word.kind ?? "—"}</td>
                    <td className="small">
                      {word.meaning ?? <span className="muted">—</span>}
                      {word.example && (
                        <div className="session-row-meta">“{word.example}”</div>
                      )}
                    </td>
                    <td className="small">
                      <span className="mastery-bar">
                        <span style={{ width: `${Math.round(word.mastery * 100)}%` }} />
                      </span>
                      <span className="tnum">{word.mastery.toFixed(2)}</span>
                    </td>
                    <td className="num">
                      {word.times_seen}
                      {word.times_seen > 0 && (
                        <span className="muted"> · {word.times_used_correctly} ok</span>
                      )}
                    </td>
                    <td className="small">
                      {word.is_due ? (
                        <span className="pill pending">due</span>
                      ) : (
                        <span className="muted">{formatDate(word.due_date)}</span>
                      )}
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
