import asyncio

from datetime import datetime
import discord

from discord import Member, User
from discord.utils import format_dt, time_snowflake
from enum import Enum

from typing import TYPE_CHECKING, NamedTuple
from .managerbase import BaseManager
from .database import RestrictionsDatabaseManager
from .utils import send_dm_message

if TYPE_CHECKING:
    from . import OptionalMember
    from asyncpg import Record
    from typing import Optional
    from kurisu import Kurisu
    from collections.abc import AsyncGenerator


class Restriction(Enum):
    Muted = 'Muted'
    TakeHelp = 'No-Help'
    Ban = 'ban'
    Probation = 'Probation'
    MetaMute = 'meta-mute'
    HelpMute = 'help-mute'
    NoElsewhere = 'No-elsewhere'
    NoEmbed = 'No-Embed'
    AppealsMute = 'appeal-mute'
    NoArt = 'No-art'
    NoAnimals = 'No-animals'
    NoTech = 'No-Tech'
    NoMemes = 'No-Memes'
    NoU = 'No-U'


messages = {Restriction.Muted: "You were muted!",
            Restriction.TakeHelp: "You lost access to help channels!",
            Restriction.Probation: "You are under probation!",
            Restriction.MetaMute: "You were meta muted!",
            Restriction.HelpMute: "You were muted in the help channels!",
            Restriction.NoElsewhere: "Your elsewhere access was revoked!",
            Restriction.NoEmbed: "You lost embed and upload permissions!",
            Restriction.AppealsMute: "You were appeal muted!",
            Restriction.NoArt: "Your art channel access was revoked!",
            Restriction.NoAnimals: "Your animal channel access was revoked!",
            Restriction.NoTech: "You lost access to the tech channel",
            Restriction.NoMemes: "You lost access to meme commands",
            Restriction.NoU: "You've been sent to the void"}


class TimedRestriction:
    __slots__ = ('restriction_id', 'user_id', 'type', 'end_date', 'alerted')

    def __init__(self, restriction_id: int, user_id: int, type: str, end_date: datetime, alerted: bool):
        self.restriction_id = restriction_id
        self.user_id = user_id
        self.type = type
        self.end_date = end_date
        self.alerted = alerted


class Softban(NamedTuple):
    user_id: int
    issuer_id: int
    reason: str


class RestrictionsManager(BaseManager, db_manager=RestrictionsDatabaseManager):
    """Manages user restrictions."""

    db: RestrictionsDatabaseManager

    def __init__(self, bot: 'Kurisu'):
        super().__init__(bot)
        asyncio.create_task(self.setup())

    async def setup(self):
        self._timed_restrictions: dict[tuple[int, str], TimedRestriction] = {(r.user_id, r.type): r
                                                                             async for r in
                                                                             self.get_timed_restrictions()}
        self._softbans: dict[int, Softban] = {r[1]: Softban(user_id=r[1], issuer_id=r[2], reason=r[3]) async for r in
                                              self.db.get_softbans()}

    @property
    def timed_restrictions(self) -> dict[tuple[int, str], TimedRestriction]:
        return self._timed_restrictions

    @property
    def softbans(self):
        return self._softbans

    async def add_restriction(self, user: 'Member | User | OptionalMember', restriction: Restriction,
                              reason: 'Optional[str]', *, end_date: 'Optional[datetime]' = None) -> int:
        """Add a restriction to the user id."""
        assert restriction in Restriction
        assert (restriction is Restriction.Ban and end_date) or restriction is not Restriction.Ban
        now = time_snowflake(datetime.now())
        if end_date:
            res = await self.db.add_timed_restriction(now, user.id, restriction.value, end_date)
        else:
            res = await self.db.add_restriction(now, user.id, restriction.value)
        if res:
            if end_date:
                self._timed_restrictions[user.id, restriction.value] = TimedRestriction(restriction_id=res,
                                                                                        user_id=user.id,
                                                                                        type=restriction.value,
                                                                                        end_date=end_date,
                                                                                        alerted=False)

            if restriction is not Restriction.Ban and isinstance(user, discord.Member):
                try:
                    if restriction not in (Restriction.AppealsMute, Restriction.Probation):
                        appeal_site = self.bot.channels['appeals'].mention
                    else:
                        appeal_site = "<@333857992170536961>"
                    await user.add_roles(self.bot.roles[restriction.value])
                    if restriction is Restriction.Muted:
                        await user.remove_roles(self.bot.roles['#elsewhere'], self.bot.roles['#art-discussion'])
                    msg_user = messages[restriction]
                    if reason:
                        msg_user += " The given reason is: " + reason
                    msg_user += ("\n\nIf you feel this was unjustified, "
                                 f"you may appeal in {appeal_site}")
                    if end_date:
                        msg_user += f"\n\nThis restriction lasts until {format_dt(end_date)}."
                    await send_dm_message(user, msg_user)
                except discord.NotFound:
                    # User may have been banned
                    pass
        return res

    async def get_restrictions_by_type(self, restriction: Restriction):
        assert restriction in Restriction
        async for r in self.db.get_restrictions_by_type(restriction.value):
            yield r

    async def get_restrictions_by_user(self,
                                       user_id: int) -> 'AsyncGenerator[Record, None]':
        assert isinstance(user_id, int)
        async for r in self.db.get_restrictions_by_user(user_id):
            yield r

    async def add_softban(self, user: 'Member | User | OptionalMember',
                          issuer: 'Member | User | OptionalMember', reason: str):
        res = await self.db.add_softban(user.id, issuer.id, reason)
        if res:
            self._softbans[user.id] = Softban(user_id=user.id, issuer_id=issuer.id, reason=reason)
            if isinstance(user, Member):
                msg = f"This account is no longer permitted to participate in {self.bot.guild.name}. The reason is: {reason}"
                await send_dm_message(user, msg)
                try:
                    await user.kick(reason=reason)
                except discord.Forbidden:
                    pass

    async def delete_softban(self, user: 'Member | User | OptionalMember'):
        res = await self.db.remove_softban(user.id)
        if res:
            del self._softbans[user.id]

    async def remove_restriction(self, user: 'Member | User | OptionalMember', restriction: Restriction) -> int:
        """Removes a restriction to."""
        assert restriction in Restriction

        res = await self.db.remove_restriction(user.id, restriction.value)

        if restriction is not Restriction.Ban and isinstance(user, discord.Member):
            await user.remove_roles(self.bot.roles[restriction.value])
        if res:
            try:
                del self._timed_restrictions[user.id, restriction.value]
            except KeyError:
                self.log.warn(f"Failed to remove timed restriction with key ({user.id}, {restriction})")

        return res

    async def set_timed_restriction_alert(self, user_id: int, restriction: str):
        """Removes a restriction to."""
        timed_res = self._timed_restrictions[user_id, restriction]
        res = await self.db.set_timed_restriction_alert(timed_res.restriction_id)
        if res:
            timed_res.alerted = True

    async def get_timed_restrictions(self):
        async for r in self.db.get_timed_restrictions():
            yield TimedRestriction(restriction_id=r[0],
                                   user_id=r[1],
                                   type=r[2],
                                   end_date=r[3],
                                   alerted=r[4])
