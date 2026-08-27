// Command relay is the server half of ADR 0006: a switchboard with an inbox, not a
// second copy of the app.
//
// It serves the same Vite build the PC serves, on the same relative `/api` base, and
// decides where those calls land: proxied to the PC over the LAN when the PC is up,
// answered from a read-only digest snapshot when it is not. Recordings made while the
// PC is off land in an inbox that `ect agent` drains later, so capture never depends on
// the desk - which is the entire point, since the sessions most worth having are the
// ones the desk cannot take: the worklog on the commute, the brainstorm on a walk.
//
// Two mechanisms guard it, on purpose. ingress-nginx basic-auth guards the browser
// side, and ECT_RELAY_TOKEN authenticates `ect agent`. Keeping them separate means the
// agent's long-lived credential is never the one typed into a phone.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)

	cfg, err := LoadConfig()
	if err != nil {
		log.Fatalf("configuration: %v", err)
	}
	if err := os.MkdirAll(cfg.DataDir, 0o750); err != nil {
		log.Fatalf("data dir: %v", err)
	}

	inbox, err := OpenInbox(cfg.DataDir, cfg.MaxUploadBytes)
	if err != nil {
		log.Fatalf("inbox: %v", err)
	}
	defer inbox.Close()

	digest, err := OpenDigest(cfg.DataDir)
	if err != nil {
		log.Fatalf("digest: %v", err)
	}

	presence := NewPresence(cfg.HeartbeatTTL)
	switchboard, err := NewSwitchboard(cfg.PCBaseURL, cfg.ProxyTimeout, presence, digest)
	if err != nil {
		log.Fatalf("switchboard: %v", err)
	}
	waker, err := NewWaker(cfg.WOLMac, cfg.WOLBroadcast, cfg.WOLCooldown)
	if err != nil {
		log.Fatalf("wake-on-lan: %v", err)
	}

	srv := &Server{cfg: cfg, inbox: inbox, digest: digest, presence: presence, board: switchboard}

	stop := make(chan struct{})
	go WatchInbox(inbox, presence, waker, 60*time.Second, stop)

	httpSrv := &http.Server{
		Addr:              cfg.Addr,
		Handler:           logging(srv.Routes()),
		ReadHeaderTimeout: 15 * time.Second,
		// No WriteTimeout: an inbox upload from a phone on mobile data is a long, slow
		// body, and cutting it off mid-transfer loses the recording it carries.
	}

	go func() {
		log.Printf("relay listening on %s (pc=%s, data=%s)", cfg.Addr, cfg.PCBaseURL, cfg.DataDir)
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("listen: %v", err)
		}
	}()

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, os.Interrupt, syscall.SIGTERM)
	<-sigs
	close(stop)

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := httpSrv.Shutdown(ctx); err != nil {
		log.Printf("shutdown: %v", err)
	}
	log.Print("relay stopped")
}

// Server wires the handlers. Exported fields so the tests can build one directly.
type Server struct {
	cfg      Config
	inbox    *Inbox
	digest   *Digest
	presence *Presence
	board    *Switchboard
}

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()

	// --- the recorder's own endpoints (guarded by ingress basic-auth) ---
	mux.HandleFunc("GET /api/relay/status", s.handleRelayStatus)
	mux.HandleFunc("POST /api/inbox", s.handleUpload)
	mux.HandleFunc("GET /api/inbox/recent", s.handleRecent)

	// --- the agent's endpoints (bearer token) ---
	mux.Handle("POST /api/agent/heartbeat", s.authed(s.handleHeartbeat))
	mux.Handle("GET /api/inbox/pending", s.authed(s.handlePending))
	mux.Handle("GET /api/inbox/{uid}/blob", s.authed(s.handleBlob))
	mux.Handle("POST /api/inbox/{uid}/ack", s.authed(s.handleAck))
	mux.Handle("POST /api/inbox/{uid}/fail", s.authed(s.handleFail))
	mux.Handle("PUT /api/digest", s.authed(s.handleDigestPush))

	// --- everything else under /api: the switchboard ---
	mux.Handle("/api/", s.board)

	// --- the app itself ---
	mux.Handle("/", s.static())
	return mux
}

