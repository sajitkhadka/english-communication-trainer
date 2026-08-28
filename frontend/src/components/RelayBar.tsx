import type { RelayStatus } from "../types";
import { formatDuration } from "./common";

/** The one thing this app must not hide: which half is answering.
 *
 *  Served by the PC this renders nothing at all - `useRelay().status` is null and every
 *  path behaves as it always has. Served by the relay it says whether the PC is up,
 *  because that decides what the page can actually do: with the PC asleep, reads come
 *  from a snapshot that carries no audio and no transcripts, and every write is refused
 *  (ADR 0006). Showing stale rows as if they were live would be the one genuinely
 *  dishonest thing this design could do. */
export default function RelayBar({ status }: { status: RelayStatus | null }) {
  if (!status) return null;

  if (status.pc_online) {
    return (
      <div className="relay-bar online">
        <span className="relay-dot online" aria-hidden="true" />
        <span>
          Remote — your PC is up, so everything works as usual.
          {status.inbox_pending > 0 && ` ${status.inbox_pending} still arriving.`}
        </span>
      </div>
    );
  }

  const since = status.pc.seconds_since_heartbeat;
  return (
    <div className="relay-bar offline" role="status">
      <span className="relay-dot offline" aria-hidden="true" />
      <span>
        <strong>PC offline</strong> — you can still record, and read history from the last
        snapshot. Playback, transcripts and anything that writes need the PC.
        {status.inbox_pending > 0 && (
          <>
            {" "}
            <strong>{status.inbox_pending}</strong> waiting to reach it.
          </>
        )}
        {status.digest_at && (
          <span className="muted">
            {" "}
            Snapshot: {status.digest_sessions} sessions
            {since !== null && `, PC last seen ${formatDuration(since)} ago`}.
          </span>
        )}
      </span>
    </div>
  );
}
