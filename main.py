import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button
import psycopg2
from psycopg2.pool import SimpleConnectionPool
import os
import random
import asyncio
import time
from dotenv import load_dotenv
from PIL import Image
import csv
from io import BytesIO, StringIO
import aiohttp

load_dotenv()

print("🚀 BOT FILE STARTED - PANGPOND EDITION")

# -------------------------------
# ENV VARIABLES
# -------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
TOKEN = os.getenv("TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
GUILD_IDS = [1275571397036347482]
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID", "1484797370951532564"))
ALLOWED_USERS = [1322126091929915454, 1254757830704103484]

# 🔹 Default emojis (fallbacks)
def get_emoji(env_name, default):
    val = os.getenv(env_name)
    if not val: return default
    # If it's a custom emoji <:name:id>, ensure it's treated as a string or partial emoji correctly
    if val.startswith("<") and val.endswith(">"):
        try:
            return str(discord.PartialEmoji.from_str(val))
        except:
            return val
    return val

BALL = get_emoji("BALL_EMOJI", "⚽")
BOBA = get_emoji("BOBA_EMOJI", "🧋")
BUTTON = get_emoji("BUTTON_EMOJI", "🔘")
CAKE = get_emoji("CAKE_EMOJI", "🍰")
CHOCOLATE = get_emoji("CHOCOLATE_EMOJI", "🍫")
CROISSANT = get_emoji("CROISSANT_EMOJI", "🥐")
ICE = get_emoji("ICE_EMOJI", "❄️")
PANCAKE = get_emoji("PANCAKE_EMOJI", "🥞")
PANG = get_emoji("PANG_EMOJI", "🍞")
PUDDING = get_emoji("PUDDING_EMOJI", "🍮")
SPIRAL = get_emoji("SPIRAL_EMOJI", "🌀")
STAR = get_emoji("STAR_EMOJI", "⭐")
TEA = get_emoji("TEA_EMOJI", "🍵")

NO1 = get_emoji("NO1_EMOJI", "1️⃣")
NO2 = get_emoji("NO2_EMOJI", "2️⃣")
NO3 = get_emoji("NO3_EMOJI", "3️⃣")
LEFT = get_emoji("LEFT_EMOJI", "⬅️")
RIGHT = get_emoji("RIGHT_EMOJI", "➡️")

# Ensure pool is initialized only if URL exists to prevent crash on local dev without DB
db_pool = None
if DATABASE_URL:
    try:
        db_pool = SimpleConnectionPool(1, 10, DATABASE_URL, sslmode="require")
    except Exception as e:
        print(f"⚠️ Failed to connect to DB: {e}")

async def run_query(query, params=None, fetchone=False, fetchall=False):
    if not db_pool:
        print(f"❌ DB Pool not initialized")
        return None
    
    def _execute():
        conn = None
        try:
            conn = db_pool.getconn()
            with conn: # Handles automatic commit on success and rollback on error
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    if fetchone: return cur.fetchone()
                    if fetchall: return cur.fetchall()
                    return True
        except Exception as e:
            print(f"[DB ERROR] {e} | Query: {query}")
            return None
        finally:
            if conn: db_pool.putconn(conn)
    
    return await asyncio.to_thread(_execute)

# -------------------------------
# HELPERS
# -------------------------------
async def ensure_user(user_id: str):
    await run_query("INSERT INTO users (user_id, boba, cakecoins) VALUES (%s, 0, 0) ON CONFLICT (user_id) DO NOTHING", (user_id,))

async def get_cooldown(user_id: str, command: str):
    res = await run_query("SELECT last_used FROM cooldowns WHERE user_id=%s AND command=%s", (user_id, command), fetchone=True)
    return int(res[0]) if res and res[0] is not None else 0

async def set_cooldown(user_id: str, command: str, now: int):
    await run_query("INSERT INTO cooldowns (user_id, command, last_used) VALUES (%s, %s, %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used = EXCLUDED.last_used", (user_id, command, now))

async def get_remaining_cooldown(user_id: str, command: str, cooldown_time: int):
    last = await get_cooldown(user_id, command)
    now = int(time.time())
    remaining = cooldown_time - (now - last)
    return max(0, remaining)

async def log_action(user_id: str, action: str, details: str):
    timestamp = int(time.time())
    await run_query('INSERT INTO logs ("user_id", "action", "details", "timestamp") VALUES (%s,%s,%s,%s)', (user_id, action, details, timestamp))
    if LOG_CHANNEL_ID:
        try:
            channel = bot.get_channel(LOG_CHANNEL_ID)
            if isinstance(channel, discord.TextChannel):
                styles = {
                    "drop": (f"{BALL} DROP", discord.Color.orange()),
                    "daily": (f"{BOBA} DAILY", discord.Color.green()),
                    "weekly": (f"{BOBA} WEEKLY", discord.Color.gold()),
                    "bake": (f"{CROISSANT} BAKE", discord.Color.pink()),
                    "pay": (f"{BOBA} PAY", discord.Color.green()),
                    "giftcard": (f"{CHOCOLATE} GIFT", discord.Color.green()),
                    "manage": (f"{PANG} ADMIN", discord.Color.red()),
                    "addcard": (f"{BUTTON} ADD CARD", discord.Color.blurple()),
                    "event_start": (f"{PANCAKE} EVENT START", discord.Color.purple()),
                    "event_end": (f"{PUDDING} EVENT END", discord.Color.dark_red())
                }
                title, color = styles.get(action, (f"{TEA} {action.upper()}", discord.Color.dark_blue()))
                embed = discord.Embed(title=title, description=details, color=color)
                embed.add_field(name="👤 User", value=f"<@{user_id}> (`{user_id}`)")
                embed.set_footer(text=f"<t:{timestamp}:F>")
                await channel.send(embed=embed)
        except Exception as e: print(f"[LOG ERROR] {e}")

async def set_reminder(user_id, command, duration, channel_id):
    now = int(time.time())
    await run_query("""
        INSERT INTO reminders (user_id, command, end_time, channel_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, command)
        DO UPDATE SET end_time = %s, channel_id = %s
    """, (user_id, command, now + duration, str(channel_id), now + duration, str(channel_id)))

def check_is_manager(interaction: discord.Interaction):
    """Checks if the user is a mod/admin of any allowed server or a dev."""
    # Always allow devs
    if interaction.user.id in ALLOWED_USERS:
        return True
    
    # Check if the current server is in our allowed list
    if interaction.guild_id not in GUILD_IDS:
        return False
        
    # Check if user has mod role or manage_guild permission
    has_role = any(r.id == MOD_ROLE_ID for r in interaction.user.roles) if hasattr(interaction.user, 'roles') else False
    has_perms = interaction.user.guild_permissions.manage_guild if hasattr(interaction.user, 'guild_permissions') else False
    
    return has_role or has_perms

# -------------------------------
# BOT CLASS (Fixed initialization)
# -------------------------------
class PangpondBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.session = None
        self.card_cache = None
        self.cache_time = 0

    async def get_cards(self):
        """Fetches card registry with 5-minute memory caching."""
        now = time.time()
        if self.card_cache and (now - self.cache_time < 300):
            return self.card_cache
            
        cards = await run_query("""
            SELECT card_id, name, era, group_name, rarity, image 
            FROM cards 
            WHERE rarity < 5 OR (rarity = 5 AND event_name IS NOT NULL AND event_name IN (SELECT event_name FROM active_events))
        """, fetchall=True)
        if cards:
            self.card_cache = cards
            self.cache_time = now
        return cards or []

    async def setup_hook(self):
        # Initialize aiohttp session for reuse
        self.session = aiohttp.ClientSession()
        self.loop.create_task(background_loop())
        print(f"✅ Global session & backgrounds started")

    async def on_ready(self):
        print("⏳ Setting up database...")
        await setup_database()
        print(f"🔥 Online: {self.user}")
        
        for g_id in GUILD_IDS:
            try:
                # ⚡ INSTANT SYNC: Push to guilds
                guild = discord.Object(id=g_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                print(f"⚡ Slash commands pushed instantly to Guild: {g_id}")
            except Exception as e:
                print(f"⚠️ Auto-sync failed for Guild {g_id}: {e}")
        
        try:
            # 🧹 GLOBAL CLEAR: This removes global commands to prevent duplicates 
            # with the guild-synced ones.
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            print("🧹 Global command cache cleared.")
        except Exception as e:
            print(f"⚠️ Global clear failed: {e}")

bot = PangpondBot()

# 🔹 Emergency Sync Command (Prefix: !)
@bot.command(name="sync")
async def sync_prefix(ctx):
    """Force sync slash commands and clear global cache using !sync"""
    if ctx.author.id in ALLOWED_USERS or (ctx.guild and ctx.guild.id in GUILD_IDS and any(r.id == MOD_ROLE_ID for r in ctx.author.roles)):
        await ctx.send("⏳ Force syncing and clearing global cache... please wait...")
        try:
            # Clear global
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
            
            # Sync guilds
            for g_id in GUILD_IDS:
                guild = discord.Object(id=g_id)
                bot.tree.copy_global_to(guild=guild)
                await bot.tree.sync(guild=guild)
            await ctx.send(f"{BUTTON} Success! Synced commands to {len(GUILD_IDS)} slash guilds and purged global ghost commands.")
        except Exception as e:
            await ctx.send(f"❌ Sync failed: {e}")
    else:
        await ctx.send(f"❌ You don't have permission to sync.")

async def background_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(30)
        rems = await run_query("SELECT user_id, command, channel_id FROM reminders WHERE end_time <= %s", (int(time.time()),), fetchall=True)
        if rems:
            for r in rems:
                try:
                    c = bot.get_channel(int(r[2]))
                    if c: await c.send(f"{TEA} <@{r[0]}>, your /{r[1]} is ready again!")
                    await run_query("DELETE FROM reminders WHERE user_id=%s AND command=%s", (r[0], r[1]))
                except: pass

async def setup_database():
    if not db_pool: return
    
    def _setup():
        conn = db_pool.getconn()
        try:
            with conn: # Standard psycopg2 transaction block
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, boba INT DEFAULT 0, cakecoins INT DEFAULT 0);")
                    cur.execute('CREATE TABLE IF NOT EXISTS inventory (id SERIAL PRIMARY KEY, user_id TEXT, card_id TEXT, "name" TEXT, "era" TEXT, group_name TEXT, "rarity" INT, "timestamp" TIMESTAMP DEFAULT CURRENT_TIMESTAMP);')
                    # Migrations for existing tables
                    try: cur.execute('ALTER TABLE inventory ALTER COLUMN card_id TYPE TEXT;')
                    except: pass
                    try: cur.execute('ALTER TABLE inventory ALTER COLUMN "name" TYPE TEXT;')
                    except: pass
                    try: cur.execute('ALTER TABLE inventory ALTER COLUMN "era" TYPE TEXT;')
                    except: pass

                    cur.execute('CREATE TABLE IF NOT EXISTS cooldowns (user_id TEXT, command TEXT, last_used BIGINT, PRIMARY KEY (user_id, command));')
                    cur.execute('CREATE TABLE IF NOT EXISTS cards (card_id TEXT PRIMARY KEY, "name" TEXT, "era" TEXT, group_name TEXT, "rarity" INT, image TEXT, category TEXT DEFAULT \'regular\', event_name TEXT);')
                    # Migrations for cards
                    try: cur.execute('ALTER TABLE cards ALTER COLUMN card_id TYPE TEXT;')
                    except: pass
                    
                    cur.execute('CREATE TABLE IF NOT EXISTS logs (id SERIAL PRIMARY KEY, user_id TEXT, "action" TEXT, details TEXT, "timestamp" BIGINT);')
                    # Migrations for logs
                    try: cur.execute('ALTER TABLE logs ADD COLUMN IF NOT EXISTS "action" TEXT DEFAULT \'unknown\';')
                    except: pass
                    try: cur.execute('ALTER TABLE logs ALTER COLUMN "action" TYPE TEXT;')
                    except: pass
                    cur.execute("CREATE TABLE IF NOT EXISTS profiles (user_id TEXT PRIMARY KEY, about TEXT DEFAULT '', fav_card_id TEXT);")
                    cur.execute("CREATE TABLE IF NOT EXISTS reminders (user_id TEXT, command TEXT, end_time BIGINT, channel_id TEXT, PRIMARY KEY (user_id, command));")
                    cur.execute("CREATE TABLE IF NOT EXISTS reminder_settings (user_id TEXT, command TEXT, enabled BOOLEAN DEFAULT TRUE, PRIMARY KEY (user_id, command));")
                    cur.execute("CREATE TABLE IF NOT EXISTS active_events (event_name TEXT PRIMARY KEY);")
        except Exception as e:
            print(f"[DB SETUP ERROR] {e}")
        finally:
            db_pool.putconn(conn)
    
    await asyncio.to_thread(_setup)

# -------------------------------
# AUTOCOMPLETES
# -------------------------------
async def group_autocomplete(interaction: discord.Interaction, current: str):
    results = await run_query("SELECT DISTINCT group_name FROM cards WHERE group_name ILIKE %s LIMIT 25", (f"%{current}%",), fetchall=True) or []
    return [app_commands.Choice(name=r[0], value=r[0]) for r in results]

async def era_autocomplete(interaction: discord.Interaction, current: str):
    results = await run_query("SELECT DISTINCT era FROM cards WHERE era ILIKE %s LIMIT 25", (f"%{current}%",), fetchall=True) or []
    return [app_commands.Choice(name=r[0], value=r[0]) for r in results]

async def category_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=c, value=c) for c in ["regular", "spec", "limited", "custom"] if current.lower() in c.lower()]

