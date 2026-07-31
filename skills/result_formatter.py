from typing import Optional

import discord

import config


def build_embeds(
    authors: list[dict],
    query: str,
    since: Optional[int],
    univ: Optional[str],
    from_cache: bool,
) -> list[discord.Embed]:
    """
    Build up to 2 Discord embeds (25 results each, max 50 total).
    """
    from datetime import date

    year_end = date.today().year
    year_start = since if since else (year_end - config.RESULTS_YEAR_RANGE)
    period = f"{year_start}–{year_end}"

    display = authors[: config.INLINE_LIMIT]
    total_shown = len(display)
    total_found = len(authors)

    filter_note = ""
    if univ:
        filter_note += f"  |  Institution: {univ}"
    if since:
        filter_note += f"  |  Since: {since}"

    embeds: list[discord.Embed] = []
    chunks = [
        display[i : i + config.EMBED_PAGE_SIZE]
        for i in range(0, len(display), config.EMBED_PAGE_SIZE)
    ]

    for idx, chunk in enumerate(chunks):
        embed = discord.Embed(
            title="🔍 Researcher Search Results"
            + (" (continued)" if idx > 0 else ""),
            color=discord.Color.blue(),
        )

        if idx == 0:
            embed.description = (
                f"**Query:** {query}\n"
                f"**Period:** {period}  |  🇰🇷 Korea  |  Article"
                + filter_note
            )

        # Split into sub-chunks of 10 to stay under Discord's 1024-char field limit
        for sub_start in range(0, len(chunk), 8):
            sub = chunk[sub_start : sub_start + 8]
            lines = []
            for i, author in enumerate(sub):
                num = idx * config.EMBED_PAGE_SIZE + sub_start + i + 1
                name = author["name"][:40]
                institution = author["institution"][:60]
                count = author.get("paper_count", 1)
                lines.append(f"{num}. **{name}** — {institution} ({count}편)")
            embed.add_field(name="​", value="\n".join(lines), inline=False)

        start = idx * config.EMBED_PAGE_SIZE + 1
        end = start + len(chunk) - 1
        cache_note = "⚡ Cached result (24h)  |  " if from_cache else ""
        embed.set_footer(
            text=f"{cache_note}{total_found} authors found  |  Showing {start}–{end} of {total_shown}"
        )

        embeds.append(embed)

    if not embeds:
        embed = discord.Embed(
            title="🔍 Researcher Search Results",
            description=(
                f"**Query:** {query}\n"
                f"**Period:** {period}  |  🇰🇷 Korea  |  Article"
                + filter_note
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="​",
            value="No corresponding authors found for this query.",
            inline=False,
        )
        embeds.append(embed)

    return embeds
