package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is the relay's entire configuration surface. Every field has an ECT_RELAY_*
// environment variable; the k8s manifests in the k8s-config repo set them, and
// relay/README.md documents running it by hand.
type Config struct {
	// Addr is the listen address. ingress-nginx terminates TLS in front of it, so
	// plain HTTP here is correct - the relay never sees a certificate.
	Addr string

	// PCBaseURL is the PC's LAN address, e.g. http://192.168.0.42:8000. The PC gets a
	// DHCP reservation; see docs/relay.md.
	PCBaseURL string

	// AgentToken authenticates `ect agent`. The browser side is guarded separately, by
	// the ingress basic-auth annotation, so this token never has to be typed into a
	// phone - which is the whole reason the two are different mechanisms.
	AgentToken string

	// DataDir holds inbox.db, the blob directory and digest.json. A PVC in k8s.
	DataDir string

	// StaticDir is the built frontend (frontend/dist). Empty disables static serving,
	// which is what the tests do.
	StaticDir string

	// MaxUploadBytes caps one inbox upload. MediaRecorder Opus is roughly 1 MB per
	// minute, so the default is a very long recording rather than a tight limit.
	MaxUploadBytes int64

	// HeartbeatTTL is how long a heartbeat keeps the PC "online". Longer than the
	// agent's poll interval, so one dropped poll does not flip the whole app into
	// offline mode.
	HeartbeatTTL time.Duration

	// ProxyTimeout bounds a proxied request. Transcription is *not* proxied - the
	// agent drives it from the PC side - so nothing legitimate needs minutes here.
	ProxyTimeout time.Duration

	// Wake-on-LAN. Empty WOLMac disables waking entirely.
	WOLMac       string
	WOLBroadcast string
	WOLCooldown  time.Duration
}

func env(key, fallback string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return fallback
}

func envDuration(key string, fallback time.Duration) (time.Duration, error) {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback, nil
	}
	d, err := time.ParseDuration(raw)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", key, err)
	}
	return d, nil
}

func envBytes(key string, fallback int64) (int64, error) {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback, nil
	}
	n, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", key, err)
	}
	return n, nil
}

func LoadConfig() (Config, error) {
	cfg := Config{
		Addr:         env("ECT_RELAY_ADDR", ":8080"),
		PCBaseURL:    strings.TrimRight(env("ECT_RELAY_PC_URL", ""), "/"),
		AgentToken:   env("ECT_RELAY_TOKEN", ""),
		DataDir:      env("ECT_RELAY_DATA_DIR", "./data"),
		StaticDir:    env("ECT_RELAY_STATIC_DIR", ""),
		WOLMac:       env("ECT_RELAY_WOL_MAC", ""),
		WOLBroadcast: env("ECT_RELAY_WOL_BROADCAST", "255.255.255.255:9"),
	}

	var err error
	if cfg.MaxUploadBytes, err = envBytes("ECT_RELAY_MAX_UPLOAD_BYTES", 512<<20); err != nil {
		return cfg, err
	}
	if cfg.HeartbeatTTL, err = envDuration("ECT_RELAY_HEARTBEAT_TTL", 90*time.Second); err != nil {
		return cfg, err
	}
	if cfg.ProxyTimeout, err = envDuration("ECT_RELAY_PROXY_TIMEOUT", 30*time.Second); err != nil {
		return cfg, err
	}
	if cfg.WOLCooldown, err = envDuration("ECT_RELAY_WOL_COOLDOWN", 5*time.Minute); err != nil {
		return cfg, err
	}

	if cfg.PCBaseURL == "" {
		return cfg, fmt.Errorf("ECT_RELAY_PC_URL is required: the relay has nothing to proxy to")
	}
	// A relay with no token is a public, unauthenticated inbox *and* an open door to
	// the PC's unauthenticated API. Refusing to start is the only safe default.
	if cfg.AgentToken == "" {
		return cfg, fmt.Errorf("ECT_RELAY_TOKEN is required: it authenticates `ect agent`")
	}
	return cfg, nil
}
