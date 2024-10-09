import json
import os
import re
from contextlib import suppress
from urllib.parse import quote_plus

import aiohttp
import discord
from aiocache import cached
from discord import app_commands
from discord.ext import commands

from utils.colorthief import get_color
from utils.jsons import SocialsJSON

platforms = {
    "spotify": {"name": "Spotify", "emote": "<:Music_Spotify:958786315883794532>"},
    "appleMusic": {
        "name": "Apple Music",
        "emote": "<:Music_AppleMusic:958786213337264169>",
    },
    "youtube": {"name": "YouTube", "emote": "<:Music_YouTube:958786388457840700>"},
}


class SuggestedSongsButton(discord.ui.Button):
    def __init__(self, cog, artist, title, color):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji="🔥",
        )
        self.cog = cog
        self.artist = artist
        self.title = title
        self.color = color

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            color=self.color,
            description="<a:discordloading:1199066225381228546> Fetching suggested songs...",
        )
        msg = await interaction.followup.send(embed=embed, ephemeral=True)
        suggested_songs = await self.cog.suggested_songs(self.artist, self.title)
        if suggested_songs:
            suggested_songs_str = "\n\n".join(
                [
                    f"**<:icons_music:1293362305886589010> {song['artist']} - {song['name']}**\n<:website:1290793095734100008> [[Apple Music]]({song['apple_music']}) [[Spotify]]({song['spotify']}) [[YouTube]]({song['youtube']})"
                    for song in suggested_songs
                ]
            )
            embed = discord.Embed(
                title="Suggested Songs",
                description=suggested_songs_str,
                color=self.color,
            )
            await msg.edit(embed=embed)
        else:
            await msg.edit(
                embed=discord.Embed(
                    description="No suggested songs found.", color=self.color
                )
            )


