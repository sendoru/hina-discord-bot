"""Curated application emoji registry."""
import asyncio
import logging
import re
import time

import discord

log = logging.getLogger("hina")

EMOJI_REFRESH_SECONDS = 5 * 60
EMOJI_RETRY_SECONDS = 60


class EmojiRegistry:
    def __init__(self, client, store, *, clock=time.monotonic):
        self.client, self.store = client, store
        self.lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self.live = {}
        self.checked_at = 0.0
        self._retry_after = 0.0
        self._clock = clock
        self._refresh_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None

    def _application_rows(self):
        return [row for row in self.store.emoji_rows() if row["source_guild_id"] is None]

    async def refresh(self) -> bool:
        """Refresh application emoji without blocking request-time catalog reads."""

        async with self._refresh_lock:
            rows = self._application_rows()
            if not rows:
                async with self.lock:
                    self.live = {}
                    self.checked_at = self._clock()
                    self._retry_after = 0.0
                return True

            now = self._clock()
            if now < self._retry_after:
                return False
            if self.checked_at and now - self.checked_at < EMOJI_REFRESH_SECONDS:
                return True

            try:
                fetched = {
                    str(emoji.id): emoji
                    for emoji in await self.client.fetch_application_emojis()
                }
            except discord.HTTPException as exc:
                self._retry_after = now + EMOJI_RETRY_SECONDS
                log.warning("Application emoji refresh failed (%s)", type(exc).__name__)
                return False

            async with self.lock:
                # Preserve an app emoji created locally while the remote fetch was in flight.
                current_ids = {
                    row["emoji_id"]
                    for row in self._application_rows()
                }
                live = {
                    emoji_id: emoji
                    for emoji_id, emoji in fetched.items()
                    if emoji_id in current_ids
                }
                for emoji_id in current_ids:
                    if emoji_id not in live and emoji_id in self.live:
                        live[emoji_id] = self.live[emoji_id]
                self.live = live
                self.checked_at = self._clock()
                self._retry_after = 0.0
            return True

    def start_polling(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(
            self._poll_loop(),
            name="application-emoji-poll",
        )

    async def close(self) -> None:
        tasks = [
            task
            for task in (self._refresh_task, self._poll_task)
            if task is not None and not task.done()
        ]
        self._refresh_task = None
        self._poll_task = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _poll_loop(self) -> None:
        while True:
            refreshed = await self.refresh()
            await asyncio.sleep(
                EMOJI_REFRESH_SECONDS if refreshed else EMOJI_RETRY_SECONDS
            )

    def _schedule_refresh(self) -> None:
        if not self._application_rows():
            return
        now = self._clock()
        if now < self._retry_after:
            return
        if self.checked_at and now - self.checked_at < EMOJI_REFRESH_SECONDS:
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        task = asyncio.create_task(
            self.refresh(),
            name="application-emoji-refresh",
        )
        self._refresh_task = task
        task.add_done_callback(self._finish_refresh)

    def _finish_refresh(self, task: asyncio.Task) -> None:
        if self._refresh_task is task:
            self._refresh_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 - isolate background refresh failures
            log.warning("Application emoji background refresh failed (%s)", type(exc).__name__)

    async def catalog(self, channel=None):
        self._schedule_refresh()
        async with self.lock:
            rows = self.store.emoji_rows()
            catalog = []
            for row in rows:
                if row["source_guild_id"] is None:
                    emoji = self.live.get(row["emoji_id"])
                else:
                    emoji = self.client.get_emoji(int(row["emoji_id"]))
                    if emoji is None or emoji.guild is None or emoji.guild.me is None or not emoji.is_usable():
                        continue
                    target = getattr(channel, "guild", None)
                    if (target is not None and target.id != int(row["source_guild_id"])
                            and (target.me is None
                                 or not channel.permissions_for(target.me).external_emojis)):
                        continue
                if emoji is not None:
                    catalog.append({"name": row["alias"], "description": row["description"],
                                    "id": row["emoji_id"], "markup": str(emoji)})
            return catalog

    async def add(self, alias, description, attachment=None, source=None):
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", alias):
            raise ValueError("별칭은 소문자로 시작하는 영문·숫자·밑줄 2~32자로 입력해 주세요.")
        description = description.strip()
        if not 1 <= len(description) <= 100:
            raise ValueError("사용 상황을 1~100자로 입력해 주세요.")
        if (attachment is None) == (source is None):
            raise ValueError("source의 기존 이모지 또는 image 파일 중 하나만 지정해 주세요.")
        if attachment is not None and attachment.size > 256 * 1024:
            raise ValueError("이미지는 256 KiB 이하여야 해요.")
        async with self.lock:
            rows = self.store.emoji_rows()
            if any(r["alias"] == alias for r in rows):
                raise ValueError("이미 등록된 별칭이에요. 설명 변경은 /emoji edit를 사용해 주세요.")
            if len(rows) >= 20:
                raise ValueError("최대 20개까지 등록할 수 있어요. 먼저 하나를 목록에서 제외해 주세요.")
            if source is not None:
                match = re.fullmatch(r"(?:<a?:[^:<>\s]{1,32}:([0-9]{1,20})>|([0-9]{1,20}))", source.strip())
                emoji = self.client.get_emoji(int(match[1] or match[2])) if match else None
                if emoji is None or emoji.guild is None or emoji.guild.me is None or not emoji.is_usable():
                    raise ValueError("봇이 사용할 수 있는 서버 이모지 또는 해당 ID를 지정해 주세요.")
                if any(r["emoji_id"] == str(emoji.id) for r in rows):
                    raise ValueError("이미 등록된 이모지예요.")
                self.store.add_emoji(alias, str(emoji.id), description, str(emoji.guild.id))
                return str(emoji)
            data = await attachment.read()
            if len(data) > 256 * 1024 or not (
                data.startswith((b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"\xff\xd8\xff"))
                or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
            ):
                raise ValueError("PNG·GIF·JPEG·WebP 이미지 파일을 첨부해 주세요.")
            emoji = await self.client.create_application_emoji(name=alias, image=data)
            try:
                self.store.add_emoji(alias, str(emoji.id), description)
            except Exception:  # Compensate remote creation after local failure.
                try:
                    await emoji.delete()
                except discord.HTTPException:
                    log.warning("Emoji registration rollback failed; inspect Developer Portal")
                raise
            self.live[str(emoji.id)] = emoji
            self.checked_at = self._clock()
            self._retry_after = 0.0
            return str(emoji)

    async def edit(self, alias, description):
        if not 1 <= len(description.strip()) <= 100:
            raise ValueError("사용 상황을 1~100자로 입력해 주세요.")
        async with self.lock:
            if not self.store.edit_emoji(alias, description.strip()):
                raise ValueError("등록되지 않은 별칭이에요.")

    async def remove(self, alias):
        async with self.lock:
            if not self.store.remove_emoji(alias):
                raise ValueError("등록되지 않은 별칭이에요.")
