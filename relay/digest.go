package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
)

// ErrNoDigest means nothing has ever been pushed - a relay deployed before the agent
// first ran. Every offline read answers 503 until then, which is honest.
var ErrNoDigest = errors.New("no digest snapshot has been pushed yet")

// ErrNotInDigest means the route is real but the snapshot cannot answer it: audio,
// transcripts, briefs and prompt files are all deliberately absent (see app/digest.py).
var ErrNotInDigest = errors.New("not available offline")

// Digest holds the last snapshot `ect agent` pushed. Read-only by construction: the
// relay serves from it and never writes into it, which is what keeps
// services.write_notes' version/409 contract a local concern rather than a
// distributed one (ADR 0006, §3).
type Digest struct {
	mu   sync.RWMutex
	path string
	raw  json.RawMessage
	doc  digestDoc
}

type digestDoc struct {
	Version         string                     `json:"version"`
	GeneratedAt     string                     `json:"generated_at"`
	SchemaVersion   int                        `json:"schema_version"`
	FeedbackHorizon int                        `json:"feedback_horizon"`
	Health          json.RawMessage            `json:"health"`
	Sessions        []map[string]any           `json:"sessions"`
	SessionDetails  map[string]json.RawMessage `json:"session_details"`
	Notes           json.RawMessage            `json:"notes"`
	Words           []map[string]any           `json:"words"`
	WordsDue        json.RawMessage            `json:"words_due"`
	WordStats       json.RawMessage            `json:"word_stats"`
	Suggestions     json.RawMessage            `json:"suggestions"`
	Queue           json.RawMessage            `json:"queue"`
	Progress        json.RawMessage            `json:"progress"`
}

func OpenDigest(dataDir string) (*Digest, error) {
	d := &Digest{path: filepath.Join(dataDir, "digest.json")}
	raw, err := os.ReadFile(d.path)
	if errors.Is(err, os.ErrNotExist) {
		return d, nil // fine: the agent has simply not pushed one yet
	}
	if err != nil {
		return nil, fmt.Errorf("read digest: %w", err)
	}
	if err := d.load(raw); err != nil {
		// A corrupt snapshot must not stop the relay booting - capture still works
		// without it, and the next agent push replaces it.
		return d, nil //nolint:nilerr
	}
	return d, nil
}

func (d *Digest) load(raw json.RawMessage) error {
	var doc digestDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		return err
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	d.raw, d.doc = raw, doc
	return nil
}

// Store persists a freshly pushed snapshot, atomically: a relay restart mid-write
// must not come back holding half a file.
func (d *Digest) Store(raw json.RawMessage) error {
	if err := d.load(raw); err != nil {
		return fmt.Errorf("digest is not valid JSON: %w", err)
	}
	tmp := d.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o640); err != nil {
		return err
	}
	return os.Rename(tmp, d.path)
}

func (d *Digest) Meta() (version, generatedAt string, sessions int) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.doc.Version, d.doc.GeneratedAt, len(d.doc.Sessions)
}

func (d *Digest) Raw() (json.RawMessage, error) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if d.raw == nil {
		return nil, ErrNoDigest
	}
	return d.raw, nil
}

// Answer serves one GET from the snapshot.
//
// The route table below mirrors app/digest.py exactly. Anything not listed is a route
// the relay must refuse rather than guess at, and `ErrNotInDigest` is how it says so -
// the frontend surfaces `detail`, so the user reads "not available offline" instead of
// watching a spinner.
func (d *Digest) Answer(path string, query url.Values) (contentType string, body []byte, err error) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if d.raw == nil {
		return "", nil, ErrNoDigest
	}
	doc := d.doc

	switch {
	case path == "/api/health":
		return jsonBody(withOffline(doc.Health))
	case path == "/api/digest":
		return "application/json", d.raw, nil
	case path == "/api/notes":
		return jsonBody(doc.Notes)
	case path == "/api/words":
		return jsonBody(rawOf(doc.Words))
	case path == "/api/words/due":
		return jsonBody(doc.WordsDue)
	case path == "/api/words/stats":
		return jsonBody(doc.WordStats)
	case path == "/api/progress":
		return jsonBody(doc.Progress)
	case path == "/api/queue":
		return jsonBody(doc.Queue)
	case path == "/api/suggestions":
		return jsonBody(doc.Suggestions)
	case path == "/api/sessions":
		return jsonBody(rawOf(filterSessions(doc.Sessions, query)))
	}

	if id, rest, ok := sessionRoute(path); ok {
		session := findSession(doc.Sessions, id)
		if session == nil {
			return "", nil, ErrNotFound
		}
		switch rest {
		case "":
			return jsonBody(rawOf(sessionDetail(session, doc.SessionDetails[id])))
		case "feedback":
			detail := decodeDetail(doc.SessionDetails[id])
			md, _ := detail["feedback_markdown"].(string)
			if md == "" {
				return "", nil, ErrNotFound
			}
			return "text/plain; charset=utf-8", []byte(md), nil
		}
		// audio, transcript, brief, prompt: files, never in the snapshot.
		return "", nil, ErrNotInDigest
	}
	return "", nil, ErrNotInDigest
}

