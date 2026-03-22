"""DB CRUD操作"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db.models import Community, CommunityMember, FollowEdge, User


def upsert_user(session: Session, user_id: str, **kwargs) -> User:
    user = session.get(User, user_id)
    if user:
        for k, v in kwargs.items():
            if v is not None:
                setattr(user, k, v)
        user.last_scraped = datetime.now(timezone.utc)
    else:
        user = User(user_id=user_id, last_scraped=datetime.now(timezone.utc), **kwargs)
        session.add(user)
    return user


def upsert_community(session: Session, community_id: str, **kwargs) -> Community:
    community = session.get(Community, community_id)
    if community:
        for k, v in kwargs.items():
            if v is not None:
                setattr(community, k, v)
        community.updated_at = datetime.now(timezone.utc)
    else:
        community = Community(id=community_id, **kwargs)
        session.add(community)
    return community


def add_community_member(
    session: Session, community_id: str, user_id: str,
    confidence: float = 0.0, source: str = "", shared_follows: int = 0,
    bio_match: bool = False,
) -> CommunityMember:
    existing = session.get(CommunityMember, (community_id, user_id))
    if existing:
        if confidence > existing.confidence:
            existing.confidence = confidence
            existing.source = source
        if shared_follows > existing.shared_follows:
            existing.shared_follows = shared_follows
        if bio_match:
            existing.bio_match = True
        return existing
    member = CommunityMember(
        community_id=community_id, user_id=user_id,
        confidence=confidence, source=source,
        shared_follows=shared_follows, bio_match=bio_match,
    )
    session.add(member)
    return member


def add_follow_edge(session: Session, source_user_id: str, target_user_id: str):
    existing = session.get(FollowEdge, (source_user_id, target_user_id))
    if not existing:
        edge = FollowEdge(source_user_id=source_user_id, target_user_id=target_user_id)
        session.add(edge)


def get_community_member_ids(session: Session, community_id: str, min_confidence: float = 0.0) -> set[str]:
    rows = (
        session.query(CommunityMember.user_id)
        .filter(CommunityMember.community_id == community_id)
        .filter(CommunityMember.confidence >= min_confidence)
        .all()
    )
    return {r[0] for r in rows}


def get_all_community_ids(session: Session) -> list[str]:
    return [r[0] for r in session.query(Community.id).all()]
