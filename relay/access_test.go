package main

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

const (
	testTeam  = "team.example.com"
	testAUD   = "aud-tag"
	testEmail = "me@example.com"
)

func b64(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }

func signToken(t *testing.T, key *rsa.PrivateKey, kid string, claims map[string]any) string {
	t.Helper()
	h, _ := json.Marshal(map[string]string{"alg": "RS256", "kid": kid})
	c, _ := json.Marshal(claims)
	signing := b64(h) + "." + b64(c)
	sum := sha256.Sum256([]byte(signing))
	sig, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, sum[:])
	if err != nil {
		t.Fatalf("sign: %v", err)
	}
	return signing + "." + b64(sig)
}

func goodClaims() map[string]any {
	return map[string]any{
		"iss":   "https://" + testTeam,
		"aud":   []string{testAUD},
		"email": testEmail,
		"exp":   time.Now().Add(time.Hour).Unix(),
		"nbf":   time.Now().Add(-time.Minute).Unix(),
	}
}

// fakeCerts serves a key set containing the given keys and counts how often it is hit.
func fakeCerts(t *testing.T, keys map[string]*rsa.PublicKey) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	hits := &atomic.Int32{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		var list []map[string]string
		for kid, k := range keys {
			list = append(list, map[string]string{
				"kid": kid, "kty": "RSA",
				"n": b64(k.N.Bytes()), "e": b64(big.NewInt(int64(k.E)).Bytes()),
			})
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"keys": list})
	}))
	t.Cleanup(srv.Close)
	return srv, hits
}

func newTestKey(t *testing.T) *rsa.PrivateKey {
	t.Helper()
	k, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate key: %v", err)
	}
	return k
}

func newVerifier(t *testing.T, keys map[string]*rsa.PublicKey) (*AccessVerifier, *atomic.Int32) {
	t.Helper()
	certs, hits := fakeCerts(t, keys)
	v := NewAccessVerifier(testTeam, testAUD, []string{testEmail})
	v.certsURL = certs.URL
	return v, hits
}

func TestVerifyAcceptsAGoodToken(t *testing.T) {
	key := newTestKey(t)
	v, _ := newVerifier(t, map[string]*rsa.PublicKey{"k1": &key.PublicKey})
	if err := v.Verify(signToken(t, key, "k1", goodClaims()), time.Now()); err != nil {
		t.Fatalf("want ok, got %v", err)
	}
}

func TestVerifyRejectsBadTokens(t *testing.T) {
	key := newTestKey(t)
	other := newTestKey(t)
	v, _ := newVerifier(t, map[string]*rsa.PublicKey{"k1": &key.PublicKey})

	mutate := func(k string, val any) map[string]any {
		c := goodClaims()
		c[k] = val
		return c
	}
	cases := map[string]string{
		"empty":           "",
		"bad signature":   signToken(t, other, "k1", goodClaims()),
		"wrong aud":       signToken(t, key, "k1", mutate("aud", []string{"other"})),
		"wrong iss":       signToken(t, key, "k1", mutate("iss", "https://evil.example.com")),
		"expired":         signToken(t, key, "k1", mutate("exp", time.Now().Add(-time.Hour).Unix())),
		"not yet valid":   signToken(t, key, "k1", mutate("nbf", time.Now().Add(time.Hour).Unix())),
		"wrong email":     signToken(t, key, "k1", mutate("email", "stranger@example.com")),
		"not a jwt":       "abc",
		"alg none header": b64([]byte(`{"alg":"none"}`)) + "." + b64([]byte(`{}`)) + ".",
	}
	for name, token := range cases {
		if err := v.Verify(token, time.Now()); err == nil {
			t.Errorf("%s: want an error", name)
		}
	}
}

func TestVerifyAcceptsAStringAudience(t *testing.T) {
	key := newTestKey(t)
	v, _ := newVerifier(t, map[string]*rsa.PublicKey{"k1": &key.PublicKey})
	c := goodClaims()
	c["aud"] = testAUD
	if err := v.Verify(signToken(t, key, "k1", c), time.Now()); err != nil {
		t.Fatalf("want ok, got %v", err)
	}
}

func TestUnknownKidRefetchesOnceThenIsRateLimited(t *testing.T) {
	old := newTestKey(t)
	rotated := newTestKey(t)
	keys := map[string]*rsa.PublicKey{"old": &old.PublicKey}
	v, hits := newVerifier(t, keys)

	if err := v.Verify(signToken(t, old, "old", goodClaims()), time.Now()); err != nil {
		t.Fatalf("old key: %v", err)
	}
	keys["new"] = &rotated.PublicKey
	v.mu.Lock()
	v.lastFetched = time.Now().Add(-2 * keyRefetchEvery)
	v.mu.Unlock()

	if err := v.Verify(signToken(t, rotated, "new", goodClaims()), time.Now()); err != nil {
		t.Fatalf("rotated key should be found after one refetch: %v", err)
	}
	if hits.Load() != 2 {
		t.Fatalf("want 2 fetches, got %d", hits.Load())
	}
	for range 5 {
		_ = v.Verify(signToken(t, rotated, "junk", goodClaims()), time.Now())
	}
	if hits.Load() != 2 {
		t.Fatalf("junk kids must not refetch inside the rate limit, got %d fetches", hits.Load())
	}
}

