ALTER TABLE `chat_sessions` ADD UNIQUE INDEX `idx_token_session_number` (`token`, `session_number`);
