import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv

load_dotenv(".ryoenv") 

RYOTOKEN = os.getenv("RYO_TOKEN")
DEV_ID = 1322126091929915454  # Neo Discord ID
GUILD_ID = 1424877893514952776 # Ryo Staff Server ID

if RYOTOKEN is None:
    raise ValueError("Token not found. Check your .env file.")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# on_ready
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild)

    print("Commands synced!")
    
# /ping
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! {latency}ms")

# /sync (DEV ONLY)
@bot.tree.command(name="sync", description="Sync commands (DEV ONLY)")
async def sync(interaction: discord.Interaction):
    if interaction.user.id != DEV_ID:
        return await interaction.response.send_message(
            "❌ Not allowed", ephemeral=True
        )

    if interaction.guild is None:
        return await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )

    await interaction.response.defer()

    guild = discord.Object(id=interaction.guild.id)
    synced = await bot.tree.sync(guild=guild)

    await interaction.followup.send(
        f"⚡ Synced {len(synced)} commands to this server!"
    )

bot.run(RYOTOKEN)
