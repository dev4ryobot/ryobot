import discord
from discord.ext import commands
from discord import app_commands
import os
import random
import time
import asyncio
import uuid
import re
import csv
import io
import aiohttp
from PIL import Image, ImageOps, ImageDraw, ImageFont
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from dotenv import load_dotenv

load_dotenv("ryo.env")

# --- Config ---
DATABASE_URL = os.getenv("RYO_DATABASE_URL")
if DATABASE_URL and not (DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")):
    # If it doesn't look like a URL, it might be just a host or a weirdly formatted string
    # But we should be careful not to break valid DSNs (e.g. "host=... user=...")
    if "=" not in DATABASE_URL:
        print("⚠️ Warning: RYO_DATABASE_URL doesn't look like a valid connection string. It should start with 'postgresql://'")
RYOTOKEN = os.getenv("RYO_TOKEN")
DEV_ID = int(os.getenv("DEV_ID", "1322126091929915454"))
RELEASE_STAFF_ROLE_ID = 1468323651689517056
MOD_ROLE_ID = 1418408629312164042
GUILD_ID = int(os.getenv("GUILD_ID", "1424877893514952776"))
GUILD = discord.Object(id=GUILD_ID)

def is_staff(interaction: discord.Interaction):
    if interaction.user.id == DEV_ID:
        return True
    if hasattr(interaction.user, 'roles'):
        return any(r.id == RELEASE_STAFF_ROLE_ID for r in interaction.user.roles)
    return False

def is_mod(interaction: discord.Interaction):
    if interaction.user.id == DEV_ID:
        return True
    if hasattr(interaction.user, 'roles'):
        return any(r.id == MOD_ROLE_ID for r in interaction.user.roles)
    return False

# ROLES FOR COOLDOWNS
ROLE_COOLDOWNS = {
    1496889346572161176: 180,  # Patreon: 3m
    1430373706621780112: 300,  # Booster: 5m
    1418408632076337295: 420,  # Beta: 7m
    1427421586272686164: 600,  # Public: 10m
}

# Image processing constants
GRID_WIDTH = 4
GRID_HEIGHT = 2
CARD_DRAW_WIDTH = 380
CARD_DRAW_HEIGHT = 570
GRID_GAP = 20
PAGE_SIZE = GRID_WIDTH * GRID_HEIGHT

def get_user_cooldown(member: discord.Member):
    # Check roles from highest priority to lowest
    # Patreon > Booster > Beta > Public
    role_ids = [r.id for r in member.roles]
    
    # Priority order
    if 1496889346572161176 in role_ids: return 180
    if 1430373706621780112 in role_ids: return 300
    if 1418408632076337295 in role_ids: return 420
    return 600 # Default to public

# Ryo Theme Color
RYO_COLOR = 0xD9E6F9
RYO_BLUE = RYO_COLOR

# Emojis (Pulling IDs from env if possible)
PAINT_ID = os.getenv("PAINT_EMOJI_ID", "1496880430320849006")
GLUE_ID = os.getenv("GLUE_EMOJI_ID", "1496880484515446874")
PAINT_EMOJI = f"<:RB_paint_DNS:{PAINT_ID}>"
GLUE_EMOJI = f"<:RB_glue_DNS:{GLUE_ID}>"
# Rarity Emojis
RARITY_ID = os.getenv("RARITY_EMOJI_ID", "1496890868571963554")
RARITY_5_ID = os.getenv("RARITY_5_EMOJI_ID", RARITY_ID) # Default to same if not set
PUBLIC_ID = os.getenv("PUBLIC_EMOJI_ID", RARITY_ID)
BOOSTER_ID = os.getenv("BOOSTER_EMOJI_ID", RARITY_ID)
PATREON_ID = os.getenv("PATREON_EMOJI_ID", RARITY_ID)
LIMITED_ID = os.getenv("LIMITED_EMOJI_ID", RARITY_ID)

RARITY_EMOJI = f"<:RB_rarity_DNS:{RARITY_ID}>"
RARITY_5_EMOJI = f"<:RB_rarity5_DNS:{RARITY_5_ID}>"
PUBLIC_EMOJI = f"<:RB_public_DNS:{PUBLIC_ID}>"
BOOSTER_EMOJI = f"<:RB_booster_DNS:{BOOSTER_ID}>"
PATREON_EMOJI = f"<:RB_patreon_DNS:{PATREON_ID}>"
LIMITED_EMOJI = f"<:RB_limited_DNS:{LIMITED_ID}>"

RARITY_CUSTOM_CACHE = {"cards": {}, "eras": {}}

async def refresh_rarity_cache():
    global RARITY_CUSTOM_CACHE
    rows = await run_query("SELECT era, card_id, icon_url FROM ryo_rarity_custom", fetchall=True)
    new_cache = {"cards": {}, "eras": {}}
    if rows:
        for era, card_id, icon_url in rows:
            if card_id:
                new_cache["cards"][card_id.lower()] = icon_url
            elif era:
                new_cache["eras"][era.lower()] = icon_url
    RARITY_CUSTOM_CACHE = new_cache

def get_rarity_emoji_single(rarity, category="regular", era=None, card_id=None):
    """Returns a single emoji based on rarity and category, checking for custom overrides."""
    if rarity is None: rarity = 1
    cat = (category or "regular").lower()
    
    # Check custom overrides first
    if card_id and card_id.lower() in RARITY_CUSTOM_CACHE["cards"]:
        return RARITY_CUSTOM_CACHE["cards"][card_id.lower()]
    
    if era and era.lower() in RARITY_CUSTOM_CACHE["eras"]:
        return RARITY_CUSTOM_CACHE["eras"][era.lower()]

    emoji = RARITY_EMOJI
    if "public event" in cat:
        emoji = PUBLIC_EMOJI
    elif "booster event" in cat:
        emoji = BOOSTER_EMOJI
    elif "patreon event" in cat:
        emoji = PATREON_EMOJI
    elif "limited" in cat:
        emoji = LIMITED_EMOJI
    elif rarity >= 5:
        emoji = RARITY_5_EMOJI
    return emoji

def get_rarity_display(rarity, category="regular", era=None, card_id=None):
    """Returns a string of emojis based on rarity, category, era, and card_id."""
    try:
        r = int(rarity) if rarity is not None else 1
    except (ValueError, TypeError):
        r = 1
    # Standardize to use repeated stars/category emojis
    return get_rarity_emoji_single(r, category, era, card_id) * r

RYO_2_EMOJI = "<:2ryo:1497330599457722479>"

# Image for flipped cards (User to update this)
FLIPPED_CARD_URL = "https://raw.githubusercontent.com/dev4ryobot/ryobot/refs/heads/main/Untitled204.png"
MYSTERY_IMAGE_CACHE = None

# --- Database Setup ---
db_pool = None
if DATABASE_URL:
    try:
        db_pool = SimpleConnectionPool(1, 10, DATABASE_URL, sslmode="require")
    except Exception as e:
        print(f"⚠️ Failed to connect to Supabase: {e}")

async def check_claim_cooldown(user_id):
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='claim'", (user_id,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 300: # 5 mins
        return 300 - (now - last_used)
    return 0

async def update_claim_cooldown(user_id):
    now = int(time.time())
    await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'claim', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used = %s", (user_id, now, now))

async def run_query(query, params=None, fetchone=False, fetchall=False):
    if not db_pool:
        return None
    
    def _execute():
        conn = None
        try:
            conn = db_pool.getconn()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    if fetchone: return cur.fetchone()
                    if fetchall: return cur.fetchall()
                    return True
        except Exception as e:
            print(f"[DB ERROR] {e}")
            return None
        finally:
            if conn: db_pool.putconn(conn)
    
    return await asyncio.to_thread(_execute)

async def setup_database():
    if not db_pool: return
    
    queries = [
        "CREATE TABLE IF NOT EXISTS ryo_users (user_id TEXT PRIMARY KEY, paint INT DEFAULT 0, glue INT DEFAULT 0, guess_streak INT DEFAULT 0, last_guess_reward BIGINT DEFAULT 0);",
        "CREATE TABLE IF NOT EXISTS ryo_cards (card_id TEXT PRIMARY KEY, era TEXT, group_name TEXT, rarity INT, image_url TEXT, category TEXT DEFAULT 'regular', member_name TEXT);",
        "CREATE TABLE IF NOT EXISTS ryo_inventory (id SERIAL PRIMARY KEY, user_id TEXT, card_id TEXT, acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);",
        "CREATE TABLE IF NOT EXISTS ryo_cooldowns (user_id TEXT, command TEXT, last_used BIGINT, PRIMARY KEY (user_id, command));",
        "ALTER TABLE ryo_cards ADD COLUMN IF NOT EXISTS group_name TEXT;",
        "ALTER TABLE ryo_cards ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'regular';",
        "ALTER TABLE ryo_cards ADD COLUMN IF NOT EXISTS member_name TEXT;",
        "ALTER TABLE ryo_users ADD COLUMN IF NOT EXISTS guess_streak INT DEFAULT 0;",
        "ALTER TABLE ryo_users ADD COLUMN IF NOT EXISTS last_guess_reward BIGINT DEFAULT 0;",
        "CREATE TABLE IF NOT EXISTS ryo_events (category TEXT, era TEXT, PRIMARY KEY (category, era));",
        "CREATE TABLE IF NOT EXISTS ryo_rarity_custom (id SERIAL PRIMARY KEY, era TEXT, card_id TEXT, icon_url TEXT);",
        "UPDATE ryo_cards SET category = 'public event' WHERE category = 'event';"
    ]
    
    for q in queries:
        await run_query(q)
    
    await refresh_rarity_cache()
                
    print("✅ Ryo database tables verified.")

_card_cache = None
_last_cache_time = 0

async def get_available_cards():
    global _card_cache, _last_cache_time
    now = time.time()
    
    # Cache for 2 minutes
    if _card_cache and (now - _last_cache_time < 120):
        cards = _card_cache
    else:
        cards = await run_query("SELECT card_id, member_name, era, group_name, rarity, image_url, category FROM ryo_cards", fetchall=True)
        if cards:
            _card_cache = cards
            _last_cache_time = now
        else:
            return []
    
    # Fetch active events
    active_events_rows = await run_query("SELECT category, era FROM ryo_events", fetchall=True)
    active_events = set()
    for row in active_events_rows:
        active_events.add((row[0].lower(), row[1].lower()))

    available = []
    for c in cards:
        # card index: 0:id, 1:member, 2:era, 3:group, 4:rarity, 5:img, 6:cat
        c_era = (c[2] or "").lower()
        category = (c[6] or "regular").lower()

        # STRICT EXCLUSION: Guess minigame cards should NEVER appear in drops or daily pools
        if category == "guess_minigame" or c_era == "guess":
            continue

        # If not regular, check if it's part of an active event
        if category != "regular":
            if (category, c_era) not in active_events:
                continue
        available.append(c)
    return available

async def get_weighted_card():
    cards = await get_available_cards()
    if not cards: return None

    # Weighting Logic
    rarity_weights = {1: 100, 2: 50, 3: 20, 4: 10, 5: 5}
    category_mults = {
        "regular": 1.0, 
        "public event": 0.4, 
        "limited": 0.2,
        "booster event": 0.6,
        "patreon event": 0.2
    }

    weighted_pool = []
    pool_weights = []

    for c in cards:
        rarity = c[4] or 1
        category = (c[6] or "regular").lower()

        weight = rarity_weights.get(rarity, 5)
        weight *= category_mults.get(category, 1.0)
        
        weighted_pool.append(c)
        pool_weights.append(weight)
    
    return random.choices(weighted_pool, weights=pool_weights, k=1)[0]

# --- Bot Logic ---
class RyoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await setup_database()
        self.tree.add_command(guess_group)
        try:
            await self.tree.sync()
            print("✅ Slash commands synced globally.")
        except Exception as e:
            print(f"❌ Global sync failed: {e}")

    async def on_ready(self):
        print(f"✅ Ryo Bot logged in as {self.user}")

bot = RyoBot()

# --- Commands ---

@bot.tree.command(name="balance", description="Check your Paint and Glue balance")
async def balance(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    res = await run_query("SELECT paint, glue FROM ryo_users WHERE user_id=%s", (uid,), fetchone=True)
    if not res:
        # Create profile if not exists
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        res = (0, 0)
    
    embed = discord.Embed(description=f"{PAINT_EMOJI} {res[0]:,} paint\n{GLUE_EMOJI} {res[1]:,} glue", color=RYO_COLOR)
    embed.set_author(name=f"{interaction.user.name}'s balance", icon_url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="daily", description="Claim your daily rewards!")
async def daily(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    
    # Cooldown check (24 hours = 86400s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='daily'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 86400:
        rem = 86400 - (now - last_used)
        hours, remainder = divmod(rem, 3600)
        minutes, seconds = divmod(remainder, 60)
        return await interaction.response.send_message(f"⌛ Ryo is preparing your gifts! Try again in {int(hours)}h {int(minutes)}m.", ephemeral=True)

    await interaction.response.defer()
    
    try:
        # 1. Roll for a card using consistent weighting
        selected_card = await get_weighted_card()
        if not selected_card:
            return await interaction.followup.send("❌ No cards in registry. Use `/addcard` first!")
        
        # 2. Award currency and card
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        await run_query("UPDATE ryo_users SET paint = paint + 5000, glue = glue + 10 WHERE user_id = %s", (uid,))
        await run_query("INSERT INTO ryo_inventory (user_id, card_id) VALUES (%s, %s)", (uid, selected_card[0]))
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'daily', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))
        
        # Formatting metadata for embed
        cat_label = (selected_card[6] or "Regular").capitalize()

        # 3. Build Embed
        embed = discord.Embed(
            description=f"Opening your daily package you received:\n\n"
                        f"{PAINT_EMOJI} **5,000 paint**\n"
                        f"{GLUE_EMOJI} **10 glue**\n"
                        f"{get_rarity_display(selected_card[4], selected_card[6], selected_card[2], selected_card[0])} ({selected_card[0]}) {selected_card[3]} {selected_card[1]} {selected_card[2]}",
            color=RYO_COLOR
        )
        embed.set_author(name=f"{interaction.user.name}'s daily rewards", icon_url=interaction.user.display_avatar.url)
        embed.set_image(url=selected_card[5])
        footer_icon = "https://cdn.discordapp.com/emojis/1497330599457722479.png"
        embed.set_footer(text="Thank you for playing Ryo!", icon_url=footer_icon)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"❌ Daily error: {e}")
        await interaction.followup.send("⚠️ Failed to claim daily rewards. Please try again.")

