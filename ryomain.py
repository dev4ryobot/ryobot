import discord
from discord.ext import commands
from discord import app_commands
import os

RYOTOKEN = RYO_TOKEN")
DEV_ID = 1322126091929915454  # Neo Discord ID

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

# /ping
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! {latency}ms")

# /sync (DEV ONLY)
@bot.tree.command(name="sync", description="Sync slash commands (DEV ONLY)")
async def sync(interaction: discord.Interaction):
    if interaction.user.id != DEV_ID:
        return await interaction.response.send_message(
            "❌ You are not allowed to use this command.",
            ephemeral=True
        )

    await bot.tree.sync()
    await interaction.response.send_message("✅ Commands synced!")

bot.run(RYOTOKEN)