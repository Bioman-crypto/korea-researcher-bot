import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands

import config
from skills import cache_manager, openalex_fetcher, query_parser, result_formatter, sheets_logger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _log_search(interaction: discord.Interaction, keyword: str, univ: Optional[str], since: Optional[int]) -> None:
    asyncio.create_task(
        asyncio.to_thread(
            sheets_logger.log_search_sync,
            interaction.user.id,
            str(interaction.user),
            interaction.guild.id if interaction.guild else None,
            interaction.guild.name if interaction.guild else None,
            keyword,
            univ,
            since,
        )
    )


class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Global slash commands synced.")

    async def on_ready(self):
        logger.info("Logged in as %s (id: %s)", self.user, self.user.id)


bot = Bot()




def _has_role(member: discord.Member, *role_names: str) -> bool:
    names = {r.name.lower() for r in member.roles}
    return bool(names & {r.lower() for r in role_names})


@bot.tree.command(name="search", description="Search Korean corresponding authors by keyword")
@app_commands.describe(
    keyword='Search keyword — supports AND, OR, "exact phrase"',
    univ="Filter by university/institution name (partial match)",
    since="Filter from year onwards (e.g. 2022)",
)
async def search(
    interaction: discord.Interaction,
    keyword: str,
    univ: Optional[str] = None,
    since: Optional[int] = None,
):
    if not isinstance(interaction.user, discord.Member) or not _has_role(
        interaction.user, config.OPERATOR_ROLE, config.ADMIN_ROLE
    ):
        await interaction.response.send_message(
            "You don't have permission to use this command. "
            "Please contact the admin.",
            ephemeral=True,
        )
        return

    parsed = query_parser.parse(keyword)
    if parsed["error"]:
        await interaction.response.send_message(parsed["error"], ephemeral=True)
        return

    _log_search(interaction, keyword, univ, since)

    query = parsed["query"]
    cache_key = cache_manager.make_key(query, univ, since)
    cached = cache_manager.get(cache_key)

    await interaction.response.defer(ephemeral=True, thinking=True)

    if cached is not None:
        embeds = result_formatter.build_embeds(cached, query, since, univ, from_cache=True)
        for embed in embeds:
            await interaction.followup.send(embed=embed, ephemeral=True)
        return

    collected_str = {"value": "0"}

    async def _edit(content: str) -> None:
        # 진행 표시 실패가 검색 자체를 멈추지 않도록 무시
        try:
            await interaction.edit_original_response(content=content)
        except discord.HTTPException as exc:
            logger.warning("Progress update failed: %s", exc)

    async def progress(collected: int, total: Optional[int]):
        collected_str["value"] = f"{collected}" + (f"/{total}" if total else "")
        await _edit(f"🔍 Searching for **{query}**... ({collected_str['value']} works collected)")

    async def on_busy(seconds: int):
        await _edit(
            f"⏳ Searching for **{query}**... ({collected_str['value']} works collected)\n"
            f"The search server is busy — retrying automatically in ~{seconds}s. Please wait."
        )

    try:
        works = await asyncio.wait_for(
            openalex_fetcher.fetch_works(query, since, progress, on_busy),
            timeout=config.SEARCH_DEADLINE_SECONDS,
        )
    except openalex_fetcher.QuotaExhaustedError as exc:
        logger.error("OpenAlex quota exhausted: %s", exc)
        await interaction.followup.send(
            content="Today's search capacity has been used up. Please try again after 9:00 AM (KST).",
            ephemeral=True,
        )
        return
    except asyncio.TimeoutError:
        logger.error("OpenAlex fetch timed out after %ds (query=%s)", config.SEARCH_DEADLINE_SECONDS, query)
        await interaction.followup.send(
            content="The search server is very busy right now. Please try again in a few minutes.",
            ephemeral=True,
        )
        return
    except Exception as exc:
        logger.error("OpenAlex fetch failed: %s", exc)
        await interaction.followup.send(
            content="API error after retries. Please try again later.",
            ephemeral=True,
        )
        return

    authors = openalex_fetcher.extract_authors(works, univ)
    cache_manager.set(cache_key, authors)

    embeds = result_formatter.build_embeds(authors, query, since, univ, from_cache=False)

    for embed in embeds:
        await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="listusers", description="List all members with the researcher role")
@app_commands.default_permissions(manage_guild=True)
async def listusers(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not _has_role(
        interaction.user, config.ADMIN_ROLE
    ):
        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    role = discord.utils.get(guild.roles, name=config.OPERATOR_ROLE)
    if role is None:
        await interaction.response.send_message(
            f'Role "{config.OPERATOR_ROLE}" not found in this server.',
            ephemeral=True,
        )
        return

    members = role.members
    if not members:
        await interaction.response.send_message(
            f'No members have the "{config.OPERATOR_ROLE}" role.',
            ephemeral=True,
        )
        return

    lines = [f"{i + 1}. {m.display_name} ({m.name})" for i, m in enumerate(members)]
    embed = discord.Embed(
        title=f'Members with "{config.OPERATOR_ROLE}" role',
        description="\n".join(lines),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Total: {len(members)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set. Copy .env.example to .env and fill in your token.")
    bot.run(config.DISCORD_TOKEN)