try:
    LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    LANCZOS = Image.LANCZOS

def get_raw_url(url: str):
    """Automatically converts GitHub blob URLs to raw.githubusercontent.com URLs."""
    if not url: return url
    if "github.com" in url and "/blob/" in url:
        return url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    return url

async def generate_triple_card_image(cards):
    GAP = 30
    PADDING = 30 
    # Dimensions for the triple draw grid
    DRAW_CARD_W = 540
    DRAW_CARD_H = 810
    
    WIDTH = (DRAW_CARD_W * 3) + (GAP * 2) + (PADDING * 2)
    HEIGHT = DRAW_CARD_H + (PADDING * 2)
    
    # Use transparent background for a cleaner look
    canvas = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    
    async with aiohttp.ClientSession() as session:
        async def process_one(i, card):
            image_url = card[5]
            x_offset = PADDING + i * (DRAW_CARD_W + GAP)
            y_offset = PADDING
            
            if not image_url or not str(image_url).lower().startswith("http"):
                draw = ImageDraw.Draw(canvas)
                draw.rectangle([x_offset, y_offset, x_offset + DRAW_CARD_W, y_offset + DRAW_CARD_H], fill=(50, 50, 50))
                draw.text((x_offset + 30, y_offset + 120), "NO IMAGE URL", fill=(200, 200, 200))
                return

            target_url = get_raw_url(str(image_url).strip())
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
                async with session.get(target_url, timeout=12, headers=headers) as resp:
                    if resp.status == 200:
                        img_data = await resp.read()
                        with Image.open(io.BytesIO(img_data)) as card_img:
                            card_img = card_img.convert("RGBA")
                            # Use contain to preserve aspect ratio without cropping
                            # Add a small internal safety margin to ensure no edge clipping
                            safety_margin = 16
                            size = (DRAW_CARD_W - safety_margin, DRAW_CARD_H - safety_margin)
                            try:
                                card_img = ImageOps.contain(card_img, size, LANCZOS)
                            except AttributeError:
                                # Fallback for older Pillow < 9.2.0
                                card_img.thumbnail(size, LANCZOS)
                            
                            # Center in the slot
                            ix, iy = card_img.size
                            px = x_offset + (DRAW_CARD_W - ix) // 2
                            py = y_offset + (DRAW_CARD_H - iy) // 2
                            
                            canvas.paste(card_img, (px, py), card_img)
                    else:
                        draw = ImageDraw.Draw(canvas)
                        draw.rectangle([x_offset, y_offset, x_offset + DRAW_CARD_W, y_offset + DRAW_CARD_H], fill=(50, 50, 50))
                        draw.text((x_offset + 50, y_offset + 150), f"FETCH ERROR\n{resp.status}", fill=(255, 100, 100))
            except Exception as e:
                draw = ImageDraw.Draw(canvas)
                draw.rectangle([x_offset, y_offset, x_offset + DRAW_CARD_W, y_offset + DRAW_CARD_H], fill=(50, 50, 50))
                draw.text((x_offset + 50, y_offset + 150), "FETCH ERROR", fill=(255, 100, 100))

        tasks = [process_one(i, card) for i, card in enumerate(cards[:3])]
        await asyncio.gather(*tasks)
    
    return canvas

@bot.tree.command(name="weekly", description="Claim your weekly rewards!")
async def weekly(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    
    # Cooldown check (7 days = 604800s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='weekly'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 604800:
        rem = 604800 - (now - last_used)
        days, remainder = divmod(rem, 86400)
        hours, remainder = divmod(remainder, 3600)
        return await interaction.response.send_message(f"⌛ Ryo is saving up your weekly stash! Try again in {int(days)}d {int(hours)}h.", ephemeral=True)

    await interaction.response.defer()
    
    try:
        # 1. Roll for 3 cards
        selected_cards = []
        for _ in range(3):
            card = await get_weighted_card()
            if card:
                selected_cards.append(card)
        
        if not selected_cards:
            return await interaction.followup.send("❌ No cards in registry. Use `/addcard` first!")
        
        # 2. Award currency and cards
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        await run_query("UPDATE ryo_users SET paint = paint + 15000, glue = glue + 30 WHERE user_id = %s", (uid,))
        
        card_details_text = ""
        for card in selected_cards:
            await run_query("INSERT INTO ryo_inventory (user_id, card_id) VALUES (%s, %s)", (uid, card[0]))
            
            card_details_text += f"• **{card[1]}** ({card[2]}) - {get_rarity_display(card[4], card[6], card[2], card[0])}\n"

        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'weekly', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))
        
        # 3. Build Embed
        card_list = "\n".join([f"{get_rarity_display(c[4], c[6], c[2], c[0])} ({c[0]}) {c[3]} {c[1]} {c[2]}" for c in selected_cards])
        
        embed = discord.Embed(
            description=f"Opening your weekly package you received:\n\n"
                        f"{PAINT_EMOJI} **15,000 paint**\n"
                        f"{GLUE_EMOJI} **30 glue**\n\n"
                        f"{card_list}",
            color=RYO_COLOR
        )
        embed.set_author(name=f"{interaction.user.name}'s weekly rewards", icon_url=interaction.user.display_avatar.url)
        
        # Display composite image for all 3 cards
        composite = await generate_triple_card_image(selected_cards)
        arr = io.BytesIO()
        composite.save(arr, format='PNG')
        arr.seek(0)
        file = discord.File(arr, filename="weekly_cards.png")
        embed.set_image(url="attachment://weekly_cards.png")
        
        footer_icon = "https://cdn.discordapp.com/emojis/1497330599457722479.png"
        embed.set_footer(text="Thank you for playing Ryo!", icon_url=footer_icon)
        
        await interaction.followup.send(embed=embed, file=file)
        
    except Exception as e:
        print(f"❌ Weekly error: {e}")
        await interaction.followup.send("⚠️ Failed to claim weekly rewards. Please try again.")

@bot.tree.command(name="sticky", description="Scrape up some extra Glue!")
async def sticky(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    
    # Cooldown check (1 hour = 3600s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='sticky'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 3600:
        rem = 3600 - (now - last_used)
        return await interaction.response.send_message(f"⌛ Ryo is still cleaning up! Try again in {rem//60}m {rem%60}s.", ephemeral=True)

    await interaction.response.defer()
    
    try:
        reward = random.randint(5, 10)
        
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        await run_query("UPDATE ryo_users SET glue = glue + %s WHERE user_id = %s", (reward, uid))
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'sticky', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))
        
        embed = discord.Embed(
            description=f"You found some extra resources!\n{GLUE_EMOJI} **{reward:,} glue**",
            color=RYO_COLOR
        )
        embed.set_author(name=f"{interaction.user.name}'s sticky rewards", icon_url=interaction.user.display_avatar.url)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"❌ Sticky error: {e}")
        await interaction.followup.send("⚠️ Failed to claim sticky rewards. Please try again.")

@bot.tree.command(name="color", description="Find some splash of color!")
async def color(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    
    # Cooldown check (30 minutes = 1800s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='color'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 1800:
        rem = 1800 - (now - last_used)
        return await interaction.response.send_message(f"⌛ Ryo is still mixing the paints! Try again in {rem//60}m {rem%60}s.", ephemeral=True)

    await interaction.response.defer()
    
    try:
        reward = random.randint(500, 1000)
        
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        await run_query("UPDATE ryo_users SET paint = paint + %s WHERE user_id = %s", (reward, uid))
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'color', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))
        
        embed = discord.Embed(
            description=f"You found a stash of paint!\n{PAINT_EMOJI} **{reward:,} paint**",
            color=RYO_COLOR
        )
        embed.set_author(name=f"{interaction.user.name}'s color rewards", icon_url=interaction.user.display_avatar.url)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"❌ Color error: {e}")
        await interaction.followup.send("⚠️ Failed to claim color rewards. Please try again.")

class PaintRevealView(discord.ui.View):
    def __init__(self, user_id, owner_name, card1, card2):
        super().__init__(timeout=15)
        self.user_id = user_id
        self.owner_name = owner_name
        self.cards = [card1, card2]
        self.owner_claimed = False
        self.claimed_slots = [False, False]
        self.copies = [None, None]
        self.users_who_claimed = set()
        self.message = None

        # Button 1
        emoji1 = get_rarity_emoji_single(card1[4], card1[6], card1[2], card1[0])
        btn1 = discord.ui.Button(label=f"{card1[4]} • {card1[3] or 'N/A'}", emoji=emoji1, style=discord.ButtonStyle.secondary, custom_id="reveal_0")
        btn1.callback = self.reveal_callback
        self.add_item(btn1)

        # Button 2
        emoji2 = get_rarity_emoji_single(card2[4], card2[6], card2[2], card2[0])
        btn2 = discord.ui.Button(label=f"{card2[4]} • {card2[3] or 'N/A'}", emoji=emoji2, style=discord.ButtonStyle.secondary, custom_id="reveal_1")
        btn2.callback = self.reveal_callback
        self.add_item(btn2)

    async def reveal_callback(self, interaction: discord.Interaction):
        try:
            uid = str(interaction.user.id)
            
            # No cooldown check for /paint, but ensure only the owner can claim
            if uid != self.user_id:
                return await interaction.response.send_message("❌ This is not your drop!", ephemeral=True)
            
            if self.owner_claimed:
                return await interaction.response.send_message("❌ You already claimed a card!", ephemeral=True)

            # Check if this specific slot is already claimed
            custom_id = interaction.data['custom_id']
            idx = int(custom_id.split("_")[1])
            if self.claimed_slots[idx]:
                return await interaction.response.send_message("❌ This card has already been claimed!", ephemeral=True)

            # Defer update
            await interaction.response.defer()
            
            if uid == self.user_id:
                self.owner_claimed = True
            
            self.users_who_claimed.add(uid)
            self.claimed_slots[idx] = True
            card = self.cards[idx]
            
            # Save to database
            await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
            await run_query("INSERT INTO ryo_inventory (user_id, card_id) VALUES (%s, %s)", (uid, card[0]))
            
            # Fetch inventory count
            count_res = await run_query("SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s AND card_id = %s", (uid, card[0]), fetchone=True)
            self.copies[idx] = count_res[0] if count_res else 1
            
            # NO update_claim_cooldown(uid) for /paint as requested
            
            # Update button style
            for child in self.children:
                if child.custom_id == custom_id:
                    child.disabled = True
                    child.style = discord.ButtonStyle.success

            # Build results embed matching screenshot style
            results_title = f"🖼️ {self.owner_name} | pick results"
            description_lines = []
            
            # Show only the claimed card
            card = self.cards[idx]
            try:
                r = int(card[4]) if card[4] is not None else 1
            except (ValueError, TypeError):
                match = re.search(r'(\d+)', str(card[4]))
                r = int(match.group(1)) if match else 1
            
            stars = get_rarity_display(r, card[6], card[2], card[0])
            m = card[1] or "N/A"
            cid = card[0] or "N/A"
            era = card[2] or "N/A"
            copies_str = str(self.copies[idx])
            
            description_lines.append(f"{stars} **{m}**")
            description_lines.append(f"{cid} {era}")
            description_lines.append(f"Copies : {copies_str}")
            
            res_embed = discord.Embed(
                title=results_title,
                description="\n".join(description_lines),
                color=RYO_COLOR
            )
            res_embed.set_image(url=card[5])
            
            # If any slot claimed, we stop the view since owner can only pick one
            self.stop()

            # For /paint, the user wants the original mystery cards embed to STAY.
            # So we edit the original to disable buttons, and send a NEW followup for result.
            await interaction.edit_original_response(view=self)
            await interaction.followup.send(embed=res_embed)
                
        except Exception as e:
            print(f"Reveal error: {e}")
            try:
                await interaction.followup.send(f"❌ An error occurred revealing the card: {e}", ephemeral=True)
            except:
                pass

    async def on_timeout(self):
        if all(self.claimed_slots):
            return
        # Disable all buttons on timeout
        for child in self.children:
            child.disabled = True
        
        if self.message:
            try:
                await self.message.edit(view=self)
            except:
                pass

