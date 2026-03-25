package service

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"strings"

	"github.com/google/uuid"
	"github.com/rs/zerolog/log"

	"astron-claw/backend/internal/infra/storage"
)

const MaxFileSize = 500 * 1024 * 1024 // 500 MB

var (
	ErrFileTooLarge = errors.New("file too large")
	ErrFileEmpty    = errors.New("file is empty")
	ErrMIMERejected = errors.New("unsupported MIME type")
)

var allowedMIMEPrefixes = []string{
	"image/",
	"audio/",
	"video/",
	"application/pdf",
	"application/zip",
	"application/octet-stream",
	"text/",
}

// MediaManager manages file uploads to object storage.
type MediaManager struct {
	storage storage.ObjectStorage
}

// NewMediaManager creates a new MediaManager.
func NewMediaManager(s storage.ObjectStorage) *MediaManager {
	return &MediaManager{storage: s}
}

// MediaResult represents the result of a file upload.
type MediaResult struct {
	FileName    string `json:"fileName"`
	MimeType    string `json:"mimeType"`
	FileSize    int64  `json:"fileSize"`
	SessionID   string `json:"sessionId"`
	DownloadURL string `json:"downloadUrl"`
}

// Store uploads a file to object storage.
func (m *MediaManager) Store(body io.Reader, fileName string, fileSize int64, mimeType, sessionID string) (*MediaResult, error) {
	if fileSize > MaxFileSize {
		log.Warn().Int64("size", fileSize).Int64("max", MaxFileSize).Msg("Media rejected: file too large")
		return nil, ErrFileTooLarge
	}
	if fileSize == 0 {
		log.Warn().Str("name", fileName).Msg("Media rejected: empty file")
		return nil, ErrFileEmpty
	}

	// MIME sniffing: read first 512 bytes to detect actual content type
	buf := make([]byte, 512)
	n, err := io.ReadAtLeast(body, buf, 1)
	if err != nil && err != io.ErrUnexpectedEOF {
		return nil, fmt.Errorf("read for MIME detection: %w", err)
	}
	detected := http.DetectContentType(buf[:n])
	// Use detected type if the declared type is generic
	if mimeType == "" || mimeType == "application/octet-stream" {
		mimeType = detected
	}

	if !isMIMEAllowed(mimeType) {
		log.Warn().Str("mime", mimeType).Str("detected", detected).Str("name", fileName).Msg("Media rejected: unsupported MIME type")
		return nil, ErrMIMERejected
	}

	// Preserve seekability for multipart uploads so S3-compatible SDKs can
	// compute checksums on plain HTTP endpoints such as local MinIO.
	storageBody, err := rewindForStorage(body, buf[:n])
	if err != nil {
		return nil, err
	}

	// Sanitize filename
	safeName := filepath.Base(fileName)
	if safeName == "" || safeName == "." || strings.HasPrefix(safeName, ".") {
		safeName = "unnamed"
	}

	// Use provided sessionId or generate UUID
	sid := sessionID
	if sid == "" {
		sid = uuid.New().String()
	}

	key := sid + "/" + safeName

	downloadURL, err := m.storage.PutObject(key, storageBody, mimeType, fileSize)
	if err != nil {
		return nil, err
	}

	log.Info().
		Str("bucket", m.storage.BucketName()).
		Str("key", key).
		Str("mime", mimeType).
		Int64("size", fileSize).
		Msg("Stored media")

	return &MediaResult{
		FileName:    safeName,
		MimeType:    mimeType,
		FileSize:    fileSize,
		SessionID:   sid,
		DownloadURL: downloadURL,
	}, nil
}

func rewindForStorage(body io.Reader, sniffed []byte) (io.Reader, error) {
	if rs, ok := body.(io.ReadSeeker); ok {
		if _, err := rs.Seek(0, io.SeekStart); err != nil {
			return nil, fmt.Errorf("rewind seekable body: %w", err)
		}
		return rs, nil
	}

	payload, err := io.ReadAll(body)
	if err != nil {
		return nil, fmt.Errorf("buffer non-seekable body: %w", err)
	}
	combined := append(append([]byte{}, sniffed...), payload...)
	return bytes.NewReader(combined), nil
}

func isMIMEAllowed(mimeType string) bool {
	if mimeType == "" {
		return false
	}
	for _, prefix := range allowedMIMEPrefixes {
		if strings.HasPrefix(mimeType, prefix) {
			return true
		}
	}
	return false
}
