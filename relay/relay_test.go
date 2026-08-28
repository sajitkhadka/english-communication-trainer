package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const testToken = "test-agent-token"

// newServer builds a relay against a throwaway data dir, pointed at `pcURL`.
func newServer(t *testing.T, pcURL string) *Server {
	t.Helper()
	dir := t.TempDir()
	cfg := Config{
		PCBaseURL:      pcURL,
		AgentToken:     testToken,
		DataDir:        dir,
		MaxUploadBytes: 1 << 20,
		HeartbeatTTL:   time.Minute,
		ProxyTimeout:   2 * time.Second,
	}
	inbox, err := OpenInbox(dir, cfg.MaxUploadBytes)
	if err != nil {
		t.Fatalf("open inbox: %v", err)
	}
	t.Cleanup(func() { _ = inbox.Close() })

	digest, err := OpenDigest(dir)
	if err != nil {
		t.Fatalf("open digest: %v", err)
	}
	presence := NewPresence(cfg.HeartbeatTTL)
	board, err := NewSwitchboard(pcURL, cfg.ProxyTimeout, presence, digest)
	if err != nil {
		t.Fatalf("switchboard: %v", err)
	}
	return &Server{cfg: cfg, inbox: inbox, digest: digest, presence: presence, board: board}
}

func do(t *testing.T, s *Server, req *http.Request) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	s.Routes().ServeHTTP(rec, req)
	return rec
}

func agentReq(method, path string, body io.Reader) *http.Request {
	req := httptest.NewRequest(method, path, body)
	req.Header.Set("authorization", "Bearer "+testToken)
	return req
}

func uploadRequest(t *testing.T, uid, mode, topic string, audio []byte) *http.Request {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	// Field order matters: the handler streams the file part straight to disk, so the
	// fields describing it have to arrive first.
	for _, kv := range [][2]string{{"uid", uid}, {"mode", mode}, {"topic", topic}} {
		if kv[1] == "" {
			continue
		}
		if err := mw.WriteField(kv[0], kv[1]); err != nil {
			t.Fatalf("write field: %v", err)
		}
	}
	part, err := mw.CreateFormFile("file", uid+".webm")
	if err != nil {
		t.Fatalf("create file part: %v", err)
	}
	if _, err := part.Write(audio); err != nil {
		t.Fatalf("write audio: %v", err)
	}
	if err := mw.Close(); err != nil {
		t.Fatalf("close writer: %v", err)
	}
	req := httptest.NewRequest("POST", "/api/inbox", &buf)
	req.Header.Set("content-type", mw.FormDataContentType())
	return req
}

func decode(t *testing.T, rec *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("decode %q: %v", rec.Body.String(), err)
	}
	return out
}

// --------------------------------------------------------------------- inbox

func TestUploadThenDrainCycle(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")

	rec := do(t, s, uploadRequest(t, "capture-0001", "worklog", "Tuesday", []byte("fake-opus")))
	if rec.Code != http.StatusCreated {
		t.Fatalf("upload: want 201, got %d (%s)", rec.Code, rec.Body)
	}

	rec = do(t, s, agentReq("GET", "/agent/inbox/pending", nil))
	body := decode(t, rec)
	items, _ := body["items"].([]any)
	if len(items) != 1 {
		t.Fatalf("want 1 pending item, got %d", len(items))
	}
	item := items[0].(map[string]any)
	if item["mode"] != "worklog" || item["topic"] != "Tuesday" {
		t.Fatalf("fields did not survive the upload: %v", item)
	}

	rec = do(t, s, agentReq("GET", "/agent/inbox/capture-0001/blob", nil))
	if rec.Code != http.StatusOK || rec.Body.String() != "fake-opus" {
		t.Fatalf("blob: got %d %q", rec.Code, rec.Body)
	}

	rec = do(t, s, agentReq("POST", "/agent/inbox/capture-0001/ack",
		strings.NewReader(`{"session_id": 42}`)))
	if rec.Code != http.StatusOK {
		t.Fatalf("ack: want 200, got %d (%s)", rec.Code, rec.Body)
	}

	// The blob is deleted on ack: it is the one copy of a private recording sitting on
	// a publicly addressable machine, and that window should be as short as possible.
	blobs, _ := filepath.Glob(filepath.Join(s.cfg.DataDir, "blobs", "*.blob"))
	if len(blobs) != 0 {
		t.Fatalf("ack left blobs behind: %v", blobs)
	}
	rec = do(t, s, agentReq("GET", "/agent/inbox/pending", nil))
	if items, _ := decode(t, rec)["items"].([]any); len(items) != 0 {
		t.Fatalf("acked item is still pending: %v", items)
	}
}