class DrawChoiceView(discord.ui.View):
    def __init__(self, user_id, owner_name, cards):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.owner_name = owner_name
        self.cards = cards
        self.owner_claimed = False
        self.claimed_slots = [False] * len(cards)
        self.claimers = [None] * len(cards)
        self.copies = [None] * len(cards)
        self.users_who_claimed = set()
        self.message = None

        for i, card in enumerate(cards):
            # card: 0:id, 1:member, 2:era, 3:group, 4:rarity, 5:img, 6:cat
            rarity = card[4] or 1
            member = card[1] or "N/A"
            if len(member) > 70: member = member[:67] + "..."
            category = card[6] or "regular"
            emoji = get_rarity_emoji_single(rarity, category, card[2], card[0])
            
            btn = discord.ui.Button(
                label=f"{rarity} • {member}", 
                emoji=emoji,
                style=discord.ButtonStyle.secondary, 
                custom_id=f"drawpick_{i}"
            )
            btn.callback = self.pick_callback
            self.add_item(btn)

    async def pick_callback(self, interaction: discord.Interaction):
        try:
            uid = str(interaction.user.id)
            
            # 1. Check if owner has claimed
            if not self.owner_claimed and uid != self.user_id:
                return await interaction.response.send_message("❌ Wait for the owner to pick first!", ephemeral=True)
            
            # 2. Check if user already claimed from THIS drop
            if uid in self.users_who_claimed:
                return await interaction.response.send_message("❌ You already claimed a card from this drop!", ephemeral=True)

            # 3. Check if this specific slot is already claimed
            custom_id = interaction.data['custom_id']
            idx = int(custom_id.split("_")[1])
            if self.claimed_slots[idx]:
                return await interaction.response.send_message("❌ This card has already been claimed!", ephemeral=True)

            # 4. Check cooldown
            cd_rem = await check_claim_cooldown(uid)
            if cd_rem > 0:
                return await interaction.response.send_message(f"⌛ Your hands are full! Take a break for {cd_rem//60}m {cd_rem%60}s.", ephemeral=True)

            # Defer update
            await interaction.response.defer()
            
            if uid == self.user_id:
                self.owner_claimed = True
            
            self.users_who_claimed.add(uid)
            self.claimed_slots[idx] = True
            self.claimers[idx] = interaction.user.mention
            card = self.cards[idx]
            
            # Save to database
            await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
            await run_query("INSERT INTO ryo_inventory (user_id, card_id) VALUES (%s, %s)", (uid, card[0]))
            
            # Fetch inventory count
            count_res = await run_query("SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s AND card_id = %s", (uid, card[0]), fetchone=True)
            self.copies[idx] = count_res[0] if count_res else 1
            
            await update_claim_cooldown(uid)
            
            # If owner, give paint reward (logic from original code)
            if uid == self.user_id:
                await run_query("UPDATE ryo_users SET paint = paint + 50 WHERE user_id=%s", (uid,))
            
            # Record that this slot is disabled
            for item in self.children:
                if item.custom_id == custom_id:
                    item.disabled = True
                    item.style = discord.ButtonStyle.success

            # Build results embed matching screenshot
            results_title = f"🖼️ {self.owner_name} | draw results"
            description_lines = []
            
            for i, c in enumerate(self.cards):
                # c: 0:id, 1:member, 2:era, 3:group, 4:rarity, 5:img, 6:cat
                try:
                    r = int(c[4]) if c[4] is not None else 1
                except (ValueError, TypeError):
                    match = re.search(r'(\d+)', str(c[4]))
                    r = int(match.group(1)) if match else 1
                
                stars = get_rarity_display(r, c[6], c[2], c[0])
                m = c[1] or "N/A"
                cid = c[0] or "N/A"
                era = c[2] or "N/A"
                
                description_lines.append(f"{stars} **{m}**")
                description_lines.append(f"{cid} {era}")
                
                if self.claimers[i]:
                    description_lines.append(f"Claimed by : {self.claimers[i]}")
                    description_lines.append(f"Copies : {self.copies[i]}")
                else:
                    description_lines.append("Unclaimed")
                
                description_lines.append("") # Spacer
            
            res_embed = discord.Embed(
                title=results_title,
                description="\n".join(description_lines),
                color=RYO_COLOR
            )
            
            # If all slots claimed, stop
            if all(self.claimed_slots):
                self.stop()

            # Update the message with the status (buttons) and send a followup for result
            await interaction.edit_original_response(view=self)
            await interaction.followup.send(embed=res_embed)
                
        except Exception as e:
            print(f"Pick error: {e}")
            try:
                await interaction.followup.send(f"❌ An error occurred picking the card: {e}", ephemeral=True)
            except:
                pass

    async def on_timeout(self):
        if all(self.claimed_slots):
            return
        for child in self.children:
            child.disabled = True
        if self.message:
            try: await self.message.edit(view=self)
            except: pass

@bot.tree.command(name="draw", description="Drop 3 random cards and pick ONE")
async def draw_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    
    # Calculate Cooldown based on roles
    cd_seconds = get_user_cooldown(interaction.user)
    
    # Cooldown check
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='draw'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    
    if now - last_used < cd_seconds:
        rem = cd_seconds - (now - last_used)
        return await interaction.response.send_message(f"⌛ Ryo is dealing your next hand! Try again in {rem//60}m {rem%60}s.", ephemeral=True)

    await interaction.response.defer()
    
    try:
        # Get 3 random cards
        selected = []
        for _ in range(3):
            card = await get_weighted_card()
            if card: selected.append(card)
        
        if not selected:
            return await interaction.followup.send("❌ No cards in database.")
        
        # Set cooldown
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'draw', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))

        embed = discord.Embed(
            description=f"{interaction.user.name} has drawn 3 cards:",
            color=RYO_COLOR
        )
        
        # Add footer with emoji icon
        footer_icon = "https://cdn.discordapp.com/emojis/1497330599457722479.png"
        embed.set_footer(text="You have 60s to claim a card!", icon_url=footer_icon)
        
        # Display composite image for all 3 cards
        composite = await generate_triple_card_image(selected)
        arr = io.BytesIO()
        composite.save(arr, format='PNG')
        arr.seek(0)
        file = discord.File(arr, filename="draw_cards.png")
        embed.set_image(url="attachment://draw_cards.png")
        
        view = DrawChoiceView(uid, interaction.user.display_name, selected)
        await interaction.followup.send(embed=embed, view=view, file=file)
        
    except Exception as e:
        print(f"❌ Draw error: {e}")
        await interaction.followup.send("⚠️ Failed to perform draw.")

