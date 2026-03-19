package model

import "time"

type ChatSession struct {
	ID            uint      `gorm:"primaryKey;autoIncrement" json:"-"`
	Token         string    `gorm:"column:token;type:varchar(64);index:idx_chat_sessions_token;not null" json:"-"`
	SessionID     string    `gorm:"column:session_id;type:varchar(36);uniqueIndex:uk_chat_sessions_session_id;not null" json:"id"`
	SessionNumber int       `gorm:"column:session_number;not null" json:"number"`
	CreatedAt     time.Time `gorm:"column:created_at;type:datetime;not null;index:idx_chat_sessions_created_at" json:"created_at"`
}

func (ChatSession) TableName() string { return "chat_sessions" }
