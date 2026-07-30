import { api } from "../api";
import { LineChart, Sparkline } from "../components/Charts";
import { DIMENSIONS, Empty, ErrorBox, Tile } from "../components/common";
import { useAsync, useDocumentTitle } from "../hooks";
import type { Score } from "../types";

export default function Progress() {
  useDocumentTitle("Progress");
  const progress = useAsync(() => api.progress(), []);
  const data = progress.data;

  if (progress.loading && !data) return <p className="muted">Loading…</p>;
  if (progress.error) return <ErrorBox error={progress.error} />;
  if (!data) return null;

  const history = data.history.filter((row) => row.overall != null);

  const points = history.map((row) => ({
    x: row.session_id,
    y: row.overall as number,
    label: row.topic ?? `Session ${row.session_id}`,
    sub: `${row.mode}${row.category ? ` · ${row.category}` : ""}`,
  }));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Progress</h1>
          <p>
            Overall is a weighted mean of the dimensions below, computed by the backend so
            it stays comparable across every session.
          </p>
        </div>
      </div>

      {history.length === 0 ? (
        <div className="card">
          <Empty title="No scores yet">
            <p>
              Record a session, press Process, then run <code>/process-session</code> in the
              Claude Code console.
            </p>
          </Empty>
        </div>
      ) : (
        <>
          <div className="tiles" style={{ marginBottom: "1rem" }}>
            <Tile
              label="Latest"
              value={data.latest?.toFixed(1) ?? "—"}
              sub="most recent session"
              delta={data.delta_vs_earlier}
            />
            <Tile label="Best" value={data.best?.toFixed(1) ?? "—"} sub="all time" />
            <Tile
              label="Recent avg"
              value={data.recent_average?.toFixed(1) ?? "—"}
              sub="last 5 sessions"
            />
            <Tile
              label="Sessions"
              value={data.sessions_scored}
              sub={`${data.vocabulary.total} words learned`}
            />
          </div>

          <div className="card">
            <div className="card-head">
              <h2>Overall score by session</h2>
              <span className="muted small">out of 10 · hover for the topic</span>
            </div>
            <LineChart points={points} />
          </div>

          <div className="card">
            <div className="card-head">
              <h2>By dimension</h2>
              <span className="muted small">
                each on its own 0–10 scale · latest value shown
              </span>
            </div>
            <div className="sparks">
              {DIMENSIONS.map((dimension) => {
                const values = history
                  .map((row) => row[dimension.key as keyof Score] as number | null)
                  .filter((value): value is number => value != null);
                if (values.length === 0) return null;
                return (
                  <div className="spark" key={dimension.key}>
                    <h4>{dimension.label}</h4>
                    <div className="now">{values.at(-1)!.toFixed(1)}</div>
                    <Sparkline values={values} />
                  </div>
                );
              })}
            </div>
            <p className="card-sub" style={{ marginTop: "1rem", marginBottom: 0 }}>
              Target-word usage is only scored for recommended and interview sessions, so
              its history skips free-form ones.
            </p>
          </div>

          <div className="card">
            <div className="card-head">
              <h2>All scores</h2>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Session</th>
                    <th>Mode</th>
                    {DIMENSIONS.map((dimension) => (
                      <th key={dimension.key} className="num">
                        {dimension.label}
                      </th>
                    ))}
                    <th className="num">Overall</th>
                  </tr>
                </thead>
                <tbody>
                  {[...history].reverse().map((row) => (
                    <tr key={row.id}>
                      <td>
                        <a href={`/session/${row.session_id}`}>
                          {row.topic ?? `Session ${row.session_id}`}
                        </a>
                      </td>
                      <td className="small muted">{row.mode}</td>
                      {DIMENSIONS.map((dimension) => {
                        const value = row[dimension.key as keyof Score] as number | null;
                        return (
                          <td key={dimension.key} className="num small">
                            {value != null ? value.toFixed(1) : <span className="muted">—</span>}
                          </td>
                        );
                      })}
                      <td className="num">
                        <strong>{row.overall?.toFixed(1)}</strong>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </>
  );
}
