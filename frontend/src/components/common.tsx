import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Mode, SessionStatus } from "../types";

export const MODE_LABEL: Record<Mode, string> = {
  recommended: "Recommended",
  freeform: "Free-form",
  interview: "Interview",
  worklog: "Worklog",
  brainstorm: "Brainstorm",
  journal: "Daily journal",
};

/** Slash command that turns a queued session into feedback/entry/ideas, or null when
 * the mode is never AI-processed at all (journal). */
export function processCommand(mode: Mode): string | null {
  if (mode === "worklog") return "/log-work";
  if (mode === "brainstorm") return "/process-brainstorm";
  if (mode === "journal") return null;
  return "/process-session";
}

/** What to call the markdown/text card a processed session renders. */
export function feedbackCardTitle(mode: Mode): string {
  if (mode === "worklog") return "Journal entry";
  if (mode === "brainstorm") return "Ideas";
  if (mode === "journal") return "Journal";
  return "Feedback";
}

const STATUS_LABEL: Record<SessionStatus, string> = {
  awaiting_recording: "Awaiting recording",
  recorded: "Recorded",
  pending: "Queued for Claude",
  processed: "Processed",
};

export function StatusPill({ status }: { status: SessionStatus }) {
  return <span className={`pill ${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="error">{error}</p>;
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}

export function Tile({
  label,
  value,
  sub,
  delta,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  delta?: number | null;
}) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">
        {value}
        {delta != null && delta !== 0 && (
          <span className={`delta small ${delta > 0 ? "up" : "down"}`}>
            {" "}
            {delta > 0 ? "↑" : "↓"}
            {Math.abs(delta).toFixed(1)}
          </span>
        )}
      </div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
  });
}

export function formatClock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function SessionLink({ id, children }: { id: number; children: ReactNode }) {
  return <Link to={`/session/${id}`}>{children}</Link>;
}

/** Dimension keys as stored, plus display labels. */
export const DIMENSIONS: { key: string; label: string }[] = [
  { key: "vocab_range", label: "Vocabulary" },
  { key: "filler_density", label: "Fillers" },
  { key: "fluency", label: "Fluency" },
  { key: "grammar", label: "Grammar" },
  { key: "structure", label: "Structure" },
  { key: "coherence", label: "Coherence" },
  { key: "target_usage", label: "Target words" },
];
