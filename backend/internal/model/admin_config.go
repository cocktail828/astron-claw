package model

type AdminConfig struct {
	ID    uint   `gorm:"primaryKey;autoIncrement"`
	Key   string `gorm:"column:key;type:varchar(64);uniqueIndex:uk_admin_config_key;not null"`
	Value string `gorm:"column:value;type:text;not null"`
}

func (AdminConfig) TableName() string { return "admin_config" }
