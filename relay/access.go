package main

import (
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math/big"
	"net/http"
	"slices"
	"strings"
	"sync"
	"time"
)

const (
	clockSkew       = 30 * time.Second
	keyRefetchEvery = time.Minute
)

// AccessVerifier checks the Cloudflare Access login token (ADR 0009, part 4).
// RS256 only; keys come from the team's certs endpoint and are cached.
type AccessVerifier struct {
	teamDomain string
	aud        string
	emails     []string
	certsURL   string
	client     *http.Client

	mu          sync.Mutex
	keys        map[string]*rsa.PublicKey
	lastFetched time.Time
}

func NewAccessVerifier(teamDomain, aud string, emails []string) *AccessVerifier {
	return &AccessVerifier{
		teamDomain: teamDomain,
		aud:        aud,
		emails:     emails,
		certsURL:   "https://" + teamDomain + "/cdn-cgi/access/certs",
		client:     &http.Client{Timeout: 10 * time.Second},
		keys:       map[string]*rsa.PublicKey{},
	}
}

// Wrap rejects any request without a valid token. The real reason goes to the log; the
// caller only sees a generic message.
func (v *AccessVerifier) Wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := v.Verify(r.Header.Get("Cf-Access-Jwt-Assertion"), time.Now()); err != nil {
			log.Printf("access: rejected %s %s: %v", r.Method, r.URL.Path, err)
			writeJSONError(w, http.StatusForbidden, "sign-in required")
			return
		}
		next.ServeHTTP(w, r)
	})
}

type jwtHeader struct {
	Alg string `json:"alg"`
	Kid string `json:"kid"`
}

type jwtClaims struct {
	Iss   string          `json:"iss"`
	Aud   json.RawMessage `json:"aud"`
	Email string          `json:"email"`
	Exp   float64         `json:"exp"`
	Nbf   float64         `json:"nbf"`
}

func (v *AccessVerifier) Verify(token string, now time.Time) error {
	if token == "" {
		return errors.New("no token")
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return errors.New("not a JWT")
	}
	var header jwtHeader
	if err := decodeSegment(parts[0], &header); err != nil {
		return fmt.Errorf("header: %w", err)
	}
	if header.Alg != "RS256" {
		return fmt.Errorf("algorithm %q not accepted", header.Alg)
	}
	key, err := v.key(header.Kid)
	if err != nil {
		return err
	}
	sig, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return fmt.Errorf("signature: %w", err)
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if err := rsa.VerifyPKCS1v15(key, crypto.SHA256, digest[:], sig); err != nil {
		return errors.New("bad signature")
	}

	var claims jwtClaims
	if err := decodeSegment(parts[1], &claims); err != nil {
		return fmt.Errorf("claims: %w", err)
	}
	if claims.Exp == 0 || now.After(time.Unix(int64(claims.Exp), 0).Add(clockSkew)) {
		return errors.New("expired")
	}
	if claims.Nbf != 0 && now.Add(clockSkew).Before(time.Unix(int64(claims.Nbf), 0)) {
		return errors.New("not valid yet")
	}
	if claims.Iss != "https://"+v.teamDomain {
		return fmt.Errorf("wrong issuer %q", claims.Iss)
	}
	if !slices.Contains(audiences(claims.Aud), v.aud) {
		return errors.New("wrong audience")
	}
	if !slices.Contains(v.emails, strings.ToLower(claims.Email)) {
		return fmt.Errorf("email %q not allowed", claims.Email)
	}
	return nil
}

// audiences handles `aud` being either a string or a list, which JWT allows.
func audiences(raw json.RawMessage) []string {
	var list []string
	if json.Unmarshal(raw, &list) == nil {
		return list
	}
	var one string
	if json.Unmarshal(raw, &one) == nil {
		return []string{one}
	}
	return nil
}

func decodeSegment(seg string, out any) error {
	b, err := base64.RawURLEncoding.DecodeString(seg)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, out)
}

// key returns the signing key for kid. An unknown kid triggers one refetch, because Access
// rotates keys; the refetch is rate-limited so junk tokens cannot make us hammer Cloudflare.
func (v *AccessVerifier) key(kid string) (*rsa.PublicKey, error) {
	v.mu.Lock()
	defer v.mu.Unlock()
	if k, ok := v.keys[kid]; ok {
		return k, nil
	}
	if !v.lastFetched.IsZero() && time.Since(v.lastFetched) < keyRefetchEvery {
		return nil, fmt.Errorf("unknown key id %q", kid)
	}
	v.lastFetched = time.Now()
	keys, err := v.fetchKeys()
	if err != nil {
		return nil, fmt.Errorf("fetch signing keys: %w", err)
	}
	v.keys = keys
	k, ok := keys[kid]
	if !ok {
		return nil, fmt.Errorf("unknown key id %q", kid)
	}
	return k, nil
}

func (v *AccessVerifier) fetchKeys() (map[string]*rsa.PublicKey, error) {
	resp, err := v.client.Get(v.certsURL)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("certs endpoint returned %d", resp.StatusCode)
	}
	var set struct {
		Keys []struct {
			Kid string `json:"kid"`
			Kty string `json:"kty"`
			N   string `json:"n"`
			E   string `json:"e"`
		} `json:"keys"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&set); err != nil {
		return nil, err
	}
	keys := map[string]*rsa.PublicKey{}
	for _, k := range set.Keys {
		if k.Kty != "RSA" {
			continue
		}
		n, errN := base64.RawURLEncoding.DecodeString(k.N)
		e, errE := base64.RawURLEncoding.DecodeString(k.E)
		if errN != nil || errE != nil {
			continue
		}
		keys[k.Kid] = &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: int(new(big.Int).SetBytes(e).Int64())}
	}
	return keys, nil
}
