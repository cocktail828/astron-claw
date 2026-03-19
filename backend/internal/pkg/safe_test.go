package pkg

import "testing"

func TestSafePrefix(t *testing.T) {
	tests := []struct {
		input string
		n     int
		want  string
	}{
		{"", 10, ""},
		{"abc", 10, "abc"},
		{"abcdefghij", 10, "abcdefghij"},
		{"abcdefghijk", 10, "abcdefghij..."},
		{"sk-abcdef1234567890", 10, "sk-abcdef1..."},
		{"short", 5, "short"},
		{"short!", 5, "short..."},
	}
	for _, tt := range tests {
		got := SafePrefix(tt.input, tt.n)
		if got != tt.want {
			t.Errorf("SafePrefix(%q, %d) = %q, want %q", tt.input, tt.n, got, tt.want)
		}
	}
}
