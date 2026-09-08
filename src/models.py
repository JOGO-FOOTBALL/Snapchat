from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    metadata = MetaData(schema="snapchat")


class PublishLog(Base):
    __tablename__ = "publish_log"
    __table_args__ = (
        UniqueConstraint(
            "ig_content_id",
            "destination",
            "source",
            name="uq_publish_log_content_dest_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ig_content_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    destination: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    permalink: Mapped[str | None] = mapped_column(String)
    channel: Mapped[str | None] = mapped_column(String)
    published_by: Mapped[str | None] = mapped_column(String)
    snapchat_media_id: Mapped[str | None] = mapped_column(String)
    snapchat_request_id: Mapped[str | None] = mapped_column(String)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    snaps: Mapped[list["PublishLogSnap"]] = relationship(
        back_populates="publish_log", cascade="all, delete-orphan"
    )


class PublishLogSnap(Base):
    __tablename__ = "publish_log_snaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publish_log_id: Mapped[int] = mapped_column(
        ForeignKey("snapchat.publish_log.id"), nullable=False
    )
    snap_index: Mapped[int] = mapped_column(Integer, nullable=False)
    snapchat_media_id: Mapped[str] = mapped_column(String, nullable=False)
    snapchat_request_id: Mapped[str | None] = mapped_column(String)

    publish_log: Mapped["PublishLog"] = relationship(back_populates="snaps")