async def card_id_autocomplete(interaction: discord.Interaction, current: str):
    results = await run_query("SELECT card_id FROM cards WHERE card_id ILIKE %s LIMIT 25", (f"%{current}%",), fetchall=True) or []
    return [app_commands.Choice(name=r[0], value=r[0]) for r in results]

async def event_autocomplete(interaction: discord.Interaction, current: str):
    # Fetch active events plus some history or just active ones
    results = await run_query("SELECT DISTINCT event_name FROM active_events WHERE event_name ILIKE %s LIMIT 25", (f"%{current}%",), fetchall=True) or []
    return [app_commands.Choice(name=r[0], value=r[0]) for r in results]

# -------------------------------
# IMAGES
# -------------------------------
async def get_image(url: str, session: aiohttp.ClientSession):
    if not isinstance(url, str) or not url.startswith("http"):
        return None
    try:
        # Added User-Agent to prevent websites from blocking the request
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        async with session.get(url, timeout=10, headers=headers) as resp:
            if resp.status != 200: 
                return None
            data = await resp.read()
            if not data:
                return None
            
            # Use BytesIO to load image safely
            img_data = BytesIO(data)
            try:
                img = Image.open(img_data)
                img.load()
                # Create a resized copy in RGBA to ensure compatibility
                return img.convert("RGBA").resize((300, 400))
            except:
                return None
    except Exception as e:
        print(f"⚠️ Image Load Error: {e} for URL: {url}")
        return None