async def generate_single_mystery_image():
    global MYSTERY_IMAGE_CACHE
    if MYSTERY_IMAGE_CACHE:
        return MYSTERY_IMAGE_CACHE.copy()
        
    try:
        async with aiohttp.ClientSession() as session:
            # Use headers to avoid being blocked
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
            async with session.get(FLIPPED_CARD_URL, timeout=10, headers=headers) as resp:
                if resp.status == 200:
                    img_data = await resp.read()
                    with Image.open(io.BytesIO(img_data)) as card_img:
                        MYSTERY_IMAGE_CACHE = card_img.convert("RGBA")
                        return MYSTERY_IMAGE_CACHE.copy()
                else:
                    print(f"Failed to fetch flipped card image: {resp.status}")
    except Exception as e:
        print(f"Error in generate_single_mystery_image: {e}")
    
    # Fallback canvas if fetch fails
    canvas = Image.new('RGB', (CARD_DRAW_WIDTH, CARD_DRAW_HEIGHT), (35, 35, 35))
    draw = ImageDraw.Draw(canvas)
    draw.text((CARD_DRAW_WIDTH//4, CARD_DRAW_HEIGHT//2), "MYSTERY", fill=(100, 100, 100))
    return canvas

@bot.tree.command(name="paint", description="Drop 2 mystery cards (Rarity 3-5 only!)")
async def paint_drop(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    
    # Cooldown check (20 minutes = 1200s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='paint'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 1200:
        rem = 1200 - (now - last_used)
        return await interaction.response.send_message(f"⌛ Ryo is drying the mystery cards! Try again in {rem//60}m {rem%60}s.", ephemeral=True)

    await interaction.response.defer()
    
    try:
        # Get cards rarity >= 3 from ALL cards (including events, excluding minigame)
        cards = await run_query("""
            SELECT card_id, member_name, era, group_name, rarity, image_url, category 
            FROM ryo_cards 
            WHERE rarity >= 3 
            AND (LOWER(category) != 'guess_minigame' AND LOWER(era) != 'guess')
        """, fetchall=True)
        
        if len(cards) < 2:
            return await interaction.followup.send("❌ Not enough cards in the registry to perform a Paint drop.")
        
        selected = random.sample(cards, 2)
        
        # Set cooldown
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'paint', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))

        embed = discord.Embed(
            title=f"🎨 Mystery Paint Drop",
            description="Two secret cards have appeared!\nClick the buttons below to reveal and claim them.",
            color=RYO_COLOR
        )
        
        # Display single mystery image
        composite = await generate_single_mystery_image()
        arr = io.BytesIO()
        composite.save(arr, format='PNG')
        arr.seek(0)
        file = discord.File(arr, filename="mystery_cards.png")
        embed.set_image(url="attachment://mystery_cards.png")
        
        # Add footer with emoji icon
        footer_icon = "https://cdn.discordapp.com/emojis/1497330599457722479.png"
        embed.set_footer(text="You have 15s to claim!", icon_url=footer_icon)
        
        view = PaintRevealView(uid, interaction.user.display_name, selected[0], selected[1])
        msg = await interaction.followup.send(embed=embed, view=view, file=file)
        view.message = msg

        
    except Exception as e:
        print(f"❌ Paint drop error: {e}")
        await interaction.followup.send("⚠️ Failed to start paint drop.")

@bot.tree.command(name="addcard", description="Add a new Ryo card to the registry")
@app_commands.describe(
    card_id="Unique ID for the card", 
    member="Idol's name",
    era="Era/Theme of the card", 
    group="Group name (NCT Wish, etc)", 
    category="Card Category (Regular, Public Event, Limited, Booster Event, Patreon Event)",
    rarity="Rarity (1-5, ignored if Public Event/Limited)", 
    image_file="Upload card image from device (optional if URL provided)",
    image_url="URL to the card image (optional if file provided)"
)
@app_commands.choices(category=[
    app_commands.Choice(name="Regular", value="regular"),
    app_commands.Choice(name="Public Event", value="public event"),
    app_commands.Choice(name="Limited", value="limited"),
    app_commands.Choice(name="Booster Event", value="booster event"),
    app_commands.Choice(name="Patreon Event", value="patreon event"),
])
async def addcard(
    interaction: discord.Interaction, 
    card_id: str, 
    member: str, 
    era: str, 
    group: str, 
    category: str, 
    image_file: discord.Attachment = None,
    image_url: str = None, 
    rarity: int = 1
):
    await addcard_logic(interaction, card_id, member, era, group, category, image_url, image_file, rarity)

@bot.tree.command(name="addrarity", description="Set a custom rarity icon for an era or a specific card (STAFF ONLY)")
@app_commands.describe(era="Apply to all cards in this era", card_id="Apply ONLY to this card ID", icon_url="The emoji string or URL for the rarity icon")
async def addrarity(interaction: discord.Interaction, icon_url: str, era: str = None, card_id: str = None):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ This command is restricted to Ryo Staff.", ephemeral=True)
    
    if not era and not card_id:
        return await interaction.response.send_message("❌ You must provide either an **Era** or a **Card ID**.", ephemeral=True)
    
    await interaction.response.defer()
    
    try:
        if card_id:
            # Delete old if exists for this card
            await run_query("DELETE FROM ryo_rarity_custom WHERE card_id = %s", (card_id.lower(),))
            await run_query("INSERT INTO ryo_rarity_custom (card_id, icon_url) VALUES (%s, %s)", (card_id.lower(), icon_url))
            msg = f"✅ Custom rarity set for card: `{card_id}`"
        else:
            # Delete old if exists for this era
            await run_query("DELETE FROM ryo_rarity_custom WHERE era = %s", (era.lower(),))
            await run_query("INSERT INTO ryo_rarity_custom (era, icon_url) VALUES (%s, %s)", (era.lower(), icon_url))
            msg = f"✅ Custom rarity set for era: `{era}`"
            
        await refresh_rarity_cache()
        await interaction.followup.send(f"{msg}\nIcon: {icon_url}")
    except Exception as e:
        await interaction.followup.send(f"❌ Error setting rarity: {e}")

@bot.tree.command(name="deletecard", description="Delete a card from the registry (STAFF ONLY)")
@app_commands.describe(card_id="The unique ID of the card to delete")
async def deletecard(interaction: discord.Interaction, card_id: str):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    # Check if card exists
    card = await run_query("SELECT member_name, era FROM ryo_cards WHERE card_id = %s", (card_id,), fetchone=True)
    if not card:
        return await interaction.followup.send(f"❌ Card with ID `{card_id}` not found.")
    
    # Delete card from registry and all inventories
    try:
        await run_query("DELETE FROM ryo_inventory WHERE card_id = %s", (card_id,))
        success = await run_query("DELETE FROM ryo_cards WHERE card_id = %s", (card_id,))
        
        if success:
            await interaction.followup.send(f"✅ Card `{card_id}` ({card[0]} - {card[1]}) has been deleted from the registry and all inventories.")
        else:
            await interaction.followup.send("❌ Failed to delete card from registry. Check logs.")
    except Exception as e:
        print(f"Error in deletecard: {e}")
        await interaction.followup.send(f"❌ An error occurred while deleting the card: {e}")

@bot.tree.command(name="editcard", description="Edit an existing card in the registry (STAFF ONLY)")
@app_commands.describe(
    card_id="The unique ID of the card to edit",
    member="Change Idol Name",
    era="Change Era",
    group="Change Group Name",
    category="Change Main Category (Regular, Public Event, Limited, Booster Event, Patreon Event)",
    new_card_id="Change the Unique ID of the card",
    image_url="Change Image URL",
    image_file="Upload new Image File",
    rarity="Change Rarity (1-6)"
)
@app_commands.choices(category=[
    app_commands.Choice(name="Regular", value="regular"),
    app_commands.Choice(name="Public Event", value="public event"),
    app_commands.Choice(name="Limited", value="limited"),
    app_commands.Choice(name="Booster Event", value="booster event"),
    app_commands.Choice(name="Patreon Event", value="patreon event"),
])
async def editcard(
    interaction: discord.Interaction,
    card_id: str,
    member: str = None,
    era: str = None,
    group: str = None,
    category: app_commands.Choice[str] = None,
    new_card_id: str = None,
    image_url: str = None,
    image_file: discord.Attachment = None,
    rarity: int = None
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    # Check if card exists
    card = await run_query("SELECT card_id FROM ryo_cards WHERE card_id = %s", (card_id,), fetchone=True)
    if not card:
        return await interaction.followup.send(f"❌ Card with ID `{card_id}` not found.")

    updates = []
    params = []

    if member:
        updates.append("member_name = %s")
        params.append(member)
    if era:
        updates.append("era = %s")
        params.append(era)
    if group:
        updates.append("group_name = %s")
        params.append(group)
    if category:
        updates.append("category = %s")
        params.append(category.value)
    if new_card_id:
        # Check if new ID already exists
        exists = await run_query("SELECT card_id FROM ryo_cards WHERE card_id = %s", (new_card_id,), fetchone=True)
        if exists:
            return await interaction.followup.send(f"❌ New ID `{new_card_id}` already exists!")
        updates.append("card_id = %s")
        params.append(new_card_id)
    
    # Handle image update
    final_image_url = image_url
    if image_file:
        final_image_url = image_file.url
    
    if final_image_url:
        updates.append("image_url = %s")
        params.append(final_image_url)
        
    if rarity is not None:
        updates.append("rarity = %s")
        params.append(rarity)

    if not updates:
        return await interaction.followup.send("⚠️ No changes provided.")

    query = f"UPDATE ryo_cards SET {', '.join(updates)} WHERE card_id = %s"
    params.append(card_id)

    try:
        # If we are changing the card_id, we need to update it in ryo_inventory first if there's no FK constraint with ON UPDATE CASCADE
        # Our table creation didn't specify ON UPDATE CASCADE, so we handle it manually
        if new_card_id:
            await run_query("UPDATE ryo_inventory SET card_id = %s WHERE card_id = %s", (new_card_id, card_id))
            
        success = await run_query(query, tuple(params))
        if success:
            display_id = new_card_id if new_card_id else card_id
            await interaction.followup.send(f"✅ Card `{card_id}` has been updated successfully (New ID: `{display_id}`).")
        else:
            # If update failed and we changed inventory, this might be messy but usually it's due to some other constraint
            await interaction.followup.send("❌ Failed to update card. Check logs.")
    except Exception as e:
        print(f"Error in editcard: {e}")
        await interaction.followup.send(f"❌ An error occurred: {e}")

@bot.tree.command(name="addgti", description="Add a card for the Guess The Idol minigame (STAFF ONLY)")
@app_commands.describe(name="Idol Name", image_file="File", image_url="URL")
async def addgti(
    interaction: discord.Interaction, 
    name: str, 
    image_file: discord.Attachment = None,
    image_url: str = None
):
    card_id = f"GTB_{uuid.uuid4().hex[:8]}"
    await addcard_logic(interaction, card_id, name, "Guess", "Solo", "guess_minigame", image_url, image_file, 1)

@bot.tree.command(name="addgtg", description="Add a card for the Guess The Group minigame (STAFF ONLY)")
@app_commands.describe(group="Group Name", image_file="File", image_url="URL")
async def addgtg(
    interaction: discord.Interaction, 
    group: str, 
    image_file: discord.Attachment = None,
    image_url: str = None
):
    card_id = f"GTG_{uuid.uuid4().hex[:8]}"
    await addcard_logic(interaction, card_id, "Group", "Guess", group, "guess_minigame", image_url, image_file, 1)

async def addcard_logic(
    interaction: discord.Interaction, 
    card_id: str, 
    member: str, 
    era: str, 
    group: str, 
    category: str, 
    image_url: str = None, 
    image_file: discord.Attachment = None,
    rarity: int = 1
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    final_image_url = get_raw_url(image_url) if image_url else None
    if image_file:
        final_image_url = image_file.url
    
    if not final_image_url:
        return await interaction.followup.send("❌ You must provide either an `image_url` or an `image_file`!")

    # Enforce rarity 5 for Public Event and Limited
    final_rarity = rarity
    if category in ["public event", "limited"]:
        final_rarity = 5

    success = await run_query(
        "INSERT INTO ryo_cards (card_id, era, group_name, rarity, image_url, category, member_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (card_id) DO UPDATE SET "
        "era=EXCLUDED.era, group_name=EXCLUDED.group_name, rarity=EXCLUDED.rarity, "
        "image_url=EXCLUDED.image_url, category=EXCLUDED.category, member_name=EXCLUDED.member_name", 
        (card_id, era, group, final_rarity, final_image_url, category, member)
    )
    
    if success:
        status_msg = f"✅ Card for `{member}` added to the **{era.upper() if era == 'Guess' else category.upper()}** pool."
        await interaction.followup.send(status_msg)
    else:
        await interaction.followup.send("❌ Failed to add card. Check logs.")

class AddGuessModal(discord.ui.Modal):
    name_input = discord.ui.TextInput(label="Name (Idol or Group)", placeholder="e.g. Joshua or Seventeen", required=True)
    url_input = discord.ui.TextInput(label="Image URL", placeholder="https://...", required=True)

    def __init__(self, mode):
        super().__init__(title=f"Add Guess {mode}")
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        card_id = f"GT{self.mode[0]}_{uuid.uuid4().hex[:8]}"
        name = self.name_input.value
        url = self.url_input.value
        
        # Reuse addcard_logic
        if self.mode == "Idol":
            await addcard_logic(interaction, card_id, name, "Guess", "Solo", "guess_minigame", url, None, None, "none", 1)
        else:
            await addcard_logic(interaction, card_id, "Group", "Guess", name, "guess_minigame", url, None, None, "none", 1)

class RemoveGuessModal(discord.ui.Modal):
    name_input = discord.ui.TextInput(label="Name to Remove", placeholder="Case-sensitive name", required=True)

    def __init__(self, mode):
        super().__init__(title=f"Remove Guess {mode}")
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        name = self.name_input.value
        
        if self.mode == "Idol":
            query = "DELETE FROM ryo_cards WHERE era = 'Guess' AND group_name = 'Solo' AND member_name = %s"
        else:
            query = "DELETE FROM ryo_cards WHERE era = 'Guess' AND member_name = 'Group' AND group_name = %s"
        
        await run_query(query, (name,))
        await interaction.followup.send(f"🗑️ Attempted to remove `{name}` from Guess {self.mode} database.")

class StaffGuessView(discord.ui.View):
    def __init__(self, mode):
        super().__init__(timeout=None)
        self.mode = mode

    @discord.ui.button(label="➕ Add New", style=discord.ButtonStyle.success)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddGuessModal(self.mode))

    @discord.ui.button(label="🗑️ Remove Entry", style=discord.ButtonStyle.danger)
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RemoveGuessModal(self.mode))

@bot.tree.command(name="staffgti", description="Manage Guess The Idol cards (STAFF ONLY)")
async def staffgti(interaction: discord.Interaction):
    cards = await run_query("SELECT member_name, image_url FROM ryo_cards WHERE era = 'Guess' AND group_name = 'Solo' ORDER BY member_name ASC", fetchall=True)
    
    if not cards:
        embed = discord.Embed(title="🕵️ Staff: Guess The Idol", description="No GTI cards found.", color=RYO_COLOR)
    else:
        desc = "Current Idol Questions:\n"
        for c in cards:
            desc += f"• `{c[0]}` - [[Image]]({c[1]})\n"
        desc += "\n💡 **Note:** To add a card using an image from your device, please use the `/addgti` command directly."
        embed = discord.Embed(title="🕵️ Staff: Guess The Idol", description=desc[:4000], color=RYO_COLOR)
    
    await interaction.response.send_message(embed=embed, view=StaffGuessView("Idol"), ephemeral=True)

@bot.tree.command(name="staffgtg", description="Manage Guess The Group cards (STAFF ONLY)")
async def staffgtg(interaction: discord.Interaction):
    cards = await run_query("SELECT group_name, image_url FROM ryo_cards WHERE era = 'Guess' AND member_name = 'Group' ORDER BY group_name ASC", fetchall=True)
    
    if not cards:
        embed = discord.Embed(title="🕵️ Staff: Guess The Group", description="No GTG cards found. Use `/addgtg` to add some!", color=RYO_COLOR)
    else:
        desc = "Current Group Questions:\n"
        for g in cards:
            desc += f"• `{g[0]}` - [[Image]]({g[1]})\n"
        desc += "\n💡 **Note:** To add a card using an image from your device, please use the `/addgtg` command directly."
        embed = discord.Embed(title="🕵️ Staff: Guess The Group", description=desc[:4000], color=RYO_COLOR)
    
    await interaction.response.send_message(embed=embed, view=StaffGuessView("Group"), ephemeral=True)

class GuessView(discord.ui.View):
    def __init__(self, user_id, correct_answer, mode, options, image_url):
        super().__init__(timeout=20)
        self.user_id = user_id
        self.correct_answer = correct_answer
        self.mode = mode
        self.image_url = image_url
        self.message = None

        for option in options:
            btn = discord.ui.Button(label=option, style=discord.ButtonStyle.secondary)
            btn.callback = self.make_callback(option)
            self.add_item(btn)

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            
            embed = discord.Embed(
                title="⏰ Time's Up!", 
                description=f"The correct {self.mode} was **{self.correct_answer}**.\nTry to be faster next time!", 
                color=discord.Color.red()
            )
            embed.set_image(url=self.image_url)
            try:
                await self.message.edit(embed=embed, view=None)
            except:
                pass

    def make_callback(self, choice):
        async def callback(interaction: discord.Interaction):
            if str(interaction.user.id) != self.user_id:
                return await interaction.response.send_message("❌ This is not your game!", ephemeral=True)
            
            await interaction.response.defer()
            self.stop()
            actual = self.correct_answer.strip().lower()
            selected = choice.strip().lower()
            now = int(time.time())
            uid = str(interaction.user.id)

            # Ensure user exists
            await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
            user_data = await run_query("SELECT guess_streak, last_guess_reward FROM ryo_users WHERE user_id=%s", (uid,), fetchone=True)
            streak, last_reward_time = user_data if user_data else (0, 0)

            if selected == actual:
                new_streak = streak + 1
                reward_msg = ""
                
                # Shared Cooldown Logic (3 mins = 180s)
                if now - last_reward_time >= 180:
                    reward_paint = 500
                    reward_glue = 0
                    if new_streak > 0 and new_streak % 10 == 0:
                        reward_glue = 1
                    
                    await run_query("UPDATE ryo_users SET paint = paint + %s, glue = glue + %s, last_guess_reward = %s, guess_streak = %s WHERE user_id = %s", (reward_paint, reward_glue, now, new_streak, uid))
                    reward_msg = f"\n💰 **Rewards:** +500 Paint {PAINT_EMOJI}"
                    if reward_glue > 0:
                        reward_msg += f", +1 Glue {GLUE_EMOJI} (10 Streak Bonus!)"
                else:
                    await run_query("UPDATE ryo_users SET guess_streak = %s WHERE user_id = %s", (new_streak, uid))
                    rem = 180 - (now - last_reward_time)
                    reward_msg = f"\n⌛ Reward Cooldown: {rem//60}m {rem%60}s remaining."

                embed = discord.Embed(title="✅ Correct!", description=f"The {self.mode} was **{self.correct_answer}**!\n🔥 Current Streak: **{new_streak}**{reward_msg}", color=RYO_COLOR)
                embed.set_image(url=self.image_url)
                
                # Disable all buttons on success
                for item in self.children:
                    item.disabled = True
                    if item.label == choice:
                        item.style = discord.ButtonStyle.success

                await interaction.edit_original_response(embed=embed, view=self)
            else:
                await run_query("UPDATE ryo_users SET guess_streak = 0 WHERE user_id = %s", (uid,))
                embed = discord.Embed(title="❌ Wrong!", description=f"The correct {self.mode} was **{self.correct_answer}**.\nStreak reset to **0**.", color=RYO_COLOR)
                embed.set_image(url=self.image_url)
                
                # Disable all buttons on failure
                for item in self.children:
                    item.disabled = True
                    if item.label == choice:
                        item.style = discord.ButtonStyle.danger

                await interaction.edit_original_response(embed=embed, view=self)
        
        return callback

# Guess Group
guess_group = app_commands.Group(name="guess", description="Guessing games")

@guess_group.command(name="idol", description="Guess the idol from the card image (Multiple Choice)")
async def guess_idol(interaction: discord.Interaction):
    # Only show pics added by staff using /addgti (era='Guess')
    all_members = await run_query("SELECT DISTINCT member_name FROM ryo_cards WHERE era = 'Guess' AND group_name = 'Solo' AND member_name IS NOT NULL", fetchall=True)
    if not all_members or len(all_members) < 4:
        return await interaction.response.send_message("❌ Not enough Guess The Idol cards in database (need at least 4 unique idols).", ephemeral=True)
    
    cards = await run_query("SELECT member_name, image_url FROM ryo_cards WHERE era = 'Guess' AND group_name = 'Solo' AND member_name IS NOT NULL", fetchall=True)
    selected = random.choice(cards)
    correct_answer, img_url = selected[0], selected[1]

    # Generate options: Correct + 3 Random others
    others_list = [m[0] for m in all_members if m[0] != correct_answer]
    options = [correct_answer] + random.sample(others_list, 3)
    random.shuffle(options)

    embed = discord.Embed(title="🕵️ Who is this Idol?", description="Choose the correct member name from the options below.", color=RYO_COLOR)
    embed.set_image(url=img_url)
    
    view = GuessView(str(interaction.user.id), correct_answer, "Idol", options, img_url)
    await interaction.response.send_message(embed=embed, view=view)
    view.message = await interaction.original_response()

@guess_group.command(name="group", description="Guess the group from the card image (Multiple Choice)")
async def guess_group_sub(interaction: discord.Interaction):
    # Only show pics added by staff using /addgtg (era='Guess', member_name='Group')
    all_groups = await run_query("SELECT DISTINCT group_name FROM ryo_cards WHERE era = 'Guess' AND member_name = 'Group' AND group_name IS NOT NULL", fetchall=True)
    if not all_groups or len(all_groups) < 4:
        return await interaction.response.send_message("❌ Not enough Guess The Group cards in database (need at least 4 unique groups).", ephemeral=True)
    
    cards = await run_query("SELECT group_name, image_url FROM ryo_cards WHERE era = 'Guess' AND member_name = 'Group' AND group_name IS NOT NULL", fetchall=True)
    selected = random.choice(cards)
    correct_answer, img_url = selected[0], selected[1]

    # Generate options: Correct + 3 Random others
    others_list = [g[0] for g in all_groups if g[0] != correct_answer]
    options = [correct_answer] + random.sample(others_list, 3)
    random.shuffle(options)

    embed = discord.Embed(title="🕵️ Which Group is this?", description="Choose the correct group name from the options below.", color=RYO_COLOR)
    embed.set_image(url=img_url)
    
    view = GuessView(str(interaction.user.id), correct_answer, "Group", options, img_url)
    await interaction.response.send_message(embed=embed, view=view)
    view.message = await interaction.original_response()

class BurnConfirm(discord.ui.View):
    def __init__(self, user_id, cards_to_burn, total_paint, total_glue):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.cards_to_burn = cards_to_burn
        self.total_paint = total_paint
        self.total_glue = total_glue

    @discord.ui.button(label="🔥 Confirm Burn", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
        
        await interaction.response.defer()
        
        # Perform deletion
        inventory_ids = [c['inv_id'] for c in self.cards_to_burn]
        if inventory_ids:
            # Batch delete in chunks if needed, but here simple should work
            query = "DELETE FROM ryo_inventory WHERE id IN %s"
            await run_query(query, (tuple(inventory_ids),))
            
            # Award currency
            await run_query("UPDATE ryo_users SET paint = paint + %s, glue = glue + %s WHERE user_id = %s", (self.total_paint, self.total_glue, self.user_id))
        
        await interaction.edit_original_response(
            content=f"✅ Successfully burned **{len(self.cards_to_burn)}** cards!\nReceived: {PAINT_EMOJI} **{self.total_paint:,} Paint** and {GLUE_EMOJI} **{self.total_glue:,} Glue**.",
            view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
        await interaction.response.edit_message(content="❌ Burn cancelled.", view=None)

class GiftConfirm(discord.ui.View):
    def __init__(self, sender_id, recipient, cards_to_gift):
        super().__init__(timeout=60)
        self.sender_id = sender_id
        self.recipient = recipient
        self.cards_to_gift = cards_to_gift

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.sender_id:
            return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
        
        await interaction.response.defer()
        
        inventory_ids = [c['inv_id'] for c in self.cards_to_gift]
        if inventory_ids:
            # Transfer cards
            query = "UPDATE ryo_inventory SET user_id = %s WHERE id IN %s"
            await run_query(query, (str(self.recipient.id), tuple(inventory_ids)))
            
            # Ensure recipient has a profile
            await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (str(self.recipient.id),))
        
        total_stars = sum(c['rarity'] for c in self.cards_to_gift)
        
        embed = discord.Embed(
            description="**Gift successfully sent!**\n\n",
            color=RYO_COLOR
        )
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

        card_lines = []
        for c in self.cards_to_gift[:15]:
            stars = get_rarity_display(c['rarity'], c.get('category', 'regular'), c.get('era'), c.get('card_id'))
            card_lines.append(f"x1 {c['card_id']} {stars} **{c['group_name']} {c['member_name']}**\n({c['era']})")
        
        embed.description += "\n\n".join(card_lines)
        
        if len(self.cards_to_gift) > 15:
            embed.description += f"\n\n*...and {len(self.cards_to_gift) - 15} more cards*"

        embed.description += f"\n\n**Total Cards: {len(self.cards_to_gift)}**\n**Total Stars: {total_stars}**"

        await interaction.edit_original_response(
            content=f"🎁 {self.recipient.mention} **- Gift received!**",
            embed=embed,
            view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.sender_id:
            return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
        await interaction.response.edit_message(content="❌ Gift cancelled.", embed=None, view=None)

class InventoryView(discord.ui.View):
    def __init__(self, title, cards, timeout=60):
        super().__init__(timeout=timeout)
        self.title = title
        self.cards = cards
        self.page = 0
        self.per_page = 15
        self.total_pages = (len(cards) - 1) // self.per_page + 1

    def create_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        current_cards = self.cards[start:end]
        
        embed = discord.Embed(title=self.title, color=RYO_COLOR)
        
        card_list = []
        for card in current_cards:
            # card structure can vary slightly but usually: (id, name, era, group, rarity, ..., copies)
            # We'll try to handle structures found in inventory and search
            if len(card) >= 5:
                cid, member, era, grp, rar = card[:5]
                copies = card[7] if len(card) > 7 else None
                
                stars = get_rarity_display(rar, card[6] if len(card) > 6 else "regular", card[2], card[0])
                # Requested: Group Name \n (rarity) Era \n CardId
                name_line = f"**{grp} {member}**" if grp and member else f"**{grp or member}**"
                
                line = f"{name_line}\n({stars}) {era}\n`{cid}`"
                if copies is not None and copies > 1:
                    # In Discord markdown, underlined text is __text__
                    line += f" __**{copies}**__ **copies**"
                
                card_list.append(line)
        
        if not card_list:
            embed.description = "No cards found."
        else:
            embed.description = "\n\n".join(card_list)
            
        total_display = len(self.cards)
        if self.cards and len(self.cards[0]) > 7:
            total_display = sum(c[7] for c in self.cards if c[7] is not None)
            
        embed.set_footer(text=f"Page {self.page + 1} of {self.total_pages} | Total: {total_display} cards")
        return embed

    @discord.ui.button(emoji="<:rb_full_left:1493145347046768721>", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(emoji="<:rb_left:1493145361601138721>", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else:
            await interaction.response.send_message("You are on the first page!", ephemeral=True)

    @discord.ui.button(emoji="<:rb_right:1493145374846615573>", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else:
            await interaction.response.send_message("You are on the last page!", ephemeral=True)

    @discord.ui.button(emoji="<:rb_full_right:1493145353996730421>", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self.total_pages - 1
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

class CollectionView(discord.ui.View):
    def __init__(self, user_id, cards, owned_ids, filter_summary, page=0):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.cards = cards
        self.owned_ids = owned_ids
        self.filter_summary = filter_summary
        self.page = page
        self.total_pages = (len(cards) - 1) // PAGE_SIZE + 1

    def create_progress_bar(self, percentage):
        length = 20
        filled = int(length * (percentage / 100))
        bar = "█" * filled + "░" * (length - filled)
        return bar

    async def update_message(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        start = self.page * PAGE_SIZE
        end = start + PAGE_SIZE
        page_cards = self.cards[start:end]
        
        owned_count = len([c for c in self.cards if c[0] in self.owned_ids])
        total_count = len(self.cards)
        percentage = (owned_count / total_count * 100) if total_count > 0 else 0
        
        progress_bar = self.create_progress_bar(percentage)
        title_str = f"{int(percentage)}% ({owned_count}/{total_count})"
        
        grid_img = await generate_collection_grid(page_cards, self.owned_ids, title_str, percentage)
        file = discord.File(fp=grid_img, filename='collection.jpg')
        
        embed = discord.Embed(title=f"📚 {title_str}", description=f"`{progress_bar}`", color=RYO_COLOR)
        embed.set_author(name=self.filter_summary)
        embed.set_image(url="attachment://collection.jpg")
        embed.set_footer(text=f"Page {self.page + 1} of {self.total_pages}")
        
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

    @discord.ui.button(emoji="<:rb_full_left:1493145347046768721>", style=discord.ButtonStyle.secondary)
    async def first(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ Not your menu!", ephemeral=True)
        self.page = 0
        await self.update_message(interaction)

    @discord.ui.button(emoji="<:rb_left:1493145361601138721>", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ Not your menu!", ephemeral=True)
        if self.page > 0:
            self.page -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.send_message("First page!", ephemeral=True)

    @discord.ui.button(emoji="<:rb_right:1493145374846615573>", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ Not your menu!", ephemeral=True)
        if self.page < self.total_pages - 1:
            self.page += 1
            await self.update_message(interaction)
        else:
            await interaction.response.send_message("Last page!", ephemeral=True)

    @discord.ui.button(emoji="<:rb_full_right:1493145353996730421>", style=discord.ButtonStyle.secondary)
    async def last(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ Not your menu!", ephemeral=True)
        self.page = self.total_pages - 1
        await self.update_message(interaction)

async def generate_collection_grid(cards, owned_ids, progress_text="", percentage=0):
    # Header and Padding
    HEADER_HEIGHT = 120
    SIDE_PADDING = 20
    TOP_PADDING = 10
    BOTTOM_PADDING = 20
    
    # Grid content dimensions
    grid_content_w = (GRID_WIDTH * CARD_DRAW_WIDTH) + ((GRID_WIDTH - 1) * GRID_GAP)
    grid_content_h = (GRID_HEIGHT * CARD_DRAW_HEIGHT) + ((GRID_HEIGHT - 1) * GRID_GAP)
    
    total_w = grid_content_w + (SIDE_PADDING * 2)
    total_h = grid_content_h + HEADER_HEIGHT + TOP_PADDING + BOTTOM_PADDING
    
    grid = Image.new('RGB', (total_w, total_h), (30, 30, 30))
    draw = ImageDraw.Draw(grid)
    
    # Draw header bar
    draw.rectangle([0, 0, total_w, HEADER_HEIGHT], fill=(20, 20, 20))
    
    # Draw progress text in header
    if progress_text:
        try:
            # Try liberation sans bold first (common in linux environments)
            font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 40)
        except:
            try:
                font = ImageFont.truetype("arial.ttf", 40)
            except:
                font = ImageFont.load_default()
        
        try:
            # textbbox is Pillow 8.0.0+
            text_bbox = draw.textbbox((0, 0), progress_text, font=font)
            text_w = text_bbox[2] - text_bbox[0]
            text_h = text_bbox[3] - text_bbox[1]
        except AttributeError:
            # Fallback for older Pillow
            text_w, text_h = draw.textsize(progress_text, font=font)
        
        # Position text higher to make room for progress bar
        draw.text(
            ((total_w - text_w) // 2, 15),
            progress_text,
            fill=(255, 255, 255),
            font=font
        )
        
        # Draw Visual Progress Bar in Image
        bar_w = total_w * 0.7
        bar_h = 24
        bar_x = (total_w - bar_w) // 2
        bar_y = 65
        
        # Background of bar
        draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=(50, 50, 50))
        # Fill of bar
        fill_w = (percentage / 100) * bar_w
        if fill_w > 0:
            draw.rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], fill=(210, 180, 100)) # Golden/Sand color
    
    async with aiohttp.ClientSession() as session:
        async def process_one(i, card):
            card_id, member, era, group, rarity, image_url = card[0], card[1], card[2], card[3], card[4], card[5]
            
            x = SIDE_PADDING + (i % GRID_WIDTH) * (CARD_DRAW_WIDTH + GRID_GAP)
            y = HEADER_HEIGHT + TOP_PADDING + (i // GRID_WIDTH) * (CARD_DRAW_HEIGHT + GRID_GAP)
            
            try:
                # Add timeout to skip problematic URLs faster
                async with session.get(get_raw_url(image_url), timeout=10) as resp:
                    if resp.status == 200:
                        img_data = await resp.read()
                        with Image.open(io.BytesIO(img_data)) as card_img:
                            card_img = card_img.convert("RGBA")
                            # Use contain to preserve aspect ratio without cropping
                            # Add a small internal safety margin to ensure no edge clipping
                            safety_margin = 6
                            size = (CARD_DRAW_WIDTH - safety_margin, CARD_DRAW_HEIGHT - safety_margin)
                            try:
                                card_img = ImageOps.contain(card_img, size, LANCZOS)
                            except AttributeError:
                                # Fallback for older Pillow < 9.2.0
                                card_img.thumbnail(size, LANCZOS)
                            
                            # Center in the slot
                            ix, iy = card_img.size
                            px = x + (CARD_DRAW_WIDTH - ix) // 2
                            py = y + (CARD_DRAW_HEIGHT - iy) // 2
                            
                            if card_id not in owned_ids:
                                # Grayscale and darken for unowned
                                card_img = ImageOps.grayscale(card_img).convert("RGBA")
                                overlay = Image.new("RGBA", card_img.size, (0, 0, 0, 150))
                                card_img = Image.alpha_composite(card_img, overlay)
                            
                            grid.paste(card_img, (px, py), card_img)
            except Exception as e:
                print(f"Error drawing card {card_id}: {e}")

        # Fetch and process all 12 cards concurrently
        tasks = [process_one(i, card) for i, card in enumerate(cards)]
        await asyncio.gather(*tasks)
                
    output = io.BytesIO()
    # JPEG is faster and smaller than PNG for these large grids
    grid.save(output, format='JPEG', quality=85)
    output.seek(0)
    return output

@bot.tree.command(name="collection", description="Show collection progress with optional filters")
@app_commands.describe(
    user="User to check collection for",
    group="Filter by group name",
    era="Filter by era",
    name="Filter by member name",
    rarity="Filter by rarity (1-5)",
    card_id="Filter by specific card ID"
)
async def collection_cmd(
    interaction: discord.Interaction, 
    user: discord.Member = None, 
    group: str = None, 
    era: str = None, 
    name: str = None, 
    rarity: int = None, 
    card_id: str = None
):
    await interaction.response.defer()
    target_user = user or interaction.user
    uid = str(target_user.id)
    
    # 1. Build Query
    query = "SELECT card_id, member_name, era, group_name, rarity, image_url, category FROM ryo_cards WHERE 1=1"
    params = []
    filter_labels = []
    
    if group:
        query += " AND LOWER(group_name) = %s"
        params.append(group.lower())
        filter_labels.append(f"Group: {group}")
    if era:
        query += " AND LOWER(era) = %s"
        params.append(era.lower())
        filter_labels.append(f"Era: {era}")
    if name:
        query += " AND LOWER(member_name) LIKE %s"
        params.append(f"%{name.lower()}%")
        filter_labels.append(f"Name: {name}")
    if rarity:
        query += " AND rarity = %s"
        params.append(rarity)
        filter_labels.append(f"Rarity: {rarity}★")
    if card_id:
        query += " AND card_id = %s"
        params.append(card_id.upper())
        filter_labels.append(f"ID: {card_id}")
        
    query += " ORDER BY group_name ASC, era ASC, member_name ASC, rarity ASC"
    
    all_cards = await run_query(query, tuple(params), fetchall=True)
    
    if not all_cards:
        return await interaction.followup.send("❌ No cards found matching those filters.")
    
    # 2. Get user's owned card IDs
    owned_rows = await run_query(
        "SELECT DISTINCT card_id FROM ryo_inventory WHERE user_id = %s",
        (uid,), fetchall=True
    )
    owned_ids = {row[0] for row in owned_rows}
    
    # 3. Initial Grid
    filter_summary = " | ".join(filter_labels) if filter_labels else "All Cards"
    author_label = f"{target_user.display_name}'s Collection - {filter_summary}"
    if len(author_label) > 256: author_label = author_label[:253] + "..."
    
    view = CollectionView(str(interaction.user.id), all_cards, owned_ids, author_label)
    
    start = 0
    end = PAGE_SIZE
    page_cards = all_cards[start:end]
    
    owned_count = len([c for c in all_cards if c[0] in owned_ids])
    total_count = len(all_cards)
    percentage = (owned_count / total_count * 100) if total_count > 0 else 0
    
    progress_bar = view.create_progress_bar(percentage)
    title_str = f"{int(percentage)}% ({owned_count}/{total_count})"
    
    grid_img = await generate_collection_grid(page_cards, owned_ids, title_str, percentage)
    file = discord.File(fp=grid_img, filename='collection.jpg')
    
    embed = discord.Embed(title=f"📚 {title_str}", description=f"`{progress_bar}`", color=RYO_COLOR)
    embed.set_author(name=author_label)
    embed.set_image(url="attachment://collection.jpg")
    embed.set_footer(text=f"Page 1 of {view.total_pages}")
    
    await interaction.followup.send(embed=embed, file=file, view=view)

@bot.tree.command(name="inventory", description="Check your or another user's inventory")
@app_commands.describe(
    user="The user to check",
    name="Filter by member name",
    group="Filter by group",
    era="Filter by era",
    rarity="Filter by rarity",
    category="Filter by category"
)
@app_commands.choices(category=[
    app_commands.Choice(name="Regular", value="regular"),
    app_commands.Choice(name="Public Event", value="public event"),
    app_commands.Choice(name="Limited", value="limited"),
    app_commands.Choice(name="None", value="none"),
    app_commands.Choice(name="Booster Event", value="booster event"),
    app_commands.Choice(name="Patreon Event", value="patreon event"),
])
async def inventory(
    interaction: discord.Interaction,
    user: discord.User = None,
    name: str = None,
    group: str = None,
    era: str = None,
    rarity: int = None,
    category: str = None
):
    target_user = user or interaction.user
    uid = str(target_user.id)
    await interaction.response.defer()
    
    # Query with filters
    q = """
        SELECT c.card_id, c.member_name, c.era, c.group_name, c.rarity, c.image_url, c.category, COUNT(*)
        FROM ryo_inventory i
        JOIN ryo_cards c ON i.card_id = c.card_id
        WHERE i.user_id = %s
        GROUP BY c.card_id, c.member_name, c.era, c.group_name, c.rarity, c.image_url, c.category
        ORDER BY c.rarity DESC, c.member_name ASC
    """
    
    results = await run_query(q, (uid,), fetchall=True)
    if not results:
        target_label = "Your" if target_user == interaction.user else f"{target_user.name}'s"
        return await interaction.followup.send(f"❌ {target_label} inventory is empty.")
    
    # Filter in Python
    filtered = []
    for r in results:
        cid, mname, cera, cgroup, crarity, cimg, ccat, copies = r

        # STRICT EXCLUSION: Guess minigame cards should NEVER appear in public inventory
        is_guess = (ccat or "").lower() == "guess_minigame" or (cera or "").lower() == "guess"
        if is_guess:
            continue

        if name and name.lower() not in (mname or "").lower(): continue
        if group and group.lower() not in (cgroup or "").lower(): continue
        if era and era.lower() not in (cera or "").lower(): continue
        if rarity and rarity != crarity: continue
        
        if category:
            cat_val = (ccat or "regular").lower()
            if category.lower() != cat_val:
                continue
                
        filtered.append(r)
        
    if not filtered:
        return await interaction.followup.send("❌ No cards found matching those filters.")
        
    view = InventoryView(f"{target_user.name}'s Inventory", filtered)
    await interaction.followup.send(embed=view.create_embed(), view=view)

@bot.tree.command(name="search", description="Search for cards in the registry")
@app_commands.describe(
    name="Filter by member name",
    group="Filter by group",
    era="Filter by era",
    rarity="Filter by rarity",
    category="Filter by category",
    unowned="Only show cards you DON'T own"
)
@app_commands.choices(category=[
    app_commands.Choice(name="Regular", value="regular"),
    app_commands.Choice(name="Public Event", value="public event"),
    app_commands.Choice(name="Limited", value="limited"),
    app_commands.Choice(name="None", value="none"),
    app_commands.Choice(name="Booster Event", value="booster event"),
    app_commands.Choice(name="Patreon Event", value="patreon event"),
])
async def search(
    interaction: discord.Interaction,
    name: str = None,
    group: str = None,
    era: str = None,
    rarity: int = None,
    category: str = None,
    unowned: bool = False
):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    
    # Query all cards
    q = "SELECT card_id, member_name, era, group_name, rarity, image_url, category FROM ryo_cards ORDER BY rarity DESC, member_name ASC"
    results = await run_query(q, fetchall=True)
    
    if not results:
        return await interaction.followup.send("❌ No cards found in the registry.")
    
    user_inventory = []
    if unowned:
        inv_results = await run_query("SELECT card_id FROM ryo_inventory WHERE user_id = %s", (uid,), fetchall=True)
        user_inventory = [r[0] for r in inv_results]
    
    filtered = []
    for r in results:
        cid, mname, cera, cgroup, crarity, cimg, ccat = r
        
        # STRICT EXCLUSION: Guess minigame cards should NEVER appear in public search results
        is_guess = (ccat or "").lower() == "guess_minigame" or (cera or "").lower() == "guess"
        if is_guess:
            continue

        if unowned and cid in user_inventory: continue
        if name and name.lower() not in (mname or "").lower(): continue
        if group and group.lower() not in (cgroup or "").lower(): continue
        if era and era.lower() not in (cera or "").lower(): continue
        if rarity and rarity != crarity: continue
        
        if category:
            cat_val = (ccat or "regular").lower()
            if category.lower() != cat_val:
                continue
                
        filtered.append(r)
        
    if not filtered:
        return await interaction.followup.send("❌ No cards found matching those filters.")
        
    view = InventoryView("Card Search Results", filtered)
    await interaction.followup.send(embed=view.create_embed(), view=view)

@bot.tree.command(name="cooldowns", description="Check how much time is left to use your commands")
async def cooldowns(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    now = int(time.time())
    
    # Define durations
    durations = {
        "daily": 86400,
        "weekly": 604800,
        "sticky": 3600,
        "color": 1800,
        "paint": 1200,
        "draw": get_user_cooldown(interaction.user),
        "event-draw": 3600
    }
    
    # Query regular cooldowns
    cooldown_rows = await run_query("SELECT command, last_used FROM ryo_cooldowns WHERE user_id = %s", (uid,), fetchall=True)
    last_used_map = {row[0]: row[1] for row in cooldown_rows}
    
    # Query guess reward cooldown
    user_data = await run_query("SELECT last_guess_reward FROM ryo_users WHERE user_id = %s", (uid,), fetchone=True)
    last_guess = user_data[0] if user_data else 0
    
    def format_time(rem):
        if rem <= 0: return "✅ **Ready**"
        return f"⌛ <t:{now + rem}:R>"

    desc = []
    
    # Regular commands
    for cmd, duration in durations.items():
        last = last_used_map.get(cmd, 0)
        rem = duration - (now - last)
        # Display nicely
        display_name = cmd.replace("-", " ").title()
        desc.append(f"**{display_name}**: {format_time(rem)}")
        
    # Guess Rewards
    rem_guess = 180 - (now - last_guess)
    desc.append(f"**Guess Rewards**: {format_time(rem_guess)}")
    
    embed = discord.Embed(
        title=f"⏳ {interaction.user.name}'s Cooldowns",
        description="\n".join(desc),
        color=RYO_COLOR
    )
    embed.set_footer(text="Commands you haven't used yet show as Ready.")
    await interaction.response.send_message(embed=embed)

# --- Event Draw Command ---
@bot.tree.command(name="event-draw", description="Draw 1 card from ongoing events (Cost: 15 Glue)")
async def event_draw(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    
    # Check cooldown (1 hour = 3600s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='event-draw'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 3600:
        rem = 3600 - (now - last_used)
        return await interaction.response.send_message(f"⌛ Ryo is preparing the event stage! Try again in {rem//3600}h {(rem%3600)//60}m {rem % 60}s.", ephemeral=True)

    await interaction.response.defer()
    
    try:
        # Check glue balance
        res_glue = await run_query("SELECT glue FROM ryo_users WHERE user_id = %s", (uid,), fetchone=True)
        current_glue = res_glue[0] if res_glue else 0
        
        if current_glue < 15:
            return await interaction.followup.send(f"❌ You need **15 glue** {GLUE_EMOJI} to draw from events! (Balance: {current_glue}{GLUE_EMOJI})")

        # Fetch active events
        events_check = await run_query("SELECT category, era FROM ryo_events", fetchall=True)
        if not events_check:
            return await interaction.followup.send("ℹ️ There are no active events at the moment. Only **Regular** cards are currently dropping.")

        # Fetch cards from active events
        cards = await run_query("""
            SELECT card_id, member_name, era, group_name, rarity, image_url, category 
            FROM ryo_cards 
            WHERE (LOWER(category), LOWER(era)) IN (SELECT category, era FROM ryo_events)
        """, fetchall=True)
        
        if not cards:
            return await interaction.followup.send("❌ No cards found for the ongoing events in the database.")
        
        card = random.choice(cards)
        
        # Deduct glue and set cooldown
        await run_query("UPDATE ryo_users SET glue = glue - 15 WHERE user_id = %s", (uid,))
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'event-draw', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))
        
        # Add to inventory
        await run_query("INSERT INTO ryo_inventory (user_id, card_id) VALUES (%s, %s)", (uid, card[0]))
        
        # Get count of copies
        count_res = await run_query("SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s AND card_id = %s", (uid, card[0]), fetchone=True)
        copies = count_res[0] if count_res else 1
        
        # Build results embed matching paint pick result logic (approximate layout)
        results_title = f"🌟 {interaction.user.display_name} | event draw"
        description_lines = []
        
        try:
            r = int(card[4]) if card[4] is not None else 1
        except (ValueError, TypeError):
            r = 1
        
        stars = get_rarity_display(r, card[6], card[2], card[0])
        m = card[1] or "N/A"
        cid = card[0] or "N/A"
        era = card[2] or "N/A"
        
        description_lines.append(f"{stars} **{m}**")
        description_lines.append(f"{cid} {era}")
        description_lines.append(f"Copies : {copies}")
        description_lines.append(f"\n*Spent 15 glue {GLUE_EMOJI}*")
        
        res_embed = discord.Embed(
            title=results_title,
            description="\n".join(description_lines),
            color=RYO_COLOR
        )
        res_embed.set_image(url=card[5])
        
        # Footer icon
        footer_icon = "https://cdn.discordapp.com/emojis/1497330599457722479.png"
        res_embed.set_footer(text=f"Event Card Claimed!", icon_url=footer_icon)
        
        await interaction.followup.send(embed=res_embed)
        
    except Exception as e:
        print(f"❌ Event draw error: {e}")
        await interaction.followup.send(f"⚠️ Failed to perform event draw: {e}")

# --- Event Management ---

@bot.tree.command(name="startevent", description="Make event cards from a specific category and era droppable")
@app_commands.describe(category="Event category", era="Era to start")
async def startevent(interaction: discord.Interaction, category: str, era: str):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await run_query("INSERT INTO ryo_events (category, era) VALUES (%s, %s) ON CONFLICT DO NOTHING", (category.lower(), era.lower()))
    await interaction.response.send_message(f"✅ Event started! Cards for `{category}` in `{era}` are now droppable.")

@bot.tree.command(name="endevent", description="Stop event cards from a specific category and era from dropping")
@app_commands.describe(category="Event category", era="Era to end")
async def endevent(interaction: discord.Interaction, category: str, era: str):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await run_query("DELETE FROM ryo_events WHERE category = %s AND era = %s", (category.lower(), era.lower()))
    await interaction.response.send_message(f"✅ Event ended! Cards for `{category}` in `{era}` are no longer droppable.")

@bot.tree.command(name="events", description="Show all active events")
async def show_events(interaction: discord.Interaction):
    events = await run_query("SELECT category, era FROM ryo_events", fetchall=True)
    if not events:
        return await interaction.response.send_message("ℹ️ There are no active events. Only **Regular** cards are currently dropping.")
    
    desc = "\n".join([f"• **{cat.title()}**: {era.title()}" for cat, era in events])
    embed = discord.Embed(title="🌟 Active Events", description=desc, color=RYO_COLOR)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="gift", description="Gift cards from your inventory to another player")
@app_commands.describe(
    user="Member to gift to",
    group="Filter by group",
    code="Filter by specific Card ID",
    name="Filter by idol name",
    era="Filter by era",
    max_rarity="Highest rarity to gift",
    dupes_only="Only gift duplicate cards (keeps one)",
    exclude_5_stars="Ignore all 5-star cards",
    exclude_members="Comma-separated names to exclude",
    copies="Number of cards to gift (defaults to 1)"
)
async def gift(
    interaction: discord.Interaction,
    user: discord.Member,
    group: str = None,
    code: str = None,
    name: str = None,
    era: str = None,
    max_rarity: int = None,
    dupes_only: bool = False,
    exclude_5_stars: bool = False,
    exclude_members: str = None,
    copies: int = 1
):
    if user.id == interaction.user.id:
        return await interaction.response.send_message("❌ You cannot gift cards to yourself!", ephemeral=True)
    if user.bot:
        return await interaction.response.send_message("❌ You cannot gift cards to bots!", ephemeral=True)

    uid = str(interaction.user.id)
    await interaction.response.defer()

    # 1. Fetch user inventory with card details
    q = """
        SELECT i.id, c.card_id, c.rarity, c.category, c.member_name, c.group_name, c.era
        FROM ryo_inventory i
        JOIN ryo_cards c ON i.card_id = c.card_id
        WHERE i.user_id = %s
    """
    inventory = await run_query(q, (uid,), fetchall=True)
    if not inventory:
        return await interaction.followup.send("❌ Your inventory is empty.")

    # 2. Apply Filters
    to_gift = []
    card_counts = {}
    
    excluded_set = set()
    if exclude_members:
        excluded_set = {m.strip().lower() for m in exclude_members.split(",")}

    if dupes_only:
        for item in inventory:
            cid = item[1]
            card_counts[cid] = card_counts.get(cid, 0) + 1

    current_counts = {}
    for item in inventory:
        # Index map: 0:inv_id, 1:card_id, 2:rarity, 3:cat, 4:member, 5:group, 6:era
        cid = item[1]
        rarity = item[2]
        member = (item[4] or "").lower()
        grp = (item[5] or "").lower()
        cera = (item[6] or "").lower()

        # Apply filters
        if group and group.lower() not in grp: continue
        if code and code.lower() != cid.lower(): continue
        if name and name.lower() not in member: continue
        if era and era.lower() not in cera: continue
        if max_rarity and rarity > max_rarity: continue
        if exclude_5_stars and rarity == 5: continue
        if member in excluded_set: continue

        if dupes_only:
            current_counts[cid] = current_counts.get(cid, 0) + 1
            if current_counts[cid] <= 1:
                continue

        to_gift.append({
            'inv_id': item[0],
            'card_id': cid,
            'rarity': rarity,
            'category': item[3],
            'member_name': item[4],
            'group_name': item[5],
            'era': item[6]
        })

    if not to_gift:
        return await interaction.followup.send("❌ No cards found matching your filters.")

    if copies < 1:
        return await interaction.followup.send("❌ You must gift at least 1 card.")

    # Apply copies limit
    to_gift = to_gift[:copies]

    # 3. Final confirmation
    total_stars = sum(c['rarity'] for c in to_gift)
    
    embed = discord.Embed(
        description=f"Are you sure you want to gift the following cards to {user.mention}?\n"
                    f"**Cards: {len(to_gift)} | Stars: {total_stars}**\n\n",
        color=RYO_COLOR
    )
    embed.set_author(name=interaction.user.name, icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
    
    # List cards (limit to first 15 for safety in embed, though request might imply all)
    card_lines = []
    for c in to_gift[:15]:
        stars = get_rarity_display(c['rarity'], c.get('category', 'regular'), c.get('era'), c.get('card_id'))
        card_lines.append(f"x1 {c['card_id']} {stars} **{c['group_name']} {c['member_name']}**\n({c['era']})")
    
    embed.description += "\n\n".join(card_lines)
    
    if len(to_gift) > 15:
        embed.description += f"\n\n*...and {len(to_gift) - 15} more cards*"

    embed.set_footer(text="Page: 1 / 1", icon_url="https://cdn.discordapp.com/emojis/1344426569103282276.png") # Dog emoji placeholder
    
    view = GiftConfirm(uid, user, to_gift)
    await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="pay", description="Send currency to another player")
@app_commands.describe(user="The player to receive the currency", paint="Amount of paint to send", glue="Amount of glue to send")
async def pay(interaction: discord.Interaction, user: discord.Member, paint: int = 0, glue: int = 0):
    if user.id == interaction.user.id:
        return await interaction.response.send_message("❌ You cannot send currency to yourself!", ephemeral=True)
    if user.bot:
        return await interaction.response.send_message("❌ You cannot send currency to bots!", ephemeral=True)
    if paint < 0 or glue < 0:
        return await interaction.response.send_message("❌ You cannot send negative amounts!", ephemeral=True)
    if paint == 0 and glue == 0:
        return await interaction.response.send_message("❌ You must specify at least one amount to send!", ephemeral=True)

    uid = str(interaction.user.id)
    target_id = str(user.id)
    
    await interaction.response.defer()
    
    # 1. Check sender balance
    user_data = await run_query("SELECT paint, glue FROM ryo_users WHERE user_id = %s", (uid,), fetchone=True)
    if not user_data:
        return await interaction.followup.send("❌ You don't have any currency to send.")
    
    current_paint, current_glue = user_data
    if paint > current_paint:
        return await interaction.followup.send(f"❌ You don't have enough paint! (Balance: {current_paint}{PAINT_EMOJI})")
    if glue > current_glue:
        return await interaction.followup.send(f"❌ You don't have enough glue! (Balance: {current_glue}{GLUE_EMOJI})")
    
    # 2. Update balances
    # Subtract from sender
    await run_query("UPDATE ryo_users SET paint = paint - %s, glue = glue - %s WHERE user_id = %s", (paint, glue, uid))
    
    # Add to recipient
    await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (target_id,))
    await run_query("UPDATE ryo_users SET paint = paint + %s, glue = glue + %s WHERE user_id = %s", (paint, glue, target_id))
    
    # 3. Success message
    parts = []
    if paint > 0: parts.append(f"{PAINT_EMOJI} **{paint:,} paint**")
    if glue > 0: parts.append(f"{GLUE_EMOJI} **{glue:,} glue**")
    
    sent_str = " and ".join(parts)
    embed = discord.Embed(
        description=f"✅ Successfully sent {sent_str} to **{user.name}**!",
        color=RYO_COLOR
    )
    await interaction.followup.send(content=user.mention, embed=embed)

@bot.tree.command(name="burn", description="Burn cards from your inventory for currency rewards")
@app_commands.describe(
    group="Filter by group",
    card_id="Filter by specific Card ID",
    name="Filter by card name",
    era="Filter by era",
    max_rarity="Highest rarity to search for",
    dupes_only="Only burn duplicate cards (keeps one of each)",
    exclude_5_stars="Ignore all 5-star cards",
    exclude_name="Comma-separated idol names to exclude"
)
async def burn(
    interaction: discord.Interaction,
    group: str = None,
    card_id: str = None,
    name: str = None,
    era: str = None,
    max_rarity: int = None,
    dupes_only: bool = False,
    exclude_5_stars: bool = False,
    exclude_name: str = None
):
    uid = str(interaction.user.id)
    await interaction.response.defer()

    # 1. Fetch user inventory with card details
    q = """
        SELECT i.id, c.card_id, c.rarity, c.category, c.member_name, c.group_name, c.era
        FROM ryo_inventory i
        JOIN ryo_cards c ON i.card_id = c.card_id
        WHERE i.user_id = %s
    """
    inventory = await run_query(q, (uid,), fetchall=True)
    if not inventory:
        return await interaction.followup.send("❌ Your inventory is empty.")

    # 2. Apply Filters in Python for flexibility
    to_burn = []
    card_counts = {} # To track dupes
    
    # Pre-process excluded members
    excluded_set = set()
    if exclude_name:
        excluded_set = {m.strip().lower() for m in exclude_name.split(",")}

    # First pass: identify duplicates if needed
    if dupes_only:
        for item in inventory:
            cid = item[1]
            card_counts[cid] = card_counts.get(cid, 0) + 1

    # Second pass: select cards to burn
    # We'll use a local counter for dupes to know which ones are "extras"
    current_counts = {}
    
    for item in inventory:
        # Index map: 0:inv_id, 1:card_id, 2:rarity, 3:cat, 4:member, 5:group, 6:era
        cid = item[1]
        rarity = item[2]
        cat = item[3]
        member = (item[4] or "").lower()
        grp = (item[5] or "").lower()
        cera = (item[6] or "").lower()

        # Apply basic filters
        if group and group.lower() not in grp: continue
        if card_id and card_id.lower() != cid.lower(): continue
        if name and name.lower() not in member: continue
        if era and era.lower() not in cera: continue
        if max_rarity and rarity > max_rarity: continue
        if exclude_5_stars and rarity == 5: continue
        if member in excluded_set: continue

        # Dupe logic
        if dupes_only:
            current_counts[cid] = current_counts.get(cid, 0) + 1
            if current_counts[cid] <= 1:
                # Keep the first one
                continue

        # If it passed all filters, it's marked for burning
        to_burn.append({
            'inv_id': item[0],
            'rarity': rarity,
            'category': (cat or "regular").lower()
        })

    if not to_burn:
        return await interaction.followup.send("❌ No cards found matching your filters.")

    # 3. Calculate rewards
    total_paint = 0
    total_glue = 0
    
    for c in to_burn:
        r = c['rarity']
        cat = c['category']
        
        if r == 1: total_paint += 50
        elif r == 2: total_paint += 80
        elif r == 3: total_paint += 100
        elif r == 4: total_paint += 250
        elif r == 5:
            if cat in ["public event", "limited"]:
                total_paint += 1000
                total_glue += 3
            else:
                total_paint += 500
                total_glue += 1

    # 4. Final confirmation
    embed = discord.Embed(
        title="🔥 Burn Protocol",
        description=f"You are about to burn **{len(to_burn)}** cards.\n\n"
                    f"**Rewards:**\n"
                    f"{PAINT_EMOJI} Paint: **{total_paint:,}**\n"
                    f"{GLUE_EMOJI} Glue: **{total_glue:,}**\n\n"
                    f"Do you wish to proceed?",
        color=RYO_COLOR
    )
    
    view = BurnConfirm(uid, to_burn, total_paint, total_glue)
    await interaction.followup.send(embed=embed, view=view)

@search.autocomplete('category')
@inventory.autocomplete('category')
async def inventory_cat_autocomplete(interaction: discord.Interaction, current: str):
    cats = ["regular", "public event", "limited", "none", "booster event", "patreon event"]
    return [app_commands.Choice(name=c.title(), value=c) for c in cats if current.lower() in c.lower()]

@startevent.autocomplete('category')
@endevent.autocomplete('category')
async def event_cat_autocomplete(interaction: discord.Interaction, current: str):
    cats = ["public event", "booster event", "patreon event", "limited"]
    return [app_commands.Choice(name=c.title(), value=c) for c in cats if current.lower() in c.lower()]

@collection_cmd.autocomplete('group')
@search.autocomplete('group')
@inventory.autocomplete('group')
@burn.autocomplete('group')
@gift.autocomplete('group')
async def group_autocomplete(interaction: discord.Interaction, current: str):
    if interaction.command and interaction.command.name == "collection":
        groups = await run_query("SELECT DISTINCT group_name FROM ryo_cards WHERE group_name IS NOT NULL", fetchall=True)
    else:
        # Exclude groups that only appear in guess minigame era
        groups = await run_query("SELECT DISTINCT group_name FROM ryo_cards WHERE group_name IS NOT NULL AND era != 'Guess' AND category != 'guess_minigame'", fetchall=True)
    group_list = [g[0] for g in groups if g[0]]
    return [app_commands.Choice(name=g, value=g) for g in group_list if current.lower() in g.lower()][:25]

@collection_cmd.autocomplete('era')
@search.autocomplete('era')
@inventory.autocomplete('era')
@burn.autocomplete('era')
@gift.autocomplete('era')
@startevent.autocomplete('era')
@endevent.autocomplete('era')
async def era_autocomplete(interaction: discord.Interaction, current: str):
    if interaction.command and interaction.command.name == "collection":
        eras = await run_query("SELECT DISTINCT era FROM ryo_cards WHERE era IS NOT NULL", fetchall=True)
    else:
        eras = await run_query("SELECT DISTINCT era FROM ryo_cards WHERE era IS NOT NULL AND era != 'Guess'", fetchall=True)
    era_list = [e[0] for e in eras if e[0]]
    return [app_commands.Choice(name=e, value=e) for e in era_list if current.lower() in e.lower()][:25]

@search.autocomplete('name')
@inventory.autocomplete('name')
@burn.autocomplete('name')
@gift.autocomplete('name')
async def name_autocomplete(interaction: discord.Interaction, current: str):
    if interaction.command and interaction.command.name == "collection":
        names = await run_query("SELECT DISTINCT member_name FROM ryo_cards WHERE member_name IS NOT NULL", fetchall=True)
    else:
        names = await run_query("SELECT DISTINCT member_name FROM ryo_cards WHERE member_name IS NOT NULL AND era != 'Guess' AND category != 'guess_minigame'", fetchall=True)
    name_list = [n[0] for n in names if n[0]]
    return [app_commands.Choice(name=n, value=n) for n in name_list if current.lower() in n.lower()][:25]

@bot.tree.command(name="bulkadd", description="Add multiple cards (STAFF ONLY)")
@app_commands.describe(file="CSV file to upload")
async def bulkadd(interaction: discord.Interaction, file: discord.Attachment):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    if not file.filename.endswith(".csv"):
        return await interaction.response.send_message("❌ Please upload a CSV file.", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        content = await file.read()
        stream = io.StringIO(content.decode('utf-8'))
        # Use simple reader to handle specific column order
        reader = csv.reader(stream)
        
        # Skip header if it looks like one (contains 'id' or 'name' in first row)
        rows = list(reader)
        if not rows:
            return await interaction.followup.send("❌ CSV file is empty.")
            
        start_idx = 0
        first_row = [str(cell).lower() for cell in rows[0]]
        if 'id' in first_row or 'name' in first_row or 'group' in first_row:
            start_idx = 1
            
        added = 0
        errors = 0
        
        for row in rows[start_idx:]:
            try:
                if len(row) < 7:
                    errors += 1
                    continue
                
                # Column order: card id, name, group, era, rarity, category, image url
                card_id = row[0].strip()
                member = row[1].strip()
                group = row[2].strip()
                era = row[3].strip()
                rarity_str = row[4].strip()
                category = row[5].strip().lower()
                image_url = get_raw_url(row[6].strip())
                
                if category == "event": category = "public event"
                
                # Extract number from rarity_str (handles "5 stars")
                rarity_match = re.search(r'(\d+)', rarity_str)
                rarity = int(rarity_match.group(1)) if rarity_match else 1
                
                if category in ["public event", "limited", "booster event", "patreon event"]:
                    rarity = 5
                
                if not all([card_id, member, era, group, image_url]):
                    errors += 1
                    continue

                await run_query(
                    "INSERT INTO ryo_cards (card_id, era, group_name, rarity, image_url, category, member_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (card_id) DO UPDATE SET "
                    "era=EXCLUDED.era, group_name=EXCLUDED.group_name, rarity=EXCLUDED.rarity, "
                    "image_url=EXCLUDED.image_url, category=EXCLUDED.category, member_name=EXCLUDED.member_name",
                    (card_id, era, group, rarity, image_url, category, member)
                )
                added += 1
            except Exception as e:
                print(f"Bulk Error row: {e}")
                errors += 1
                
        await interaction.followup.send(f"✅ Successfully processed CSV. Added/Updated: {added}, Errors: {errors}")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to process file: {e}")

@bot.tree.command(name="reset_cooldown", description="Reset a user's command cooldown")
@app_commands.describe(user="The user to reset", command="The command to reset")
async def reset_cooldown(interaction: discord.Interaction, user: discord.User, command: str):
    if not is_mod(interaction):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)

    uid_str = str(user.id)
    if command == "all":
        await run_query("DELETE FROM ryo_cooldowns WHERE user_id=%s", (uid_str,))
    else:
        await run_query("DELETE FROM ryo_cooldowns WHERE user_id=%s AND command=%s", (uid_str, command))
    
    await interaction.response.send_message(f"✅ Reset {command} cooldown(s) for user {user.mention}.", ephemeral=True)

@reset_cooldown.autocomplete('command')
async def command_autocomplete(interaction: discord.Interaction, current: str):
    valid_commands = ["all", "daily", "weekly", "sticky", "color", "draw", "paint"]
    return [
        app_commands.Choice(name=cmd, value=cmd)
        for cmd in valid_commands if current.lower() in cmd.lower()
    ]

@bot.tree.command(name="sync", description="Refresh command registry (Dev Only)")
@app_commands.describe(scope="Whether to sync globally or to the current guild", clear_guild="Whether to clear guild commands before syncing")
async def sync_cmd(interaction: discord.Interaction, scope: str = "global", clear_guild: bool = False):
    if interaction.user.id != DEV_ID:
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    try:
        if clear_guild:
            bot.tree.clear_commands(guild=interaction.guild)
            await bot.tree.sync(guild=interaction.guild)
            msg = "🧹 Cleared and synced guild commands."
        
        if scope == "global":
            synced = await bot.tree.sync()
            msg = f"✅ Successfully synced {len(synced)} commands globally."
        else:
            synced = await bot.tree.sync(guild=interaction.guild)
            msg = f"✅ Successfully synced {len(synced)} commands to this guild."
            
        await interaction.followup.send(msg)
    except Exception as e:
        await interaction.followup.send(f"❌ Sync failed: {e}")

@sync_cmd.autocomplete('scope')
async def sync_scope_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name="Global", value="global"),
        app_commands.Choice(name="Guild", value="guild")
    ]

if RYOTOKEN and RYOTOKEN != "" and RYOTOKEN != "PLACEHOLDER":
    bot.run(RYOTOKEN)
else:
    print("❌ ERROR: RYO_TOKEN is missing or set to PLACEHOLDER in ryo.env.")
    print("Please update ryo.env with your actual Discord Bot Token and Supabase Database URL.")
