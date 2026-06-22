-- Инициализация схемы базы данных

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    tg_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    daily_limit INT NOT NULL DEFAULT 10,
    cooldown_seconds INT NOT NULL DEFAULT 60,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workflows (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    n8n_webhook_url VARCHAR(512),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS generations (
    id UUID PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    workflow_id INT REFERENCES workflows(id) ON DELETE SET NULL,
    model_type VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending', -- pending, processing, completed, failed
    comfy_prompt_id VARCHAR(255),
    negative_prompt TEXT,
    inputs JSONB NOT NULL DEFAULT '{}'::JSONB,
    outputs JSONB NOT NULL DEFAULT '{}'::JSONB,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индекс для быстрого поиска генераций по идентификатору prompt_id из ComfyUI
CREATE INDEX IF NOT EXISTS idx_generations_comfy_prompt_id ON generations(comfy_prompt_id);
-- Индекс для быстрого поиска генераций по пользователю
CREATE INDEX IF NOT EXISTS idx_generations_user_id ON generations(user_id);
