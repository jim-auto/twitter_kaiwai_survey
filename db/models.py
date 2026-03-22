from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from config.settings import DB_PATH

Base = declarative_base()


class Community(Base):
    __tablename__ = "communities"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    config_path = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class User(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True)
    screen_name = Column(String, unique=True, index=True)
    display_name = Column(String, default="")
    bio = Column(Text, default="")
    followers_count = Column(Integer, default=0)
    following_count = Column(Integer, default=0)
    tweet_count = Column(Integer, default=0)
    created_at = Column(DateTime)
    profile_image = Column(String, default="")
    last_scraped = Column(DateTime)
    is_active = Column(Boolean, default=True)


class CommunityMember(Base):
    __tablename__ = "community_members"

    community_id = Column(String, ForeignKey("communities.id"), primary_key=True)
    user_id = Column(String, ForeignKey("users.user_id"), primary_key=True)
    confidence = Column(Float, default=0.0)
    source = Column(String, default="")  # seed, search, follow_expansion, manual
    shared_follows = Column(Integer, default=0)
    bio_match = Column(Boolean, default=False)
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_community_members_user", "user_id"),
    )


class FollowEdge(Base):
    __tablename__ = "follow_edges"

    source_user_id = Column(String, ForeignKey("users.user_id"), primary_key=True)
    target_user_id = Column(String, ForeignKey("users.user_id"), primary_key=True)
    scraped_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_follow_edges_target", "target_user_id"),
    )


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String, nullable=False)  # search, following, profile, interaction
    community_id = Column(String, ForeignKey("communities.id"))
    status = Column(String, default="pending")  # pending, running, completed, failed
    progress = Column(Integer, default=0)
    total = Column(Integer, default=0)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error = Column(Text)


def get_engine():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{DB_PATH}", echo=False)


def get_session() -> Session:
    engine = get_engine()
    return sessionmaker(bind=engine)()


def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
    return engine
