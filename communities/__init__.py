"""界隈定義の読み込み"""
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from config.settings import COMMUNITY_DIR


@dataclass
class CommunityDef:
    id: str
    name: str
    description: str = ""
    seeds: list[dict] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    bio_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    expansion: dict = field(default_factory=lambda: {
        "max_depth": 2,
        "min_shared_follows": 3,
        "max_members": 5000,
    })


def load_community(path: Path) -> CommunityDef:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return CommunityDef(
        id=data["id"],
        name=data["name"],
        description=data.get("description", ""),
        seeds=data.get("seeds", []),
        hashtags=data.get("hashtags", []),
        keywords=data.get("keywords", []),
        bio_patterns=data.get("bio_patterns", []),
        exclude_patterns=data.get("exclude_patterns", []),
        expansion=data.get("expansion", {"max_depth": 2, "min_shared_follows": 3, "max_members": 5000}),
    )


def load_all_communities() -> list[CommunityDef]:
    defs = []
    if not COMMUNITY_DIR.exists():
        return defs
    for f in sorted(COMMUNITY_DIR.glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        try:
            defs.append(load_community(f))
        except Exception as e:
            print(f"  [WARN] {f.name} 読み込み失敗: {e}")
    return defs
