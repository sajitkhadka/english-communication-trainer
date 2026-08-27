package main

import (
	"database/sql"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

// ErrNotFound is returned for a uid the inbox has never seen, or has already acked.
var ErrNotFound = errors.New("inbox item not found")

// Modes the recorder may file a capture under. Deliberately only the four that need no
// prior setup - `recommended` and `interview` are built by /generate-topic with target
// words attached, which is desk work by definition (ADR 0006, Consequences).
var remoteModes = map[string]bool{
	"freeform":   true,
	"worklog":    true,
	"brainstorm": true,
	"journal":    true,
}

const inboxSchema = `
PRAGMA journal_mode = WAL;

-- One row per capture waiting to reach the PC. The audio itself is a file under
-- blobs/, named by uid; this table is the index over it.
--
-- The uid is minted by the *recorder*, not here: the phone may retry an upload whose
-- response it never saw, and a client-side id is what makes that retry an overwrite
-- instead of a second copy. The same uid then travels to the PC as
-- sessions.external_uid, so the drain is idempotent for the same reason.
CREATE TABLE IF NOT EXISTS inbox (
  uid          TEXT PRIMARY KEY,
  mode         TEXT NOT NULL,
  topic        TEXT,
  notes        TEXT,
  filename     TEXT,
  content_type TEXT,
  bytes        INTEGER NOT NULL,
  created_at   TEXT NOT NULL,
  -- Set when the agent confirms the recording is on the PC's disk. The blob is
  -- deleted at the same moment: a worklog recording carries employer and project
  -- detail, and this server is publicly addressable.
  acked_at     TEXT,
  session_id   INTEGER,
  attempts     INTEGER NOT NULL DEFAULT 0,
  last_error   TEXT
);

CREATE INDEX IF NOT EXISTS idx_inbox_pending ON inbox(acked_at, created_at);
`

// Item is one capture in the inbox, as `ect agent` sees it.
type Item struct {
	UID         string `json:"uid"`
	Mode        string `json:"mode"`
	Topic       string `json:"topic,omitempty"`
	Notes       string `json:"notes,omitempty"`
	Filename    string `json:"filename,omitempty"`
	ContentType string `json:"content_type,omitempty"`
	Bytes       int64  `json:"bytes"`
	CreatedAt   string `json:"created_at"`
	Attempts    int    `json:"attempts"`
	LastError   string `json:"last_error,omitempty"`
	SessionID   *int64 `json:"session_id,omitempty"`
	AckedAt     string `json:"acked_at,omitempty"`
}

// Inbox is the blob store plus its index.
type Inbox struct {
	db       *sql.DB
	blobDir  string
	maxBytes int64
}

func OpenInbox(dataDir string, maxBytes int64) (*Inbox, error) {
	blobDir := filepath.Join(dataDir, "blobs")
	if err := os.MkdirAll(blobDir, 0o750); err != nil {
		return nil, fmt.Errorf("create blob dir: %w", err)
	}
	db, err := sql.Open("sqlite", filepath.Join(dataDir, "inbox.db"))
	if err != nil {
		return nil, fmt.Errorf("open inbox.db: %w", err)
	}
	// One writer. SQLite allows exactly one anyway, and serialising here turns a
	// SQLITE_BUSY under concurrent uploads into a short wait instead of an error.
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(inboxSchema); err != nil {
		db.Close()
		return nil, fmt.Errorf("apply inbox schema: %w", err)
	}
	return &Inbox{db: db, blobDir: blobDir, maxBytes: maxBytes}, nil
}

func (i *Inbox) Close() error { return i.db.Close() }

func (i *Inbox) blobPath(uid string) string { return filepath.Join(i.blobDir, uid+".blob") }

// validUID keeps a uid usable as a filename. The recorder sends a crypto.randomUUID(),
// but the endpoint is reachable from the internet, so this is a path-traversal guard
// rather than a format check.
func validUID(uid string) bool {
	if len(uid) < 8 || len(uid) > 64 {
		return false
	}
	for _, r := range uid {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '-', r == '_':
		default:
			return false
		}
	}
	return true
}

// Put stores one capture. Re-putting a uid overwrites it, so a retried upload from a
// phone on a flaky connection costs bandwidth and nothing else.
func (i *Inbox) Put(item Item, body io.Reader) (Item, error) {
	if !validUID(item.UID) {
		return item, fmt.Errorf("invalid uid")
	}
	if !remoteModes[item.Mode] {
		return item, fmt.Errorf(
			"mode %q cannot be captured remotely - use one of freeform, worklog, brainstorm, journal",
			item.Mode,
		)
	}

	// Write to a temp file first: a connection that drops mid-upload must not leave a
	// truncated blob indexed as a complete recording.
	tmp, err := os.CreateTemp(i.blobDir, ".upload-*")
	if err != nil {
		return item, fmt.Errorf("stage upload: %w", err)
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)

	written, err := io.Copy(tmp, io.LimitReader(body, i.maxBytes+1))
	closeErr := tmp.Close()
	if err != nil {
		return item, fmt.Errorf("read upload: %w", err)
	}
	if closeErr != nil {
		return item, fmt.Errorf("stage upload: %w", closeErr)
	}
	if written > i.maxBytes {
		return item, fmt.Errorf("recording exceeds the %d byte limit", i.maxBytes)
	}
	if written == 0 {
		return item, fmt.Errorf("uploaded recording is empty")
	}

	if err := os.Rename(tmpName, i.blobPath(item.UID)); err != nil {
		return item, fmt.Errorf("store blob: %w", err)
	}

	item.Bytes = written
	item.CreatedAt = time.Now().UTC().Format(time.RFC3339)
	_, err = i.db.Exec(
		`INSERT INTO inbox (uid, mode, topic, notes, filename, content_type, bytes, created_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		 ON CONFLICT(uid) DO UPDATE SET
		   mode=excluded.mode, topic=excluded.topic, notes=excluded.notes,
		   filename=excluded.filename, content_type=excluded.content_type,
		   bytes=excluded.bytes, created_at=excluded.created_at,
		   acked_at=NULL, session_id=NULL, attempts=0, last_error=NULL`,
		item.UID, item.Mode, nullable(item.Topic), nullable(item.Notes),
		nullable(item.Filename), nullable(item.ContentType), item.Bytes, item.CreatedAt,
	)
	if err != nil {
		return item, fmt.Errorf("index upload: %w", err)
	}
	return item, nil
}