class Songs(commands.Cog, name="songs"):
    def __init__(self, bot):
        self.bot = bot
        self.config = SocialsJSON().load_json()
        self.config_cog = self.bot.get_cog("Config")
        self.pattern = re.compile(
            r"https:\/\/(open\.spotify\.com\/track\/[A-Za-z0-9]+|"
            r"music\.apple\.com\/[a-zA-Z]{2}\/album\/[a-zA-Z\d%\(\)-]+\/[\d]{1,10}\?i=[\d]{1,15}|"
            r"spotify\.link\/[A-Za-z0-9]+|"
            r"youtu\.be\/[A-Za-z0-9_-]{11}|"
            r"(?:www\.|m\.)?youtube\.com\/watch\?v=[A-Za-z0-9_-]{11}|"
            r"music\.youtube\.com\/watch\?v=[A-Za-z0-9_-]{11})"
        )
        self.suppress_embed_pattern = re.compile(
            r"https:\/\/(open\.spotify\.com\/track\/[A-Za-z0-9]+|"
            r"music\.apple\.com\/[a-zA-Z]{2}\/album\/[a-zA-Z\d%\(\)-]+\/[\d]{1,10}\?i=[\d]{1,15}|"
            r"music\.youtube\.com\/watch\?v=[A-Za-z0-9_-]{11})"
        )
        self.thumbnail = None

    async def check_enabled(self, site: str, config, guild_id: int = None):
        if guild_id is None:
            if not self.config[site]["enabled"]:
                return False
        else:
            if not await self.config_cog.get_config_value(guild_id, site, "enabled"):
                return False
        return True

    @cached(ttl=86400)
    async def suggested_songs(self, artist: str, track: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://ws.audioscrobbler.com/2.0/?method=track.getsimilar&artist={quote_plus(artist)}&track={quote_plus(track)}&api_key={os.getenv('LASTFM_TOKEN')}&format=json"
            ) as resp:
                if resp.status != 200:
                    return None
                res = await resp.json()
                suggested_songs = []
                for track in res["similartracks"]["track"][:5]:
                    spotify_url = await self.lastfm_to_spotify(track["url"])
                    if spotify_url:
                        links = await self.get_song_links(spotify_url)
                        suggested_songs.append(
                            {
                                "name": track["name"],
                                "artist": track["artist"]["name"],
                                "spotify": links.get("spotify") + "?autoplay=0",
                                "apple_music": links.get("appleMusic"),
                                "youtube": links.get("youtube"),
                            }
                        )
                return suggested_songs

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        if message.author.bot and not message.author.id == 356268235697553409:
            return
        if not await self.check_enabled("songs", self.config, message.guild.id):
            return
        if message.author.bot and message.author.id == 356268235697553409:
            if message.embeds:
                lastfm_pattern = re.compile(
                    r"https:\/\/www\.last\.fm\/music\/[\w\+\-_%&]+\/_\/[\w\+\-_%,'\s().&]+"
                )
                embed_json = str(message.embeds[0].to_dict())
                lastfm_match = lastfm_pattern.search(embed_json)
                if lastfm_match:
                    lastfm_link = lastfm_match.group(0)
                    if lastfm_link.endswith(")"):
                        lastfm_link = lastfm_link[:-1]
                    spotify_link = await self.lastfm_to_spotify(lastfm_link)
                    if spotify_link:
                        await self.generate_view(message, spotify_link)
                        await self.config_cog.increment_link_fix_count("songs")
                        return
        if match := self.pattern.search(message.content.strip("<>")):
            link = match.group(0)
            await self.generate_view(message, link)
            await self.config_cog.increment_link_fix_count("songs")
            return

    @cached(ttl=86400)
    async def lastfm_to_spotify(self, link: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(link) as resp:
                if resp.status != 200:
                    return None
                content = await resp.text()
                match = re.search(
                    r'href="(https:\/\/open\.spotify\.com\/track\/[a-zA-Z0-9]+)"',
                    content,
                )
                if match:
                    spotify_link = match.group(1)
                    return spotify_link
                else:
                    return None

    @cached(ttl=86400)
    async def get_song_links(self, url: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.song.link/v1-alpha.1/links?url={url}"
            ) as resp:
                if resp.status != 200:
                    return None
                res = await resp.json()

        links = {}
        for platform in ["spotify", "appleMusic", "youtube"]:
            if platform_data := res.get("linksByPlatform", {}).get(platform):
                links[platform] = platform_data.get("url")

        return links

    async def generate_view(self, message: discord.Message, link: str):
        links = await self.get_song_links(link)
        if not links:
            return None

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.song.link/v1-alpha.1/links?url={link}"
            ) as resp:
                if resp.status != 200:
                    return None
                res = await resp.json()

        spotify_data = res.get("linksByPlatform", {}).get("spotify")
        unique_id = (
            spotify_data.get("entityUniqueId")
            if spotify_data is not None
            else res.get("entityUniqueId")
        )
        data = res.get("entitiesByUniqueId", {}).get(unique_id, {})
        artist = data.get("artistName")
        title = data.get("title")
        thumbnail = data.get("thumbnailUrl")

        if not all([artist, title, thumbnail]):
            return

        color = await get_color(thumbnail)
        view = discord.ui.View()
        original_platform = None
        has_spotify_or_apple = False
        has_youtube = False

        view.add_item(SuggestedSongsButton(self, artist, title, color))

        for platform, body in platforms.items():
            if platform in links:
                platform_url = links[platform]
                if platform_url in link:
                    original_platform = platform
                if platform in ["spotify", "appleMusic"]:
                    has_spotify_or_apple = True
                elif platform == "youtube":
                    has_youtube = True
                view.add_item(
                    discord.ui.Button(
                        style=discord.ButtonStyle.link,
                        emoji=body["emote"],
                        url=(
                            platform_url + "?autoplay=0"
                            if platform.lower() == "spotify"
                            else platform_url
                        ),
                    )
                )

        should_reply = (original_platform in ["spotify", "appleMusic"]) or (
            has_youtube and has_spotify_or_apple
        )
        if should_reply:
            embed = discord.Embed(color=color)
            embed.set_author(name=f"{artist} - {title}", icon_url=thumbnail)

            if message.channel.permissions_for(message.guild.me).send_messages:
                original_embed_suppressed = (
                    self.suppress_embed_pattern.search(link) is not None
                )
                if (
                    original_embed_suppressed
                    and not message.author.id == 356268235697553409
                ):
                    await message.reply(embed=embed, view=view, mention_author=False)
                else:
                    await message.reply(view=view, mention_author=False)

        if not message.author.bot and not message.author.id == 356268235697553409:
            if self.suppress_embed_pattern.search(link):
                with suppress(discord.errors.Forbidden, discord.errors.NotFound):
                    await message.edit(suppress=True)

    @app_commands.command(name="song", description="Generate a fixed embed for a song.")
    @app_commands.describe(url="The URL of the song.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def song_command(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer()

        if not self.pattern.match(url):
            await interaction.followup.send(
                "Invalid song URL. Please provide a valid Spotify, Apple Music, or YouTube link.",
                ephemeral=True,
            )
            return

        links = await self.get_song_links(url)
        if not links:
            await interaction.followup.send(
                "Unable to fetch song information.", ephemeral=True
            )
            return

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.song.link/v1-alpha.1/links?url={url}"
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        "Unable to fetch song information.", ephemeral=True
                    )
                    return
                res = await resp.json()

        spotify_data = res.get("linksByPlatform", {}).get("spotify")
        unique_id = (
            spotify_data.get("entityUniqueId")
            if spotify_data is not None
            else res.get("entityUniqueId")
        )
        data = res.get("entitiesByUniqueId", {}).get(unique_id, {})
        artist = data.get("artistName")
        title = data.get("title")
        self.thumbnail = data.get("thumbnailUrl")

        if not all([artist, title, self.thumbnail]):
            await interaction.followup.send(
                "Unable to fetch complete song information.", ephemeral=True
            )
            return

        color = await get_color(self.thumbnail)
        embed = discord.Embed(color=color)
        embed.set_author(name=f"{artist} - {title}", icon_url=self.thumbnail)

        view = discord.ui.View()
        view.add_item(SuggestedSongsButton(self, artist, title, color))

        for platform, body in platforms.items():
            if platform in links:
                platform_url = links[platform]
                view.add_item(
                    discord.ui.Button(
                        style=discord.ButtonStyle.link,
                        emoji=body["emote"],
                        url=(
                            platform_url + "?autoplay=0"
                            if platform.lower() == "spotify"
                            else platform_url
                        ),
                    )
                )

        await interaction.followup.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Songs(bot))
