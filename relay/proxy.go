package main

import (
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"sync"
	"time"
)

// Presence tracks whether the PC is reachable.
//
// "Online" means *requests will be answered*, not "the machine has power": the agent's
// heartbeat carries whether its local API actually responded, and a proxy attempt that
// fails marks the PC down immediately rather than waiting for the heartbeat to expire.
// Getting that wrong is the difference between a readable "PC offline" and a page that
// hangs until a timeout.
type Presence struct {
	mu       sync.RWMutex
	ttl      time.Duration
	lastSeen time.Time
	apiOK    bool
	// Set when a proxy attempt fails, cleared by the next good heartbeat. Without it,
	// every request in the TTL window after the PC sleeps pays the full proxy timeout.
	proxyFailed bool
}

func NewPresence(ttl time.Duration) *Presence { return &Presence{ttl: ttl} }

func (p *Presence) Heartbeat(apiOK bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.lastSeen = time.Now()
	p.apiOK = apiOK
	p.proxyFailed = false
}

func (p *Presence) MarkProxyFailure() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.proxyFailed = true
}

func (p *Presence) Online() bool {
	p.mu.RLock()
	defer p.mu.RUnlock()
	if p.proxyFailed || !p.apiOK || p.lastSeen.IsZero() {
		return false
	}
	return time.Since(p.lastSeen) < p.ttl
}

func (p *Presence) Status() map[string]any {
	p.mu.RLock()
	defer p.mu.RUnlock()
	status := map[string]any{
		"online":       !p.proxyFailed && p.apiOK && !p.lastSeen.IsZero() && time.Since(p.lastSeen) < p.ttl,
		"api_ok":       p.apiOK,
		"proxy_failed": p.proxyFailed,
		"ttl_seconds":  p.ttl.Seconds(),
	}
	if p.lastSeen.IsZero() {
		status["last_heartbeat"] = nil
		status["seconds_since_heartbeat"] = nil
	} else {
		status["last_heartbeat"] = p.lastSeen.UTC().Format(time.RFC3339)
		status["seconds_since_heartbeat"] = int(time.Since(p.lastSeen).Seconds())
	}
	return status
}

// Switchboard implements ADR 0006's routing table:
//
//	PC state | GET sessions/feedback/notes | writes, /transcribe, /process
//	online   | proxied to the PC over LAN  | proxied to the PC
//	offline  | answered from the digest    | 503 {"detail": "PC offline"}
//
// frontend/src/api.ts already surfaces `detail` on failure, so an offline write
// degrades into a readable message with no frontend change.
type Switchboard struct {
	proxy    *httputil.ReverseProxy
	presence *Presence
	digest   *Digest
}

func NewSwitchboard(pcBaseURL string, timeout time.Duration, presence *Presence, digest *Digest) (*Switchboard, error) {
	target, err := url.Parse(pcBaseURL)
	if err != nil {
		return nil, err
	}
	sb := &Switchboard{presence: presence, digest: digest}
	sb.proxy = &httputil.ReverseProxy{
		Rewrite: func(r *httputil.ProxyRequest) {
			r.SetURL(target)
			// The PC's CORS list and its logs should see where this came from; the
			// backend is same-origin behind the relay either way.
			r.Out.Host = target.Host
			r.SetXForwarded()
		},
		Transport: &http.Transport{
			ResponseHeaderTimeout: timeout,
			IdleConnTimeout:       90 * time.Second,
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			// The PC went away between the heartbeat and this request. Record it so
			// the next one goes straight to the digest, and answer readably now.
			log.Printf("proxy to PC failed for %s %s: %v", r.Method, r.URL.Path, err)
			presence.MarkProxyFailure()
			if r.Method == http.MethodGet {
				contentType, body, derr := digest.Answer(r.URL.Path, r.URL.Query())
				if derr == nil {
					w.Header().Set("content-type", contentType)
					w.Header().Set("x-ect-source", "digest")
					w.WriteHeader(http.StatusOK)
					_, _ = w.Write(body)
					return
				}
			}
			writeJSONError(w, http.StatusServiceUnavailable,
				"PC offline - the request could not be delivered")
		},
	}
	return sb, nil
}

func (s *Switchboard) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if s.presence.Online() {
		w.Header().Set("x-ect-source", "pc")
		s.proxy.ServeHTTP(w, r)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		// Writes are never queued or replayed. A write that silently landed somewhere
		// other than the one SQLite file would be a second source of truth, which is
		// exactly what the digest exists to avoid.
		writeJSONError(w, http.StatusServiceUnavailable, "PC offline")
		return
	}
	contentType, body, err := s.digest.Answer(r.URL.Path, r.URL.Query())
	if err != nil {
		writeDigestError(w, err)
		return
	}
	w.Header().Set("content-type", contentType)
	w.Header().Set("x-ect-source", "digest")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
}
