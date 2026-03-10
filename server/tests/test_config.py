"""Tests for infra/config.py — load_config() and URL encoding."""

import os
from unittest.mock import patch

import pytest

from infra.config import load_config, MysqlConfig

_CONFIG_KEYS = [
    "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE",
    "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB", "REDIS_CLUSTER",
    "SERVER_HOST", "SERVER_PORT", "SERVER_WORKERS", "SERVER_LOG_LEVEL", "SERVER_ACCESS_LOG",
    "OSS_TYPE", "OSS_ENDPOINT", "OSS_PUBLIC_ENDPOINT", "OSS_ACCESS_KEY", "OSS_SECRET_KEY",
    "OSS_BUCKET", "OSS_REGION", "OSS_TTL",
]


def _clean_env():
    """Return a copy of os.environ with all config keys removed."""
    return {k: v for k, v in os.environ.items() if k not in _CONFIG_KEYS}


class TestLoadConfigDefaults:
    def test_load_config_defaults(self):
        """All default values are applied when no env vars are set."""
        with patch.dict(os.environ, _clean_env(), clear=True):
            cfg = load_config()

        assert cfg.mysql.host == "127.0.0.1"
        assert cfg.mysql.port == 3306
        assert cfg.mysql.user == "root"
        assert cfg.mysql.password == ""
        assert cfg.mysql.database == "astron_claw"

        assert cfg.redis.host == "127.0.0.1"
        assert cfg.redis.port == 6379
        assert cfg.redis.password == ""
        assert cfg.redis.db == 0
        assert cfg.redis.cluster is False

        assert cfg.server.host == "0.0.0.0"
        assert cfg.server.port == 8765
        assert cfg.server.log_level == "info"
        assert cfg.server.access_log is True

        assert cfg.storage.type == "s3"
        assert cfg.storage.endpoint == "http://localhost:9000"
        assert cfg.storage.bucket == "astron-claw-media"
        assert cfg.storage.ttl == 157788000

    def test_load_config_custom_env(self):
        """All env vars are picked up and parsed."""
        env = {
            **_clean_env(),
            "MYSQL_HOST": "db.example.com",
            "MYSQL_PORT": "3307",
            "MYSQL_USER": "admin",
            "MYSQL_PASSWORD": "secret",
            "MYSQL_DATABASE": "mydb",
            "REDIS_HOST": "redis.example.com",
            "REDIS_PORT": "6380",
            "REDIS_PASSWORD": "redispw",
            "REDIS_DB": "5",
            "REDIS_CLUSTER": "true",
            "SERVER_HOST": "127.0.0.1",
            "SERVER_PORT": "9000",
            "SERVER_WORKERS": "4",
            "SERVER_LOG_LEVEL": "debug",
            "SERVER_ACCESS_LOG": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config()

        assert cfg.mysql.host == "db.example.com"
        assert cfg.mysql.port == 3307
        assert cfg.mysql.user == "admin"
        assert cfg.mysql.password == "secret"
        assert cfg.mysql.database == "mydb"

        assert cfg.redis.host == "redis.example.com"
        assert cfg.redis.port == 6380
        assert cfg.redis.password == "redispw"
        assert cfg.redis.db == 5
        assert cfg.redis.cluster is True

        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 9000
        assert cfg.server.workers == 4
        assert cfg.server.log_level == "debug"
        assert cfg.server.access_log is False


class TestMysqlUrl:
    def test_mysql_url_special_chars(self):
        """Passwords with @, #, / are properly URL-encoded."""
        from urllib.parse import quote_plus

        cfg = MysqlConfig(
            host="localhost", port=3306, user="root",
            password="p@ss#w/rd", database="testdb",
        )
        url = cfg.url
        encoded_pw = quote_plus("p@ss#w/rd")
        assert encoded_pw in url
        assert "@localhost:3306/testdb" in url
        assert url.startswith("mysql+aiomysql://")
        assert url.endswith("?charset=utf8mb4")


class TestBooleanParsing:
    def test_redis_cluster_flag_true(self):
        with patch.dict(os.environ, {**_clean_env(), "REDIS_CLUSTER": "true"}, clear=True):
            assert load_config().redis.cluster is True

    def test_redis_cluster_flag_false(self):
        with patch.dict(os.environ, {**_clean_env(), "REDIS_CLUSTER": "false"}, clear=True):
            assert load_config().redis.cluster is False

    def test_redis_cluster_flag_uppercase(self):
        with patch.dict(os.environ, {**_clean_env(), "REDIS_CLUSTER": "TRUE"}, clear=True):
            assert load_config().redis.cluster is True

    def test_server_access_log_false(self):
        with patch.dict(os.environ, {**_clean_env(), "SERVER_ACCESS_LOG": "false"}, clear=True):
            assert load_config().server.access_log is False


class TestStorageConfig:
    def test_storage_defaults(self):
        """Storage config uses defaults when no OSS_* env vars are set."""
        with patch.dict(os.environ, _clean_env(), clear=True):
            cfg = load_config()
        assert cfg.storage.type == "s3"
        assert cfg.storage.endpoint == "http://localhost:9000"
        assert cfg.storage.public_endpoint == "http://localhost:9000"
        assert cfg.storage.access_key == "minioadmin"
        assert cfg.storage.bucket == "astron-claw-media"
        assert cfg.storage.region == "us-east-1"
        assert cfg.storage.ttl == 157788000

    def test_storage_ifly_gateway(self):
        """iFlytek Gateway storage config is parsed correctly."""
        env = {
            **_clean_env(),
            "OSS_TYPE": "ifly_gateway",
            "OSS_ENDPOINT": "http://sgw.xf-yun.com",
            "OSS_ACCESS_KEY": "test_key",
            "OSS_SECRET_KEY": "test_secret",
            "OSS_BUCKET": "test_bucket",
            "OSS_TTL": "3600",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config()
        assert cfg.storage.type == "ifly_gateway"
        assert cfg.storage.endpoint == "http://sgw.xf-yun.com"
        assert cfg.storage.access_key == "test_key"
        assert cfg.storage.secret_key == "test_secret"
        assert cfg.storage.bucket == "test_bucket"
        assert cfg.storage.ttl == 3600

    def test_storage_public_endpoint_fallback(self):
        """OSS_PUBLIC_ENDPOINT falls back to OSS_ENDPOINT if not set."""
        env = {**_clean_env(), "OSS_ENDPOINT": "http://internal:9000"}
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config()
        assert cfg.storage.public_endpoint == "http://internal:9000"

    def test_storage_invalid_type(self):
        """Invalid OSS_TYPE raises ValueError."""
        env = {**_clean_env(), "OSS_TYPE": "invalid_type"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match=r"Invalid OSS_TYPE.*invalid_type"):
                load_config()