func publicRequest(t *testing.T, s *Server, v *AccessVerifier, method, path, token string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, nil)
	if token != "" {
		req.Header.Set("Cf-Access-Jwt-Assertion", token)
	}
	rec := httptest.NewRecorder()
	s.PublicRoutes(v).ServeHTTP(rec, req)
	return rec
}

func TestPublicRoutesNeedALogin(t *testing.T) {
	key := newTestKey(t)
	v, _ := newVerifier(t, map[string]*rsa.PublicKey{"k1": &key.PublicKey})
	s := newServer(t, "http://127.0.0.1:1")

	if rec := publicRequest(t, s, v, "GET", "/api/relay/status", ""); rec.Code != http.StatusForbidden {
		t.Fatalf("no token: want 403, got %d", rec.Code)
	}
	token := signToken(t, key, "k1", goodClaims())
	if rec := publicRequest(t, s, v, "GET", "/api/relay/status", token); rec.Code != http.StatusOK {
		t.Fatalf("valid token: want 200, got %d", rec.Code)
	}
}

func TestPublicRoutesHaveNoAgentEndpoints(t *testing.T) {
	key := newTestKey(t)
	v, _ := newVerifier(t, map[string]*rsa.PublicKey{"k1": &key.PublicKey})
	s := newServer(t, "http://127.0.0.1:1")
	token := signToken(t, key, "k1", goodClaims())

	for _, path := range []string{"/agent/status", "/agent/inbox/pending", "/agent/digest", "/agent/nope"} {
		req := httptest.NewRequest("GET", path, nil)
		req.Header.Set("Cf-Access-Jwt-Assertion", token)
		req.Header.Set("authorization", "Bearer "+testToken)
		rec := httptest.NewRecorder()
		s.PublicRoutes(v).ServeHTTP(rec, req)
		if rec.Code != http.StatusNotFound {
			t.Errorf("%s: want 404, got %d", path, rec.Code)
		}
	}
}

func TestInternalRoutesStillTakeTheAgentToken(t *testing.T) {
	s := newServer(t, "http://127.0.0.1:1")
	if rec := do(t, s, agentReq("GET", "/agent/status", nil)); rec.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", rec.Code)
	}
}

func TestPublicUploadOverTheLimitSaysSo(t *testing.T) {
	key := newTestKey(t)
	v, _ := newVerifier(t, map[string]*rsa.PublicKey{"k1": &key.PublicKey})
	s := newServer(t, "http://127.0.0.1:1")
	s.cfg.PublicMaxUploadBytes = 1 << 10

	req := uploadRequest(t, "11111111-aaaa", "freeform", "", make([]byte, 4<<10))
	req.Header.Set("Cf-Access-Jwt-Assertion", signToken(t, key, "k1", goodClaims()))
	rec := httptest.NewRecorder()
	s.PublicRoutes(v).ServeHTTP(rec, req)

	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("want 413, got %d", rec.Code)
	}
	if detail := decode(t, rec)["detail"]; detail == nil || detail == "" {
		t.Fatalf("want a readable detail, got %v", detail)
	}
}

func TestConfigRefusesAHalfSetPublicListener(t *testing.T) {
	t.Setenv("ECT_RELAY_PC_URL", "http://192.168.0.42:8000")
	t.Setenv("ECT_RELAY_TOKEN", "x")
	t.Setenv("ECT_RELAY_PUBLIC_ADDR", ":8081")
	if _, err := LoadConfig(); err == nil {
		t.Fatal("public address with no Access settings: want an error")
	}
	t.Setenv("ECT_RELAY_ACCESS_TEAM_DOMAIN", testTeam)
	t.Setenv("ECT_RELAY_ACCESS_AUD", testAUD)
	if _, err := LoadConfig(); err == nil {
		t.Fatal("public address with no email allowlist: want an error")
	}
	t.Setenv("ECT_RELAY_ACCESS_EMAILS", "A@example.com, b@example.com")
	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("fully set: %v", err)
	}
	if len(cfg.AccessEmails) != 2 || cfg.AccessEmails[0] != "a@example.com" {
		t.Fatalf("emails should be trimmed and lowercased, got %v", cfg.AccessEmails)
	}
}
