-- tokens table
CREATE TABLE IF NOT EXISTS `tokens` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `token` VARCHAR(64) NOT NULL,
    `name` VARCHAR(255) NOT NULL DEFAULT '',
    `created_at` DATETIME NOT NULL,
    `expires_at` DATETIME NOT NULL,
    UNIQUE INDEX `uk_tokens_token` (`token`),
    INDEX `idx_tokens_expires_at` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- admin_config table
CREATE TABLE IF NOT EXISTS `admin_config` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `key` VARCHAR(64) NOT NULL,
    `value` TEXT NOT NULL,
    UNIQUE INDEX `uk_admin_config_key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- chat_sessions table
CREATE TABLE IF NOT EXISTS `chat_sessions` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `token` VARCHAR(64) NOT NULL,
    `session_id` VARCHAR(36) NOT NULL,
    `session_number` INT NOT NULL,
    `created_at` DATETIME NOT NULL,
    INDEX `idx_chat_sessions_token` (`token`),
    UNIQUE INDEX `uk_chat_sessions_session_id` (`session_id`),
    INDEX `idx_chat_sessions_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