async def create_card_grid(image_urls, session):
    """
    Creates a 1x3 grid of cards with significant spacing. 
    image_urls should contain 3 entries (URLs or None).
    """
    # Use return_exceptions=True to prevent one bad image from crashing the whole drop
    raw_images = await asyncio.gather(*(get_image(url, session) for url in image_urls), return_exceptions=True)
    
    images = []
    for res in raw_images:
        # If it's an exception object or None, use placeholder
        if isinstance(res, Exception) or res is None: 
            placeholder = Image.new("RGBA", (300, 400), (40, 40, 40, 255))
            images.append(placeholder)
        else:
            images.append(res)
            
    # Always ensure we have at least 1 image to avoid math errors
    if not images:
        images = [Image.new("RGBA", (300, 400), (40, 40, 40, 255))]

    # Wider spacing: 300px width + 50px gap
    slot_width = 350
    total_width = len(images) * slot_width
    grid = Image.new("RGBA", (total_width, 400), (0, 0, 0, 0))
    
    for i, img in enumerate(images): 
        # Paste centered in the slot
        grid.paste(img, (i * slot_width, 0))
        
    buffer = BytesIO()
    grid.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

class DropView(discord.ui.View):
    def __init__(self, user_id, cards):
        super().__init__(timeout=30)
        self.user_id = str(user_id)
        self.cards = cards
        self.claimed = False
        self.message = None

    async def pick(self, interaction: discord.Interaction, index: int):
        if str(interaction.user.id) != self.user_id: 
            return await interaction.response.send_message(f"❌ This isn't your drop!", ephemeral=True)
        if self.claimed: 
            return await interaction.response.send_message(f"❌ Already picked a card!", ephemeral=True)
        
        # Defer immediately to prevent "Interaction failed" timeouts
        await interaction.response.defer()
        
        try:
            self.claimed = True
            c = self.cards[index]
            
            # 1. Add to inventory
            success = await run_query('INSERT INTO inventory (user_id, card_id, "name", "era", group_name, "rarity") VALUES (%s, %s, %s, %s, %s, %s)', 
                                (self.user_id, c[0], c[1], c[2], c[3], c[4]))
            
            if not success:
                raise Exception("Database failed to save card.")
            
            # 2. Get stats
            res_copies = await run_query("SELECT COUNT(*) FROM inventory WHERE user_id=%s AND card_id=%s", (self.user_id, c[0]), fetchone=True)
            copies = res_copies[0] if res_copies else 1
            
            progress = (0, 0)
            try:
                progress = await run_query("SELECT COUNT(DISTINCT card_id), (SELECT COUNT(*) FROM cards WHERE group_name=%s) FROM inventory WHERE user_id=%s AND group_name=%s", 
                                     (c[3], self.user_id, c[3]), fetchone=True) or (0,0)
            except: pass

            # 3. Build Result Embed
            desc = f"{interaction.user.mention} claimed card **#{index+1}**!\n\n"
            for i, card in enumerate(self.cards):
                r_val = int(card[4]) if card[4] is not None else 1
                if i == index:
                    desc += f"{BUTTON} **#{i+1} • `{card[0]}`**\n{SPIRAL} **{card[1]}** ({card[2]})\n{STAR} {card[3]} | {PANG * r_val}\n"
                    desc += f"Group Progress: {progress[0]}/{progress[1]} | Copies: {copies}\n\n"
                else:
                    desc += f"**#{i+1} • `{card[0]}`**\n{SPIRAL} **{card[1]}** ({card[2]})\n{STAR} {card[3]} | {PANG * r_val}\n\n"
            
            embed = discord.Embed(title="Drop Results", description=desc.strip(), color=discord.Color.purple())
            if c[5]: embed.set_image(url=c[5])
            
            for child in self.children: 
                child.disabled = True
            
            await interaction.edit_original_response(embed=embed, view=self)
            await interaction.followup.send(f"{BUTTON} You have picked **#{index+1}**", ephemeral=True)
            await log_action(self.user_id, "drop_pick", f"Picked {c[1]} ({c[0]})")
            
        except Exception as e:
            print(f"❌ Pick Error Trace: {e}")
            await interaction.followup.send(f"⚠️ An error occurred while claiming: {e}", ephemeral=True)

    @discord.ui.button(emoji=NO1, style=discord.ButtonStyle.secondary)
    async def b1(self, i, b): await self.pick(i, 0)
    @discord.ui.button(emoji=NO2, style=discord.ButtonStyle.secondary)
    async def b2(self, i, b): await self.pick(i, 1)
    @discord.ui.button(emoji=NO3, style=discord.ButtonStyle.secondary)
    async def b3(self, i, b): await self.pick(i, 2)

    async def on_timeout(self):
        if self.claimed: return
        for child in self.children: child.disabled = True
        if self.message: 
            try:
                desc = "⌛ This drop has expired.\n\n"
                for i, card in enumerate(self.cards):
                    r_val = int(card[4]) if card[4] is not None else 1
                    desc += f"**#{i+1} • `{card[0]}`**\n{SPIRAL} **{card[1]}** ({card[2]})\n{STAR} {card[3]} | {PANG * r_val}\n\n"
                
                embed = discord.Embed(title=f"{TEA} Order Expired", description=desc.strip(), color=discord.Color.dark_red())
                await self.message.edit(embed=embed, view=self)
            except: pass

# -------------------------------
# COMMANDS
# -------------------------------