func TestReuploadingTheSameUIDReplacesRatherThanDuplicates(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")

	do(t, s, uploadRequest(t, "capture-0002", "freeform", "", []byte("first")))
	do(t, s, uploadRequest(t, "capture-0002", "freeform", "", []byte("second-longer")))

	rec := do(t, s, agentReq("GET", "/agent/inbox/pending", nil))
	items, _ := decode(t, rec)["items"].([]any)
	if len(items) != 1 {
		t.Fatalf("a retried upload created %d items, want 1", len(items))
	}
	rec = do(t, s, agentReq("GET", "/agent/inbox/capture-0002/blob", nil))
	if rec.Body.String() != "second-longer" {
		t.Fatalf("retry did not replace the blob: %q", rec.Body)
	}
}

func TestUploadRejectsCoachedModes(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	// `recommended` and `interview` carry target words chosen by /generate-topic, so
	// they cannot be started from a phone with no prior setup.
	rec := do(t, s, uploadRequest(t, "capture-0003", "recommended", "x", []byte("audio")))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("want 400 for a coached mode, got %d", rec.Code)
	}
}

func TestUploadRejectsTraversalUID(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	rec := do(t, s, uploadRequest(t, "../../etc/passwd", "freeform", "", []byte("audio")))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("want 400 for a path-traversal uid, got %d", rec.Code)
	}
}

func TestUploadRejectsOversizedRecording(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	rec := do(t, s, uploadRequest(t, "capture-0004", "freeform", "",
		bytes.Repeat([]byte("x"), int(s.cfg.MaxUploadBytes)+64)))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("want 400 for an oversized upload, got %d", rec.Code)
	}
	blobs, _ := filepath.Glob(filepath.Join(s.cfg.DataDir, "blobs", "*"))
	if len(blobs) != 0 {
		t.Fatalf("a rejected upload left files behind: %v", blobs)
	}
}

func TestFailAdvancesAttemptsAndKeepsTheItem(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	do(t, s, uploadRequest(t, "capture-0005", "journal", "", []byte("audio")))

	do(t, s, agentReq("POST", "/agent/inbox/capture-0005/fail",
		strings.NewReader(`{"error": "session create failed"}`)))

	rec := do(t, s, agentReq("GET", "/agent/inbox/pending", nil))
	items, _ := decode(t, rec)["items"].([]any)
	if len(items) != 1 {
		t.Fatalf("a failed drain lost the capture")
	}
	item := items[0].(map[string]any)
	if item["attempts"].(float64) != 1 {
		t.Fatalf("attempts did not advance: %v", item["attempts"])
	}
	if item["last_error"] != "session create failed" {
		t.Fatalf("the reason is not readable from the relay: %v", item["last_error"])
	}
}

// ----------------------------------------------------------------------- auth

func TestAgentEndpointsRequireTheToken(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	for _, route := range []struct {
		method, path string
	}{
		{"GET", "/agent/inbox/pending"},
		{"GET", "/agent/inbox/abcdefgh/blob"},
		{"POST", "/agent/inbox/abcdefgh/ack"},
		{"POST", "/agent/inbox/abcdefgh/fail"},
		{"POST", "/agent/heartbeat"},
		{"PUT", "/agent/digest"},
	} {
		rec := do(t, s, httptest.NewRequest(route.method, route.path, strings.NewReader("{}")))
		if rec.Code != http.StatusUnauthorized {
			t.Errorf("%s %s: want 401 without a token, got %d", route.method, route.path, rec.Code)
		}
	}
}

