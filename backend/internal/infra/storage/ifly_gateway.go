package storage

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/rs/zerolog/log"

	"astron-claw/backend/internal/config"
)

type IFlyGatewayStorage struct {
	cfg    config.StorageConfig
	client *http.Client
}

func NewIFlyGatewayStorage(cfg config.StorageConfig) *IFlyGatewayStorage {
	return &IFlyGatewayStorage{cfg: cfg}
}

func (s *IFlyGatewayStorage) Start() error {
	s.client = &http.Client{Timeout: 60 * time.Second}
	log.Info().Msg("iFlytek Gateway client initialised")
	return nil
}

func (s *IFlyGatewayStorage) Close() error {
	if s.client != nil {
		s.client.CloseIdleConnections()
		log.Info().Msg("iFlytek Gateway client closed")
	}
	return nil
}

func (s *IFlyGatewayStorage) EnsureBucket() error {
	// iFlytek Gateway does not require pre-creating buckets
	return nil
}

func (s *IFlyGatewayStorage) PutObject(key string, body io.Reader, contentType string, contentLength int64) (string, error) {
	// Extract filename from key
	filename := key
	if idx := strings.LastIndex(key, "/"); idx >= 0 {
		filename = key[idx+1:]
	}

	// Read body into bytes
	fileBytes, err := io.ReadAll(body)
	if err != nil {
		return "", fmt.Errorf("read body: %w", err)
	}

	// Build request URL
	params := url.Values{
		"get_link": {"true"},
		"link_ttl": {strconv.Itoa(s.cfg.TTL)},
		"filename": {filename},
		"expose":   {"true"},
	}
	reqURL := fmt.Sprintf("%s/api/v1/%s?%s", s.cfg.Endpoint, s.cfg.Bucket, params.Encode())

	// Build auth headers
	headers, err := buildAuthHeader(reqURL, "POST", s.cfg.AccessKey, s.cfg.SecretKey)
	if err != nil {
		return "", fmt.Errorf("build auth header: %w", err)
	}

	req, err := http.NewRequest("POST", reqURL, bytes.NewReader(fileBytes))
	if err != nil {
		return "", fmt.Errorf("create request: %w", err)
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	req.Header.Set("X-TTL", strconv.Itoa(s.cfg.TTL))
	req.Header.Set("Content-Length", strconv.Itoa(len(fileBytes)))

	t0 := time.Now()
	resp, err := s.client.Do(req)
	if err != nil {
		log.Error().Err(err).Str("key", key).Dur("took", time.Since(t0)).Msg("iFlytek put failed")
		return "", fmt.Errorf("http request: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	elapsed := time.Since(t0)

	if resp.StatusCode != 200 {
		log.Error().Str("key", key).Int("status", resp.StatusCode).Dur("took", elapsed).Msg("iFlytek put failed")
		return "", fmt.Errorf("iFlytek Gateway upload failed: status=%d, body=%s", resp.StatusCode, string(respBody[:min(len(respBody), 500)]))
	}

	var result struct {
		Code int `json:"code"`
		Data struct {
			Link string `json:"link"`
		} `json:"data"`
	}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return "", fmt.Errorf("parse response: %w", err)
	}
	if result.Code != 0 {
		return "", fmt.Errorf("iFlytek Gateway rejected: code=%d, body=%s", result.Code, string(respBody))
	}
	if result.Data.Link == "" {
		return "", fmt.Errorf("iFlytek Gateway response missing link field")
	}

	log.Info().Str("key", key).Int("size", len(fileBytes)).Dur("took", elapsed).Msg("iFlytek put")
	return result.Data.Link, nil
}

func (s *IFlyGatewayStorage) BucketName() string {
	return s.cfg.Bucket
}

// buildAuthHeader creates HMAC-SHA256 authentication headers for iFlytek Gateway.
func buildAuthHeader(rawURL, method, apiKey, apiSecret string) (map[string]string, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, fmt.Errorf("parse URL: %w", err)
	}
	host := u.Hostname()
	path := u.Path

	now := time.Now().UTC()
	date := now.Format(http.TimeFormat)

	// Digest of empty string
	emptyHash := sha256.Sum256([]byte(""))
	digest := "SHA256=" + base64.StdEncoding.EncodeToString(emptyHash[:])

	// Build signature string
	signatureStr := "host: " + host + "\n"
	signatureStr += "date: " + date + "\n"
	signatureStr += method + " " + path + " HTTP/1.1\n"
	signatureStr += "digest: " + digest

	// HMAC-SHA256 sign
	mac := hmac.New(sha256.New, []byte(apiSecret))
	mac.Write([]byte(signatureStr))
	sign := base64.StdEncoding.EncodeToString(mac.Sum(nil))

	authHeader := fmt.Sprintf(
		`api_key="%s", algorithm="hmac-sha256", headers="host date request-line digest", signature="%s"`,
		apiKey, sign,
	)

	return map[string]string{
		"Method":        method,
		"Host":          host,
		"Date":          date,
		"Digest":        digest,
		"Authorization": authHeader,
	}, nil
}
