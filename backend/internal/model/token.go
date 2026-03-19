package model

import "time"

type Token struct {
	ID        uint      `gorm:"primaryKey;autoIncrement" json:"-"`
	Token     string    `gorm:"column:token;type:varchar(64);uniqueIndex:uk_tokens_token;not null" json:"token"`
	Name      string    `gorm:"column:name;type:varchar(255);default:''" json:"name"`
	CreatedAt time.Time `gorm:"column:created_at;type:datetime;not null" json:"created_at"`
	ExpiresAt time.Time `gorm:"column:expires_at;type:datetime;not null;index:idx_tokens_expires_at" json:"expires_at"`
}

func (Token) TableName() string { return "tokens" }