// sessionRoute splits /api/sessions/{id}[/{rest}].
func sessionRoute(path string) (id, rest string, ok bool) {
	const prefix = "/api/sessions/"
	if !strings.HasPrefix(path, prefix) {
		return "", "", false
	}
	tail := strings.Trim(strings.TrimPrefix(path, prefix), "/")
	if tail == "" {
		return "", "", false
	}
	id, rest, _ = strings.Cut(tail, "/")
	if _, err := strconv.Atoi(id); err != nil {
		return "", "", false
	}
	return id, rest, true
}

func findSession(sessions []map[string]any, id string) map[string]any {
	want, err := strconv.ParseFloat(id, 64)
	if err != nil {
		return nil
	}
	for _, s := range sessions {
		if n, ok := s["id"].(float64); ok && n == want {
			return s
		}
	}
	return nil
}

// sessionDetail rebuilds the SessionDetail shape GET /api/sessions/{id} returns.
func sessionDetail(session map[string]any, detail json.RawMessage) map[string]any {
	out := make(map[string]any, len(session)+2)
	for k, v := range session {
		out[k] = v
	}
	parsed := decodeDetail(detail)
	out["feedback_markdown"] = parsed["feedback_markdown"]
	if twd, ok := parsed["target_words_detail"]; ok {
		out["target_words_detail"] = twd
	} else {
		out["target_words_detail"] = []any{}
	}
	return out
}

func decodeDetail(raw json.RawMessage) map[string]any {
	out := map[string]any{}
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &out)
	}
	return out
}

// filterSessions applies the same mode/status/limit query parameters the real
// GET /api/sessions accepts, so the frontend's calls do not change shape offline.
func filterSessions(sessions []map[string]any, query url.Values) []map[string]any {
	mode := query.Get("mode")
	status := query.Get("status")
	limit := 200
	if raw := query.Get("limit"); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil && n > 0 {
			limit = n
		}
	}
	out := make([]map[string]any, 0, len(sessions))
	for _, s := range sessions {
		if mode != "" && s["mode"] != mode {
			continue
		}
		if status != "" && s["status"] != status {
			continue
		}
		out = append(out, s)
		if len(out) >= limit {
			break
		}
	}
	return out
}

// withOffline stamps the health payload so the UI can say *why* it is read-only rather
// than silently showing a snapshot as if it were live.
func withOffline(health json.RawMessage) json.RawMessage {
	parsed := map[string]any{}
	if len(health) > 0 {
		_ = json.Unmarshal(health, &parsed)
	}
	parsed["pc_online"] = false
	parsed["source"] = "digest"
	return rawOf(parsed)
}

func rawOf(v any) json.RawMessage {
	b, err := json.Marshal(v)
	if err != nil {
		return json.RawMessage(`null`)
	}
	return b
}

func jsonBody(raw json.RawMessage) (string, []byte, error) {
	if len(raw) == 0 {
		return "", nil, ErrNotInDigest
	}
	return "application/json", raw, nil
}

// writeDigestError turns the sentinel errors above into the `detail` shape
// frontend/src/api.ts already surfaces, so an offline read degrades into a readable
// message with no frontend change.
func writeDigestError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrNotFound):
		writeJSONError(w, http.StatusNotFound, "not in the offline snapshot")
	case errors.Is(err, ErrNoDigest):
		writeJSONError(w, http.StatusServiceUnavailable,
			"PC offline, and no offline snapshot has been received yet")
	default:
		writeJSONError(w, http.StatusServiceUnavailable,
			"PC offline - this needs the PC (audio, transcripts and briefs are never mirrored)")
	}
}
