"""One validated TOML document, with no network or filesystem writes on load."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .schemas import DEFAULT_PROMPT, validate_qq


class ConfigError(ValueError):
    """Safe configuration feedback that never includes submitted values."""


class _Section(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )


def _http_url(value, *, allow_empty=False, api=False):
    if allow_empty and not value:
        return value
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() for character in value)
        or (api and (parsed.query or parsed.fragment))
    ):
        raise ValueError("invalid_http_url")
    parsed.port
    return value


def _header_token(value):
    if not isinstance(value, str):
        return value
    # Validate before whitespace trimming so edge CR/LF cannot disappear.
    if any(ord(character) < 32 or ord(character) > 126 for character in value):
        raise ValueError("invalid_header_token")
    return value.strip(" ")


def _access_token(value):
    value = _header_token(value)
    if isinstance(value, str) and value and len(value) < 16:
        raise ValueError("invalid_access_token")
    return value


class ServerConfig(_Section):
    host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    port: int = Field(default=8765, ge=1, le=65535)
    data_dir: str = Field(default=".data", min_length=1, max_length=4096)
    admin_token: str = Field(default="", max_length=4000, repr=False)

    @field_validator("host")
    @classmethod
    def host_address(cls, value):
        if any(c.isspace() or c in "/\\\x00" for c in value):
            raise ValueError("invalid_host")
        return value

    @field_validator("data_dir")
    @classmethod
    def data_path(cls, value):
        if "\x00" in value:
            raise ValueError("invalid_path")
        return value

    @field_validator("admin_token", mode="before")
    @classmethod
    def administration_token(cls, value):
        return _access_token(value)


class ModelConfig(_Section):
    mode: Literal["live", "demo"] = "live"
    base_url: str = Field(default="", max_length=2000)
    name: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=4000, repr=False)
    temperature: float = Field(default=0.7, ge=0, le=2)
    context_messages: int = Field(default=30, ge=2, le=100)
    max_reply_chars: int = Field(default=800, ge=20, le=10000)

    @field_validator("api_key", mode="before")
    @classmethod
    def model_credential(cls, value):
        return _header_token(value)

    @field_validator("base_url")
    @classmethod
    def model_endpoint(cls, value):
        return _http_url(value, allow_empty=True, api=True)


class QQConfig(_Section):
    api_url: str = Field(default="http://127.0.0.1:3000", max_length=2000)
    api_token: str = Field(default="", max_length=4000, repr=False)
    login_url: str = Field(default="http://127.0.0.1:6099/webui/qq_login", max_length=2000)
    event_token: str = Field(default="", max_length=4000, repr=False)

    @field_validator("api_token", mode="before")
    @classmethod
    def bridge_credential(cls, value):
        return _header_token(value)

    @field_validator("api_url")
    @classmethod
    def bridge_endpoint(cls, value):
        return _http_url(value, api=True)

    @field_validator("login_url")
    @classmethod
    def login_page(cls, value):
        return _http_url(value)

    @field_validator("event_token", mode="before")
    @classmethod
    def webhook_token(cls, value):
        return _access_token(value)


class AgentConfig(_Section):
    friends: list[str] = Field(default_factory=list, max_length=500)
    prompt: str = Field(default=DEFAULT_PROMPT, max_length=20000)
    auto_start: bool = False
    reply_delay_seconds: float = Field(default=2.0, ge=0, le=60)

    @field_validator("friends")
    @classmethod
    def recipient_scope(cls, values):
        values = [validate_qq(value.strip()) for value in values]
        if len(set(values)) != len(values):
            raise ValueError("duplicate_friends")
        return values


class _Document(_Section):
    server: ServerConfig = Field(default_factory=ServerConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)


@dataclass(frozen=True)
class FileConfig:
    source_path: Path
    server: ServerConfig
    model: ModelConfig
    qq: QQConfig
    agent: AgentConfig

    @property
    def data_directory(self) -> Path:
        path = Path(self.server.data_dir).expanduser()
        if not path.is_absolute():
            path = self.source_path.parent / path
        return path.resolve()

    def settings_patch(self) -> dict:
        """Materialize all file-owned settings, including intentionally empty secrets."""
        return {
            "owner_name": "",
            "model_mode": self.model.mode,
            "model_base_url": self.model.base_url,
            "model_name": self.model.name,
            "model_api_key": self.model.api_key,
            "temperature": self.model.temperature,
            "context_messages": self.model.context_messages,
            "max_reply_chars": self.model.max_reply_chars,
            "bridge_base_url": self.qq.api_url,
            "bridge_token": self.qq.api_token,
            "bridge_login_url": self.qq.login_url,
            "global_prompt": self.agent.prompt,
            "reply_delay_seconds": self.agent.reply_delay_seconds,
            "simple_mode": True,
        }


def load_config(path: str | Path) -> FileConfig:
    try:
        source = Path(path).expanduser().resolve()
        document = tomllib.loads(source.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ConfigError("配置文件不存在（file_not_found）") from None
    except tomllib.TOMLDecodeError:
        raise ConfigError("配置文件 TOML 语法无效（toml_syntax）") from None
    except UnicodeError:
        raise ConfigError("配置文件需要使用 UTF-8 编码（encoding_error）") from None
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ConfigError("无法读取配置文件（file_read_error）") from None
    try:
        validated = _Document.model_validate(document)
    except ValidationError as exc:
        # Pydantic's default message includes input values, including API keys.
        errors = []
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            location = error["loc"]
            if error["type"] == "extra_forbidden":
                # A pasted credential can itself become an unknown TOML key.
                location = (*location[:-1], "unknown_field")
            field = ".".join(str(part) for part in location) or "config"
            errors.append(f"{field}: {error['type']}")
        raise ConfigError("配置字段无效：" + "; ".join(errors)) from None
    if validated.agent.auto_start:
        if (
            validated.model.mode != "live"
            or not validated.model.base_url
            or not validated.model.name
        ):
            raise ConfigError("agent.auto_start: requires_live_model")
        if not validated.agent.friends:
            raise ConfigError("agent.friends: required_for_auto_start")
        if not validated.agent.prompt:
            raise ConfigError("agent.prompt: required_for_auto_start")
    result = FileConfig(source, validated.server, validated.model, validated.qq, validated.agent)
    try:
        result.data_directory
    except (OSError, RuntimeError, ValueError):
        raise ConfigError("server.data_dir: path_error") from None
    return result