// The /agent/ prefix is exempt from the ingress basic-auth annotation, so anything
// falling through it unauthenticated would be reachable with no credential at all.
// `ect agent status` reads this. It cannot use /api/relay/status: that path is behind
// the ingress basic-auth annotation, which rejects a Bearer header outright.
func TestAgentStatusMirrorsTheBrowserView(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	do(t, s, uploadRequest(t, "capture-0007", "worklog", "", []byte("audio")))

	if rec := do(t, s, httptest.NewRequest("GET", "/agent/status", nil)); rec.Code != http.StatusUnauthorized {
		t.Fatalf("want 401 without a token, got %d", rec.Code)
	}
	rec := do(t, s, agentReq("GET", "/agent/status", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("want 200 with the token, got %d", rec.Code)
	}
	status := decode(t, rec)
	if status["relay"] != true || status["inbox_pending"].(float64) != 1 {
		t.Fatalf("agent status does not match the browser view: %v", status)
	}
}

func TestUnknownAgentRoutesDoNotFallThrough(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	for _, path := range []string{"/agent/", "/agent/nonsense", "/agent/api/sessions"} {
		rec := do(t, s, httptest.NewRequest("GET", path, nil))
		if rec.Code != http.StatusUnauthorized {
			t.Errorf("%s: want 401 without a token, got %d", path, rec.Code)
		}
		rec = do(t, s, agentReq("GET", path, nil))
		if rec.Code != http.StatusNotFound {
			t.Errorf("%s: want 404 with a token, got %d", path, rec.Code)
		}
	}
}

func TestWrongTokenIsRejected(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	req := httptest.NewRequest("GET", "/agent/inbox/pending", nil)
	req.Header.Set("authorization", "Bearer not-the-token")
	if rec := do(t, s, req); rec.Code != http.StatusUnauthorized {
		t.Fatalf("want 401, got %d", rec.Code)
	}
}

// ----------------------------------------------------------------- switchboard

// sampleDigest is the shape app/digest.py produces, trimmed to what the relay reads.
func sampleDigest() []byte {
	return []byte(`{
	  "version": "abc123",
	  "generated_at": "2026-08-25T10:00:00Z",
	  "schema_version": 1,
	  "feedback_horizon": 50,
	  "health": {"ok": true, "pending_sessions": 1},
	  "sessions": [
	    {"id": 7, "mode": "worklog", "status": "processed", "topic": "Tuesday"},
	    {"id": 8, "mode": "freeform", "status": "pending", "topic": "Latency"}
	  ],
	  "session_details": {
	    "7": {"feedback_markdown": "# Tuesday\n\nWent well.", "target_words_detail": []}
	  },
	  "notes": {"markdown": "# Learning Notes", "version": "v1"},
	  "words": [{"term": "throughput"}],
	  "words_due": [],
	  "word_stats": {"total": 1},
	  "suggestions": [],
	  "queue": {"pending": [], "count": 0},
	  "progress": {"history": []}
	}`)
}

func pushDigest(t *testing.T, s *Server) {
	t.Helper()
	rec := do(t, s, agentReq("PUT", "/agent/digest", bytes.NewReader(sampleDigest())))
	if rec.Code != http.StatusOK {
		t.Fatalf("digest push: want 200, got %d (%s)", rec.Code, rec.Body)
	}
}

func TestOfflineGETsAreAnsweredFromTheDigest(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1") // nothing listening: the PC is "off"
	pushDigest(t, s)

	rec := do(t, s, httptest.NewRequest("GET", "/api/sessions", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("want 200 from the snapshot, got %d (%s)", rec.Code, rec.Body)
	}
	if got := rec.Header().Get("x-ect-source"); got != "digest" {
		t.Fatalf("want the digest to answer, got source=%q", got)
	}
	var sessions []map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &sessions); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(sessions) != 2 {
		t.Fatalf("want 2 sessions, got %d", len(sessions))
	}

	// The detail route rebuilds the SessionDetail shape, feedback markdown included.
	rec = do(t, s, httptest.NewRequest("GET", "/api/sessions/7", nil))
	detail := decode(t, rec)
	if !strings.Contains(detail["feedback_markdown"].(string), "Went well") {
		t.Fatalf("feedback markdown missing: %v", detail)
	}

	rec = do(t, s, httptest.NewRequest("GET", "/api/sessions/7/feedback", nil))
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "Went well") {
		t.Fatalf("feedback text: %d %q", rec.Code, rec.Body)
	}
}

func TestOfflineSessionFilteringMatchesTheRealAPI(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	pushDigest(t, s)

	rec := do(t, s, httptest.NewRequest("GET", "/api/sessions?mode=worklog", nil))
	var sessions []map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &sessions)
	if len(sessions) != 1 || sessions[0]["id"].(float64) != 7 {
		t.Fatalf("mode filter did not apply: %v", sessions)
	}

	rec = do(t, s, httptest.NewRequest("GET", "/api/sessions?status=pending", nil))
	_ = json.Unmarshal(rec.Body.Bytes(), &sessions)
	if len(sessions) != 1 || sessions[0]["id"].(float64) != 8 {
		t.Fatalf("status filter did not apply: %v", sessions)
	}
}

func TestOfflineHealthSaysItIsASnapshot(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	pushDigest(t, s)

	rec := do(t, s, httptest.NewRequest("GET", "/api/health", nil))
	health := decode(t, rec)
	if health["pc_online"] != false || health["source"] != "digest" {
		t.Fatalf("health does not admit it is offline: %v", health)
	}
}

