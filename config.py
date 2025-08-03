from typing import Literal, Optional, TypedDict
import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from os import path
from jsonschema import RefResolver, validate


class DotEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    config: str = Field(default="./config.yml")
    github_token: Optional[str] = Field(default=None)


class TargetCondition:
    eventType: str
    action: str


class Target(TypedDict):
    repo: str
    path: Optional[str]
    secret: Optional[str]
    conditions: list[TargetCondition]
    deploy: Literal["git", "download_file", "relation"]
    filename: Optional[str]
    relation: Optional[str]


class Base(TypedDict):
    tmp: Optional[str]


class Settings(TypedDict):
    base: Optional[Base]
    this: Target
    targets: dict[str, Target]


dot = DotEnv()
settings: Optional[Settings] = None


if path.exists(dot.config):
    with open("./config_format.yml", "r") as f:
        schema = yaml.safe_load(f)
    base_dir = path.abspath("./")
    resolver = RefResolver(f"file://{base_dir}/", referrer=schema)
    with open(dot.config, "r") as f:
        settings = yaml.safe_load(f)
    validate(settings, schema, resolver=resolver)