// authed guards the agent's endpoints with the shared bearer token.
func (s *Server) authed(h http.HandlerFunc) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		token := strings.TrimPrefix(r.Header.Get("authorization"), "Bearer ")
		// Constant-time comparison: the token is long-lived and the endpoint is
		// reachable from the internet.
		if !secureEqual(strings.TrimSpace(token), s.cfg.AgentToken) {
			writeJSONError(w, http.StatusUnauthorized, "agent token required")
			return
		}
		h(w, r)
	})
}

// ------------------------------------------------------------------ handlers

func (s *Server) handleRelayStatus(w http.ResponseWriter, r *http.Request) {
	version, generatedAt, sessions := s.digest.Meta()
	pending, err := s.inbox.PendingCount()
	if err != nil {
		log.Printf("status: pending count: %v", err)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		// The frontend uses this one field to know it is talking to the relay rather
		// than to the PC directly; the PC's API has no such route and 404s.
		"relay":           true,
		"pc":              s.presence.Status(),
		"pc_online":       s.presence.Online(),
		"inbox_pending":   pending,
		"digest_version":  nullString(version),
		"digest_at":       nullString(generatedAt),
		"digest_sessions": sessions,
		"modes":           []string{"freeform", "worklog", "brainstorm", "journal"},
	})
}

// handleUpload receives one capture from the recorder.
//
// This is the only write that works with the PC off, and it is deliberately not a
// session: the relay stores audio plus the handful of fields needed to create one
// later, and `ect agent` does the creating against the PC's own API. One SQLite file
// stays the source of truth.
func (s *Server) handleUpload(w http.ResponseWriter, r *http.Request) {
	reader, err := r.MultipartReader()
	if err != nil {
		writeJSONError(w, http.StatusBadRequest, "expected a multipart upload")
		return
	}

	item := Item{Mode: "freeform"}
	var stored *Item
	for {
		part, err := reader.NextPart()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			writeJSONError(w, http.StatusBadRequest, "malformed upload: "+err.Error())
			return
		}
		switch part.FormName() {
		case "uid":
			item.UID = readField(part)
		case "mode":
			if v := readField(part); v != "" {
				item.Mode = v
			}
		case "topic":
			item.Topic = readField(part)
		case "notes":
			item.Notes = readField(part)
		case "file":
			// The file part must come last - the fields before it describe it. The
			// recorder appends them in that order; anything else is a client bug.
			if item.UID == "" {
				writeJSONError(w, http.StatusBadRequest, "send `uid` before `file`")
				_ = part.Close()
				return
			}
			item.Filename = part.FileName()
			item.ContentType = part.Header.Get("Content-Type")
			saved, err := s.inbox.Put(item, part)
			_ = part.Close()
			if err != nil {
				writeJSONError(w, http.StatusBadRequest, err.Error())
				return
			}
			stored = &saved
			continue
		}
		_ = part.Close()
	}

	if stored == nil {
		writeJSONError(w, http.StatusBadRequest, "no `file` part in the upload")
		return
	}
	pending, _ := s.inbox.PendingCount()
	log.Printf("inbox: stored %s (%s, %d bytes)", stored.UID, stored.Mode, stored.Bytes)
	writeJSON(w, http.StatusCreated, map[string]any{
		"uid":           stored.UID,
		"mode":          stored.Mode,
		"bytes":         stored.Bytes,
		"created_at":    stored.CreatedAt,
		"inbox_pending": pending,
		"pc_online":     s.presence.Online(),
		"hint": "Saved on the server. It becomes a session the next time your PC is up " +
			"and `ect agent` drains the inbox.",
	})
}

func (s *Server) handleRecent(w http.ResponseWriter, r *http.Request) {
	items, err := s.inbox.Recent(25)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": items})
}

