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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    metadata = MetaData(schema="crosspost")


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
    platform: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    permalink: Mapped[str | None] = mapped_column(String)
    channel: Mapped[str | None] = mapped_column(String)
    published_by: Mapped[str | None] = mapped_column(String)
    external_media_id: Mapped[str | None] = mapped_column(String)
    external_request_id: Mapped[str | None] = mapped_column(String)
    extra: Mapped[dict | None] = mapped_column(JSONB)
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

    items: Mapped[list["PublishLogItem"]] = relationship(
        back_populates="publish_log", cascade="all, delete-orphan"
    )


class PublishLogItem(Base):
    __tablename__ = "publish_log_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publish_log_id: Mapped[int] = mapped_column(
        ForeignKey("crosspost.publish_log.id"), nullable=False
    )
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    external_media_id: Mapped[str] = mapped_column(String, nullable=False)
    external_request_id: Mapped[str | None] = mapped_column(String)

    publish_log: Mapped["PublishLog"] = relationship(back_populates="items")
