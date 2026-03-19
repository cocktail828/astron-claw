package pkg

// SafePrefix returns at most the first n characters of s followed by "...".
// If s is shorter than or equal to n, it returns s unchanged.
func SafePrefix(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