func (s *Server) handleHeartbeat(w http.ResponseWriter, r *http.Request) {
	var body struct {
		APIOK bool `json:"api_ok"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&body)
	s.presence.Heartbeat(body.APIOK)
	pending, _ := s.inbox.PendingCount()
	version, _, _ := s.digest.Meta()
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":             true,
		"inbox_pending":  pending,
		"digest_version": nullString(version),
	})
}

func (s *Server) handlePending(w http.ResponseWriter, r *http.Request) {
	items, err := s.inbox.Pending()
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": items, "count": len(items)})
}

func (s *Server) handleBlob(w http.ResponseWriter, r *http.Request) {
	f, err := s.inbox.Blob(r.PathValue("uid"))
	if errors.Is(err, ErrNotFound) {
		writeJSONError(w, http.StatusNotFound, "no blob for that uid")
		return
	}
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}
	w.Header().Set("content-type", "application/octet-stream")
	http.ServeContent(w, r, filepath.Base(f.Name()), info.ModTime(), f)
}

func (s *Server) handleAck(w http.ResponseWriter, r *http.Request) {
	var body struct {
		SessionID int64 `json:"session_id"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&body)
	uid := r.PathValue("uid")
	if err := s.inbox.Ack(uid, body.SessionID); err != nil {
		if errors.Is(err, ErrNotFound) {
			writeJSONError(w, http.StatusNotFound, "no inbox item for that uid")
			return
		}
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}
	log.Printf("inbox: acked %s -> session %d (blob deleted)", uid, body.SessionID)
	writeJSON(w, http.StatusOK, map[string]any{"uid": uid, "acked": true})
}

func (s *Server) handleFail(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Error string `json:"error"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&body)
	uid := r.PathValue("uid")
	if err := s.inbox.Fail(uid, body.Error); err != nil {
		if errors.Is(err, ErrNotFound) {
			writeJSONError(w, http.StatusNotFound, "no pending inbox item for that uid")
			return
		}
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}
	log.Printf("inbox: drain failed for %s: %s", uid, body.Error)
	writeJSON(w, http.StatusOK, map[string]any{"uid": uid, "recorded": true})
}

func (s *Server) handleDigestPush(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(io.LimitReader(r.Body, 128<<20))
	if err != nil {
		writeJSONError(w, http.StatusBadRequest, "could not read the snapshot")
		return
	}
	if err := s.digest.Store(raw); err != nil {
		writeJSONError(w, http.StatusBadRequest, err.Error())
		return
	}
	version, generatedAt, sessions := s.digest.Meta()
	log.Printf("digest: stored version %s (%d sessions, %d bytes)", version, sessions, len(raw))
	writeJSON(w, http.StatusOK, map[string]any{
		"stored":   true,
		"version":  version,
		"at":       generatedAt,
		"sessions": sessions,
	})
}

// static serves the built frontend with an SPA fallback: react-router owns the paths,
// so anything that is not a real file becomes index.html.
func (s *Server) static() http.Handler {
	if s.cfg.StaticDir == "" {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			writeJSONError(w, http.StatusNotFound, "no frontend build is bundled with this relay")
		})
	}
	files := http.Dir(s.cfg.StaticDir)
	fileServer := http.FileServer(files)
	index := filepath.Join(s.cfg.StaticDir, "index.html")
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upath := strings.TrimPrefix(r.URL.Path, "/")
		if upath != "" {
			if f, err := files.Open(upath); err == nil {
				info, statErr := f.Stat()
				_ = f.Close()
				if statErr == nil && !info.IsDir() {
					fileServer.ServeHTTP(w, r)
					return
				}
			}
		}
		http.ServeFile(w, r, index)
	})
}

// --------------------------------------------------------------------- helpers

func readField(part io.Reader) string {
	// Form fields are short by construction; the cap is a guard, not a budget.
	b, err := io.ReadAll(io.LimitReader(part, 8<<10))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("content-type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

// writeJSONError uses FastAPI's `detail` key on purpose: frontend/src/api.ts already
// reads it, so a relay error surfaces in the UI with no frontend change.
func writeJSONError(w http.ResponseWriter, status int, detail string) {
	writeJSON(w, status, map[string]string{"detail": detail})
}

func nullString(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func secureEqual(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	var diff byte
	for i := range len(a) {
		diff |= a[i] ^ b[i]
	}
	return diff == 0
}

func logging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)
		// Static assets are noise; the API surface is what anyone debugging cares about.
		if strings.HasPrefix(r.URL.Path, "/api/") {
			log.Printf("%s %s -> %d (%s) %s",
				r.Method, r.URL.Path, rec.status, w.Header().Get("x-ect-source"),
				time.Since(start).Round(time.Millisecond))
		}
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(status int) {
	r.status = status
	r.ResponseWriter.WriteHeader(status)
}
