package service

import (
	"bytes"
	"io"
	"testing"
)

type stubStorage struct {
	putObject func(key string, body io.Reader, contentType string, contentLength int64) (string, error)
}

func (s *stubStorage) Start() error        { return nil }
func (s *stubStorage) Close() error        { return nil }
func (s *stubStorage) EnsureBucket() error { return nil }
func (s *stubStorage) BucketName() string  { return "test-bucket" }

func (s *stubStorage) PutObject(key string, body io.Reader, contentType string, contentLength int64) (string, error) {
	return s.putObject(key, body, contentType, contentLength)
}

func TestMediaManagerStore_PassesSeekableReaderToStorage(t *testing.T) {
	original := []byte("hello world")

	storage := &stubStorage{
		putObject: func(key string, body io.Reader, contentType string, contentLength int64) (string, error) {
			rs, ok := body.(io.ReadSeeker)
			if !ok {
				t.Fatalf("expected storage body to implement io.ReadSeeker, got %T", body)
			}

			payload, err := io.ReadAll(rs)
			if err != nil {
				t.Fatalf("read storage body: %v", err)
			}
			if !bytes.Equal(payload, original) {
				t.Fatalf("unexpected payload: got %q want %q", payload, original)
			}
			if _, err := rs.Seek(0, io.SeekStart); err != nil {
				t.Fatalf("seek storage body: %v", err)
			}

			return "http://example.com/file.txt", nil
		},
	}

	manager := NewMediaManager(storage)
	result, err := manager.Store(bytes.NewReader(original), "file.txt", int64(len(original)), "text/plain", "session-1")
	if err != nil {
		t.Fatalf("Store returned error: %v", err)
	}
	if result.DownloadURL != "http://example.com/file.txt" {
		t.Fatalf("unexpected download URL: %s", result.DownloadURL)
	}
}
