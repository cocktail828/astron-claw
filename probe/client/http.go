package client

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"

	"github.com/hygao1024/astron-claw/probe/model"
)

// AdminLogin posts to /api/admin/auth/login and returns the admin_session cookie value.
func AdminLogin(ctx context.Context, baseURL, password string) (string, error) {
	body, _ := json.Marshal(map[string]string{"password": password})
	req, _ := http.NewRequestWithContext(ctx, "POST", baseURL+"/api/admin/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", model.NewCodeError(-1, "request failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", model.NewCodeError(resp.StatusCode, "http %d", resp.StatusCode)
	}

	var cr model.CodeResponse
	if err := json.NewDecoder(resp.Body).Decode(&cr); err != nil {
		return "", model.NewCodeError(-1, "decode: %v", err)
	}
	if cr.Code != 0 {
		return "", model.NewCodeError(cr.Code, "%s", cr.Error)
	}

	for _, c := range resp.Cookies() {
		if c.Name == "admin_session" {
			return c.Value, nil
		}
	}
	return "", model.NewCodeError(-1, "admin_session cookie not found")
}

// CreateToken posts to /api/admin/tokens and returns the new token string.
func CreateToken(ctx context.Context, baseURL, sessionCookie string, expiresIn int) (string, error) {
	body, _ := json.Marshal(map[string]any{
		"name":       "probe-test",
		"expires_in": expiresIn,
	})
	req, _ := http.NewRequestWithContext(ctx, "POST", baseURL+"/api/admin/tokens", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.AddCookie(&http.Cookie{Name: "admin_session", Value: sessionCookie})

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", model.NewCodeError(-1, "request failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", model.NewCodeError(resp.StatusCode, "http %d", resp.StatusCode)
	}

	var tr model.TokenResponse
	if err := json.NewDecoder(resp.Body).Decode(&tr); err != nil {
		return "", model.NewCodeError(-1, "decode: %v", err)
	}
	if tr.Code != 0 {
		return "", model.NewCodeError(tr.Code, "%s", tr.Error)
	}
	if tr.Token == "" {
		return "", model.NewCodeError(-1, "empty token in response")
	}
	return tr.Token, nil
}

// DeleteToken sends DELETE /api/admin/tokens/{token}.
func DeleteToken(baseURL, token, sessionCookie string) error {
	req, _ := http.NewRequest("DELETE", baseURL+"/api/admin/tokens/"+token, nil)
	req.AddCookie(&http.Cookie{Name: "admin_session", Value: sessionCookie})

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return model.NewCodeError(-1, "request failed: %v", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)

	if resp.StatusCode != http.StatusOK {
		return model.NewCodeError(resp.StatusCode, "http %d", resp.StatusCode)
	}
	return nil
}
