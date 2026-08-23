import { NavLink, Route, Routes } from "react-router-dom";

import { api } from "./api";
import { useAsync, useInterval } from "./hooks";
import Notes from "./pages/Notes";
import Practice from "./pages/Practice";
import Progress from "./pages/Progress";
import SessionDetail from "./pages/SessionDetail";
import Sessions from "./pages/Sessions";
import Suggestions from "./pages/Suggestions";
import Vocabulary from "./pages/Vocabulary";

export default function App() {
  const queue = useAsync(() => api.queue(), []);
  // The queue changes outside this tab: the user drains it by running
  // /process-session in a terminal. Poll so the badge does not go stale.
  useInterval(() => queue.reload(), 15000);

  const pending = queue.data?.count ?? 0;

  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand">
          English Communication Trainer
        </NavLink>
        <nav className="nav">
          <NavLink to="/" end>
            Practice
          </NavLink>
          <NavLink to="/recommended">Recommended</NavLink>
          <NavLink to="/freeform">Free-form</NavLink>
          <NavLink to="/interview">Interview</NavLink>
          <NavLink to="/worklog">Worklog</NavLink>
          <NavLink to="/brainstorm">Brainstorm</NavLink>
          <NavLink to="/journal">Journal</NavLink>
          <NavLink to="/vocabulary">Vocabulary</NavLink>
          <NavLink to="/notes">Notes</NavLink>
          <NavLink to="/suggestions">Suggestions</NavLink>
          <NavLink to="/progress">Progress</NavLink>
        </nav>
        {pending > 0 && (
          <span className="queue-badge" title="Run /process-session in the Claude Code console">
            {pending} queued for Claude
          </span>
        )}
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Practice onQueueChange={queue.reload} />} />
          <Route
            path="/recommended"
            element={<Sessions mode="recommended" onQueueChange={queue.reload} />}
          />
          <Route
            path="/freeform"
            element={<Sessions mode="freeform" onQueueChange={queue.reload} />}
          />
          <Route
            path="/interview"
            element={<Sessions mode="interview" onQueueChange={queue.reload} />}
          />
          <Route
            path="/worklog"
            element={<Sessions mode="worklog" onQueueChange={queue.reload} />}
          />
          <Route
            path="/brainstorm"
            element={<Sessions mode="brainstorm" onQueueChange={queue.reload} />}
          />
          <Route
            path="/journal"
            element={<Sessions mode="journal" onQueueChange={queue.reload} />}
          />
          <Route path="/session/:id" element={<SessionDetail onQueueChange={queue.reload} />} />
          <Route path="/vocabulary" element={<Vocabulary />} />
          <Route path="/notes" element={<Notes />} />
          <Route path="/suggestions" element={<Suggestions />} />
          <Route path="/progress" element={<Progress />} />
          <Route
            path="*"
            element={
              <div className="empty">
                <h3>Page not found</h3>
                <p>
                  <NavLink to="/">Back to Practice</NavLink>
                </p>
              </div>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
