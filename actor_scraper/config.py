"""配置校验与对浏览器的密钥遮蔽。"""

import math
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .graphis import MODEL_ID


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server: str = ""
    user_id: str = ""
    api_key: str = ""
    workers: int = Field(default=4, ge=1, le=32)
    rps: float = Field(default=2, gt=0, le=30)
    timeout: float = Field(default=10, gt=0, le=120)
    retries: int = Field(default=1, ge=0, le=5)
    ttl_days: float = Field(default=30, ge=0, le=3650)
    miss_hours: float = Field(default=24, ge=0, le=8760)
    proxy: str = ""
    avatar_source: str = "auto"
    graphis_selection: str = "classic"
    no_backdrop: bool = False
    images_only: bool = True
    overwrite: bool = False
    replace_avatar: bool = False
    replace_backdrop: bool = False
    download_images: bool = True
    refresh: bool = False
    retention_days: int = Field(default=30, ge=1, le=3650)
    actor_mapping: dict[str, str] = Field(default_factory=dict)
    graphis_mapping: dict[str, str] = Field(default_factory=dict)

    @field_validator("server", "proxy")
    @classmethod
    def validate_url(cls, value, info):
        value = value.strip().rstrip("/")
        if value:
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("请输入 http(s) 地址")
            if info.field_name == "server" and (url.username or url.query or url.fragment):
                raise ValueError("FYMS 地址不应包含登录信息、查询参数或锚点")
        return value

    @field_validator("rps", "timeout", "ttl_days", "miss_hours")
    @classmethod
    def finite(cls, value):
        if not math.isfinite(value):
            raise ValueError("数值必须是有限数")
        return value

    @field_validator("avatar_source")
    @classmethod
    def source(cls, value):
        if value not in {"auto", "graphis", "profile", "gfriends", "none"}:
            raise ValueError("未知头像来源")
        return value

    @field_validator("graphis_selection")
    @classmethod
    def selection(cls, value):
        if value not in {"classic", "latest"}:
            raise ValueError("未知写真选择方式")
        return value

    @field_validator("actor_mapping", "graphis_mapping")
    @classmethod
    def mappings(cls, value, info):
        for name, source_id in value.items():
            valid = source_id.isdigit() if info.field_name == "actor_mapping" else MODEL_ID.fullmatch(source_id)
            if not name.strip() or not valid:
                raise ValueError(f"映射格式错误：{name}")
        return value

    def public(self):
        data = self.model_dump(exclude={"api_key"})
        data["api_key_configured"] = bool(self.api_key)
        return data


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    names: list[str] = Field(default_factory=list, max_length=10000)
    all: bool = False
    limit: int | None = Field(default=None, ge=1, le=1000000)
    mode: str = "preview"

    @model_validator(mode="after")
    def validate_scope(self):
        self.names = list(dict.fromkeys(n.strip() for n in self.names if n.strip()))
        if self.all == bool(self.names):
            raise ValueError("选择全部演员或提供姓名清单，两者选其一")
        if self.mode not in {"preview", "apply"}:
            raise ValueError("任务模式应为 preview / apply")
        return self
