package router

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestCheckDependencyHealthIgnoresRequestCancellation(t *testing.T) {
	parent, cancel := context.WithCancel(context.Background())
	cancel()

	called := false
	ok := checkDependencyHealth(parent, func(ctx context.Context) error {
		called = true
		if err := ctx.Err(); err != nil {
			t.Fatalf("dependency check received canceled context: %v", err)
		}
		return nil
	})

	if !called {
		t.Fatal("dependency check was not called")
	}
	if !ok {
		t.Fatal("dependency check should succeed when the request context is already canceled")
	}
}

func TestCheckDependencyHealthFailsOnTimeout(t *testing.T) {
	parent := context.Background()

	ok := checkDependencyHealth(parent, func(ctx context.Context) error {
		<-ctx.Done()
		return ctx.Err()
	})

	if ok {
		t.Fatal("dependency check should fail when the internal health timeout is exceeded")
	}
}

func TestHealthCheckTimeoutUsesParentDeadlineBudget(t *testing.T) {
	parent, cancel := context.WithTimeout(context.Background(), 80*time.Millisecond)
	defer cancel()

	start := time.Now()
	ok := checkDependencyHealth(parent, func(ctx context.Context) error {
		<-ctx.Done()
		return ctx.Err()
	})
	elapsed := time.Since(start)

	if ok {
		t.Fatal("dependency check should fail when the derived timeout expires")
	}
	if elapsed > 200*time.Millisecond {
		t.Fatalf("dependency check took too long: %v", elapsed)
	}
}

func TestCheckDependencyHealthReturnsFalseOnDependencyError(t *testing.T) {
	parent := context.Background()

	ok := checkDependencyHealth(parent, func(ctx context.Context) error {
		return errors.New("boom")
	})

	if ok {
		t.Fatal("dependency check should fail when the dependency returns an error")
	}
}