// Pending lists undrained captures, oldest first - the order they were spoken in.
func (i *Inbox) Pending() ([]Item, error) {
	rows, err := i.db.Query(
		`SELECT uid, mode, COALESCE(topic,''), COALESCE(notes,''), COALESCE(filename,''),
		        COALESCE(content_type,''), bytes, created_at, attempts, COALESCE(last_error,'')
		   FROM inbox WHERE acked_at IS NULL ORDER BY created_at ASC`,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	items := []Item{}
	for rows.Next() {
		var it Item
		if err := rows.Scan(&it.UID, &it.Mode, &it.Topic, &it.Notes, &it.Filename,
			&it.ContentType, &it.Bytes, &it.CreatedAt, &it.Attempts, &it.LastError); err != nil {
			return nil, err
		}
		items = append(items, it)
	}
	return items, rows.Err()
}

// PendingCount is what the WoL ticker and /api/relay/status ask for.
func (i *Inbox) PendingCount() (int, error) {
	var n int
	err := i.db.QueryRow(`SELECT COUNT(*) FROM inbox WHERE acked_at IS NULL`).Scan(&n)
	return n, err
}

// Blob opens a stored recording for the agent to download.
func (i *Inbox) Blob(uid string) (*os.File, error) {
	if !validUID(uid) {
		return nil, ErrNotFound
	}
	f, err := os.Open(i.blobPath(uid))
	if errors.Is(err, os.ErrNotExist) {
		return nil, ErrNotFound
	}
	return f, err
}

// Ack marks a capture drained and deletes its blob.
//
// Deletion is part of the design, not cleanup: the inbox is the one place a recording
// sits on a publicly addressable machine, and it should sit there for as short a time
// as the network allows.
func (i *Inbox) Ack(uid string, sessionID int64) error {
	if !validUID(uid) {
		return ErrNotFound
	}
	res, err := i.db.Exec(
		`UPDATE inbox SET acked_at = ?, session_id = ?, last_error = NULL
		  WHERE uid = ? AND acked_at IS NULL`,
		time.Now().UTC().Format(time.RFC3339), sessionID, uid,
	)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		// Already acked, or never existed. Both are fine: the agent retries acks, and
		// a second ack for a drained item must not become an error it reports.
		var exists int
		if err := i.db.QueryRow(`SELECT COUNT(*) FROM inbox WHERE uid = ?`, uid).Scan(&exists); err != nil {
			return err
		}
		if exists == 0 {
			return ErrNotFound
		}
	}
	if err := os.Remove(i.blobPath(uid)); err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("delete blob: %w", err)
	}
	return nil
}

// Fail records a drain failure so the attempt count advances.
//
// Without it a blob the PC can never accept is retried on every poll forever, and the
// reason lives only in the PC's log - the one place unreachable from the phone that
// recorded it. `ect agent` skips an item past MAX_ATTEMPTS so one bad capture cannot
// starve the ones behind it.
func (i *Inbox) Fail(uid, message string) error {
	if !validUID(uid) {
		return ErrNotFound
	}
	if len(message) > 500 {
		message = message[:500]
	}
	res, err := i.db.Exec(
		`UPDATE inbox SET attempts = attempts + 1, last_error = ? WHERE uid = ? AND acked_at IS NULL`,
		message, uid,
	)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

// Recent lists the last n items, acked or not - what the relay's status page shows so
// "did it arrive?" is answerable from the phone.
func (i *Inbox) Recent(limit int) ([]Item, error) {
	rows, err := i.db.Query(
		`SELECT uid, mode, COALESCE(topic,''), bytes, created_at, COALESCE(acked_at,''),
		        session_id, attempts, COALESCE(last_error,'')
		   FROM inbox ORDER BY created_at DESC LIMIT ?`, limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	items := []Item{}
	for rows.Next() {
		var it Item
		var sessionID sql.NullInt64
		if err := rows.Scan(&it.UID, &it.Mode, &it.Topic, &it.Bytes, &it.CreatedAt,
			&it.AckedAt, &sessionID, &it.Attempts, &it.LastError); err != nil {
			return nil, err
		}
		if sessionID.Valid {
			v := sessionID.Int64
			it.SessionID = &v
		}
		items = append(items, it)
	}
	return items, rows.Err()
}

func nullable(s string) any {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	return s
}
