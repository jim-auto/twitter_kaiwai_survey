"""DB CRUD操作"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db.models import Community, CommunityMember, FollowEdge, User


def merge_user_ids(
    session: Session,
    from_user_id: str,
    to_user_id: str,
    touch_last_scraped: bool = True,
    **user_updates,
) -> User:
    """ユーザーIDを正規化し、参照を壊さずに統合する。"""
    if from_user_id == to_user_id:
        return upsert_user(
            session,
            to_user_id,
            touch_last_scraped=touch_last_scraped,
            **user_updates,
        )

    source = session.get(User, from_user_id)
    target = session.get(User, to_user_id)

    if not target:
        target = User(user_id=to_user_id)
        session.add(target)

    if source:
        for field in [
            "screen_name", "display_name", "bio", "followers_count",
            "following_count", "tweet_count", "created_at",
            "profile_image", "last_scraped", "is_active",
        ]:
            source_value = getattr(source, field)
            target_value = getattr(target, field)
            if target_value in (None, "", 0) and source_value not in (None, "", 0):
                setattr(target, field, source_value)

    for member in session.query(CommunityMember).filter(CommunityMember.user_id == from_user_id).all():
        add_community_member(
            session,
            member.community_id,
            to_user_id,
            confidence=member.confidence,
            source=member.source,
            shared_follows=member.shared_follows or 0,
            bio_match=bool(member.bio_match),
        )
        session.delete(member)

    for edge in session.query(FollowEdge).filter(FollowEdge.source_user_id == from_user_id).all():
        remapped_target = to_user_id if edge.target_user_id == from_user_id else edge.target_user_id
        add_follow_edge(session, to_user_id, remapped_target)
        session.delete(edge)

    for edge in session.query(FollowEdge).filter(FollowEdge.target_user_id == from_user_id).all():
        remapped_source = to_user_id if edge.source_user_id == from_user_id else edge.source_user_id
        add_follow_edge(session, remapped_source, to_user_id)
        session.delete(edge)

    for key, value in user_updates.items():
        if value is not None:
            setattr(target, key, value)
    if touch_last_scraped:
        target.last_scraped = datetime.now(timezone.utc)

    if source and source is not target:
        session.delete(source)

    return target


def upsert_user(
    session: Session,
    user_id: str,
    touch_last_scraped: bool = True,
    **kwargs,
) -> User:
    user = session.get(User, user_id)
    screen_name = kwargs.get("screen_name")
    if screen_name:
        existing = session.query(User).filter(User.screen_name == screen_name).first()
        if existing and existing.user_id != user_id:
            return merge_user_ids(
                session,
                user_id,
                existing.user_id,
                touch_last_scraped=touch_last_scraped,
                **kwargs,
            )

    if user:
        for k, v in kwargs.items():
            if v is not None:
                setattr(user, k, v)
        if touch_last_scraped:
            user.last_scraped = datetime.now(timezone.utc)
    else:
        # screen_nameが既に別のuser_idで登録されている場合はそちらを更新
        user = User(
            user_id=user_id,
            last_scraped=datetime.now(timezone.utc) if touch_last_scraped else None,
            **kwargs,
        )
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


def ensure_user_rows_for_references(session: Session, community_id: str | None = None) -> int:
    """community_members / follow_edges にだけ存在する user_id の placeholder 行を補完する。"""
    referenced_ids: set[str] = set()

    member_query = session.query(CommunityMember.user_id)
    if community_id:
        member_query = member_query.filter(CommunityMember.community_id == community_id)
    referenced_ids.update(user_id for (user_id,) in member_query.distinct().all())

    if not community_id:
        referenced_ids.update(user_id for (user_id,) in session.query(FollowEdge.source_user_id).distinct().all())
        referenced_ids.update(user_id for (user_id,) in session.query(FollowEdge.target_user_id).distinct().all())

    existing_ids = {user_id for (user_id,) in session.query(User.user_id).all()}
    existing_screen_names = {screen_name for (screen_name,) in session.query(User.screen_name).filter(User.screen_name != None).all()}  # noqa: E711
    missing_ids = sorted(referenced_ids - existing_ids)

    for user_id in missing_ids:
        screen_name = user_id[3:] if user_id.startswith("sn:") else None
        if screen_name in existing_screen_names:
            screen_name = None
        session.add(User(user_id=user_id, screen_name=screen_name))

    return len(missing_ids)


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