@bot.tree.command(name="balance", description="Check your balance")
async def balance(interaction: discord.Interaction):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    await ensure_user(uid)
    res = await run_query("SELECT boba, cakecoins, (SELECT COUNT(*) FROM inventory WHERE user_id=%s) FROM users WHERE user_id=%s", (uid, uid), fetchone=True)
    if not res: return await interaction.followup.send(f"❌ Error loading balance.")
    embed = discord.Embed(title=f"{ICE} Cafe Tab", description=f"{BOBA} Boba: **{res[0]}**\n{CAKE} Cakecoins: **{res[1]}**\n{CROISSANT} Cards: **{res[2]}**", color=discord.Color.orange())
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="drop", description="Get a random card")
@app_commands.describe(reminder="Turn reminder on/off")
async def drop_cmd(interaction: discord.Interaction, reminder: bool = None):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    rem_time = await get_remaining_cooldown(uid, "drop", 600)
    if rem_time > 0: 
        return await interaction.followup.send(f"{BALL} Wait {rem_time//60}m {rem_time%60}s", ephemeral=True)
    
    await ensure_user(uid)
    # Rarity 1-4 are standard. Rarity 5 are event-only.
    cards = await run_query("""
        SELECT card_id, name, era, group_name, rarity, image 
        FROM cards 
        WHERE rarity < 5 OR (rarity = 5 AND event_name IS NOT NULL AND event_name IN (SELECT event_name FROM active_events))
    """, fetchall=True)
    if not cards: return await interaction.followup.send(f"❌ No cards available in current drop pool.")
    def pick():
        pool = []
        for c in cards: pool.extend([c] * (6 - (c[4] or 1)))
        return random.choice(pool)
    selection = [pick() for _ in range(3)]
    await set_cooldown(uid, "drop", int(time.time()))
    if reminder is not None: await run_query("INSERT INTO reminder_settings (user_id, command, enabled) VALUES (%s, %s, %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, "drop", reminder, reminder))
    rem_enabled = await run_query("SELECT enabled FROM reminder_settings WHERE user_id=%s AND command=%s", (uid, "drop"), fetchone=True)
    if not rem_enabled or rem_enabled[0]: await set_reminder(uid, "drop", 600, interaction.channel_id)
    # Ensure we always pass exactly 3 items to the grid generator
    image_list = [c[5] for c in selection] # selection is guaranteed to have 3 items
    
    try:
        grid = await create_card_grid(image_list, bot.session)
        
        embed = discord.Embed(description=f"{interaction.user.mention} drops...", color=discord.Color.orange())
        embed.set_footer(text="you have 30s to claim a card!")
        embed.set_image(url="attachment://drop.png")
        view = DropView(uid, selection)
        msg = await interaction.followup.send(embed=embed, file=discord.File(grid, "drop.png"), view=view)
        view.message = msg
    except Exception as e:
        print(f"❌ Drop Error: {e}")
        await interaction.followup.send(f"⚠️ An error occurred while generating the drop images: {e}")
    await log_action(uid, "drop", "Generated 3-card drop")

@bot.tree.command(name="inventory", description="View your inventory")
@app_commands.describe(
    user="User to view", 
    name="Filter by card name", 
    group="Filter by group", 
    era="Filter by era", 
    rarity="Filter by rarity (1-5)",
    card_id="Filter by card ID"
)
async def inventory(interaction: discord.Interaction, user: discord.Member = None, name: str = None, group: str = None, era: str = None, rarity: int = None, card_id: str = None):
    target = user or interaction.user
    uid = str(target.id)
    await interaction.response.defer()
    data = await run_query('SELECT card_id, "name", "era", group_name, "rarity", COUNT(*) FROM inventory WHERE user_id=%s GROUP BY card_id, "name", "era", group_name, "rarity" ORDER BY "name"', (uid,), fetchall=True)
    if data is None: return await interaction.followup.send("❌ Error connecting to database.", ephemeral=True)
    if not data: return await interaction.followup.send("❌ Empty inventory.", ephemeral=True)
    
    if any([name, group, era, rarity, card_id]):
        if card_id: data = [c for c in data if card_id.lower() in (c[0] or "").lower()]
        if name: data = [c for c in data if name.lower() in (c[1] or "").lower()]
        if era: data = [c for c in data if era.lower() in (c[2] or "").lower()]
        if group: data = [c for c in data if group.lower() in (c[3] or "").lower()]
        if rarity: data = [c for c in data if c[4] == rarity]
    if not data: return await interaction.followup.send("❌ No items match your filters.", ephemeral=True)
    pages = [data[i:i+5] for i in range(0, len(data), 5)]
    class InvView(View):
        def __init__(self): super().__init__(timeout=120); self.p = 0
        def get_embed(self):
            e = discord.Embed(title=f"{TEA} {target.name}'s Collection ({self.p+1}/{len(pages)})", color=discord.Color.orange())
            for c in pages[self.p]: 
                r_val = int(c[4]) if c[4] is not None else 1
                e.add_field(name=f"{c[1]} ({c[0]})", value=f"{SPIRAL} {c[2]} | {STAR} {c[3]}\n{PANG*r_val} | Copies: {c[5]}", inline=False)
            return e
        @discord.ui.button(emoji=LEFT)
        async def prev(self, i, b): 
            if self.p > 0: self.p -= 1; await i.response.edit_message(embed=self.get_embed(), view=self)
        @discord.ui.button(emoji=RIGHT)
        async def next(self, i, b): 
            if self.p < len(pages)-1: self.p += 1; await i.response.edit_message(embed=self.get_embed(), view=self)
    v = InvView()
    await interaction.followup.send(embed=v.get_embed(), view=v)

@bot.tree.command(name="daily", description="Claim daily rewards")
@app_commands.describe(reminder="Turn reminder on/off")
async def daily_cmd(interaction: discord.Interaction, reminder: bool = None):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    rem = await get_remaining_cooldown(uid, "daily", 86400)
    if rem > 0: 
        return await interaction.followup.send(f"{ICE} Claim in {rem//3600}h {(rem%3600)//60}m", ephemeral=True)
    
    await ensure_user(uid)
    await run_query("UPDATE users SET boba=boba+2000, cakecoins=cakecoins+10 WHERE user_id=%s", (uid,))
    await set_cooldown(uid, "daily", int(time.time()))
    if reminder is not None: await run_query("INSERT INTO reminder_settings (user_id, command, enabled) VALUES (%s, %s, %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, "daily", reminder, reminder))
    rem_enabled = await run_query("SELECT enabled FROM reminder_settings WHERE user_id=%s AND command=%s", (uid, "daily"), fetchone=True)
    if not rem_enabled or rem_enabled[0]: await set_reminder(uid, "daily", 86400, interaction.channel_id)
    e = discord.Embed(
        title=f"{BUTTON} Daily Special Claimed!", 
        description=f"Here is your freshly brewed morning special! Enjoy your visit today.\n\n{BOBA} +2000 | {CAKE} +10", 
        color=discord.Color.green()
    )
    e.set_image(url="https://media2.giphy.com/media/v1.Y2lkPTZjMDliOTUydmlrODh6YXlxcWI4dGhhbXl3czZpejVmZzVnOXEydDN2dmswdmM5aSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/uKKSAhC0gb5roHsy9v/giphy.gif")
    await interaction.followup.send(embed=e)
    await log_action(uid, "daily", "+2000 boba, +10 cakecoins")

@bot.tree.command(name="weekly", description="Claim weekly rewards")
@app_commands.describe(reminder="Turn reminder on/off")
async def weekly_cmd(interaction: discord.Interaction, reminder: bool = None):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    rem = await get_remaining_cooldown(uid, "weekly", 604800)
    if rem > 0: 
        return await interaction.followup.send(f"{ICE} Claim in {rem//86400}d {(rem%86400)//3600}h", ephemeral=True)
    
    await ensure_user(uid)
    await run_query("UPDATE users SET boba=boba+5000, cakecoins=cakecoins+50 WHERE user_id=%s", (uid,))
    await set_cooldown(uid, "weekly", int(time.time()))
    if reminder is not None: await run_query("INSERT INTO reminder_settings (user_id, command, enabled) VALUES (%s, %s, %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, "weekly", reminder, reminder))
    rem_enabled = await run_query("SELECT enabled FROM reminder_settings WHERE user_id=%s AND command=%s", (uid, "weekly"), fetchone=True)
    if not rem_enabled or rem_enabled[0]: await set_reminder(uid, "weekly", 604800, interaction.channel_id)
    e = discord.Embed(
        title=f"{BUTTON} Weekly Gourmet Rewarded!", 
        description=f"A true regular deserves a gourmet feast. Our chef prepared this just for you!\n\n{BOBA} +5000 | {CAKE} +50", 
        color=discord.Color.gold()
    )
    e.set_image(url="https://media0.giphy.com/media/v1.Y2lkPTZjMDliOTUyY2xhcHA5cDM1aWhkcGl5MDR1MzY1bmZuNGF6aXMxeWl0dTM0ODNjMyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/5wKuwXycuNfl0VEOgI/giphy.gif")
    await interaction.followup.send(embed=e)
    await log_action(uid, "weekly", "+5000 boba, +50 cakecoins")

@bot.tree.command(name="bake", description="Bake rewards")
@app_commands.describe(reminder="Turn reminder on/off")
async def bake_cmd(interaction: discord.Interaction, reminder: bool = None):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    rem = await get_remaining_cooldown(uid, "bake", 3600)
    if rem > 0: 
        return await interaction.followup.send(f"{ICE} Wait {rem//3600}h {(rem%3600)//60}m", ephemeral=True)
    
    await ensure_user(uid)
    b, c = random.randint(200,800), random.randint(1,5)
    await run_query("UPDATE users SET boba=boba+%s, cakecoins=cakecoins+%s WHERE user_id=%s", (b, c, uid))
    await set_cooldown(uid, "bake", int(time.time()))
    if reminder is not None: await run_query("INSERT INTO reminder_settings (user_id, command, enabled) VALUES (%s, %s, %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, "bake", reminder, reminder))
    rem_enabled = await run_query("SELECT enabled FROM reminder_settings WHERE user_id=%s AND command=%s", (uid, "bake"), fetchone=True)
    if not rem_enabled or rem_enabled[0]: await set_reminder(uid, "bake", 3600, interaction.channel_id)
    e = discord.Embed(
        title=f"{CROISSANT} Baking Complete!", 
        description=f"The aroma of hot pastries fills the air. Refreshing and sweet!\n\n{BOBA} +{b} | {CAKE} +{c}", 
        color=discord.Color.pink()
    )
    e.set_image(url="https://media4.giphy.com/media/v1.Y2lkPTZjMDliOTUyZGZnMDcwM2o3Zmp6Y2tndHFweHZydTZtMmU1MzE2bHBrc201cjJlZiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/LMuPuB2jQkmgX59vWX/giphy.gif")
    await interaction.followup.send(embed=e)
    await log_action(uid, "bake", f"+{b} boba, +{c} cakecoins")

@bot.tree.command(name="cooldown", description="Check cooldowns")
async def cooldown_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    e = discord.Embed(title=f"{TEA} Kitchen Timers", color=discord.Color.blue())
    
    # Optimize: Fetch all relevant cooldowns and reminder settings in one turn
    res = await run_query("SELECT command, last_used FROM cooldowns WHERE user_id=%s", (uid,), fetchall=True)
    if res is None: return await interaction.followup.send("❌ Error connecting to database.", ephemeral=True)
    last_used = {row[0]: row[1] for row in res}
    
    rem_res = await run_query("SELECT command, enabled FROM reminder_settings WHERE user_id=%s", (uid,), fetchall=True) or []
    rem_settings = {row[0]: row[1] for row in rem_res}
    
    now = int(time.time())
    cmds = {"drop": 600, "bake": 3600, "daily": 86400, "weekly": 604800}
    
    for cmd, sec in cmds.items():
        last = last_used.get(cmd, 0)
        rem = max(0, sec - (now - last))
        is_rem_on = rem_settings.get(cmd, True) # Default to true as per schema
        
        rem_status = "🔔 On" if is_rem_on else "🔕 Off"
        
        if rem <= 0:
            val = f"{BUTTON} **Ready**\n{rem_status}"
        else:
            d = rem // 86400
            h = (rem % 86400) // 3600
            m = (rem % 3600) // 60
            s = rem % 60
            
            time_str = ""
            if d > 0:
                time_str = f"{d}d {h}h {m}m"
            elif h > 0:
                time_str = f"{h}h {m}m"
            else:
                time_str = f"{m}m {s}s"
            
            val = f"{ICE} **{time_str}**\n{rem_status}"
        
        e.add_field(name=f"/{cmd}", value=val, inline=True)
    
    await interaction.followup.send(embed=e)

@bot.tree.command(name="view", description="Enlarged view of a card by ID")
@app_commands.describe(card_id="Card ID to view")
async def view_cmd(interaction: discord.Interaction, card_id: str):
    await interaction.response.defer()
    cid = card_id.strip()
    c = await run_query('SELECT "name", "era", group_name, "rarity", image, category, event_name FROM cards WHERE card_id=%s', (cid,), fetchone=True)
    if not c: return await interaction.followup.send(f"❌ Card `{cid}` not found in the registry.")
    
    r_val = int(c[3]) if c[3] is not None else 1
    e = discord.Embed(title=f"{TEA} Menu Item: {c[0]}", description=f"`{cid}`", color=discord.Color.orange())
    e.add_field(name=f"{SPIRAL} Era", value=c[1], inline=True)
    e.add_field(name=f"{STAR} Group", value=c[2], inline=True)
    e.add_field(name="Rating", value=PANG * r_val, inline=True)
    e.add_field(name=f"{CHOCOLATE} Category", value=c[5].title(), inline=True)
    if c[6]: e.add_field(name=f"{PANCAKE} Event", value=c[6], inline=True)
    
    if c[4]: e.set_image(url=c[4])
    await interaction.followup.send(embed=e)

view_cmd.autocomplete("card_id")(card_id_autocomplete)

@bot.tree.command(name="manage", description="Admin: Add or remove currency/cards for a user")
@app_commands.describe(
    user="The user to manage",
    action="Whether to add or remove",
    item_type="What to manage (Boba, Cakecoins, or Card)",
    amount="Amount of currency (Only used for Boba/Cakecoins)",
    card_id="The ID of the card (Only used for Card)",
    copies="Number of copies of the card (Only used for Card)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="Add", value="add"),
    app_commands.Choice(name="Remove", value="remove")
], item_type=[
    app_commands.Choice(name="Boba", value="boba"),
    app_commands.Choice(name="Cakecoins", value="cakecoins"),
    app_commands.Choice(name="Card", value="card")
])
async def manage_cmd(interaction: discord.Interaction, user: discord.Member, action: app_commands.Choice[str], item_type: app_commands.Choice[str], amount: int = None, card_id: str = None, copies: int = 1):
    if not check_is_manager(interaction): 
        return await interaction.response.send_message("❌ Permission denied. Only Mods can use this command.", ephemeral=True)
    
    await interaction.response.defer()
    
    tid = str(user.id)
    await ensure_user(tid)
    act = action.value
    itype = item_type.value

    embed = discord.Embed(title=f"{TEA} Kitchen Management", color=discord.Color.blue())

    if itype in ["boba", "cakecoins"]:
        if amount is None or amount <= 0:
            return await interaction.followup.send("❌ Please provide a valid amount to management!", ephemeral=True)
        
        op = "+" if act == "add" else "-"
        success = await run_query(f"UPDATE users SET {itype} = GREATEST({itype} {op} %s, 0) WHERE user_id=%s", (amount, tid))
        if not success: return await interaction.followup.send("❌ Database error while updating balance.", ephemeral=True)
        
        emoji = BOBA if itype == "boba" else CAKE
        if act == "add":
            embed.description = f"{PANCAKE} **Freshly brewed!** Added `{amount}` {emoji} {itype} to {user.mention}'s tab."
        else:
            embed.description = f"{PUDDING} **Order canceled!** Removed `{amount}` {emoji} {itype} from {user.mention}'s tab."
            
        await interaction.followup.send(content=user.mention, embed=embed)
        
    elif itype == "card":
        if not card_id:
            return await interaction.followup.send("❌ Please provide a Card ID!", ephemeral=True)
        
        card = await run_query("SELECT card_id, name, era, group_name, rarity FROM cards WHERE card_id=%s", (card_id,), fetchone=True)
        if not card:
            return await interaction.followup.send(f"❌ Card `{card_id}` not found in the registry!", ephemeral=True)
        
        c_id, c_name, c_era, c_group, c_rarity = card
        display_rarity = int(c_rarity) if c_rarity is not None else 1

        if act == "add":
            if copies <= 0:
                return await interaction.followup.send("❌ Copies must be at least 1.", ephemeral=True)
            
            # Batch insert for performance and reliability
            values_template = "(%s,%s,%s,%s,%s,%s)"
            query = f'INSERT INTO inventory (user_id, card_id, "name", "era", group_name, "rarity") VALUES {",".join([values_template] * copies)}'
            params = []
            for _ in range(copies):
                params.extend([tid, c_id, c_name, c_era, c_group, c_rarity])
            
            success = await run_query(query, tuple(params))
            if not success:
                return await interaction.followup.send("❌ Database error while adding cards.", ephemeral=True)
                
            embed.description = f"{CROISSANT} **Special Delivery!** Added {copies}x **{c_name}** {PANG*display_rarity} to {user.mention}'s collection."
        else:
            success = await run_query('DELETE FROM inventory WHERE id IN (SELECT id FROM inventory WHERE user_id=%s AND card_id=%s LIMIT %s)', (tid, card_id, copies))
            if not success:
                return await interaction.followup.send("❌ Database error while removing cards.", ephemeral=True)
            embed.description = f"{CHOCOLATE} **Inventory Cleanup!** Removed {copies}x **{c_name}** {PANG*display_rarity} from {user.mention}'s collection."
        
        await interaction.followup.send(content=user.mention, embed=embed)

    await log_action(str(interaction.user.id), "manage", f"{act.upper()} {itype.upper()} for {tid} (Amount/Copies: {amount or copies})")

@bot.tree.command(name="logs", description="Admin: View logs")
async def logs_cmd(interaction: discord.Interaction, user: discord.User = None, action: str = None):
    if interaction.guild_id not in GUILD_IDS: 
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    q, p = 'SELECT user_id, "action", details, "timestamp" FROM logs', []
    if user or action:
        q += " WHERE "
        if user: q += "user_id=%s"; p.append(str(user.id))
        if user and action: q += " AND "
        if action: q += '"action"=%s'; p.append(action.lower())
    q += ' ORDER BY id DESC LIMIT 15'
    data = await run_query(q, tuple(p), fetchall=True)
    if data is None: 
        return await interaction.followup.send("❌ Error loading logs.", ephemeral=True)
    if not data: 
        return await interaction.followup.send("❌ No activity history found.", ephemeral=True)
    
    e = discord.Embed(title=f"{TEA} Order History", color=discord.Color.dark_blue())
    for u, a, d, t in data: 
        e.add_field(name=f"{a.upper()} | {u}", value=f"{d}\n<t:{t}:R>", inline=False)
    
    await interaction.followup.send(embed=e, ephemeral=True)

@bot.tree.command(name="pay", description="Pay another user")
async def pay_cmd(interaction: discord.Interaction, user: discord.Member, amount: int, currency: str):
    await interaction.response.defer()
    sid, rid, cur = str(interaction.user.id), str(user.id), currency.lower()
    if amount <= 0 or sid == rid or cur not in ["boba", "cakecoins"]: return await interaction.followup.send("❌ Invalid pay.")
    await ensure_user(sid); await ensure_user(rid)
    res_bal = await run_query(f"SELECT {cur} FROM users WHERE user_id=%s", (sid,), fetchone=True)
    bal = res_bal[0] if res_bal else 0
    if bal < amount: return await interaction.followup.send("❌ Insufficient balance.")
    await run_query(f"UPDATE users SET {cur} = {cur} - %s WHERE user_id=%s", (amount, sid))
    await run_query(f"UPDATE users SET {cur} = {cur} + %s WHERE user_id=%s", (amount, rid))
    
    emoji = BOBA if cur == "boba" else CAKE
    e = discord.Embed(title=f"{PUDDING} Paid!", description=f"{interaction.user.mention} sent **{amount} {emoji} {cur}** to {user.mention}", color=discord.Color.green())
    await interaction.followup.send(content=f"{interaction.user.mention} 💸 {user.mention}", embed=e)
    await log_action(sid, "pay", f"Sent {amount} {cur} to {rid}")

@bot.tree.command(name="menu", description="View all cards")
@app_commands.describe(
    name="Filter by name", 
    group="Filter by group", 
    era="Filter by era", 
    rarity="Filter by rarity", 
    category="Filter by category",
    card_id="Filter by card ID"
)
async def menu_cmd(interaction: discord.Interaction, name: str = None, group: str = None, era: str = None, rarity: int = None, category: str = None, card_id: str = None):
    await interaction.response.defer()
    cards = await run_query('SELECT card_id, "name", "era", group_name, "rarity", category, image FROM cards ORDER BY "rarity" DESC', fetchall=True)
    if cards is None: return await interaction.followup.send("❌ Error connecting to database.", ephemeral=True)
    if not cards: return await interaction.followup.send("❌ Empty card database.")
    
    data = cards
    if any([name, group, era, rarity, category, card_id]):
        if card_id: data = [c for c in data if card_id.lower() in (c[0] or "").lower()]
        if name: data = [c for c in data if name.lower() in (c[1] or "").lower()]
        if era: data = [c for c in data if era.lower() in (c[2] or "").lower()]
        if group: data = [c for c in data if group.lower() in (c[3] or "").lower()]
        if rarity: data = [c for c in data if c[4] == rarity]
        if category: data = [c for c in data if category.lower() in (c[5] or "").lower()]
    
    if not data: return await interaction.followup.send("❌ No items match filters.")
    pages = [data[i:i+5] for i in range(0, len(data), 5)]
    class MenuView(View):
        def __init__(self): super().__init__(timeout=120); self.p = 0
        def get_e(self):
            e = discord.Embed(title=f"{TEA} Cafe Menu ({self.p+1}/{len(pages)})", color=discord.Color.orange())
            for c in pages[self.p]: 
                r_val = int(c[4]) if c[4] is not None else 1
                e.add_field(name=f"{c[1]} ({c[0]})", value=f"{SPIRAL} {c[2]} | {STAR} {c[3]}\n{PANG*r_val}\n{CHOCOLATE} {c[5]}", inline=False)
            return e
        @discord.ui.button(emoji=LEFT)
        async def prev(self, i, b): 
            if self.p > 0: self.p -= 1; await i.response.edit_message(embed=self.get_e(), view=self)
        @discord.ui.button(emoji=RIGHT)
        async def next(self, i, b): 
            if self.p < len(pages)-1: self.p += 1; await i.response.edit_message(embed=self.get_e(), view=self)
    v = MenuView()
    await interaction.followup.send(embed=v.get_e(), view=v)

@bot.tree.command(name="giftcard", description="Gift up to 5 cards")
async def giftcard_cmd(interaction: discord.Interaction, user: discord.User, card1: str, amount1: int, card2: str = None, amount2: int = None, card3: str = None, amount3: int = None, card4: str = None, amount4: int = None, card5: str = None, amount5: int = None):
    sid, rid = str(interaction.user.id), str(user.id)
    if sid == rid: return await interaction.response.send_message("❌ Cannot gift cards to yourself.", ephemeral=True)
    await interaction.response.defer()
    gifts = [(c, a) for c, a in [(card1, amount1), (card2, amount2), (card3, amount3), (card4, amount4), (card5, amount5)] if c and a]
    if not gifts or len(set(c for c, a in gifts)) != len(gifts): return await interaction.followup.send("❌ Invalid card gift list.")
    await ensure_user(sid); await ensure_user(rid)
    sumry = []
    for cid, amt in gifts:
        # Get specific IDs to transfer
        owned = await run_query('SELECT id, "name", "rarity", "era", group_name FROM inventory WHERE user_id=%s AND card_id=%s LIMIT %s', (sid, cid, amt), fetchall=True)
        if not owned or len(owned) < amt: 
            return await interaction.followup.send(f"❌ You do not have enough copies of {cid}.")
        
        ids_to_del = [row[0] for row in owned]
        c_name, c_rarity, c_era, c_group = owned[0][1], owned[0][2], owned[0][3], owned[0][4]
        
        # Batch Move: Delete from Sender, Add to Receiver
        del_success = await run_query("DELETE FROM inventory WHERE id = ANY(%s)", (ids_to_del,))
        if not del_success:
            return await interaction.followup.send(f"❌ Database error while processing gift for {cid}.")
        
        values_template = "(%s,%s,%s,%s,%s,%s)"
        query = f'INSERT INTO inventory (user_id, card_id, "name", "era", group_name, "rarity") VALUES {",".join([values_template] * amt)}'
        parms = []
        for _ in range(amt):
            parms.extend([rid, cid, c_name, c_era, c_group, c_rarity])
        
        ins_success = await run_query(query, tuple(parms))
        if not ins_success:
            # Note: In a production app you'd want a transaction here to rollback the delete
            return await interaction.followup.send(f"❌ Database error while delivering gift for {cid}.")
            
        display_rarity = int(c_rarity) if c_rarity is not None else 1
        sumry.append(f"{amt}x {c_name} {PANG*display_rarity}")
    e = discord.Embed(title=f"{CHOCOLATE} Gift Sent!", description="\n".join(sumry), color=discord.Color.green())
    await interaction.followup.send(content=user.mention, embed=e)
    await log_action(sid, "giftcard", f"Gifted {rid}: {sumry}")

@bot.tree.command(name="ping", description="Check bot latency")
async def ping_cmd(interaction: discord.Interaction): await interaction.response.send_message(f"{BALL} Pong! ({round(bot.latency * 1000)}ms)")

@bot.tree.command(name="profile", description="View user profile")
async def profile_cmd(interaction: discord.Interaction, user: discord.Member = None):
    await interaction.response.defer()
    t = user or interaction.user; uid = str(t.id); await ensure_user(uid)
    res = await run_query("SELECT boba, cakecoins, (SELECT COUNT(*) FROM inventory WHERE user_id=%s) FROM users WHERE user_id=%s", (uid, uid), fetchone=True)
    p = await run_query("SELECT about, fav_card_id FROM profiles WHERE user_id=%s", (uid,), fetchone=True)
    if not res: return await interaction.followup.send("❌ Error loading profile.")
    e = discord.Embed(title=f"{TEA} Patron Profile: {t.name}", color=discord.Color.purple())
    e.add_field(name=f"{BOBA} Boba Tab", value=str(res[0]))
    e.add_field(name=f"{CAKE} Cakecoins", value=str(res[1]))
    e.add_field(name=f"{BUTTON} Menu Items Collected", value=str(res[2]), inline=False)
    e.add_field(name=f"{PANCAKE} About the Patron", value=p[0] if p and p[0] else "No description set.", inline=False)
    if p and p[1]:
        f = await run_query('SELECT "name", "era", group_name, "rarity" FROM inventory WHERE user_id=%s AND card_id=%s LIMIT 1', (uid, p[1]), fetchone=True)
        if f: 
            r_val = int(f[3]) if f[3] is not None else 1
            e.add_field(name=f"{PUDDING} Signature Selection", value=f"**{f[0]}** ({p[1]})\n{SPIRAL} {f[1]} | {STAR} {f[2]}\n{PANG*r_val}", inline=False)
    await interaction.followup.send(embed=e)

@bot.tree.command(name="setabout", description="Change profile about text")
async def setabout_cmd(interaction: discord.Interaction, text: str):
    if len(text) > 300: return await interaction.response.send_message("❌ Description too long (max 300 chars).", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id); await ensure_user(uid)
    await run_query("INSERT INTO profiles (user_id, about) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET about=%s", (uid, text, text))
    await interaction.followup.send(f"{BUTTON} Your profile about has been updated!", ephemeral=True)

@bot.tree.command(name="setfav", description="Select your favourite card")
async def setfav_cmd(interaction: discord.Interaction, card_id: str):
    uid, cid = str(interaction.user.id), card_id.strip()
    await interaction.response.defer(ephemeral=True)
    owned = await run_query("SELECT 1 FROM inventory WHERE user_id=%s AND card_id=%s LIMIT 1", (uid, cid), fetchone=True)
    if not owned: return await interaction.followup.send("❌ You do not own this card.", ephemeral=True)
    await run_query("INSERT INTO profiles (user_id, fav_card_id) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET fav_card_id=%s", (uid, cid, cid))
    await interaction.followup.send(f"{BUTTON} Set `{cid}` as your favourite card!", ephemeral=True)

@bot.tree.command(name="addcard", description="Manager: Register a new card")
@app_commands.describe(
    card_id="Unique ID for the card",
    name="Name of the character/idol",
    group="Group name (Autocomplete enabled)",
    era="Era name (Autocomplete enabled)",
    rarity="Rarity 1-5",
    category="Type of card (Autocomplete enabled)",
    event="Event name for rarity 5/spec cards (Autocomplete enabled)",
    image_url="A direct link to an image (Optional if uploading file)",
    image_file="Upload an image directly from your device (Optional)"
)
async def addcard_cmd(interaction: discord.Interaction, card_id: str, name: str, group: str, era: str, rarity: int, category: str, event: str = None, image_url: str = None, image_file: discord.Attachment = None):
    if not check_is_manager(interaction): return await interaction.response.send_message("❌ Permission denied. Only Mods can add cards.", ephemeral=True)
    if rarity < 1 or rarity > 5: return await interaction.response.send_message("❌ Rarity must be 1-5.", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    # Use uploaded file URL if provided, otherwise use text URL
    final_image = None
    if image_file:
        final_image = image_file.url
    elif image_url:
        final_image = image_url
    
    if not final_image:
        return await interaction.followup.send("❌ You must provide an image URL or upload an image file!")

    await run_query("""
        INSERT INTO cards (card_id, name, era, group_name, rarity, image, category, event_name) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) 
        ON CONFLICT (card_id) DO UPDATE SET 
        name=%s, era=%s, group_name=%s, rarity=%s, image=%s, category=%s, event_name=%s
    """, (card_id, name, era, group, rarity, final_image, category.lower(), event, name, era, group, rarity, final_image, category.lower(), event))
    
    await log_action(str(interaction.user.id), "addcard", f"Added/Updated Card ID: {card_id}")
    await interaction.followup.send(f"{BUTTON} Card ID `{card_id}` added to system with image!")

# Autocomplete setup
addcard_cmd.autocomplete("group")(group_autocomplete)
addcard_cmd.autocomplete("era")(era_autocomplete)
addcard_cmd.autocomplete("category")(category_autocomplete)
addcard_cmd.autocomplete("event")(event_autocomplete)

@bot.tree.command(name="bulkadd", description="Manager: Upload cards using a CSV file")
@app_commands.describe(file="CSV file with columns: card_id, name, group_name, era, rarity, category, image_url, event_name")
async def bulkadd_cmd(interaction: discord.Interaction, file: discord.Attachment):
    if not check_is_manager(interaction): return await interaction.response.send_message("❌ Permission denied. Only Mods can bulk add.", ephemeral=True)
    if not file.filename.endswith('.csv'): return await interaction.response.send_message("❌ Please upload a .csv file.", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        content = await file.read()
        stream = StringIO(content.decode('utf-8'))
        reader = csv.reader(stream)
        
        # Skip header if it exists
        header = next(reader, None)
        
        count = 0
        for row in reader:
            if len(row) < 7: continue
            cid, name, group, era, rarity, cat, img = row[:7]
            event = row[7] if len(row) > 7 else None
            await run_query("""
                INSERT INTO cards (card_id, name, era, group_name, rarity, image, category, event_name) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) 
                ON CONFLICT (card_id) DO UPDATE SET 
                name=%s, era=%s, group_name=%s, rarity=%s, image=%s, category=%s, event_name=%s
            """, (cid, name, era, group, int(rarity), img, cat.lower(), event, name, era, group, int(rarity), img, cat.lower(), event))
            count += 1
            
        await interaction.followup.send(f"{BUTTON} Successfully added/updated {count} cards!")
        await log_action(str(interaction.user.id), "bulkadd", f"Uploaded bulk CSV with {count} cards.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error processing CSV: {str(e)}")

@bot.tree.command(name="deletecard", description="Manager: Remove a card registry")
async def deletecard_cmd(interaction: discord.Interaction, card_id: str):
    if not check_is_manager(interaction): return await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    await run_query("DELETE FROM cards WHERE card_id=%s", (card_id,))
    await interaction.followup.send(f"{BUTTON} Card registry `{card_id}` cleared.", ephemeral=True)

@bot.tree.command(name="startevent", description="Admin: Enable a special phase")
async def startevent(interaction: discord.Interaction, event: str):
    if not interaction.user.guild_permissions.administrator: return await interaction.response.send_message("❌ Admin only command.", ephemeral=True)
    await interaction.response.defer()
    await run_query("INSERT INTO active_events (event_name) VALUES (%s) ON CONFLICT DO NOTHING", (event,))
    await interaction.followup.send(f"{BUTTON} Phase `{event}` is now active!")

@bot.tree.command(name="endevent", description="Admin: Stop a special phase")
async def endevent(interaction: discord.Interaction, event: str):
    if not interaction.user.guild_permissions.administrator: return await interaction.response.send_message("❌ Admin only command.", ephemeral=True)
    await interaction.response.defer()
    await run_query("DELETE FROM active_events WHERE event_name=%s", (event,))
    await interaction.followup.send(f"{BUTTON} Phase `{event}` has ended.")

@bot.tree.command(name="events", description="View all currently active phases")
async def events_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    evs = await run_query("SELECT event_name FROM active_events", fetchall=True)
    await interaction.followup.send(f"🎪 Active Phases: {', '.join(e[0] for e in evs) if evs else 'None'}")

@bot.tree.command(name="sync", description="Manager: Refresh command registry")
async def sync_cmd(interaction: discord.Interaction):
    if not check_is_manager(interaction): return await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    await bot.tree.sync()
    await interaction.followup.send(f"{BUTTON} Command cloud synced!", ephemeral=True)

# -------------------------------
# EXECUTION
# -------------------------------
if __name__ == "__main__":
    if not TOKEN:
        print("❌ Error: TOKEN environment variable is missing.")
    else:
        bot.run(TOKEN)