func TestOfflineWritesAre503WithAReadableDetail(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	pushDigest(t, s)

	for _, route := range []struct{ method, path string }{
		{"POST", "/api/sessions"},
		{"PUT", "/api/notes"},
		{"POST", "/api/sessions/7/transcribe"},
		{"POST", "/api/sessions/7/process"},
		{"DELETE", "/api/sessions/7"},
	} {
		rec := do(t, s, httptest.NewRequest(route.method, route.path, strings.NewReader("{}")))
		if rec.Code != http.StatusServiceUnavailable {
			t.Errorf("%s %s: want 503, got %d", route.method, route.path, rec.Code)
			continue
		}
		if decode(t, rec)["detail"] != "PC offline" {
			t.Errorf("%s %s: detail is not the readable message", route.method, route.path)
		}
	}
}

func TestRoutesThatNeedFilesRefuseOffline(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	pushDigest(t, s)

	// The digest carries no audio, no transcripts, no per-word timings and no briefs -
	// lossy by construction. Saying so beats serving a plausible-looking nothing.
	for _, path := range []string{
		"/api/sessions/7/audio",
		"/api/sessions/7/transcript",
		"/api/sessions/7/brief",
		"/api/sessions/7/prompt",
		"/api/doctor",
	} {
		rec := do(t, s, httptest.NewRequest("GET", path, nil))
		if rec.Code != http.StatusServiceUnavailable {
			t.Errorf("%s: want 503 offline, got %d", path, rec.Code)
		}
	}
}

func TestWithoutADigestOfflineReadsSaySo(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	rec := do(t, s, httptest.NewRequest("GET", "/api/sessions", nil))
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("want 503, got %d", rec.Code)
	}
	if !strings.Contains(decode(t, rec)["detail"].(string), "no offline snapshot") {
		t.Fatalf("detail should explain that nothing has been pushed: %v", rec.Body)
	}
}

func TestOnlinePCIsProxiedNotServedFromTheDigest(t *testing.T) {
	pc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("content-type", "application/json")
		fmt.Fprintf(w, `{"from": "pc", "path": %q}`, r.URL.Path)
	}))
	defer pc.Close()

	s := newServer(t, pc.URL)
	pushDigest(t, s)
	s.presence.Heartbeat(true)

	rec := do(t, s, httptest.NewRequest("GET", "/api/sessions", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", rec.Code)
	}
	if decode(t, rec)["from"] != "pc" {
		t.Fatalf("the digest answered while the PC was up: %s", rec.Body)
	}
	if got := rec.Header().Get("x-ect-source"); got != "pc" {
		t.Fatalf("want source=pc, got %q", got)
	}

	// Writes reach the PC unchanged while it is up.
	rec = do(t, s, httptest.NewRequest("POST", "/api/sessions", strings.NewReader("{}")))
	if rec.Code != http.StatusOK || decode(t, rec)["from"] != "pc" {
		t.Fatalf("write was not proxied: %d %s", rec.Code, rec.Body)
	}
}

func TestAHeartbeatWithoutAWorkingAPIIsNotOnline(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	pushDigest(t, s)
	// The agent is alive but its local uvicorn is not answering. "Online" has to mean
	// requests get answered, or every call pays a proxy timeout to find out.
	s.presence.Heartbeat(false)

	rec := do(t, s, httptest.NewRequest("GET", "/api/sessions", nil))
	if rec.Header().Get("x-ect-source") != "digest" {
		t.Fatalf("api_ok=false should not count as online")
	}
}

func TestAStaleHeartbeatFallsBackToTheDigest(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	s.presence.ttl = 10 * time.Millisecond
	pushDigest(t, s)
	s.presence.Heartbeat(true)
	time.Sleep(30 * time.Millisecond)

	rec := do(t, s, httptest.NewRequest("GET", "/api/sessions", nil))
	if rec.Code != http.StatusOK || rec.Header().Get("x-ect-source") != "digest" {
		t.Fatalf("a stale heartbeat should fall back: %d %s", rec.Code,
			rec.Header().Get("x-ect-source"))
	}
}

func TestAProxyFailureFallsBackToTheDigestImmediately(t *testing.T) {
	// Heartbeat says up, but the PC went to sleep in the meantime. The first request
	// pays the failure; every one after it should go straight to the snapshot rather
	// than waiting on a dead socket again.
	s := newServer(t, "http://127.0.0.1:1")
	pushDigest(t, s)
	s.presence.Heartbeat(true)

	rec := do(t, s, httptest.NewRequest("GET", "/api/sessions", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("the failed proxy should have degraded to the digest, got %d", rec.Code)
	}
	if s.presence.Online() {
		t.Fatal("a proxy failure should mark the PC offline")
	}
	rec = do(t, s, httptest.NewRequest("GET", "/api/sessions", nil))
	if rec.Header().Get("x-ect-source") != "digest" {
		t.Fatal("the second request should not retry the dead PC")
	}
}

func TestHeartbeatClearsAProxyFailure(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	s.presence.Heartbeat(true)
	s.presence.MarkProxyFailure()
	if s.presence.Online() {
		t.Fatal("proxy failure should mark offline")
	}
	s.presence.Heartbeat(true)
	if !s.presence.Online() {
		t.Fatal("a fresh heartbeat should bring the PC back online")
	}
}

// --------------------------------------------------------------------- digest

func TestDigestSurvivesARestart(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	pushDigest(t, s)

	reopened, err := OpenDigest(s.cfg.DataDir)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	version, _, sessions := reopened.Meta()
	if version != "abc123" || sessions != 2 {
		t.Fatalf("snapshot did not survive: version=%q sessions=%d", version, sessions)
	}
}

func TestDigestPushRejectsGarbage(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	rec := do(t, s, agentReq("PUT", "/agent/digest", strings.NewReader("not json")))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("want 400, got %d", rec.Code)
	}
	if _, err := os.Stat(filepath.Join(s.cfg.DataDir, "digest.json")); !os.IsNotExist(err) {
		t.Fatal("a rejected snapshot should not have been written to disk")
	}
}

// --------------------------------------------------------------- relay status

func TestRelayStatusIdentifiesTheRelay(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	do(t, s, uploadRequest(t, "capture-0006", "brainstorm", "", []byte("audio")))

	rec := do(t, s, httptest.NewRequest("GET", "/api/relay/status", nil))
	status := decode(t, rec)
	if status["relay"] != true {
		t.Fatalf("the frontend keys off this field: %v", status)
	}
	if status["pc_online"] != false {
		t.Fatalf("want pc_online=false with no heartbeat: %v", status)
	}
	if status["inbox_pending"].(float64) != 1 {
		t.Fatalf("want 1 pending capture: %v", status)
	}
}

// ------------------------------------------------------------ wake-on-lan

func TestMagicPacketShape(t *testing.T) {
	w, err := NewWaker("aa:bb:cc:dd:ee:ff", "255.255.255.255:9", time.Minute)
	if err != nil {
		t.Fatalf("waker: %v", err)
	}
	packet := w.magicPacket()
	if len(packet) != 102 {
		t.Fatalf("want a 102-byte magic packet, got %d", len(packet))
	}
	if !bytes.Equal(packet[:6], bytes.Repeat([]byte{0xFF}, 6)) {
		t.Fatal("magic packet must start with six 0xFF bytes")
	}
	if !bytes.Equal(packet[6:12], []byte{0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF}) {
		t.Fatalf("MAC not repeated correctly: %x", packet[6:12])
	}
}

func TestWakerCooldown(t *testing.T) {
	w, err := NewWaker("aa:bb:cc:dd:ee:ff", "127.0.0.1:9", time.Hour)
	if err != nil {
		t.Fatalf("waker: %v", err)
	}
	if sent, err := w.Wake(); err != nil || !sent {
		t.Fatalf("first wake should send: sent=%v err=%v", sent, err)
	}
	if sent, _ := w.Wake(); sent {
		t.Fatal("the cooldown should suppress a second packet")
	}
}

func TestWakerDisabledWithoutAMac(t *testing.T) {
	w, err := NewWaker("", "255.255.255.255:9", time.Minute)
	if err != nil {
		t.Fatalf("an empty MAC is a valid configuration: %v", err)
	}
	if w != nil {
		t.Fatal("want a nil waker when waking is disabled")
	}
	if sent, err := w.Wake(); sent || err != nil {
		t.Fatalf("a nil waker must be safe to call: sent=%v err=%v", sent, err)
	}
}

// ------------------------------------------------------------------- config

func TestConfigRefusesToStartWithoutATokenOrPC(t *testing.T) {
	t.Setenv("ECT_RELAY_PC_URL", "")
	t.Setenv("ECT_RELAY_TOKEN", "x")
	if _, err := LoadConfig(); err == nil {
		t.Fatal("want an error with no PC URL")
	}
	t.Setenv("ECT_RELAY_PC_URL", "http://192.168.0.42:8000")
	t.Setenv("ECT_RELAY_TOKEN", "")
	if _, err := LoadConfig(); err == nil {
		t.Fatal("an unauthenticated relay is an open door to the PC's API; want an error")
	}
}
