import discord
from discord.ext import commands
from discord import app_commands
import functools
import os
import string
import random
import time
import datetime
import asyncio
import uuid
import re
import csv
import io
import aiohttp
from PIL import Image, ImageOps, ImageDraw, ImageFont
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
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

ALLOWED_USERS = [1322126091929915454]
DISABLED_COMMANDS = {}
LAST_DISABLED_LOAD = 0.0

async def get_disabled_commands():
    global LAST_DISABLED_LOAD, DISABLED_COMMANDS
    now = time.time()
    if now - LAST_DISABLED_LOAD > 10:
        try:
            rows = await run_query("SELECT command_name, disabled_text FROM disabled_commands", fetchall=True) or []
            new_map = {}
            for row in rows:
                if row and len(row) >= 2:
                    new_map[row[0].strip().lower()] = row[1]
            DISABLED_COMMANDS = new_map
            LAST_DISABLED_LOAD = now
        except Exception as e:
            print(f"⚠️ Error loading disabled commands in cache refresh: {e}")
    return DISABLED_COMMANDS

def generate_unique_code(length=4):
    """Generates a 4-6 char alphanumeric unique code."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))

async def get_valid_unique_code():
    """Generates a code and ensures it's not already in use."""
    for _ in range(10):
        code = generate_unique_code()
        exists = await run_query("SELECT 1 FROM ryo_inventory WHERE unique_code = %s", (code,), fetchone=True)
        if not exists:
            return code
    return generate_unique_code(6)

async def grant_card(user_id: str, card_id: str):
    """Safely grants a card. Guess cards get a unique code."""
    # Obtain the canonical card_id from registry with case-insensitivity
    db_card = await run_query("SELECT card_id, category, era FROM ryo_cards WHERE LOWER(card_id) = LOWER(%s)", (card_id,), fetchone=True)
    canonical_id = db_card[0] if db_card else card_id
    category_db = db_card[1] if db_card else "regular"
    era_db = db_card[2] if db_card else "regular"
    
    # Check category/era to see if it's a guess card
    is_guess = (category_db == 'guess_minigame' or era_db == 'Guess')
    
    code = None
    if is_guess:
        code = await get_valid_unique_code()

    await run_query(
        "INSERT INTO ryo_inventory (user_id, card_id, unique_code) VALUES (%s, %s, %s)",
        (str(user_id), canonical_id, code)
    )
    return code

GUILD_IDS = [
    1424877893514952776,
    1418395289173233707
]

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
GRID_WIDTH = 3
GRID_HEIGHT = 2
CARD_DRAW_WIDTH = 450
CARD_DRAW_HEIGHT = 675
GRID_GAP = 5
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
RYO_COLOR = 0x825A3C
RYO_BLUE = RYO_COLOR

# --- Global aiohttp Session ---
GLOBAL_SESSION = None

async def get_session():
    global GLOBAL_SESSION
    if GLOBAL_SESSION is None or GLOBAL_SESSION.closed:
        timeout = aiohttp.ClientTimeout(total=15)
        connector = aiohttp.TCPConnector(limit=100, keepalive_timeout=30)
        GLOBAL_SESSION = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return GLOBAL_SESSION

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

# --- Autocomplete Cache ---
# Decorator to make autocomplete safe against NotFound and HTTPException
def safe_autocomplete(coro):
    @functools.wraps(coro)
    async def wrapper(interaction: discord.Interaction, current: str):
        try:
            choices = await coro(interaction, current)
            if choices is None:
                choices = []
            if not interaction.response.is_done():
                try:
                    await interaction.response.autocomplete(choices[:25])
                except (discord.errors.NotFound, discord.errors.HTTPException, discord.errors.InteractionResponded):
                    pass
            return []
        except Exception as e:
            if not isinstance(e, (discord.errors.NotFound, discord.errors.HTTPException, discord.errors.InteractionResponded)):
                print(f"[AUTOCOMPLETE ERROR] {coro.__name__}: {e}")
            return []
    return wrapper

AUTOCOMPLETE_CACHE = {
    "groups": [],
    "eras": [],
    "names": [],
    "last_update": 0
}
_update_in_progress = False

async def update_autocomplete_cache(force=False):
    global AUTOCOMPLETE_CACHE, _update_in_progress
    now = time.time()
    if not force and now - AUTOCOMPLETE_CACHE["last_update"] < 600: # 10 mins
        return
    
    if _update_in_progress:
        return
        
    _update_in_progress = True
    try:
        # Fetch data including guess minigame
        groups_task = run_query("SELECT DISTINCT group_name FROM ryo_cards WHERE group_name IS NOT NULL", fetchall=True)
        eras_task = run_query("SELECT DISTINCT era FROM ryo_cards WHERE era IS NOT NULL", fetchall=True)
        names_task = run_query("SELECT DISTINCT member_name FROM ryo_cards WHERE member_name IS NOT NULL", fetchall=True)
        
        groups, eras, names = await asyncio.gather(groups_task, eras_task, names_task)
        
        if groups is not None: AUTOCOMPLETE_CACHE["groups"] = sorted([g[0] for g in groups if g[0]])
        if eras is not None: AUTOCOMPLETE_CACHE["eras"] = sorted([e[0] for e in eras if e[0]])
        if names is not None: AUTOCOMPLETE_CACHE["names"] = sorted([n[0] for n in names if n[0]])
        
        AUTOCOMPLETE_CACHE["last_update"] = now
    except Exception as e:
        print(f"[CACHE ERROR] Autocomplete: {e}")
    finally:
        _update_in_progress = False

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

def matches_tag_rule(card_id, member, era, group, rule):
    # rule is a tuple (rule_member, rule_group, rule_era, rule_card_id)
    r_member, r_group, r_era, r_card_id = rule
    if r_member and (not member or member.lower() != r_member.lower()):
        return False
    if r_group and (not group or group.lower() != r_group.lower()):
        return False
    if r_era and (not era or era.lower() != r_era.lower()):
        return False
    if r_card_id and (not card_id or card_id.lower() != r_card_id.lower()):
        return False
    return True

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
        db_pool = ThreadedConnectionPool(5, 40, DATABASE_URL, sslmode="require")
    except Exception as e:
        print(f"⚠️ Failed to connect to Supabase: {e}")

async def check_claim_cooldown(user_id):
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='claim'", (user_id,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 120: # 2 mins
        return 120 - (now - last_used)
    return 0

async def update_claim_cooldown(user_id):
    now = int(time.time())
    await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'claim', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used = %s", (user_id, now, now))

async def run_query(query, params=None, fetchone=False, fetchall=False, return_rowcount=False, retries=1):
    if not db_pool:
        return None
    
    for attempt in range(retries + 1):
        def _execute():
            conn = None
            try:
                conn = db_pool.getconn()
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(query, params)
                        if fetchone: return cur.fetchone()
                        if fetchall: return cur.fetchall()
                        if return_rowcount: return cur.rowcount
                        return True
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                if attempt < retries:
                    if conn:
                        try: db_pool.putconn(conn, close=True)
                        except: pass
                        conn = None
                    return "RETRY"
                print(f"[DB ERROR] {e}")
                return None
            except Exception as e:
                print(f"[DB ERROR] {e}")
                return None
            finally:
                if conn:
                    try: db_pool.putconn(conn)
                    except: pass
        
        result = await asyncio.to_thread(_execute)
        if result != "RETRY":
            return result

async def setup_database():
    if not db_pool: return
    
    queries = [
        "CREATE TABLE IF NOT EXISTS ryo_users (user_id TEXT PRIMARY KEY, paint INT DEFAULT 0, glue INT DEFAULT 0, guess_streak INT DEFAULT 0, last_guess_reward BIGINT DEFAULT 0, bio TEXT, fav_card_id TEXT, registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);",
        "CREATE TABLE IF NOT EXISTS ryo_cards (card_id TEXT PRIMARY KEY, era TEXT, group_name TEXT, rarity INT, image_url TEXT, category TEXT DEFAULT 'regular', member_name TEXT);",
        "CREATE TABLE IF NOT EXISTS ryo_inventory (id SERIAL PRIMARY KEY, user_id TEXT, card_id TEXT, acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, unique_code TEXT UNIQUE);",
        "CREATE TABLE IF NOT EXISTS ryo_cooldowns (user_id TEXT, command TEXT, last_used BIGINT, PRIMARY KEY (user_id, command));",
        "ALTER TABLE ryo_inventory ADD COLUMN IF NOT EXISTS unique_code TEXT UNIQUE;",
        "ALTER TABLE ryo_cards ADD COLUMN IF NOT EXISTS group_name TEXT;",
        "ALTER TABLE ryo_cards ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'regular';",
        "ALTER TABLE ryo_cards ADD COLUMN IF NOT EXISTS member_name TEXT;",
        "ALTER TABLE ryo_users ADD COLUMN IF NOT EXISTS guess_streak INT DEFAULT 0;",
        "ALTER TABLE ryo_users ADD COLUMN IF NOT EXISTS last_guess_reward BIGINT DEFAULT 0;",
        "ALTER TABLE ryo_users ADD COLUMN IF NOT EXISTS bio TEXT;",
        "ALTER TABLE ryo_users ADD COLUMN IF NOT EXISTS fav_card_id TEXT;",
        "ALTER TABLE ryo_users ADD COLUMN IF NOT EXISTS registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;",
        "CREATE TABLE IF NOT EXISTS ryo_blocks (user_id TEXT, entity_type TEXT, entity_name TEXT, PRIMARY KEY (user_id, entity_type, entity_name));",
        "CREATE TABLE IF NOT EXISTS ryo_staff_blocks (entity_type TEXT, entity_name TEXT, PRIMARY KEY (entity_type, entity_name));",
        "CREATE TABLE IF NOT EXISTS ryo_events (category TEXT, era TEXT, rate FLOAT DEFAULT 15.0, PRIMARY KEY (category, era));",
        "ALTER TABLE ryo_events ADD COLUMN IF NOT EXISTS rate FLOAT DEFAULT 15.0;",
        "CREATE TABLE IF NOT EXISTS ryo_rarity_custom (id SERIAL PRIMARY KEY, era TEXT, card_id TEXT, icon_url TEXT);",
        "CREATE TABLE IF NOT EXISTS ryo_quests (quest_id SERIAL PRIMARY KEY, name TEXT, description TEXT, quest_type TEXT, target_value INT, reward_paint INT DEFAULT 0, reward_glue INT DEFAULT 0, is_daily BOOLEAN DEFAULT TRUE, category TEXT DEFAULT 'Daily');",
        "ALTER TABLE ryo_quests ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'Daily';",
        "CREATE TABLE IF NOT EXISTS user_quests (user_id TEXT, quest_id INT, current_value INT DEFAULT 0, completed BOOLEAN DEFAULT FALSE, last_reset BIGINT DEFAULT 0, PRIMARY KEY (user_id, quest_id));",
        "CREATE TABLE IF NOT EXISTS ryo_reminders (user_id TEXT, command TEXT, end_time BIGINT, channel_id TEXT, PRIMARY KEY (user_id, command));",
        "CREATE TABLE IF NOT EXISTS ryo_reminder_settings (user_id TEXT, command TEXT, enabled BOOLEAN DEFAULT TRUE, PRIMARY KEY (user_id, command));",
        "CREATE TABLE IF NOT EXISTS ryo_marketplace (market_id TEXT PRIMARY KEY, seller_id TEXT, card_id TEXT, paint_price INT DEFAULT 0, glue_price INT DEFAULT 0, listed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);",
        "ALTER TABLE ryo_marketplace ADD COLUMN IF NOT EXISTS unique_code TEXT;",
        "CREATE TABLE IF NOT EXISTS ryo_shop_packs (pack_id TEXT PRIMARY KEY, name TEXT, description TEXT, price INT DEFAULT 0);",
        "CREATE TABLE IF NOT EXISTS ryo_tags (id SERIAL PRIMARY KEY, user_id TEXT, name TEXT, emoji TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);",
        "CREATE TABLE IF NOT EXISTS ryo_tag_rules (id SERIAL PRIMARY KEY, user_id TEXT, tag_name TEXT, member_name TEXT, group_name TEXT, era TEXT, card_id TEXT);",
        "CREATE TABLE IF NOT EXISTS disabled_commands (command_name TEXT PRIMARY KEY, disabled_text TEXT, disabled_at BIGINT);",
        "ALTER TABLE ryo_cards ADD COLUMN IF NOT EXISTS creator_id TEXT;",
        "ALTER TABLE ryo_cards ADD COLUMN IF NOT EXISTS custom_emoji TEXT;",
        "ALTER TABLE ryo_cards ADD COLUMN IF NOT EXISTS disabled BOOLEAN DEFAULT FALSE;",
        "CREATE TABLE IF NOT EXISTS ryo_refundable_cards (card_id TEXT PRIMARY KEY);",
        "UPDATE ryo_cards SET category = 'public event' WHERE category = 'event';"
    ]
    
    for q in queries:
        await run_query(q)
    
    # 1. Deduplicate user_quests from duplicate quests
    await run_query("""
        DELETE FROM user_quests 
        WHERE (user_id, quest_id) IN (
            SELECT uq.user_id, uq.quest_id
            FROM user_quests uq
            JOIN ryo_quests rq ON uq.quest_id = rq.quest_id
            JOIN (
                SELECT LOWER(name) as name, LOWER(category) as category, MIN(quest_id) as min_id
                FROM ryo_quests
                GROUP BY LOWER(name), LOWER(category)
            ) m ON LOWER(rq.name) = m.name AND LOWER(rq.category) = m.category
            WHERE rq.quest_id != m.min_id
        );
    """)
    # 2. Deduplicate ryo_quests
    await run_query("""
        DELETE FROM ryo_quests
        WHERE quest_id NOT IN (
            SELECT MIN(quest_id)
            FROM ryo_quests
            GROUP BY LOWER(name), LOWER(category)
        );
    """)
    # 3. Add unique constraint to ryo_quests
    await run_query("ALTER TABLE ryo_quests DROP CONSTRAINT IF EXISTS ryo_quests_name_category_unq;")
    await run_query("ALTER TABLE ryo_quests ADD CONSTRAINT ryo_quests_name_category_unq UNIQUE (name, category);")

    # 4. Insert default core quests using ON CONFLICT (name, category) DO NOTHING
    await run_query("INSERT INTO ryo_quests (name, description, quest_type, target_value, reward_paint, reward_glue, is_daily, category) VALUES ('Daily Drawer', 'Draw 10 cards today', 'draw', 10, 50, 20, TRUE, 'Daily') ON CONFLICT (name, category) DO NOTHING;")
    await run_query("INSERT INTO ryo_quests (name, description, quest_type, target_value, reward_paint, reward_glue, is_daily, category) VALUES ('Generous Soul', 'Gift 3 cards to friends', 'gift', 3, 30, 10, TRUE, 'Daily') ON CONFLICT (name, category) DO NOTHING;")
    await run_query("INSERT INTO ryo_quests (name, description, quest_type, target_value, reward_paint, reward_glue, is_daily, category) VALUES ('Card Burner', 'Burn 5 unwanted cards', 'burn', 5, 20, 50, TRUE, 'Daily') ON CONFLICT (name, category) DO NOTHING;")
    
    # MIGRATION: Fill existing NULL unique_codes ONLY for guess cards
    null_codes = await run_query("""
        SELECT i.id 
        FROM ryo_inventory i
        JOIN ryo_cards c ON i.card_id = c.card_id
        WHERE i.unique_code IS NULL 
        AND (c.category = 'guess_minigame' OR c.era = 'Guess')
    """, fetchall=True)
    if null_codes:
        print(f"🔄 Migrating {len(null_codes)} guess inventory items to have unique codes...")
        for (row_id,) in null_codes:
            code = generate_unique_code()
            # Try to update, ignore if collision (next restart will try again)
            await run_query("UPDATE ryo_inventory SET unique_code = %s WHERE id = %s", (code, row_id))
        print("✅ Migration complete.")
    
    await refresh_rarity_cache()
    
    # Ensure reminder settings for default commands
    for cmd in ["draw", "paint", "daily", "weekly", "craft", "guess-reward", "sticky", "color", "claim"]:
        await run_query(f"INSERT INTO ryo_reminder_settings (user_id, command, enabled) SELECT user_id, '{cmd}', TRUE FROM ryo_users ON CONFLICT DO NOTHING")
                
    print("✅ Ryo database tables verified.")

async def set_reminder(user_id, command, duration, channel_id):
    now = int(time.time())
    await run_query("""
        INSERT INTO ryo_reminders (user_id, command, end_time, channel_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, command)
        DO UPDATE SET end_time = %s, channel_id = %s
    """, (user_id, command, now + duration, str(channel_id), now + duration, str(channel_id)))

async def background_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(60) # Check every minute
        now = int(time.time())
        rems = await run_query("SELECT user_id, command, channel_id FROM ryo_reminders WHERE end_time <= %s", (now,), fetchall=True)
        if rems:
            for r in rems:
                try:
                    c = bot.get_channel(int(r[2]))
                    if c: 
                        # Choose an appropriate emoji for the command
                        emoji = PAINT_EMOJI
                        if "draw" in r[1]: emoji = "🖼️"
                        elif "daily" in r[1] or "weekly" in r[1]: emoji = "🎁"
                        elif "guess" in r[1]: emoji = "🕵️"
                        elif "sticky" in r[1]: emoji = GLUE_EMOJI
                        elif "color" in r[1]: emoji = PAINT_EMOJI
                        
                        await c.send(f"{emoji} <@{r[0]}>, your `/{r[1]}` is ready again!")
                    await run_query("DELETE FROM ryo_reminders WHERE user_id=%s AND command=%s", (r[0], r[1]))
                except Exception as e:
                    print(f"[REMINDER ERROR] {e}")
    
async def update_quest_progress(user_id: str, quest_type: str, amount: int = 1):
    """Update progress for all active quests of a certain type for a user."""
    quests = await run_query(
        "SELECT quest_id, target_value, reward_paint, reward_glue, name, is_daily FROM ryo_quests WHERE quest_type = %s",
        (quest_type,),
        fetchall=True
    )
    
    if not quests: return []
    completed_messages = []
    now_ms = int(time.time() * 1000)
    day_ms = 86400000

    for q_id, target, r_paint, r_glue, q_name, is_daily in quests:
        prog = await run_query(
            "SELECT current_value, completed, last_reset FROM user_quests WHERE user_id = %s AND quest_id = %s",
            (user_id, q_id),
            fetchone=True
        )
        
        current_val, is_completed, last_reset = (0, False, 0)
        if prog:
            current_val, is_completed, last_reset = prog
            if is_daily:
                current_day = (now_ms // day_ms) * day_ms
                last_day = (last_reset // day_ms) * day_ms
                if current_day > last_day:
                    current_val, is_completed, last_reset = (0, False, now_ms)
                    await run_query(
                        "UPDATE user_quests SET current_value = 0, completed = FALSE, last_reset = %s WHERE user_id = %s AND quest_id = %s",
                        (last_reset, user_id, q_id)
                    )
        else:
            last_reset = now_ms
            await run_query("INSERT INTO user_quests (user_id, quest_id, last_reset) VALUES (%s, %s, %s)", (user_id, q_id, last_reset))

        if is_completed: continue
        new_val = current_val + amount
        if new_val >= target:
            await run_query("UPDATE user_quests SET current_value = %s, completed = TRUE WHERE user_id = %s AND quest_id = %s", (target, user_id, q_id))
            await run_query("UPDATE ryo_users SET paint = paint + %s, glue = glue + %s WHERE user_id = %s", (r_paint, r_glue, user_id))
            completed_messages.append(f"🎉 **Quest Completed: {q_name}!**\nReward: `{r_paint} Paint`, `{r_glue} Glue`")
        else:
            await run_query("UPDATE user_quests SET current_value = %s WHERE user_id = %s AND quest_id = %s", (new_val, user_id, q_id))
    return completed_messages

_card_cache = None
_last_cache_time = 0
_active_events_cache = None
_last_events_cache_time = 0

async def get_available_cards():
    global _card_cache, _last_cache_time, _active_events_cache, _last_events_cache_time
    now = time.time()
    
    # Cache for 2 minutes
    if _card_cache and (now - _last_cache_time < 120):
        cards = _card_cache
    else:
        cards = await run_query("SELECT card_id, member_name, era, group_name, rarity, image_url, category, disabled FROM ryo_cards", fetchall=True)
        if cards:
            _card_cache = cards
            _last_cache_time = now
        else:
            return []
    
    # Fetch active events (cache for 1 minute)
    if _active_events_cache and (now - _last_events_cache_time < 60):
        active_events = _active_events_cache
    else:
        active_events_rows = await run_query("SELECT category, era FROM ryo_events", fetchall=True)
        active_events = set()
        if active_events_rows:
            for row in active_events_rows:
                active_events.add((row[0].lower(), row[1].lower()))
        _active_events_cache = active_events
        _last_events_cache_time = now

    available = []
    for c in cards:
        # card index: 0:id, 1:member, 2:era, 3:group, 4:rarity, 5:img, 6:cat, 7:disabled
        if len(c) > 7 and c[7]:
            continue
        c_era = (c[2] or "").lower()
        category = (c[6] or "regular").lower()

        # STRICT EXCLUSION: Guess minigame or custom cards should NEVER appear in drops or daily pools
        if category == "guess_minigame" or c_era == "guess" or category == "custom":
            continue

        # If not regular, check if it's part of an active event
        if category != "regular":
            if (category, c_era) not in active_events:
                continue
        available.append(c)
    return available

async def get_user_blocks(user_id):
    """Fetches blocked groups and idols for a specific user."""
    blocks = await run_query("SELECT entity_type, entity_name FROM ryo_blocks WHERE user_id = %s", (user_id,), fetchall=True)
    res = {"group": [], "idol": []}
    if blocks:
        for t, n in blocks:
            res[t].append(n)
    return res

async def get_weighted_card(user_id=None, blocks=None):
    cards = await get_available_cards()
    if not cards: return None
    
    # Filter by user blocks if provided
    if user_id and blocks is None:
        blocks = await get_user_blocks(user_id)
        
    if blocks and (blocks.get('group') or blocks.get('idol')):
        filtered = [c for c in cards if (not c[3] or c[3] not in blocks['group']) and (not c[1] or c[1] not in blocks['idol'])]
        if filtered:
            cards = filtered

    # Weighting Logic
    rarity_weights = {1: 100, 2: 50, 3: 20, 4: 10, 5: 5}
    category_mults = {
        "regular": 1.0, 
        "public event": 0.4, 
        "limited": 0.2,
        "booster event": 0.6,
        "patreon event": 0.2
    }

    # Fetch active events with their target rate mappings
    active_events_rows = await run_query("SELECT category, era, rate FROM ryo_events", fetchall=True)
    events_map = {}
    if active_events_rows:
        for row in active_events_rows:
            events_map[(row[0].lower(), row[1].lower())] = float(row[2])

    default_weights = []
    for c in cards:
        rarity = c[4] or 1
        category = (c[6] or "regular").lower()
        w = rarity_weights.get(rarity, 5) * category_mults.get(category, 1.0)
        default_weights.append(w)

    non_event_weight_sum = 0.0
    event_weight_sums = {}
    non_event_indices = []
    event_indices = {}
    
    for idx, c in enumerate(cards):
        c_era = (c[2] or "").lower()
        c_cat = (c[6] or "regular").lower()
        ev_key = (c_cat, c_era)
        
        if c_cat != "regular" and ev_key in events_map:
            if ev_key not in event_indices:
                event_indices[ev_key] = []
                event_weight_sums[ev_key] = 0.0
            event_indices[ev_key].append(idx)
            event_weight_sums[ev_key] += default_weights[idx]
        else:
            non_event_indices.append(idx)
            non_event_weight_sum += default_weights[idx]

    if not event_indices:
        adjusted_weights = default_weights
    elif non_event_weight_sum == 0.0:
        adjusted_weights = [0.0] * len(cards)
        total_rate = sum(events_map[ev] for ev in event_indices)
        if total_rate <= 0:
            total_rate = 1.0
        for ev, indices in event_indices.items():
            ev_p = events_map[ev] / total_rate
            ev_def_sum = event_weight_sums[ev]
            if ev_def_sum <= 0:
                ev_def_sum = len(indices)
            for idx in indices:
                share = default_weights[idx] / ev_def_sum
                adjusted_weights[idx] = ev_p * share
    else:
        allocated_p_sum = 0.0
        active_event_probabilities = {}
        for ev in event_indices:
            p_val = events_map[ev] / 100.0
            active_event_probabilities[ev] = p_val
            allocated_p_sum += p_val
            
        if allocated_p_sum >= 0.95:
            scale_factor = 0.90 / allocated_p_sum
            for ev in active_event_probabilities:
                active_event_probabilities[ev] *= scale_factor
            allocated_p_sum = 0.90
            
        T = non_event_weight_sum / (1.0 - allocated_p_sum)
        adjusted_weights = [0.0] * len(cards)
        
        for idx in non_event_indices:
            adjusted_weights[idx] = float(default_weights[idx])
            
        for ev, indices in event_indices.items():
            ev_p = active_event_probabilities[ev]
            target_weight_sum = ev_p * T
            default_weight_sum = event_weight_sums[ev]
            if default_weight_sum <= 0:
                default_weight_sum = 1.0
            scaling_factor = target_weight_sum / default_weight_sum
            
            for idx in indices:
                adjusted_weights[idx] = float(default_weights[idx]) * scaling_factor

    return random.choices(cards, weights=adjusted_weights, k=1)[0]

# --- Bot Logic ---
class RyoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await setup_database()
        self.tree.add_command(guess_group)
        self.tree.add_command(market_group)
        self.loop.create_task(background_loop())
        try:
            # 🧹 PREVENT DUPLICATES: Clear guild-level duplicate commands from target guilds
            for g_id in GUILD_IDS:
                try:
                    guild = discord.Object(id=g_id)
                    self.tree.clear_commands(guild=guild)
                    await self.tree.sync(guild=guild)
                    print(f"🧹 Successfully cleared guild-level commands for guild {g_id}")
                except Exception as e:
                    print(f"⚠️ Could not clear guild-level commands for guild {g_id}: {e}")

            # ⚡ GLOBAL SYNC: Register commands globally as the single source of truth
            await self.tree.sync()
            print("✅ Slash commands synced globally as the single source of truth.")
        except Exception as e:
            print(f"❌ Global sync failed: {e}")

    async def on_ready(self):
        print(f"✅ Ryo Bot logged in as {self.user}")
        await update_autocomplete_cache(force=True)
        await refresh_rarity_cache()

bot = RyoBot()

# 🔹 Emergency Sync Command (Prefix: !)
@bot.command(name="sync")
async def sync_prefix(ctx, scope: str = "guilds"):
    """Force sync slash commands using !sync.
    Options for scope:
      - 'guilds' (default): Synthesizes/copies commands directly to and syncs all GUILD_IDS (instant!).
      - 'global': Syncs globally across all servers (can take up to an hour).
      - 'guild': Syncs commands only for this current guild (instant!).
      - 'clear': Clears guild commands from all GUILD_IDS and then syncs globally.
    """
    is_dev = ctx.author.id in [691771271129858129, DEV_ID]
    # Check if they have the mod, staff role, or standard permissions
    is_staff_or_mod = False
    if ctx.guild:
        user_roles = [r.id for r in ctx.author.roles] if hasattr(ctx.author, 'roles') else []
        is_staff_or_mod = any(role_id in [RELEASE_STAFF_ROLE_ID, MOD_ROLE_ID] for role_id in user_roles)
        
    if is_dev or is_staff_or_mod:
        scope = scope.lower().strip()
        await ctx.send(f"⏳ Syncing slash commands (Scope: `{scope}`)... please wait...")
        try:
            if scope == "global":
                synced = await bot.tree.sync()
                await ctx.send(f"✅ Successfully synced {len(synced)} commands globally.")
            elif scope == "guild":
                if not ctx.guild:
                    return await ctx.send("❌ This scope can only be used inside a server.")
                synced = await bot.tree.sync(guild=ctx.guild)
                await ctx.send(f"✅ Successfully synced {len(synced)} commands to this guild.")
            elif scope == "guilds":
                success_guilds = []
                failed_guilds = []
                for g_id in GUILD_IDS:
                    try:
                        guild_obj = discord.Object(id=g_id)
                        bot.tree.copy_global_to(guild=guild_obj)
                        synced = await bot.tree.sync(guild=guild_obj)
                        success_guilds.append(f"`{g_id}` ({len(synced)} commands)")
                    except Exception as ex:
                        failed_guilds.append(f"`{g_id}` (Error: {ex})")
                
                msg = ""
                if success_guilds:
                    msg += f"✅ Successfully synced commands directly to {len(success_guilds)} target guild(s):\n" + "\n".join(f"• {x}" for x in success_guilds)
                if failed_guilds:
                    if msg:
                        msg += "\n\n"
                    msg += f"⚠️ Failed to sync for {len(failed_guilds)} guild(s) (is the bot invited with applications.commands permission?):\n" + "\n".join(f"• {x}" for x in failed_guilds)
                await ctx.send(msg)
            elif scope == "clear":
                success_guilds = []
                for g_id in GUILD_IDS:
                    try:
                        guild_obj = discord.Object(id=g_id)
                        bot.tree.clear_commands(guild=guild_obj)
                        await bot.tree.sync(guild=guild_obj)
                        success_guilds.append(str(g_id))
                    except Exception as ex:
                        print(f"Failed to clear {g_id}: {ex}")
                await bot.tree.sync()
                await ctx.send(f"🧹 Cleared guild-level cache for guilds {success_guilds} and synced globally.")
            else:
                await ctx.send("❌ Invalid scope. Choose from: `guilds`, `global`, `guild`, or `clear`.")
        except Exception as e:
            await ctx.send(f"❌ Sync failed: {e}")
    else:
        await ctx.send("❌ You don't have permission to sync.")

# Global Tree-wide App Command Error Handler
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    import sys
    import traceback
    
    # If the command raised an exception under the hood
    if isinstance(error, app_commands.errors.CommandInvokeError):
        original = error.original
        # If it's a NotFound with code 10062 (Unknown Interaction), the interaction expired.
        if isinstance(original, discord.errors.NotFound) and getattr(original, "code", None) == 10062:
            print(f"⚠️ Interaction expired (10062 Unknown Interaction) in command: /{interaction.command.name if interaction.command else 'unknown'}")
            return
        # If it's any other NotFound, we format and log it cleanly
        if isinstance(original, discord.errors.NotFound):
            print(f"⚠️ NotFound exception in command: /{interaction.command.name if interaction.command else 'unknown'}: {original}")
            return
            
    # Print the exception output gracefully to console
    print(f"❌ Error in command /{interaction.command.name if interaction.command else 'unknown'}:", file=sys.stderr)
    traceback.print_exception(type(error), error, error.__traceback__)

bot.tree.on_error = on_app_command_error

# --- Commands ---

@bot.tree.command(name="balance", description="Check your Paint and Glue balance")
async def balance(interaction: discord.Interaction):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    res = await run_query("SELECT paint, glue FROM ryo_users WHERE user_id=%s", (uid,), fetchone=True)
    if not res:
        # Create profile if not exists
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        res = (0, 0)
    
    embed = discord.Embed(description=f"{PAINT_EMOJI} {res[0]:,} paint\n{GLUE_EMOJI} {res[1]:,} glue", color=RYO_COLOR)
    embed.set_author(name=f"{interaction.user.name}'s balance", icon_url=interaction.user.display_avatar.url)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="setbio", description="Set your profile bio")
@app_commands.describe(bio="Your new bio (max 300 characters)")
async def setbio(interaction: discord.Interaction, bio: str):
    if len(bio) > 300:
        return await interaction.response.send_message("❌ Bio is too long! (Max 300 characters)", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
    await run_query("UPDATE ryo_users SET bio = %s WHERE user_id = %s", (bio, uid))
    await interaction.followup.send("✅ Bio updated!", ephemeral=True)

@bot.tree.command(name="setfav", description="Set your favorite card for your profile")
@app_commands.describe(card_id="The ID of the card you want to set as favorite")
async def setfav(interaction: discord.Interaction, card_id: str):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    
    # Get canonical card_id from registry
    card_data = await run_query("SELECT card_id FROM ryo_cards WHERE card_id ILIKE %s", (card_id.strip(),), fetchone=True)
    if not card_data:
        return await interaction.followup.send(f"❌ Card `{card_id}` doesn't exist in the database!", ephemeral=True)
    
    canonical_id = card_data[0]
    
    # Check if user owns at least one copy of this card
    owned = await run_query("SELECT 1 FROM ryo_inventory WHERE user_id = %s AND card_id = %s LIMIT 1", (uid, canonical_id), fetchone=True)
    if not owned:
        return await interaction.followup.send(f"❌ You don't own card `{canonical_id}` in your inventory!", ephemeral=True)
    
    await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
    await run_query("UPDATE ryo_users SET fav_card_id = %s WHERE user_id = %s", (canonical_id, uid))
    await interaction.followup.send(f"✅ Favorite card set to `{canonical_id}`!", ephemeral=True)

@bot.tree.command(name="profile", description="View your or another user's profile")
@app_commands.describe(user="The user whose profile you want to view")
async def profile(interaction: discord.Interaction, user: discord.Member = None):
    await interaction.response.defer()
    target = user or interaction.user
    uid = str(target.id)
    
    # Get user data
    userdata = await run_query("SELECT paint, glue, bio, fav_card_id, registered_at FROM ryo_users WHERE user_id = %s", (uid,), fetchone=True)
    
    if not userdata:
        if target == interaction.user:
            await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
            userdata = await run_query("SELECT paint, glue, bio, fav_card_id, registered_at FROM ryo_users WHERE user_id = %s", (uid,), fetchone=True)
        else:
            return await interaction.followup.send("❌ This user hasn't played Ryo yet!")
            
    if not userdata:
         return await interaction.followup.send("❌ Failed to load profile data.")

    paint_raw, glue_raw, bio, fav_id, registered_at = userdata
    paint = paint_raw or 0
    glue = glue_raw or 0
    
    # Get total card count
    count_res = await run_query("SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s", (uid,), fetchone=True)
    total_cards = count_res[0] if count_res else 0
    
    # Format registration date
    if not registered_at:
        registered_at = datetime.datetime.now()
        
    reg_timestamp = int(registered_at.timestamp())
    hammer_time = f"<t:{reg_timestamp}:D>"
    
    embed = discord.Embed(color=RYO_COLOR)
    embed.set_author(name=f"{target.name}'s profile", icon_url=target.display_avatar.url)
    embed.set_thumbnail(url=target.display_avatar.url)
    
    description = f"**Registered:** {hammer_time}\n\n"
    description += f"**Balance**\n{PAINT_EMOJI} {paint:,} paint\n{GLUE_EMOJI} {glue:,} glue\n\n"
    description += f"**Total card count**\n{total_cards}\n\n"
    description += f"**Bio**\n{bio or 'No bio set.'}"
    
    embed.description = description
    
    if fav_id:
        card = await run_query("SELECT card_id, image_url FROM ryo_cards WHERE card_id = %s", (fav_id,), fetchone=True)
        if card:
            # Count copies
            copies_res = await run_query("SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s AND card_id = %s", (uid, fav_id), fetchone=True)
            copies = copies_res[0] if copies_res else 0
            
            img_url = get_raw_url(card[1])
            embed.set_image(url=img_url)
            embed.set_footer(text=f"{copies} {'copies' if copies != 1 else 'copy'} of their favorite card!")
            
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="block", description="Block a group or idol from appearing in your drops (Limit: 5 each)")
@app_commands.describe(category="What to block", name="Name of the group or idol")
@app_commands.choices(category=[
    app_commands.Choice(name="Group", value="group"),
    app_commands.Choice(name="Idol", value="idol")
])
async def block(interaction: discord.Interaction, category: str, name: str):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    name = name.strip()
    
    # Check current limits
    current_blocks = await run_query("SELECT entity_name FROM ryo_blocks WHERE user_id = %s AND entity_type = %s", (uid, category), fetchall=True)
    current_names = [r[0] for r in current_blocks] if current_blocks else []
    
    if len(current_names) >= 5:
        return await interaction.followup.send(f"❌ You have already blocked 5 {category}s! Unblock something first.", ephemeral=True)
    
    # CASE INSENSITIVE CHECK for existing block
    name_lower = name.lower()
    if any(n.lower() == name_lower for n in current_names):
        return await interaction.followup.send(f"❌ `{name}` is already on your block list.", ephemeral=True)
    
    # Verify name exists in staff approved block list
    allowed_res = await run_query("SELECT entity_name FROM ryo_staff_blocks WHERE entity_type = %s AND entity_name ILIKE %s LIMIT 1", (category, name), fetchone=True)
    if not allowed_res:
         return await interaction.followup.send(f"❌ `{name}` is not in the staff-approved block list. You can only block groups/idols approved by staff!", ephemeral=True)
    
    canonical_name = allowed_res[0]

    await run_query("INSERT INTO ryo_blocks (user_id, entity_type, entity_name) VALUES (%s, %s, %s)", (uid, category, canonical_name))
    
    all_blocks = await get_user_blocks(uid)
    groups_str = ", ".join(all_blocks['group']) or "None"
    idols_str = ", ".join(all_blocks['idol']) or "None"
    
    embed = discord.Embed(title="🚫 Block List Updated", color=RYO_COLOR)
    embed.description = f"Added **{canonical_name}** to your {category} block list.\n\n"
    embed.add_field(name="Blocked Groups", value=groups_str, inline=False)
    embed.add_field(name="Blocked Idols", value=idols_str, inline=False)
    embed.set_footer(text="These will no longer appear in your drops (draw, paint, etc.)")
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@block.autocomplete('name')
@safe_autocomplete
async def block_autocomplete(interaction: discord.Interaction, current: str):
    cat = interaction.namespace.category
    if not cat:
        return []
    results = await run_query("SELECT entity_name FROM ryo_staff_blocks WHERE entity_type = %s AND entity_name ILIKE %s ORDER BY entity_name ASC LIMIT 25", (cat, f"%{current}%"), fetchall=True)
    if not results:
        return []
    return [app_commands.Choice(name=r[0], value=r[0]) for r in results]

@bot.tree.command(name="staffblocklist", description="Show all groups and idols approved by staff for blocking")
async def staffblocklist(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    rows = await run_query("SELECT entity_type, entity_name FROM ryo_staff_blocks ORDER BY entity_type ASC, entity_name ASC", fetchall=True)
    
    groups = []
    idols = []
    if rows:
        for r_type, r_name in rows:
            if r_type == "group":
                groups.append(r_name)
            else:
                idols.append(r_name)
                
    groups_str = ", ".join(groups) or "None"
    idols_str = ", ".join(idols) or "None"
    
    embed = discord.Embed(title="📋 Staff-Approved Blockable List", color=RYO_COLOR)
    embed.description = "Players can use `/block` to block any of the following entities from their drops."
    embed.add_field(name="Approved Groups", value=groups_str, inline=False)
    embed.add_field(name="Approved Idols", value=idols_str, inline=False)
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="blocklist", description="Show your personal blocked groups and idols")
async def blocklist(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    
    rows = await run_query("SELECT entity_type, entity_name FROM ryo_blocks WHERE user_id = %s ORDER BY entity_type ASC, entity_name ASC", (uid,), fetchall=True)
    
    groups = []
    idols = []
    if rows:
        for r_type, r_name in rows:
            if r_type == "group":
                groups.append(r_name)
            else:
                idols.append(r_name)
                
    groups_str = ", ".join(groups) or "None"
    idols_str = ", ".join(idols) or "None"
    
    embed = discord.Embed(title="🚫 Your Personal Block list", color=RYO_COLOR)
    embed.description = "The following entities are currently blocked and will not appear in your drops (such as draw, paint, etc.)."
    embed.add_field(name="Blocked Groups", value=groups_str, inline=False)
    embed.add_field(name="Blocked Idols", value=idols_str, inline=False)
    embed.set_footer(text="Use /unblock to restore any group or idol.")
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="unblock", description="Remove a group or idol from your block list")
@app_commands.describe(category="What to unblock", name="Name of the group or idol")
@app_commands.choices(category=[
    app_commands.Choice(name="Group", value="group"),
    app_commands.Choice(name="Idol", value="idol")
])
async def unblock(interaction: discord.Interaction, category: str, name: str):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    
    # Check if exists
    exists = await run_query("SELECT 1 FROM ryo_blocks WHERE user_id = %s AND entity_type = %s AND entity_name ILIKE %s", (uid, category, name), fetchone=True)
    if not exists:
        return await interaction.followup.send(f"❌ `{name}` is not in your {category} block list.", ephemeral=True)
    
    # Need to get exact name to delete
    exact_res = await run_query("SELECT entity_name FROM ryo_blocks WHERE user_id = %s AND entity_type = %s AND entity_name ILIKE %s", (uid, category, name), fetchone=True)
    exact_name = exact_res[0]
    
    await run_query("DELETE FROM ryo_blocks WHERE user_id = %s AND entity_type = %s AND entity_name = %s", (uid, category, exact_name))
    
    await interaction.followup.send(f"✅ Removed **{exact_name}** from your {category} block list.", ephemeral=True)

@unblock.autocomplete('name')
@safe_autocomplete
async def unblock_autocomplete(interaction: discord.Interaction, current: str):
    uid = str(interaction.user.id)
    cat = interaction.namespace.category
    if not cat: return []
    
    results = await run_query("SELECT entity_name FROM ryo_blocks WHERE user_id = %s AND entity_type = %s AND entity_name ILIKE %s", (uid, cat, f"%{current}%"), fetchall=True)
    if not results: return []
    return [app_commands.Choice(name=r[0], value=r[0]) for r in results][:25]

@bot.tree.command(name="daily", description="Claim your daily rewards!")
@app_commands.describe(reminder="Turn reminder on/off")
async def daily(interaction: discord.Interaction, reminder: bool = None):
    uid = str(interaction.user.id)
    
    # Cooldown check (24 hours = 86400s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='daily'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 86400:
        rem = 86400 - (now - last_used)
        target_time = int(time.time() + rem)
        return await interaction.response.send_message(f"⌛ Ryo is preparing your gifts! Try again <t:{target_time}:R>.", ephemeral=True)

    await interaction.response.defer()

    try:
        if reminder is not None:
            await run_query("INSERT INTO ryo_reminder_settings (user_id, command, enabled) VALUES (%s, 'daily', %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, reminder, reminder))
        
        rem_enabled = await run_query("SELECT enabled FROM ryo_reminder_settings WHERE user_id=%s AND command='daily'", (uid,), fetchone=True)
        if not rem_enabled or rem_enabled[0]:
            await set_reminder(uid, "daily", 86400, interaction.channel_id)
            
        # 1. Roll for a card using consistent weighting
        selected_card = await get_weighted_card(uid)
        if not selected_card:
            return await interaction.followup.send("❌ No cards in registry. Use `/addcard` first!")
        
        # 2. Award currency and card
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        await run_query("UPDATE ryo_users SET paint = paint + 5000, glue = glue + 10 WHERE user_id = %s", (uid,))
        code = await grant_card(uid, selected_card[0])
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'daily', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))
        
        # Formatting metadata for embed
        cat_label = (selected_card[6] or "Regular").capitalize()

        # 3. Build Embed
        embed = discord.Embed(
            description=f"Opening your daily package you received:\n\n"
                        f"{PAINT_EMOJI} **5,000 paint**\n"
                        f"{GLUE_EMOJI} **10 glue**\n"
                        f"{get_rarity_display(selected_card[4], selected_card[6], selected_card[2], selected_card[0])} (`{selected_card[0]}`) {selected_card[3]} {selected_card[1]} {selected_card[2]}",
            color=RYO_COLOR
        )
        embed.set_author(name=f"{interaction.user.name}'s daily rewards", icon_url=interaction.user.display_avatar.url)
        
        # Generate card image as a file to ensure it shows up
        img_canvas = await generate_card_image(selected_card)
        arr = io.BytesIO()
        img_canvas.save(arr, format='PNG')
        arr.seek(0)
        file = discord.File(arr, filename="daily_card.png")
        embed.set_image(url="attachment://daily_card.png")
        
        footer_icon = "https://cdn.discordapp.com/emojis/1497330599457722479.png"
        embed.set_footer(text="Thank you for playing Ryo!", icon_url=footer_icon)
        
        await interaction.followup.send(embed=embed, file=file)
        
    except Exception as e:
        print(f"❌ Daily error: {e}")
        await interaction.followup.send("⚠️ Failed to claim daily rewards. Please try again.")

try:
    LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    LANCZOS = Image.LANCZOS

def get_raw_url(url: str):
    """Automatically converts GitHub/Dropbox/Imgur/Catbox blob URLs to raw direct URLs."""
    if not url: return url
    if "github.com" in url:
        if "/blob/" in url:
            url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
        elif "/raw/" in url:
            url = url.replace("github.com", "raw.githubusercontent.com").replace("/raw/", "/")
        
        # Handle new GitHub URL style: /refs/heads/
        if "/refs/heads/" in url:
            url = url.replace("/refs/heads/", "/")
            
    if "dropbox.com" in url:
        url = url.replace("www.dropbox.com", "dl.dropboxusercontent.com").replace("?dl=0", "").replace("?dl=1", "")
        
    if "imgur.com" in url and "i.imgur.com" not in url:
        # Check if it's a direct link to a page
        if "/a/" not in url and "/gallery/" not in url:
            url = url.replace("imgur.com", "i.imgur.com") + ".png" if "." not in url.split("/")[-1] else url.replace("imgur.com", "i.imgur.com")

    if "catbox.moe" in url and "files.catbox.moe" not in url:
        url = url.replace("catbox.moe", "files.catbox.moe")
        
    return url

async def get_discord_file_from_url(url: str, filename: str = "image.png"):
    if not url:
        return None
    raw_url = get_raw_url(url)
    try:
        session = await get_session()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        async with session.get(raw_url, timeout=10, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.read()
                return discord.File(io.BytesIO(data), filename=filename)
    except Exception as e:
        print(f"Failed to get_discord_file_from_url: {e}")
    return None

async def generate_card_image(card):
    # Dimensions for a single card
    DRAW_CARD_W = 420
    DRAW_CARD_H = 630
    
    canvas = Image.new('RGBA', (DRAW_CARD_W, DRAW_CARD_H), (0, 0, 0, 0))
    
    image_url = card[5]
    if not image_url or not str(image_url).lower().startswith("http"):
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([0, 0, DRAW_CARD_W, DRAW_CARD_H], fill=(50, 50, 50))
        draw.text((30, 200), "NO IMAGE URL", fill=(200, 200, 200))
        return canvas

    target_url = get_raw_url(str(image_url).strip())
    session = await get_session()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
    try:
        async with session.get(target_url, timeout=12, headers=headers) as resp:
            if resp.status == 200:
                img_data = await resp.read()
                with Image.open(io.BytesIO(img_data)) as card_img:
                    card_img = card_img.convert("RGBA")
                    size = (DRAW_CARD_W, DRAW_CARD_H)
                    try:
                        card_img = ImageOps.contain(card_img, size, LANCZOS)
                    except AttributeError:
                        card_img.thumbnail(size, LANCZOS)
                    
                    ix, iy = card_img.size
                    px = (DRAW_CARD_W - ix) // 2
                    py = (DRAW_CARD_H - iy) // 2
                    canvas.paste(card_img, (px, py), card_img)
            else:
                draw = ImageDraw.Draw(canvas)
                draw.rectangle([0, 0, DRAW_CARD_W, DRAW_CARD_H], fill=(50, 50, 50))
                draw.text((50, 200), f"FETCH ERROR\n{resp.status}", fill=(255, 100, 100))
    except Exception:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([0, 0, DRAW_CARD_W, DRAW_CARD_H], fill=(50, 50, 50))
        draw.text((50, 200), "FETCH ERROR", fill=(255, 100, 100))
        
    return canvas

async def generate_triple_card_image(cards):
    GAP = 10
    PADDING = 2 
    # Dimensions for the triple draw grid
    DRAW_CARD_W = 420
    DRAW_CARD_H = 630
    
    WIDTH = (DRAW_CARD_W * 3) + (GAP * 2) + (PADDING * 2)
    HEIGHT = DRAW_CARD_H + (PADDING * 2)
    
    # Use transparent background for a cleaner look
    canvas = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    
    session = await get_session()
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
                        # Add a minimal internal safety margin
                        safety_margin = 4
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
@app_commands.describe(reminder="Turn reminder on/off")
async def weekly(interaction: discord.Interaction, reminder: bool = None):
    uid = str(interaction.user.id)
    
    # Cooldown check (7 days = 604800s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='weekly'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 604800:
        rem = 604800 - (now - last_used)
        target_time = int(time.time() + rem)
        return await interaction.response.send_message(f"⌛ Ryo is saving up your weekly stash! Try again <t:{target_time}:R>.", ephemeral=True)

    await interaction.response.defer()

    try:
        if reminder is not None:
            await run_query("INSERT INTO ryo_reminder_settings (user_id, command, enabled) VALUES (%s, 'weekly', %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, reminder, reminder))
        
        rem_enabled = await run_query("SELECT enabled FROM ryo_reminder_settings WHERE user_id=%s AND command='weekly'", (uid,), fetchone=True)
        if not rem_enabled or rem_enabled[0]:
            await set_reminder(uid, "weekly", 604800, interaction.channel_id)

        # 1. Roll for 3 cards
        selected_cards = []
        blocks = await get_user_blocks(uid)
        for _ in range(3):
            card = await get_weighted_card(uid, blocks=blocks)
            if card:
                selected_cards.append(card)
        
        if not selected_cards:
            return await interaction.followup.send("❌ No cards in registry. Use `/addcard` first!")
        
        # 2. Award currency and cards
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        await run_query("UPDATE ryo_users SET paint = paint + 15000, glue = glue + 30 WHERE user_id = %s", (uid,))
        
        card_details_text = ""
        for card in selected_cards:
            await grant_card(uid, card[0])
            
            card_details_text += f"• **{card[1]}** ({card[2]}) - {get_rarity_display(card[4], card[6], card[2], card[0])}\n"

        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'weekly', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))
        
        # 3. Build Embed
        card_list = "\n".join([f"{get_rarity_display(c[4], c[6], c[2], c[0])} (`{c[0]}`) {c[3]} {c[1]} {c[2]}" for c in selected_cards])
        
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
@app_commands.describe(reminder="Turn reminder on/off")
async def sticky(interaction: discord.Interaction, reminder: bool = None):
    uid = str(interaction.user.id)
    
    # Cooldown check (1 hour = 3600s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='sticky'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 3600:
        rem = 3600 - (now - last_used)
        target_time = int(time.time() + rem)
        return await interaction.response.send_message(f"⌛ Ryo is still cleaning up! Try again <t:{target_time}:R>.", ephemeral=True)

    await interaction.response.defer()

    try:
        if reminder is not None:
            await run_query("INSERT INTO ryo_reminder_settings (user_id, command, enabled) VALUES (%s, 'sticky', %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, reminder, reminder))
        
        rem_enabled = await run_query("SELECT enabled FROM ryo_reminder_settings WHERE user_id=%s AND command='sticky'", (uid,), fetchone=True)
        if not rem_enabled or rem_enabled[0]:
            await set_reminder(uid, "sticky", 3600, interaction.channel_id)

        reward = random.randint(5, 10)
        
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        await run_query("UPDATE ryo_users SET glue = glue + %s WHERE user_id = %s", (reward, uid))
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'sticky', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))
        
        # Fetch updated balances
        user_data = await run_query("SELECT paint, glue FROM ryo_users WHERE user_id = %s", (uid,), fetchone=True)
        t_paint, t_glue = user_data if user_data else (0, 0)

        embed = discord.Embed(
            description=f"{interaction.user.name} collected {GLUE_EMOJI} {reward:,} glue\n\n`Updated balance:`\n{PAINT_EMOJI} {t_paint:,}\n{GLUE_EMOJI} {t_glue:,}",
            color=RYO_COLOR
        )
        embed.set_author(name="sticky icky icky", icon_url=interaction.user.display_avatar.url)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"❌ Sticky error: {e}")
        await interaction.followup.send("⚠️ Failed to claim sticky rewards. Please try again.")

@bot.tree.command(name="color", description="Find some splash of color!")
@app_commands.describe(reminder="Turn reminder on/off")
async def color(interaction: discord.Interaction, reminder: bool = None):
    uid = str(interaction.user.id)
    
    # Cooldown check (30 minutes = 1800s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='color'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 1800:
        rem = 1800 - (now - last_used)
        target_time = int(time.time() + rem)
        return await interaction.response.send_message(f"⌛ Ryo is still mixing the paints! Try again <t:{target_time}:R>.", ephemeral=True)

    await interaction.response.defer()

    try:
        if reminder is not None:
            await run_query("INSERT INTO ryo_reminder_settings (user_id, command, enabled) VALUES (%s, 'color', %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, reminder, reminder))
        
        rem_enabled = await run_query("SELECT enabled FROM ryo_reminder_settings WHERE user_id=%s AND command='color'", (uid,), fetchone=True)
        if not rem_enabled or rem_enabled[0]:
            await set_reminder(uid, "color", 1800, interaction.channel_id)

        reward = random.randint(500, 1000)
        
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        await run_query("UPDATE ryo_users SET paint = paint + %s WHERE user_id = %s", (reward, uid))
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'color', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))
        
        # Fetch updated balances
        user_data = await run_query("SELECT paint, glue FROM ryo_users WHERE user_id = %s", (uid,), fetchone=True)
        t_paint, t_glue = user_data if user_data else (0, 0)

        embed = discord.Embed(
            description=f"{interaction.user.name} collected {PAINT_EMOJI} {reward:,} paint\n\n`Updated balance:`\n{PAINT_EMOJI} {t_paint:,}\n{GLUE_EMOJI} {t_glue:,}",
            color=RYO_COLOR
        )
        embed.set_author(name="bring out the color nae mamdaero", icon_url=interaction.user.display_avatar.url)
        
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
        btn1 = discord.ui.Button(label=f"{card1[4]} | {card1[3] or 'N/A'}", emoji=emoji1, style=discord.ButtonStyle.secondary, custom_id="reveal_0")
        btn1.callback = self.reveal_callback
        self.add_item(btn1)

        # Button 2
        emoji2 = get_rarity_emoji_single(card2[4], card2[6], card2[2], card2[0])
        btn2 = discord.ui.Button(label=f"{card2[4]} | {card2[3] or 'N/A'}", emoji=emoji2, style=discord.ButtonStyle.secondary, custom_id="reveal_1")
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
            await grant_card(uid, card[0])
            
            # Fetch inventory count
            count_res = await run_query("SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s AND card_id = %s", (uid, card[0]), fetchone=True)
            self.copies[idx] = count_res[0] if count_res else 1
            
            # NO update_claim_cooldown(uid) for /paint as requested
            
            # Update button style
            for child in self.children:
                if child.custom_id == custom_id:
                    child.disabled = True
                    child.style = discord.ButtonStyle.success

            # Build results embed matching requested style
            cid, name, era, group, rarity, image_url, category = card
            
            res_embed = discord.Embed(color=RYO_COLOR)
            res_embed.set_author(name=f"{interaction.user.name} | paint result", icon_url=interaction.user.display_avatar.url)
            
            group_display = f" ({group})" if group else ""
            desc = [
                f"### {name}{group_display}",
                "",
                f"**Era:** {era or 'N/A'}",
                f"**Code:** `{cid}`",
                f"**Rarity:** {get_rarity_display(rarity, category, era, cid)}"
            ]
            res_embed.description = "\n".join(desc)
            
            file = None
            if image_url:
                file = await get_discord_file_from_url(image_url, "paint_result.png")
                if file:
                    res_embed.set_image(url="attachment://paint_result.png")
                else:
                    res_embed.set_image(url=get_raw_url(image_url))
                
            res_embed.set_footer(text=f"You have {self.copies[idx]} copies of this card!")
            
            # If any slot claimed, we stop the view since owner can only pick one
            self.stop()

            # For /paint, the user wants the original mystery cards embed to STAY.
            # So we edit the original to disable buttons, and send a NEW followup for result.
            await interaction.edit_original_response(view=self)
            if file:
                await interaction.followup.send(embed=res_embed, file=file)
            else:
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
        self.creation_time = time.time()
        self.claimed_slots_uids = [None] * len(cards)
        self.claimers_mentions = [None] * len(cards)
        self.copies = [0] * len(cards)
        self.users_who_claimed = set()
        self.message = None

        for i, card in enumerate(cards):
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
            if interaction.user.id not in ALLOWED_USERS:
                disabled = await get_disabled_commands()
                if "all" in disabled:
                    return await interaction.response.send_message(disabled["all"], ephemeral=True)
                if "claim" in disabled:
                    return await interaction.response.send_message(disabled["claim"], ephemeral=True)
                if "draw" in disabled:
                    return await interaction.response.send_message(disabled["draw"], ephemeral=True)
                if "paint" in disabled:
                    return await interaction.response.send_message(disabled["paint"], ephemeral=True)

            is_owner = (uid == self.user_id)
            
            # Check owner delay for others
            if not is_owner:
                target_time = self.creation_time + 10
                if time.time() < target_time:
                    return await interaction.response.send_message(f"You can claim <t:{int(target_time)}:R>!", ephemeral=True)

            # 1. Check if user already claimed from THIS drop
            if uid in self.users_who_claimed:
                return await interaction.response.send_message("❌ You already claimed a card from this drop!", ephemeral=True)

            # 2. Check cooldown
            if not is_owner:
                cd_rem = await check_claim_cooldown(uid)
                if cd_rem > 0:
                    target_time = int(time.time() + cd_rem)
                    return await interaction.response.send_message(f"⌛ Your hands are full! Take a break <t:{target_time}:R>.", ephemeral=True)

            custom_id = interaction.data['custom_id']
            idx = int(custom_id.split("_")[1])
            card = self.cards[idx]
            
            # 3. Priority Logic
            current_holder_uid = self.claimed_slots_uids[idx]
            
            if not is_owner and current_holder_uid is not None:
                return await interaction.response.send_message("❌ This card has already been claimed!", ephemeral=True)

            # Immediate success response for better perceived speed
            await interaction.response.send_message(f"✅ You claimed **{card[1]}** successfully!", ephemeral=True)

            # 4. Process Priority/Reclaim in background
            if is_owner and current_holder_uid is not None:
                await run_query("""
                    DELETE FROM ryo_inventory 
                    WHERE id = (
                        SELECT id FROM ryo_inventory 
                        WHERE user_id = %s AND card_id = %s 
                        ORDER BY id DESC LIMIT 1
                    )
                """, (current_holder_uid, card[0]))
                if current_holder_uid in self.users_who_claimed:
                    self.users_who_claimed.remove(current_holder_uid)

            # 5. Save to database
            await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
            await grant_card(uid, card[0])
            
            # Update state
            self.users_who_claimed.add(uid)
            self.claimed_slots_uids[idx] = uid
            self.claimers_mentions[idx] = interaction.user.mention
            
            if is_owner:
                await run_query("UPDATE ryo_users SET paint = paint + 50 WHERE user_id=%s", (uid,))
            
            # Fetch total copies
            count_res = await run_query("SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s AND card_id = %s", (uid, card[0]), fetchone=True)
            self.copies[idx] = count_res[0] if count_res else 1
            
            if not is_owner:
                await update_claim_cooldown(uid)

            # Disable the button for this slot immediately
            for child in self.children:
                if child.custom_id == f"drawpick_{idx}":
                    child.disabled = True
                    child.style = discord.ButtonStyle.success

            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception as e:
                    print(f"Error editing message on pick: {e}")

            quest_msgs = await update_quest_progress(uid, 'draw')
            if quest_msgs:
                try: await interaction.followup.send("\n".join(quest_msgs), ephemeral=True)
                except: pass

            # If all cards claimed, finish early
            if all(u is not None for u in self.claimed_slots_uids):
                self.stop()
                await self.on_timeout()

        except Exception as e:
            print(f"Pick error: {e}")
            try: await interaction.followup.send(f"❌ Claim failed: {e}", ephemeral=True)
            except: pass

    async def on_timeout(self):
        # Build results embed
        description_lines = []
        
        for i, c in enumerate(self.cards):
            try: r = int(c[4]) if c[4] is not None else 1
            except: r = 1
            
            stars = get_rarity_display(r, c[6], c[2], c[0])
            m = c[1] or "N/A"
            cid = c[0] or "N/A"
            era = c[2] or "N/A"
            
            description_lines.append(f"{stars} **{m}**")
            description_lines.append(f"`{cid}` {era}")
            
            if self.claimers_mentions[i]:
                description_lines.append(f"Claimed by : {self.claimers_mentions[i]}")
                description_lines.append(f"Copies : {self.copies[i]}")
            else:
                description_lines.append("Unclaimed")
            description_lines.append("")
        
        res_embed = discord.Embed(
            description="\n".join(description_lines),
            color=RYO_COLOR
        )
        
        # Disable buttons and change style for original message
        for child in self.children:
            child.disabled = True
            btn_idx_parts = getattr(child, 'custom_id', '').split("_")
            if len(btn_idx_parts) > 1 and btn_idx_parts[1].isdigit():
                idx = int(btn_idx_parts[1])
                if self.claimed_slots_uids[idx]:
                    child.style = discord.ButtonStyle.success

        if self.message:
            # Try to edit original message to disable interaction
            try: 
                await self.message.edit(view=self)
            except Exception as edit_err:
                print(f"on_timeout view edit error: {edit_err}")

            # Send draw results. Try reply first, and fall back to channel.send if references or replies are forbidden.
            try:
                await self.message.reply(content=f"**Draw results for {self.owner_name}:**", embed=res_embed)
            except Exception as reply_err:
                print(f"Failed to reply to draw message, trying channel.send instead: {reply_err}")
                try:
                    await self.message.channel.send(content=f"**Draw results for {self.owner_name}:**", embed=res_embed)
                except Exception as send_err:
                    print(f"Failed to send draw results to channel: {send_err}")


class EventDrawConfirmView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.confirmed = False
        self.interaction = None

    @discord.ui.button(label="Buy", style=discord.ButtonStyle.primary)
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
        self.confirmed = True
        self.interaction = interaction
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(content="❌ Event draw cancelled.", embed=None, view=None)

@bot.tree.command(name="draw", description="Drop 3 random cards and pick ONE")
@app_commands.describe(reminder="Turn reminder on/off")
async def draw_cmd(interaction: discord.Interaction, reminder: bool = None):
    uid = str(interaction.user.id)
    
    # Calculate Cooldown based on roles
    cd_seconds = get_user_cooldown(interaction.user)
    
    # Cooldown check
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='draw'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    
    if now - last_used < cd_seconds:
        rem = cd_seconds - (now - last_used)
        target_time = int(time.time() + rem)
        return await interaction.response.send_message(f"⌛ Ryo is dealing your next hand! Try again <t:{target_time}:R>.", ephemeral=True)

    await interaction.response.defer()

    try:
        if reminder is not None:
            await run_query("INSERT INTO ryo_reminder_settings (user_id, command, enabled) VALUES (%s, 'draw', %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, reminder, reminder))
        
        rem_enabled = await run_query("SELECT enabled FROM ryo_reminder_settings WHERE user_id=%s AND command='draw'", (uid,), fetchone=True)
        if not rem_enabled or rem_enabled[0]:
            await set_reminder(uid, "draw", cd_seconds, interaction.channel_id)

        # Get 3 random cards
        selected = []
        blocks = await get_user_blocks(uid)
        for _ in range(3):
            card = await get_weighted_card(uid, blocks=blocks)
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
        msg = await interaction.followup.send(embed=embed, view=view, file=file)
        view.message = msg
        
    except Exception as e:
        print(f"❌ Draw error: {e}")
        await interaction.followup.send("⚠️ Failed to perform draw.")

async def generate_single_mystery_image():
    global MYSTERY_IMAGE_CACHE
    if MYSTERY_IMAGE_CACHE:
        return MYSTERY_IMAGE_CACHE.copy()
        
    try:
        session = await get_session()
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
@app_commands.describe(reminder="Turn reminder on/off")
async def paint_drop(interaction: discord.Interaction, reminder: bool = None):
    uid = str(interaction.user.id)
    
    # Cooldown check (20 minutes = 1200s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='paint'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 1200:
        rem = 1200 - (now - last_used)
        target_time = int(time.time() + rem)
        return await interaction.response.send_message(f"⌛ Ryo is drying the mystery cards! Try again <t:{target_time}:R>.", ephemeral=True)

    try:
        await interaction.response.defer()
    except discord.errors.NotFound:
        print("⚠️ Interaction expired (NotFound: 10062) on defer in /paint.")
        return

    try:
        if reminder is not None:
            await run_query("INSERT INTO ryo_reminder_settings (user_id, command, enabled) VALUES (%s, 'paint', %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, reminder, reminder))
        
        rem_enabled = await run_query("SELECT enabled FROM ryo_reminder_settings WHERE user_id=%s AND command='paint'", (uid,), fetchone=True)
        if not rem_enabled or rem_enabled[0]:
            await set_reminder(uid, "paint", 1200, interaction.channel_id)

        # Get cards rarity >= 3 from ALL cards (including events, excluding minigame and custom cards)
        cards = await run_query("""
            SELECT card_id, member_name, era, group_name, rarity, image_url, category 
            FROM ryo_cards 
            WHERE rarity >= 3 
            AND (LOWER(category) != 'guess_minigame' AND LOWER(era) != 'guess' AND LOWER(category) != 'custom')
            AND group_name NOT IN (SELECT entity_name FROM ryo_blocks WHERE user_id = %s AND entity_type = 'group')
            AND member_name NOT IN (SELECT entity_name FROM ryo_blocks WHERE user_id = %s AND entity_type = 'idol')
        """, (uid, uid), fetchall=True)
        
        if len(cards) < 2:
            return await interaction.followup.send("❌ Not enough cards in the registry to perform a Paint drop.")
        
        selected = random.sample(cards, 2)
        
        # Set cooldown
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'paint', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))

        embed = discord.Embed(
            color=RYO_COLOR
        )
        embed.set_author(name=f"{interaction.user.name} brought out some supplies:", icon_url=interaction.user.display_avatar.url)
        
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
    image_url="URL to the card image"
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
    image_url: str = None, 
    rarity: int = 1
):
    await addcard_logic(interaction, card_id, member, era, group, category, image_url, rarity)

@bot.tree.command(name="addrarity", description="Set a custom rarity icon for an era or a specific card (STAFF/MOD ONLY)")
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
            await run_query("DELETE FROM ryo_rarity_custom WHERE LOWER(card_id) = LOWER(%s)", (card_id,))
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

@bot.tree.command(name="deletecard", description="Delete a card from the registry (STAFF/MOD ONLY)")
@app_commands.describe(card_id="The unique ID of the card to delete")
async def deletecard(interaction: discord.Interaction, card_id: str):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    # Check if card exists
    card = await run_query("SELECT member_name, era, card_id FROM ryo_cards WHERE LOWER(card_id) = LOWER(%s)", (card_id,), fetchone=True)
    if not card:
        return await interaction.followup.send(f"❌ Card with ID `{card_id}` not found.")
    
    canonical_id = card[2]
    
    # Delete card from registry and all inventories
    try:
        await run_query("DELETE FROM ryo_inventory WHERE LOWER(card_id) = LOWER(%s)", (canonical_id,))
        success = await run_query("DELETE FROM ryo_cards WHERE LOWER(card_id) = LOWER(%s)", (canonical_id,))
        
        if success:
            await interaction.followup.send(f"✅ Card `{canonical_id}` ({card[0]} - {card[1]}) has been deleted from the registry and all inventories.")
        else:
            await interaction.followup.send("❌ Failed to delete card from registry. Check logs.")
    except Exception as e:
        print(f"Error in deletecard: {e}")
        await interaction.followup.send(f"❌ An error occurred while deleting the card: {e}")

@bot.tree.command(name="editcard", description="Edit an existing card in the registry (STAFF/MOD ONLY)")
@app_commands.describe(
    card_id="The unique ID of the card to edit",
    member="Change Idol Name",
    era="Change Era",
    group="Change Group Name",
    category="Change Main Category (Regular, Public Event, Limited, Booster Event, Patreon Event)",
    new_card_id="Change the Unique ID of the card",
    image_url="Change Image URL",
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
    rarity: int = None
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    # Check if card exists
    card = await run_query("SELECT card_id FROM ryo_cards WHERE LOWER(card_id) = LOWER(%s)", (card_id,), fetchone=True)
    if not card:
        return await interaction.followup.send(f"❌ Card with ID `{card_id}` not found.")

    canonical_id = card[0]
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
        exists = await run_query("SELECT card_id FROM ryo_cards WHERE LOWER(card_id) = LOWER(%s)", (new_card_id,), fetchone=True)
        if exists:
            return await interaction.followup.send(f"❌ New ID `{new_card_id}` already exists!")
        updates.append("card_id = %s")
        params.append(new_card_id)
    
    # Handle image update
    final_image_url = image_url
    
    if final_image_url:
        updates.append("image_url = %s")
        params.append(final_image_url)
        
    if rarity is not None:
        updates.append("rarity = %s")
        params.append(rarity)

    if not updates:
        return await interaction.followup.send("⚠️ No changes provided.")

    query = f"UPDATE ryo_cards SET {', '.join(updates)} WHERE LOWER(card_id) = LOWER(%s)"
    params.append(canonical_id)

    try:
        # If we are changing the card_id, we need to update it in ryo_inventory first if there's no FK constraint with ON UPDATE CASCADE
        # Our table creation didn't specify ON UPDATE CASCADE, so we handle it manually
        if new_card_id:
            await run_query("UPDATE ryo_inventory SET card_id = %s WHERE LOWER(card_id) = LOWER(%s)", (new_card_id, canonical_id))
            
        success = await run_query(query, tuple(params))
        if success:
            display_id = new_card_id if new_card_id else canonical_id
            await interaction.followup.send(f"✅ Card `{canonical_id}` has been updated successfully (New ID: `{display_id}`).")
        else:
            # If update failed and we changed inventory, this might be messy but usually it's due to some other constraint
            await interaction.followup.send("❌ Failed to update card. Check logs.")
    except Exception as e:
        print(f"Error in editcard: {e}")
        await interaction.followup.send(f"❌ An error occurred: {e}")


@bot.tree.command(name="bulkeditcard", description="Edit multiple existing cards in the registry at once (STAFF/MOD ONLY)")
@app_commands.describe(
    card_ids="Comma or space-separated Card IDs to edit (e.g. BTS-101, BTS-102)",
    member="Change Idol Name (use commas for one-to-one, e.g. V, J-hope)",
    era="Change Era (use commas for one-to-one, e.g. Her, Tear)",
    group="Change Group Name (use commas for one-to-one)",
    category="Change Category (use commas for one-to-one, e.g. regular, limited)",
    rarity="Change Rarity (1-6) (use commas for one-to-one, e.g. 5, 4)",
    image_url="Change Image URL (use commas for one-to-one)"
)
async def bulkeditcard(
    interaction: discord.Interaction,
    card_ids: str,
    member: str = None,
    era: str = None,
    group: str = None,
    category: str = None,
    rarity: str = None,
    image_url: str = None
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    # Parse Card IDs from input
    id_list = [cid.strip().upper() for cid in card_ids.replace(",", " ").split() if cid.strip()]
    if not id_list:
        return await interaction.followup.send("❌ No valid Card IDs could be parsed from your input.", ephemeral=True)

    N = len(id_list)

    def validate_rarity(val):
        try:
            r = int(val)
            if 1 <= r <= 6:
                return r
        except ValueError:
            pass
        return None

    def validate_category(val):
        v_clean = val.lower().strip()
        valid_cats = ["regular", "public event", "limited", "booster event", "patreon event"]
        if v_clean in valid_cats:
            return v_clean
        return None

    def parse_param(param_value, param_name, validator=None):
        if param_value is None:
            return None
        parts = [p.strip() for p in param_value.split(',')]
        # Filter out empty entries, but keep if user specified something
        parts = [p for p in parts if p]
        if not parts:
            return None

        # Apply validation if a validator is provided
        if validator:
            validated_parts = []
            for part in parts:
                val = validator(part)
                if val is None:
                    raise ValueError(f"Invalid value `{part}` for parameter `{param_name}`.")
                validated_parts.append(val)
            parts = validated_parts

        if len(parts) == 1:
            return [parts[0]] * N
        elif len(parts) == N:
            return parts
        else:
            raise ValueError(f"The parameter `{param_name}` has {len(parts)} values, but you specified {N} Card IDs. Provide either 1 value to apply to all, or exactly {N} values separated by commas.")

    try:
        member_vals = parse_param(member, "member")
        era_vals = parse_param(era, "era")
        group_vals = parse_param(group, "group")
        category_vals = parse_param(category, "category", validate_category)
        rarity_vals = parse_param(rarity, "rarity", validate_rarity)
        image_url_vals = parse_param(image_url, "image_url")
    except ValueError as val_err:
        return await interaction.followup.send(f"❌ {val_err}", ephemeral=True)

    # Check database format: at least one field must be provided
    if not any(v is not None for v in [member_vals, era_vals, group_vals, category_vals, rarity_vals, image_url_vals]):
        return await interaction.followup.send("⚠️ No changes provided. Please specify at least one field to update (member, era, group, category, rarity, or image_url).", ephemeral=True)

    try:
        # Find which card IDs actually exist in the registry
        placeholders = ", ".join(["%s"] * len(id_list))
        query_select = f"SELECT card_id FROM ryo_cards WHERE LOWER(card_id) IN ({placeholders})"
        existing_rows = await run_query(query_select, tuple(cid.lower() for cid in id_list), fetchall=True) or []
        found_ids_upper = {row[0].upper(): row[0] for row in existing_rows} # Maps uppercase input keys to canonical database IDs

        if not found_ids_upper:
            return await interaction.followup.send("❌ None of the specified Card IDs were found in the registry.", ephemeral=True)

        updated_count = 0
        skipped_ids = []
        updated_ids = []

        for i, cid in enumerate(id_list):
            if cid not in found_ids_upper:
                skipped_ids.append(cid)
                continue

            canonical_cid = found_ids_upper[cid]

            # Formulate the updates for this specific card
            card_updates = []
            card_params = []

            if member_vals is not None:
                card_updates.append("member_name = %s")
                card_params.append(member_vals[i])
            if era_vals is not None:
                card_updates.append("era = %s")
                card_params.append(era_vals[i])
            if group_vals is not None:
                card_updates.append("group_name = %s")
                card_params.append(group_vals[i])
            if category_vals is not None:
                card_updates.append("category = %s")
                card_params.append(category_vals[i])
            if rarity_vals is not None:
                card_updates.append("rarity = %s")
                card_params.append(rarity_vals[i])
            if image_url_vals is not None:
                card_updates.append("image_url = %s")
                card_params.append(image_url_vals[i])

            if card_updates:
                update_query = f"UPDATE ryo_cards SET {', '.join(card_updates)} WHERE card_id = %s"
                card_params.append(canonical_cid)
                success = await run_query(update_query, tuple(card_params))
                if success:
                    updated_count += 1
                    updated_ids.append(canonical_cid)

        if updated_count > 0:
            # Clear card cache so changes are immediately visible
            global _card_cache
            _card_cache = None

            # Build a summary of fields that were changed
            changed_summary = []
            if member_vals is not None:
                if len(set(member_vals)) == 1:
                    changed_summary.append(f"Idol Name: `{member_vals[0]}` (All)")
                else:
                    changed_summary.append(f"Idol Name (Parallel): {', '.join([f'`{v}`' for v in member_vals])}")
            if era_vals is not None:
                if len(set(era_vals)) == 1:
                    changed_summary.append(f"Era: `{era_vals[0]}` (All)")
                else:
                    changed_summary.append(f"Era (Parallel): {', '.join([f'`{v}`' for v in era_vals])}")
            if group_vals is not None:
                if len(set(group_vals)) == 1:
                    changed_summary.append(f"Group: `{group_vals[0]}` (All)")
                else:
                    changed_summary.append(f"Group (Parallel): {', '.join([f'`{v}`' for v in group_vals])}")
            if category_vals is not None:
                if len(set(category_vals)) == 1:
                    changed_summary.append(f"Category: `{category_vals[0].title()}` (All)")
                else:
                    changed_summary.append(f"Category (Parallel): {', '.join([f'`{v.title()}`' for v in category_vals])}")
            if rarity_vals is not None:
                if len(set(rarity_vals)) == 1:
                    changed_summary.append(f"Rarity: `{rarity_vals[0]}` (All)")
                else:
                    changed_summary.append(f"Rarity (Parallel): {', '.join([f'`{v}`' for v in rarity_vals])}")
            if image_url_vals is not None:
                if len(set(image_url_vals)) == 1:
                    changed_summary.append(f"Image Link: `{image_url_vals[0]}` (All)")
                else:
                    changed_summary.append(f"Image Link (Parallel): {', '.join([f'`{v}`' for v in image_url_vals])}")

            summary_details = "\n".join([f"• {item}" for item in changed_summary])
            not_found_str = f"\n\n⚠️ **Card IDs skipped (not found in registry):** {', '.join([f'`{sid}`' for sid in skipped_ids])}" if skipped_ids else ""

            embed = discord.Embed(
                title="✨ Bulk Card Edit Successful",
                description=f"Successfully updated **{updated_count}** card(s) in the registry!\n\n"
                            f"**Changes Applied:**\n{summary_details}\n\n"
                            f"**Updated Card ID(s):**\n{', '.join([f'`{fid}`' for fid in updated_ids])}"
                            f"{not_found_str}",
                color=RYO_COLOR
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("❌ No cards were successfully updated.", ephemeral=True)

    except Exception as e:
        print(f"Error in bulkeditcard: {e}")
        await interaction.followup.send(f"❌ An error occurred: {e}", ephemeral=True)


async def addcard_logic(
    interaction: discord.Interaction, 
    card_id: str, 
    member: str, 
    era: str, 
    group: str, 
    category: str, 
    image_url: str = None, 
    rarity: int = 1,
    ephemeral: bool = False
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer(ephemeral=ephemeral)
    
    final_image_url = get_raw_url(image_url) if image_url else None
    
    if not final_image_url:
        return await interaction.followup.send("❌ You must provide an `image_url`!")

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
        if member == "Group":
            status_msg = f"✅ Card for group `{group}` added to the **{era.upper() if era == 'Guess' else category.upper()}** pool."
        else:
            status_msg = f"✅ Card for `{member}` added to the **{era.upper() if era == 'Guess' else category.upper()}** pool."
        await interaction.followup.send(status_msg)
    else:
        await interaction.followup.send("❌ Failed to add card. Check logs.")

class AddGuessModal(discord.ui.Modal):
    name_input = discord.ui.TextInput(label="Name (Idol or Group)", placeholder="e.g. Joshua or Seventeen", required=True)
    url_input = discord.ui.TextInput(label="Image URL", placeholder="https://...", required=True)

    def __init__(self, mode, parent_view=None):
        super().__init__(title=f"Add Guess {mode}")
        self.mode = mode
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        # We don't defer here because addcard_logic does it
        
        # Use UUID to ensure uniqueness and prevent collisions (fixing "deleting others" issue)
        card_id = f"GT{self.mode[0]}_{uuid.uuid4().hex[:8]}"
        
        name = self.name_input.value
        url = self.url_input.value
        
        # Correct arguments for addcard_logic:
        # interaction, card_id, member, era, group, category, image_url, rarity, ephemeral
        if self.mode == "Idol":
            await addcard_logic(interaction, card_id, name, "Guess", "Solo", "guess_minigame", url, 1, True)
        else:
            await addcard_logic(interaction, card_id, "Group", "Guess", name, "guess_minigame", url, 1, True)
        
        # Refresh the parent view if provided
        if self.parent_view:
            if self.mode == "Idol":
                new_cards = await run_query("SELECT card_id, member_name, image_url FROM ryo_cards WHERE era = 'Guess' AND group_name = 'Solo' ORDER BY member_name ASC", fetchall=True)
            else:
                new_cards = await run_query("SELECT card_id, group_name, image_url FROM ryo_cards WHERE era = 'Guess' AND member_name = 'Group' ORDER BY group_name ASC", fetchall=True)
            
            self.parent_view.cards = new_cards
            self.parent_view.total_pages = (len(new_cards) - 1) // self.parent_view.per_page + 1 if new_cards else 1
            
            if interaction.message:
                try:
                    await interaction.message.edit(embed=self.parent_view.create_embed(), view=self.parent_view)
                except:
                    pass

class RemoveGuessModal(discord.ui.Modal):
    idx_input = discord.ui.TextInput(label="Number to Remove", placeholder="Enter the number from the list (e.g. 1)", required=True)

    def __init__(self, mode, parent_view=None):
        super().__init__(title=f"Remove Guess {mode}")
        self.mode = mode
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        val = self.idx_input.value
        
        if not val.isdigit():
            return await interaction.followup.send("❌ Please enter a valid number from the list.")

        idx = int(val) - 1
        cid = None

        # Use the actual list from the parent view for accuracy
        if self.parent_view and self.parent_view.cards:
            if 0 <= idx < len(self.parent_view.cards):
                cid = self.parent_view.cards[idx][0]
            else:
                return await interaction.followup.send(f"❌ Number `{val}` is out of range.")
        else:
            # Fallback if parent_view is missing (unlikely)
            if self.mode == "Idol":
                rows = await run_query("SELECT card_id FROM ryo_cards WHERE era = 'Guess' AND group_name = 'Solo' ORDER BY member_name ASC", fetchall=True)
            else:
                rows = await run_query("SELECT card_id FROM ryo_cards WHERE era = 'Guess' AND member_name = 'Group' ORDER BY group_name ASC", fetchall=True)
            
            if 0 <= idx < len(rows):
                cid = rows[idx][0]
            else:
                return await interaction.followup.send(f"❌ Number `{val}` is out of range.")

        if cid:
            await run_query("DELETE FROM ryo_cards WHERE card_id = %s", (cid,))
            await interaction.followup.send(f"🗑️ Removed entry number `{val}`.", ephemeral=True)

        # Refresh the parent view if provided
        if self.parent_view:
            if self.mode == "Idol":
                new_cards = await run_query("SELECT card_id, member_name, image_url FROM ryo_cards WHERE era = 'Guess' AND group_name = 'Solo' ORDER BY member_name ASC", fetchall=True)
            else:
                new_cards = await run_query("SELECT card_id, group_name, image_url FROM ryo_cards WHERE era = 'Guess' AND member_name = 'Group' ORDER BY group_name ASC", fetchall=True)
            
            self.parent_view.cards = new_cards
            self.parent_view.total_pages = (len(new_cards) - 1) // self.parent_view.per_page + 1 if new_cards else 1
            
            if interaction.message:
                try:
                    await interaction.message.edit(embed=self.parent_view.create_embed(), view=self.parent_view)
                except:
                    pass

class GoToPageModal(discord.ui.Modal):
    page_input = discord.ui.TextInput(label="Enter Page Number", placeholder="e.g. 1", required=True)

    def __init__(self, parent_view):
        super().__init__(title="Go to Page")
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        val = self.page_input.value
        if not val.isdigit():
            return await interaction.response.send_message("❌ Please enter a valid number.", ephemeral=True)
        
        target_page = int(val) - 1
        if 0 <= target_page < self.parent_view.total_pages:
            self.parent_view.page = target_page
            await interaction.response.edit_message(embed=self.parent_view.create_embed(), view=self.parent_view)
        else:
            await interaction.response.send_message(f"❌ Page must be between 1 and {self.parent_view.total_pages}.", ephemeral=True)

class StaffGuessView(discord.ui.View):
    def __init__(self, mode, cards, user_id):
        super().__init__(timeout=180)
        self.mode = mode
        self.cards = cards
        self.user_id = user_id
        self.page = 0
        self.per_page = 20
        self.total_pages = (len(cards) - 1) // self.per_page + 1 if cards else 1

    def create_embed(self):
        title = f"🕵️ Staff: Guess The {self.mode}"
        if not self.cards:
            desc = f"No {self.mode} cards found."
            if self.mode == "Group": desc += " Use `/addgtg` to add some!"
            return discord.Embed(title=title, description=desc, color=RYO_COLOR)
        
        start = self.page * self.per_page
        end = start + self.per_page
        current_cards = self.cards[start:end]
        
        desc = f"Current **{self.mode}** Questions (Page {self.page + 1}/{self.total_pages}):\n"
        for i, c in enumerate(current_cards, start + 1):
            desc += f"{i}. **{c[1]}** - [[Image]]({get_raw_url(c[2])})\n"
        
        cmd_hint = "addgti" if self.mode == "Idol" else "addgtg"
        desc += f"\n💡 **Note:** To add using an image file, use `/{cmd_hint}` directly."
        embed = discord.Embed(title=title, description=desc[:4000], color=RYO_COLOR)
        embed.set_footer(text=f"Total: {len(self.cards)} entries")
        return embed

    @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary)
    async def first_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id: return await interaction.response.send_message("❌ Not your menu!", ephemeral=True)
        if self.page != 0:
            self.page = 0
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else:
            await interaction.response.send_message("Already on the first page.", ephemeral=True)

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id: return await interaction.response.send_message("❌ Not your menu!", ephemeral=True)
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else:
            await interaction.response.send_message("First page reached.", ephemeral=True)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id: return await interaction.response.send_message("❌ Not your menu!", ephemeral=True)
        if self.page < self.total_pages - 1:
            self.page += 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else:
            await interaction.response.send_message("Last page reached.", ephemeral=True)

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def last_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id: return await interaction.response.send_message("❌ Not your menu!", ephemeral=True)
        if self.page != self.total_pages - 1:
            self.page = self.total_pages - 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else:
            await interaction.response.send_message("Already on the last page.", ephemeral=True)

    @discord.ui.button(label="🔢 Go to Page", style=discord.ButtonStyle.secondary)
    async def goto_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id: return await interaction.response.send_message("❌ Not your menu!", ephemeral=True)
        await interaction.response.send_modal(GoToPageModal(self))

    @discord.ui.button(label="➕ Add New", style=discord.ButtonStyle.success)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (is_staff(interaction) or is_mod(interaction)):
            return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
        await interaction.response.send_modal(AddGuessModal(self.mode, self))

    @discord.ui.button(label="🗑️ Remove Entry", style=discord.ButtonStyle.danger)
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (is_staff(interaction) or is_mod(interaction)):
            return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
        await interaction.response.send_modal(RemoveGuessModal(self.mode, self))

@bot.tree.command(name="staffgti", description="Manage Guess The Idol cards (STAFF/MOD ONLY)")
async def staffgti(interaction: discord.Interaction):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    cards = await run_query("SELECT card_id, member_name, image_url FROM ryo_cards WHERE era = 'Guess' AND group_name = 'Solo' ORDER BY member_name ASC", fetchall=True)
    
    view = StaffGuessView("Idol", cards, str(interaction.user.id))
    await interaction.followup.send(embed=view.create_embed(), view=view, ephemeral=True)

@bot.tree.command(name="staffgtg", description="Manage Guess The Group cards (STAFF/MOD ONLY)")
async def staffgtg(interaction: discord.Interaction):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    cards = await run_query("SELECT card_id, group_name, image_url FROM ryo_cards WHERE era = 'Guess' AND member_name = 'Group' ORDER BY group_name ASC", fetchall=True)
    
    view = StaffGuessView("Group", cards, str(interaction.user.id))
    await interaction.followup.send(embed=view.create_embed(), view=view, ephemeral=True)

@bot.tree.command(name="addgti", description="Add Guess The Idol card(s) (STAFF/MOD ONLY)")
@app_commands.describe(name="Idol Name(s) (can be comma-separated)", image_url="Direct image link(s) (can be comma-separated)")
async def addgti(interaction: discord.Interaction, name: str, image_url: str = None):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    names = [n.strip() for n in name.split(",") if n.strip()]
    urls = [u.strip() for u in image_url.split(",") if u.strip()] if image_url else []
    
    if not names:
        return await interaction.followup.send("❌ You must specify at least one name.")
        
    added = []
    for idx, n in enumerate(names):
        card_id = f"GTI_{uuid.uuid4().hex[:8]}"
        url = urls[idx] if idx < len(urls) else None
        final_image_url = get_raw_url(url) if url else None
        
        await run_query(
            "INSERT INTO ryo_cards (card_id, era, group_name, rarity, image_url, category, member_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (card_id) DO UPDATE SET "
            "era=EXCLUDED.era, group_name=EXCLUDED.group_name, rarity=EXCLUDED.rarity, "
            "image_url=EXCLUDED.image_url, category=EXCLUDED.category, member_name=EXCLUDED.member_name", 
            (card_id, "Guess", "Solo", 1, final_image_url, "guess_minigame", n)
        )
        added.append(f"`{n}`")
        
    await interaction.followup.send(f"✅ Added {len(added)} Guess Idol card(s): {', '.join(added)}")

@bot.tree.command(name="addgtg", description="Add Guess The Group card(s) (STAFF/MOD ONLY)")
@app_commands.describe(name="Group Name(s) (can be comma-separated)", image_url="Direct image link(s) (can be comma-separated)")
async def addgtg(interaction: discord.Interaction, name: str, image_url: str = None):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    names = [n.strip() for n in name.split(",") if n.strip()]
    urls = [u.strip() for u in image_url.split(",") if u.strip()] if image_url else []
    
    if not names:
        return await interaction.followup.send("❌ You must specify at least one group name.")
        
    added = []
    for idx, n in enumerate(names):
        card_id = f"GTG_{uuid.uuid4().hex[:8]}"
        url = urls[idx] if idx < len(urls) else None
        final_image_url = get_raw_url(url) if url else None
        
        await run_query(
            "INSERT INTO ryo_cards (card_id, era, group_name, rarity, image_url, category, member_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (card_id) DO UPDATE SET "
            "era=EXCLUDED.era, group_name=EXCLUDED.group_name, rarity=EXCLUDED.rarity, "
            "image_url=EXCLUDED.image_url, category=EXCLUDED.category, member_name=EXCLUDED.member_name", 
            (card_id, "Guess", n, 1, final_image_url, "guess_minigame", "Group")
        )
        added.append(f"`{n}`")
        
    await interaction.followup.send(f"✅ Added {len(added)} Guess Group card(s): {', '.join(added)}")

@bot.tree.command(name="addcustom", description="Staff: Register a new custom card")
@app_commands.describe(
    card_id="Unique ID for the card",
    name="Name of the character/idol",
    group="Group name (Autocomplete enabled)",
    era="Era name (Autocomplete enabled)",
    rarity="Rarity 1-5",
    image_url="A direct link to an image",
    creator="The creator of this custom card (discord User)",
    rarity_emoji="Custom emoji ID/string to display instead of standard rarity emoji"
)
async def addcustom(
    interaction: discord.Interaction,
    card_id: str,
    name: str,
    group: str,
    era: str,
    rarity: int,
    image_url: str,
    creator: discord.User,
    rarity_emoji: str = None
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
        
    if rarity < 1 or rarity > 5:
        return await interaction.response.send_message("❌ Rarity must be 1-5.", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    
    final_image = get_raw_url(image_url) if image_url else None
    if not final_image:
        return await interaction.followup.send("❌ You must provide an image URL!")
        
    category = "custom"
    creator_id_str = str(creator.id)
    emoji_stripped = rarity_emoji.strip() if rarity_emoji else None
    
    # Insert or update ryo_cards table
    await run_query("""
        INSERT INTO ryo_cards (card_id, era, group_name, rarity, image_url, category, member_name, creator_id, custom_emoji)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (card_id) DO UPDATE SET
        era=EXCLUDED.era, group_name=EXCLUDED.group_name, rarity=EXCLUDED.rarity,
        image_url=EXCLUDED.image_url, category=EXCLUDED.category, member_name=EXCLUDED.member_name,
        creator_id=EXCLUDED.creator_id, custom_emoji=EXCLUDED.custom_emoji
    """, (
        card_id, era, group, rarity, final_image, category, name, creator_id_str, emoji_stripped
    ))
    
    # Automatically add to creator's inventory if they don't already have it
    exists = await run_query("SELECT id FROM ryo_inventory WHERE user_id = %s AND LOWER(card_id) = LOWER(%s)", (creator_id_str, card_id.lower()), fetchone=True)
    if not exists:
        await grant_card(creator_id_str, card_id)
        
    global _card_cache
    _card_cache = None
    await refresh_rarity_cache()
    
    await log_action(str(interaction.user.id), "addcustom", f"Added Custom Card ID: {card_id} for creator {creator_id_str}")
    await interaction.followup.send(f"✅ Custom Card ID `{card_id}` registered to {creator.mention} (Creator) with custom emoji/rarity!")

@bot.tree.command(name="disablecard", description="Staff: Disable cards from drops, daily, weekly, and shop")
@app_commands.describe(
    card_id="Card ID(s) to disable (can be comma-separated)",
    name="Idol Name(s) to disable (can be comma-separated)",
    group="Group name(s) to disable (can be comma-separated)",
    era="Era(s) to disable (can be comma-separated)",
    rarity="Rarity/Rarities to disable (can be comma-separated, e.g. 1, 2)"
)
async def disablecard_cmd(
    interaction: discord.Interaction,
    card_id: str = None,
    name: str = None,
    group: str = None,
    era: str = None,
    rarity: str = None
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Permission denied. Only Mods can use this command.", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    
    conditions = []
    params = []
    
    if card_id:
        ids = [i.strip().lower() for i in card_id.split(",") if i.strip()]
        if ids:
            conditions.append("LOWER(card_id) IN ({})".format(", ".join(["%s"] * len(ids))))
            params.extend(ids)
            
    if name:
        names = [n.strip().lower() for n in name.split(",") if n.strip()]
        if names:
            conditions.append("LOWER(member_name) IN ({})".format(", ".join(["%s"] * len(names))))
            params.extend(names)
            
    if group:
        groups = [g.strip().lower() for g in group.split(",") if g.strip()]
        if groups:
            conditions.append("LOWER(group_name) IN ({})".format(", ".join(["%s"] * len(groups))))
            params.extend(groups)
            
    if era:
        eras = [e.strip().lower() for e in era.split(",") if e.strip()]
        if eras:
            conditions.append("LOWER(era) IN ({})".format(", ".join(["%s"] * len(eras))))
            params.extend(eras)
            
    if rarity:
        rarities = []
        for r in rarity.split(","):
            r_clean = r.strip()
            if r_clean.isdigit():
                rarities.append(int(r_clean))
        if rarities:
            conditions.append("rarity IN ({})".format(", ".join(["%s"] * len(rarities))))
            params.extend(rarities)
            
    if not conditions:
        return await interaction.followup.send("❌ You must specify at least one filter option.")
        
    check_query = "SELECT COUNT(*) FROM ryo_cards WHERE " + " OR ".join(conditions)
    count_row = await run_query(check_query, tuple(params), fetchone=True)
    total_matches = count_row[0] if count_row else 0
    
    if total_matches == 0:
        return await interaction.followup.send("❌ No registered cards match the specified criteria.")
        
    update_query = "UPDATE ryo_cards SET disabled = TRUE WHERE " + " OR ".join(conditions)
    await run_query(update_query, tuple(params))
    
    global _card_cache
    _card_cache = None
    
    await log_action(str(interaction.user.id), "disablecard", f"Disabled {total_matches} cards matching criteria.")
    await interaction.followup.send(f"✅ Successfully disabled **{total_matches}** card(s) matching your criteria.")

@bot.tree.command(name="staffrefund", description="Staff: Add cards to the refundable list")
@app_commands.describe(
    card_id="Card ID(s) to add to refundable list (can be comma-separated)",
    name="Idol Name(s) to add to refundable list (can be comma-separated)",
    group="Group name(s) to add to refundable list (can be comma-separated)",
    era="Era(s) to add to refundable list (can be comma-separated)",
    rarity="Rarity/Rarities to add to refundable list (can be comma-separated, e.g. 1, 2)"
)
async def staffrefund_cmd(
    interaction: discord.Interaction,
    card_id: str = None,
    name: str = None,
    group: str = None,
    era: str = None,
    rarity: str = None
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Permission denied. Only Mods can use this command.", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    
    conditions = []
    params = []
    
    if card_id:
        ids = [i.strip().lower() for i in card_id.split(",") if i.strip()]
        if ids:
            conditions.append("LOWER(card_id) IN ({})".format(", ".join(["%s"] * len(ids))))
            params.extend(ids)
            
    if name:
        names = [n.strip().lower() for n in name.split(",") if n.strip()]
        if names:
            conditions.append("LOWER(member_name) IN ({})".format(", ".join(["%s"] * len(names))))
            params.extend(names)
            
    if group:
        groups = [g.strip().lower() for g in group.split(",") if g.strip()]
        if groups:
            conditions.append("LOWER(group_name) IN ({})".format(", ".join(["%s"] * len(groups))))
            params.extend(groups)
            
    if era:
        eras = [e.strip().lower() for e in era.split(",") if e.strip()]
        if eras:
            conditions.append("LOWER(era) IN ({})".format(", ".join(["%s"] * len(eras))))
            params.extend(eras)
            
    if rarity:
        rarities = []
        for r in rarity.split(","):
            r_clean = r.strip()
            if r_clean.isdigit():
                rarities.append(int(r_clean))
        if rarities:
            conditions.append("rarity IN ({})".format(", ".join(["%s"] * len(rarities))))
            params.extend(rarities)
            
    if not conditions:
        return await interaction.followup.send("❌ You must specify at least one filter option.")
        
    select_query = "SELECT card_id FROM ryo_cards WHERE " + " OR ".join(conditions)
    matching_rows = await run_query(select_query, tuple(params), fetchall=True) or []
    
    if not matching_rows:
        return await interaction.followup.send("❌ No registered cards match the specified criteria.")
        
    added_count = 0
    for row in matching_rows:
        cid = row[0]
        await run_query("INSERT INTO ryo_refundable_cards (card_id) VALUES (%s) ON CONFLICT (card_id) DO NOTHING", (cid,))
        added_count += 1
        
    await log_action(str(interaction.user.id), "staffrefund", f"Added {added_count} cards to refundable list.")
    await interaction.followup.send(f"✅ Successfully added **{added_count}** card(s) to the refundable list.")

@bot.tree.command(name="refund", description="Exchange staff-approved refundable cards for other cards of the exact same rarity")
@app_commands.describe(
    exchange="Card ID(s) you want to exchange (can be multiple with commas)",
    to="Target card ID(s) you want to exchange to (can be multiple with commas)"
)
async def refund_cmd(
    interaction: discord.Interaction,
    exchange: str,
    to: str
):
    await interaction.response.defer()
    
    exchange_ids = [i.strip().upper() for i in exchange.split(",") if i.strip()]
    to_ids = [i.strip().upper() for i in to.split(",") if i.strip()]
    
    if not exchange_ids or not to_ids:
        return await interaction.followup.send("❌ Both exchange and to options must contain valid card IDs.")
        
    if len(exchange_ids) != len(to_ids):
        return await interaction.followup.send(f"❌ Mismatch: You provided {len(exchange_ids)} card(s) to exchange, but {len(to_ids)} target card(s).")
        
    # Check if cards are refundable
    refundable_rows = await run_query("SELECT card_id FROM ryo_refundable_cards WHERE UPPER(card_id) IN ({})".format(", ".join(["%s"] * len(exchange_ids))), exchange_ids, fetchall=True) or []
    refundable_set = {row[0].upper() for row in refundable_rows}
    
    for cid in exchange_ids:
        if cid not in refundable_set:
            return await interaction.followup.send(f"❌ Card `{cid}` is not in the staff-approved refundable list.")
            
    # Check inventory ownership
    needed_counts = {}
    for cid in exchange_ids:
        needed_counts[cid] = needed_counts.get(cid, 0) + 1
        
    uid = str(interaction.user.id)
    user_copies = await run_query("SELECT card_id FROM ryo_inventory WHERE user_id = %s AND UPPER(card_id) IN ({})".format(", ".join(["%s"] * len(needed_counts))), [uid] + list(needed_counts.keys()), fetchall=True) or []
    owned_counts = {}
    for row in user_copies:
        cid = row[0].upper()
        owned_counts[cid] = owned_counts.get(cid, 0) + 1
        
    for cid, need in needed_counts.items():
        owned = owned_counts.get(cid, 0)
        if owned < need:
            return await interaction.followup.send(f"❌ You do not own enough copies of `{cid}`. You need {need} but only own {owned}.")
            
    # Verify card existences and rarities
    all_checked_ids = list(set(exchange_ids + to_ids))
    card_rows = await run_query("SELECT card_id, rarity FROM ryo_cards WHERE UPPER(card_id) IN ({})".format(", ".join(["%s"] * len(all_checked_ids))), all_checked_ids, fetchall=True) or []
    rarity_map = {row[0].upper(): row[1] for row in card_rows}
    
    # Check exchange existences
    for cid in exchange_ids:
        if cid not in rarity_map:
            return await interaction.followup.send(f"❌ Exchange card `{cid}` does not exist in registry.")
            
    # Check target existences
    for cid in to_ids:
        if cid not in rarity_map:
            return await interaction.followup.send(f"❌ Target card `{cid}` does not exist in registry.")
            
    # Match rarities exactly in order
    for idx, (exc_id, to_id) in enumerate(zip(exchange_ids, to_ids)):
        exc_rar = rarity_map[exc_id]
        to_rar = rarity_map[to_id]
        if exc_rar != to_rar:
            return await interaction.followup.send(f"❌ Rarity mismatch at position {idx+1}: `{exc_id}` is Rarity {exc_rar}, but target `{to_id}` is Rarity {to_rar}.")
            
    # Perform exchanges
    results_lines = []
    for exc_id, to_id in zip(exchange_ids, to_ids):
        # Delete one copy of exc_id
        await run_query(
            "DELETE FROM ryo_inventory WHERE id = (SELECT id FROM ryo_inventory WHERE user_id = %s AND UPPER(card_id) = %s LIMIT 1)",
            (uid, exc_id)
        )
        # Grant one copy of to_id
        await grant_card(uid, to_id)
        results_lines.append(f"• `{exc_id}` ➡️ `{to_id}`")
        
    embed = discord.Embed(
        title="🔄 Card Refund/Exchange Complete!",
        description=f"Successfully processed your exchanges:\n\n" + "\n".join(results_lines),
        color=RYO_COLOR
    )
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="staffgift", description="Gift cards to a user (STAFF/MOD ONLY)")
@app_commands.describe(
    user="The user to receive the gift",
    card1="Card ID for the first gift",
    copies1="Number of copies for the first gift",
    card2="Card ID for the second gift",
    copies2="Number of copies for the second gift",
    card3="Card ID for the third gift",
    copies3="Number of copies for the third gift",
    card4="Card ID for the fourth gift",
    copies4="Number of copies for the fourth gift",
    card5="Card ID for the fifth gift",
    copies5="Number of copies for the fifth gift"
)
async def staffgift(
    interaction: discord.Interaction, 
    user: discord.Member,
    card1: str,
    copies1: int = 1,
    card2: str = None,
    copies2: int = 1,
    card3: str = None,
    copies3: int = 1,
    card4: str = None,
    copies4: int = 1,
    card5: str = None,
    copies5: int = 1
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer()
    
    # Collect all provided gifts
    potential_gifts = [
        (card1, copies1),
        (card2, copies2),
        (card3, copies3),
        (card4, copies4),
        (card5, copies5)
    ]
    
    valid_gifts = []
    failed_gifts = []
    
    for c_id, count in potential_gifts:
        if c_id:
            # Check if card exists
            card_data = await run_query("SELECT member_name, rarity, group_name, era, card_id FROM ryo_cards WHERE LOWER(card_id) = LOWER(%s)", (c_id,), fetchone=True)
            if card_data:
                valid_gifts.append({
                    "id": card_data[4],
                    "name": card_data[0],
                    "rarity": card_data[1],
                    "group": card_data[2],
                    "era": card_data[3],
                    "count": max(1, count)
                })
            else:
                failed_gifts.append(f"`{c_id}`")
                
    if not valid_gifts:
        return await interaction.followup.send(f"❌ No valid Card IDs provided. (Failed: {', '.join(failed_gifts)})" if failed_gifts else "❌ No Card IDs provided.")

    # Add cards to user
    results_detailed = []
    for gift in valid_gifts:
        for _ in range(gift["count"]):
            await grant_card(str(user.id), gift["id"])
        
        results_detailed.append(f"• **{gift['count']}x** {get_rarity_display(gift['rarity'], era=gift['era'], card_id=gift['id'])} {gift['name']} ({gift['group']} - {gift['era']}) `{gift['id']}`")

    embed = discord.Embed(
        title="🎁 Staff Gift Sent!",
        description=f"{user.mention}, you have received gifts from the staff!\n\n" + "\n".join(results_detailed),
        color=RYO_COLOR
    )
    if failed_gifts:
        embed.set_footer(text=f"Note: Some IDs were invalid: {', '.join(failed_gifts)}")
    
    await interaction.followup.send(content=user.mention, embed=embed)

@bot.tree.command(name="staffpay", description="Pay paint and glue to a user (STAFF/MOD ONLY)")
@app_commands.describe(user="The recipient", paint="Amount of paint", glue="Amount of glue")
async def staffpay(interaction: discord.Interaction, user: discord.Member, paint: int = 0, glue: int = 0):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer()
    
    # Ensure user exists in ryo_users
    await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (str(user.id),))
    
    await run_query("UPDATE ryo_users SET paint = paint + %s, glue = glue + %s WHERE user_id = %s", (max(0, paint), max(0, glue), str(user.id)))
    
    parts = []
    if paint > 0: parts.append(f"{PAINT_EMOJI} **{paint:,} paint**")
    if glue > 0: parts.append(f"{GLUE_EMOJI} **{glue:,} glue**")
    
    if not parts:
        return await interaction.followup.send("❌ Please specify paint or glue amount.")

    embed = discord.Embed(
        title="💸 Staff Payment Received!",
        description=f"{user.mention}, you have received currency from the staff!\n\n" + " and ".join(parts),
        color=RYO_COLOR
    )
    
    await interaction.followup.send(content=user.mention, embed=embed)

@bot.tree.command(name="removecard", description="Remove cards from a user (STAFF/MOD ONLY)")
@app_commands.describe(
    user="The user to take cards from",
    card1="Card ID for the first removal",
    copies1="Number of copies to remove",
    card2="Card ID for the second removal",
    copies2="Number of copies to remove",
    card3="Card ID for the third removal",
    copies3="Number of copies to remove",
    card4="Card ID for the fourth removal",
    copies4="Number of copies to remove",
    card5="Card ID for the fifth removal",
    copies5="Number of copies to remove"
)
async def removecard(
    interaction: discord.Interaction, 
    user: discord.Member,
    card1: str,
    copies1: int = 1,
    card2: str = None,
    copies2: int = 1,
    card3: str = None,
    copies3: int = 1,
    card4: str = None,
    copies4: int = 1,
    card5: str = None,
    copies5: int = 1
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer()
    
    potential_removals = [
        (card1, copies1),
        (card2, copies2),
        (card3, copies3),
        (card4, copies4),
        (card5, copies5)
    ]
    
    valid_removals = []
    failed_ids = []
    
    for c_id, count in potential_removals:
        if c_id:
            # Check if card exists in database first
            card_data = await run_query("SELECT member_name, rarity, group_name, era, card_id FROM ryo_cards WHERE LOWER(card_id) = LOWER(%s)", (c_id,), fetchone=True)
            if card_data:
                canonical_id = card_data[4]
                # Check if user has it
                count_res = await run_query("SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s AND LOWER(card_id) = LOWER(%s)", (str(user.id), canonical_id), fetchone=True)
                if count_res and count_res[0] > 0:
                    valid_removals.append({
                        "id": canonical_id,
                        "name": card_data[0],
                        "rarity": card_data[1],
                        "group": card_data[2],
                        "era": card_data[3],
                        "count": max(1, count),
                        "user_has": count_res[0]
                    })
                else:
                    failed_ids.append(f"`{c_id}` (User doesn't own)")
            else:
                failed_ids.append(f"`{c_id}` (Invalid ID)")
                
    if not valid_removals:
        return await interaction.followup.send(f"❌ No valid cards found to remove. Errors: {', '.join(failed_ids)}")

    results_detailed = []
    for rem in valid_removals:
        await run_query("""
            DELETE FROM ryo_inventory 
            WHERE id IN (
                SELECT id FROM ryo_inventory 
                WHERE user_id = %s AND card_id = %s 
                LIMIT %s
            )
        """, (str(user.id), rem["id"], rem["count"]))
        actual_removed = min(rem["user_has"], rem["count"])
        
        results_detailed.append(f"• **{actual_removed}x** {get_rarity_display(rem['rarity'], era=rem['era'], card_id=rem['id'])} {rem['name']} ({rem['group']} - {rem['era']}) `{rem['id']}`")

    embed = discord.Embed(
        title="🗑️ Staff Removal: Cards",
        description=f"{user.mention}, the following cards have been removed from your inventory by staff:\n\n" + "\n".join(results_detailed),
        color=0xFF0000 # Red for removal
    )
    if failed_ids:
        embed.set_footer(text=f"Note: Some IDs couldn't be processed: {', '.join(failed_ids[:3])}...")
    
    await interaction.followup.send(content=user.mention, embed=embed)

@bot.tree.command(name="removecurrency", description="Remove paint and glue from a user (STAFF/MOD ONLY)")
@app_commands.describe(user="The user to deduct from", paint="Amount of paint to remove", glue="Amount of glue to remove")
async def removecurrency(interaction: discord.Interaction, user: discord.Member, paint: int = 0, glue: int = 0):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer()
    
    # Check current balance
    user_data = await run_query("SELECT paint, glue FROM ryo_users WHERE user_id = %s", (str(user.id),), fetchone=True)
    if not user_data:
        return await interaction.followup.send("❌ User has no currency data.")
    
    curr_paint, curr_glue = user_data
    
    # Calculate new balances (cannot go below 0)
    paint_to_rem = max(0, paint)
    glue_to_rem = max(0, glue)
    
    new_paint = max(0, curr_paint - paint_to_rem)
    new_glue = max(0, curr_glue - glue_to_rem)
    
    actual_paint_removed = curr_paint - new_paint
    actual_glue_removed = curr_glue - new_glue
    
    if actual_paint_removed == 0 and actual_glue_removed == 0:
        return await interaction.followup.send("❌ User already has 0 paint and 0 glue.")

    await run_query("UPDATE ryo_users SET paint = %s, glue = %s WHERE user_id = %s", (new_paint, new_glue, str(user.id)))
    
    parts = []
    if actual_paint_removed > 0: parts.append(f"{PAINT_EMOJI} **{actual_paint_removed:,} paint**")
    if actual_glue_removed > 0: parts.append(f"{GLUE_EMOJI} **{actual_glue_removed:,} glue**")
    
    embed = discord.Embed(
        title="📉 Staff Removal: Currency",
        description=f"{user.mention}, the staff has deducted currency from your balance:\n\n" + " and ".join(parts),
        color=0xFF0000 # Red
    )
    
    await interaction.followup.send(content=user.mention, embed=embed)

class GuessView(discord.ui.View):
    def __init__(self, user_id, correct_answer, mode, options, image_url):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.correct_answer = correct_answer
        self.mode = mode
        self.image_url = image_url
        self.message = None
        self.has_attachment = False

        for option in options:
            btn = discord.ui.Button(label=option, style=discord.ButtonStyle.secondary)
            btn.callback = self.make_callback(option)
            self.add_item(btn)

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            
            # Keep the image stable during timeout
            if self.message.embeds:
                embed = self.message.embeds[0]
                embed.title = "⏰ Time's Up!"
                embed.description = f"The correct {self.mode} was **{self.correct_answer}**.\nTry to be faster next time!"
                if self.has_attachment:
                    embed.set_image(url="attachment://guess.png")
                elif self.image_url:
                    embed.set_image(url=get_raw_url(self.image_url))
                embed.color = discord.Color.red()
                try:
                    await self.message.edit(embed=embed, view=self)
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
                # Shared Cooldown Logic (3 mins = 180s)
                on_cooldown = now - last_reward_time < 180
                new_streak = streak if on_cooldown else streak + 1
                reward_msg = ""
                
                if not on_cooldown:
                    if new_streak <= 40:
                        # Standard doubling reward system up to and including streak 40
                        multiplier_level = min(new_streak // 10, 10)
                        
                        reward_paint = 100 * (2 ** multiplier_level)
                        reward_glue = 0
                        
                        # Glue bonus on every 10 streak milestone
                        if new_streak > 0 and new_streak % 10 == 0:
                            glue_level = min(new_streak // 10, 10)
                            reward_glue = 2 ** (glue_level - 1)
                    else:
                        # Streak > 40: For every 10-streak increment (tier) after streak 40, add 1.6k paint and 8 glue
                        tier = (new_streak // 10) - 4  # tier 0 for 41-49, tier 1 for 50-59, etc.
                        reward_paint = 1600 + tier * 1600
                        reward_glue = 0
                        
                        # Glue bonus on exactly every 10-streak milestone (e.g. 50, 60...)
                        if new_streak % 10 == 0:
                            reward_glue = 8 + tier * 8
                    
                    await run_query("UPDATE ryo_users SET paint = paint + %s, glue = glue + %s, last_guess_reward = %s, guess_streak = %s WHERE user_id = %s", (reward_paint, reward_glue, now, new_streak, uid))
                    
                    # Set reminder if enabled
                    rem_enabled = await run_query("SELECT enabled FROM ryo_reminder_settings WHERE user_id=%s AND command='guess-reward'", (uid,), fetchone=True)
                    if not rem_enabled or rem_enabled[0]:
                        await set_reminder(uid, "guess-reward", 180, interaction.channel_id)
                        
                    reward_msg = f"\n💰 **Rewards:** +{reward_paint:,} Paint {PAINT_EMOJI}"
                    if reward_glue > 0:
                        reward_msg += f", +{reward_glue:,} Glue {GLUE_EMOJI} ({new_streak} Streak Bonus!)"
                else:
                    rem = 180 - (now - last_reward_time)
                    reward_msg = f"\n⌛ Reward Cooldown: {rem//60}m {rem%60}s remaining. (Streak frozen)"

                embed = discord.Embed(title="✅ Correct!", description=f"{interaction.user.mention} guessed correctly!\nThe {self.mode} was **{self.correct_answer}**!\n🔥 Current Streak: **{new_streak}**{reward_msg}", color=RYO_COLOR)
                if self.has_attachment:
                    embed.set_image(url="attachment://guess.png")
                elif self.image_url:
                    embed.set_image(url=get_raw_url(self.image_url))
                
                # Disable all buttons on success
                for item in self.children:
                    item.disabled = True
                    if item.label == choice:
                        item.style = discord.ButtonStyle.success

                await interaction.edit_original_response(embed=embed, view=self)
            else:
                on_cooldown = now - last_reward_time < 180
                streak_msg = "Streak reset to **0**."
                if not on_cooldown:
                    await run_query("UPDATE ryo_users SET guess_streak = 0 WHERE user_id = %s", (uid,))
                else:
                    streak_msg = f"Streak maintained at **{streak}** (Practice Mode)."

                embed = discord.Embed(title="❌ Wrong!", description=f"{interaction.user.mention} guessed wrong.\nThe correct {self.mode} was **{self.correct_answer}**.\n{streak_msg}", color=RYO_COLOR)
                if self.has_attachment:
                    embed.set_image(url="attachment://guess.png")
                elif self.image_url:
                    embed.set_image(url=get_raw_url(self.image_url))
                
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
@app_commands.describe(reminder="Turn reminder on/off")
async def guess_idol(interaction: discord.Interaction, reminder: bool = None):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    if reminder is not None:
        await run_query("INSERT INTO ryo_reminder_settings (user_id, command, enabled) VALUES (%s, 'guess-reward', %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, reminder, reminder))

    # Only show pics added by staff using /addgti (era='Guess')
    all_members = await run_query("SELECT DISTINCT member_name FROM ryo_cards WHERE era = 'Guess' AND group_name = 'Solo' AND member_name IS NOT NULL", fetchall=True)
    if not all_members or len(all_members) < 4:
        return await interaction.followup.send("❌ Not enough Guess The Idol cards in database (need at least 4 unique idols).", ephemeral=True)
    
    cards = await run_query("SELECT member_name, image_url FROM ryo_cards WHERE era = 'Guess' AND group_name = 'Solo' AND member_name IS NOT NULL", fetchall=True)
    selected = random.choice(cards)
    correct_answer, img_url = selected[0], selected[1]

    # Generate options: Correct + 3 Random others
    others_list = [m[0] for m in all_members if m[0] != correct_answer]
    options = [correct_answer] + random.sample(others_list, 3)
    random.shuffle(options)

    file = None
    embed = discord.Embed(title="🕵️ Who is this Idol?", description="Choose the correct member name from the options below.", color=RYO_COLOR)
    if img_url:
        file = await get_discord_file_from_url(img_url, "guess.png")
        if file:
            embed.set_image(url="attachment://guess.png")
        else:
            embed.set_image(url=get_raw_url(img_url))
    else:
        embed.set_image(url="")
    
    view = GuessView(str(interaction.user.id), correct_answer, "Idol", options, img_url)
    view.has_attachment = bool(file)
    if file:
        await interaction.followup.send(embed=embed, view=view, file=file)
    else:
        await interaction.followup.send(embed=embed, view=view)
    view.message = await interaction.original_response()

@guess_group.command(name="group", description="Guess the group from the card image (Multiple Choice)")
@app_commands.describe(reminder="Turn reminder on/off")
async def guess_group_sub(interaction: discord.Interaction, reminder: bool = None):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    if reminder is not None:
        await run_query("INSERT INTO ryo_reminder_settings (user_id, command, enabled) VALUES (%s, 'guess-reward', %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, reminder, reminder))

    # Only show pics added by staff using /addgtg (era='Guess', member_name='Group')
    all_groups = await run_query("SELECT DISTINCT group_name FROM ryo_cards WHERE era = 'Guess' AND member_name = 'Group' AND group_name IS NOT NULL", fetchall=True)
    if not all_groups or len(all_groups) < 4:
        return await interaction.followup.send("❌ Not enough Guess The Group cards in database (need at least 4 unique groups).", ephemeral=True)
    
    cards = await run_query("SELECT group_name, image_url FROM ryo_cards WHERE era = 'Guess' AND member_name = 'Group' AND group_name IS NOT NULL", fetchall=True)
    selected = random.choice(cards)
    correct_answer, img_url = selected[0], selected[1]

    # Generate options: Correct + 3 Random others
    others_list = [g[0] for g in all_groups if g[0] != correct_answer]
    options = [correct_answer] + random.sample(others_list, 3)
    random.shuffle(options)

    file = None
    embed = discord.Embed(title="🕵️ Which Group is this?", description="Choose the correct group name from the options below.", color=RYO_COLOR)
    if img_url:
        file = await get_discord_file_from_url(img_url, "guess.png")
        if file:
            embed.set_image(url="attachment://guess.png")
        else:
            embed.set_image(url=get_raw_url(img_url))
    else:
        embed.set_image(url="")
    
    view = GuessView(str(interaction.user.id), correct_answer, "Group", options, img_url)
    view.has_attachment = bool(file)
    if file:
        await interaction.followup.send(embed=embed, view=view, file=file)
    else:
        await interaction.followup.send(embed=embed, view=view)
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
        
        # Quest Progress
        quest_msgs = await update_quest_progress(self.user_id, 'burn', len(self.cards_to_burn))
        if quest_msgs:
            try: await interaction.followup.send("\n".join(quest_msgs), ephemeral=True)
            except: pass
            
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
        
        # Quest Progress
        quest_msgs = await update_quest_progress(self.sender_id, 'gift', len(self.cards_to_gift))
        if quest_msgs:
            try: await interaction.followup.send("\n".join(quest_msgs), ephemeral=True)
            except: pass
            
        total_stars = sum(c['rarity'] for c in self.cards_to_gift)
        
        embed = discord.Embed(
            description="**Gift successfully sent!**\n\n",
            color=RYO_COLOR
        )
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

        card_lines = []
        for c in self.cards_to_gift[:15]:
            stars = get_rarity_display(c['rarity'], c.get('category', 'regular'), c.get('era'), c.get('card_id'))
            card_lines.append(f"x1 `{c['card_id']}` {stars} **{c['group_name']} {c['member_name']}**\n({c['era']})")
        
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
    def __init__(self, title, cards, timeout=60, user=None, filters=None, author_name=None, user_tags=None, user_rules=None):
        super().__init__(timeout=timeout)
        self.title = title
        self.cards = cards
        self.page = 0
        self.per_page = 6
        self.total_pages = (len(cards) - 1) // self.per_page + 1
        self.user = user
        self.filters = filters
        self.author_name = author_name
        self.user_tags = user_tags or []
        self.user_rules = user_rules or []

    def create_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        current_cards = self.cards[start:end]
        
        embed = discord.Embed(color=RYO_COLOR)
        if self.user:
            name = self.author_name or f"{self.user.name} is viewing a card!"
            embed.set_author(name=name, icon_url=self.user.display_avatar.url)
        else:
            embed.title = self.title
        
        tag_to_emoji = {name.lower(): emoji for name, emoji in self.user_tags}
        
        card_list = []
        for card in current_cards:
            if len(card) >= 5:
                cid, member, era, grp, rar = card[:5]
                copies = card[7] if len(card) > 7 else None
                cat = card[6] if len(card) > 6 else "regular"
                stars = get_rarity_display(rar, cat, era, cid)
                
                # Tag logic
                card_emojis = []
                for r_tag_name, r_member, r_group, r_era, r_card_id in self.user_rules:
                    if matches_tag_rule(cid, member, era, grp, (r_member, r_group, r_era, r_card_id)):
                        emoji = tag_to_emoji.get(r_tag_name.lower())
                        if emoji and emoji not in card_emojis:
                            card_emojis.append(emoji)
                
                tag_prefix = "".join(card_emojis) + " " if card_emojis else ""
                
                name_line = f"{tag_prefix}**{grp} {member}**" if grp and member else f"{tag_prefix}**{grp or member}**"
                
                line = f"{name_line}\n{stars} ({era})\n`{cid}`"
                
                if copies is not None and copies > 1:
                    line += f" __**{copies}**__ **copies**"
                
                card_list.append(line)
        
        if not card_list:
            embed.description = "No cards found."
        else:
            embed.description = "\n".join(card_list)
            
        total_display = len(self.cards)
        if self.cards and len(self.cards[0]) > 7:
            total_display = sum(c[7] for c in self.cards if c[7] is not None)
            
        # Footer: Page X/Y | Filters: ...
        footer_text = f"Page {self.page + 1}/{self.total_pages}"
        if self.filters:
            filter_parts = []
            for k, v in self.filters.items():
                if v:
                    filter_parts.append(f"{k.title()}: {v}")
            if filter_parts:
                footer_text += f" | Filters: {', '.join(filter_parts)}"
        footer_text += f" | Total: {total_display}"
        
        embed.set_footer(text=footer_text)
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

    @discord.ui.button(emoji="📠", style=discord.ButtonStyle.success)
    async def copy_ids(self, interaction: discord.Interaction, button: discord.ui.Button):
        start = self.page * self.per_page
        end = start + self.per_page
        current_cards = self.cards[start:end]
        
        cids = [c[0] for c in current_cards]
        if not cids:
            return await interaction.response.send_message("No cards on this page.", ephemeral=True)
            
        await interaction.response.send_message(f"`{', '.join(cids)}`", ephemeral=True)

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
        
        embed = discord.Embed(title=f"📚 {title_str}", description=f"## **{owned_count} / {total_count} ({int(percentage)}%)**\n`{progress_bar}`", color=RYO_COLOR)
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
    HEADER_HEIGHT = 170
    SIDE_PADDING = 8
    TOP_PADDING = 4
    BOTTOM_PADDING = 8
    
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
            font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 90)
        except:
            try:
                font = ImageFont.truetype("arial.ttf", 90)
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
        
        # Position text
        draw.text(
            ((total_w - text_w) // 2, 10),
            progress_text,
            fill=(255, 255, 255),
            font=font
        )
        
        # Draw Visual Progress Bar in Image
        bar_w = total_w - 60
        bar_h = 44
        bar_x = (total_w - bar_w) // 2
        bar_y = 110
        
        # Background of bar
        draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=(50, 50, 50))
        # Fill of bar
        fill_w = (percentage / 100) * bar_w
        if fill_w > 0:
            draw.rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], fill=(210, 180, 100)) # Golden/Sand color
    
    session = await get_session()
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
                        safety_margin = 0
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
                            # Preserve alpha to avoid black background in transparent areas
                            alpha = card_img.getchannel('A')
                            # Grayscale and darken
                            gray = ImageOps.grayscale(card_img.convert("RGB"))
                            gray = Image.eval(gray, lambda x: int(x * 0.5)) # 50% brightness
                            card_img = gray.convert("RGBA")
                            card_img.putalpha(alpha)
                        
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
    query = "SELECT card_id, member_name, era, group_name, rarity, image_url, category FROM ryo_cards WHERE 1=1 AND COALESCE(LOWER(category), '') != 'guess_minigame' AND COALESCE(LOWER(era), '') != 'guess'"
    params = []
    filter_labels = []
    
    if group:
        parts = [p.strip().lower() for p in group.split(",") if p.strip()]
        if parts:
            query += " AND (" + " OR ".join(["LOWER(group_name) LIKE %s"] * len(parts)) + ")"
            params.extend([f"%{p}%" for p in parts])
            filter_labels.append(f"Group: {group}")
    if era:
        parts = [p.strip().lower() for p in era.split(",") if p.strip()]
        if parts:
            query += " AND (" + " OR ".join(["LOWER(era) LIKE %s"] * len(parts)) + ")"
            params.extend([f"%{p}%" for p in parts])
            filter_labels.append(f"Era: {era}")
    if name:
        parts = [p.strip().lower() for p in name.split(",") if p.strip()]
        if parts:
            query += " AND (" + " OR ".join(["LOWER(member_name) LIKE %s"] * len(parts)) + ")"
            params.extend([f"%{p}%" for p in parts])
            filter_labels.append(f"Name: {name}")
    if rarity:
        query += " AND rarity = %s"
        params.append(rarity)
        filter_labels.append(f"Rarity: {rarity}★")
    if card_id:
        parts = [p.strip().lower() for p in card_id.split(",") if p.strip()]
        if parts:
            query += " AND LOWER(card_id) IN (" + ", ".join(["%s"] * len(parts)) + ")"
            params.extend(parts)
            filter_labels.append(f"ID: `{card_id}`")
        
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
    
    embed = discord.Embed(title=f"📚 {title_str}", description=f"## **{owned_count} / {total_count} ({int(percentage)}%)**\n`{progress_bar}`", color=RYO_COLOR)
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
    user: discord.Member = None,
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
        cid, mname, cera, cgroup, crarity, cimg, ccat, copies = r[:8]

        # STRICT EXCLUSION: Guess minigame cards should NEVER appear in public inventory
        is_guess = (ccat or "").lower() == "guess_minigame" or (cera or "").lower() == "guess"
        if is_guess:
            continue

        if name:
            parts = [p.strip().lower() for p in name.split(",") if p.strip()]
            if parts and not any(p in (mname or "").strip().lower() for p in parts): continue
        if group:
            parts = [p.strip().lower() for p in group.split(",") if p.strip()]
            if parts and not any(p in (cgroup or "").strip().lower() for p in parts): continue
        if era:
            parts = [p.strip().lower() for p in era.split(",") if p.strip()]
            if parts and not any(p in (cera or "").strip().lower() for p in parts): continue
        if rarity and rarity != crarity: continue
        
        if category:
            parts = [p.strip().lower() for p in category.split(",") if p.strip()]
            cat_val = (ccat or "regular").lower()
            if parts and not any(p in cat_val for p in parts): continue
                
        filtered.append(r)
        
    if not filtered:
        return await interaction.followup.send("❌ No cards found matching those filters.")
        
    filters = {
        "name": name,
        "group": group,
        "era": era,
        "rarity": rarity,
        "category": category
    }
    
    user_tags = await run_query("SELECT name, emoji FROM ryo_tags WHERE user_id = %s", (uid,), fetchall=True)
    user_rules = await run_query("SELECT tag_name, member_name, group_name, era, card_id FROM ryo_tag_rules WHERE user_id = %s", (uid,), fetchall=True)
    
    view = InventoryView(
        f"{target_user.name}'s Inventory", 
        filtered, 
        user=target_user, 
        filters=filters, 
        author_name=f"{target_user.name}'s inventory!",
        user_tags=user_tags,
        user_rules=user_rules
    )
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
    q = "SELECT card_id, member_name, era, group_name, rarity, image_url, category FROM ryo_cards WHERE COALESCE(LOWER(category), '') != 'guess_minigame' AND COALESCE(LOWER(era), '') != 'guess' ORDER BY rarity DESC, member_name ASC"
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
        
        if unowned and cid in user_inventory: continue
        if name:
            parts = [p.strip().lower() for p in name.split(",") if p.strip()]
            if parts and not any(p in (mname or "").strip().lower() for p in parts): continue
        if group:
            parts = [p.strip().lower() for p in group.split(",") if p.strip()]
            if parts and not any(p in (cgroup or "").strip().lower() for p in parts): continue
        if era:
            parts = [p.strip().lower() for p in era.split(",") if p.strip()]
            if parts and not any(p in (cera or "").strip().lower() for p in parts): continue
        if rarity and rarity != crarity: continue
        
        if category:
            parts = [p.strip().lower() for p in category.split(",") if p.strip()]
            cat_val = (ccat or "regular").lower()
            if parts and not any(p in cat_val for p in parts): continue
                
        filtered.append(r)
        
    if not filtered:
        return await interaction.followup.send("❌ No cards found matching those filters.")
        
    filters = {
        "name": name,
        "group": group,
        "era": era,
        "rarity": rarity,
        "category": category,
        "unowned": "Yes" if unowned else None
    }
    view = InventoryView("Card Search Results", filtered, user=interaction.user, filters=filters, author_name=f"{interaction.user.name} is searching for cards!")
    await interaction.followup.send(embed=view.create_embed(), view=view)

# --- Marketplace System ---
class MarketView(discord.ui.View):
    def __init__(self, title, listings, user_id=None, filters=None):
        super().__init__(timeout=120)
        self.title = title
        self.listings = listings
        self.page = 0
        self.per_page = 6
        self.total_pages = max(1, (len(listings) - 1) // self.per_page + 1)
        self.user_id = user_id
        self.filters = filters

    def create_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        current_listings = self.listings[start:end]
        
        embed = discord.Embed(title=self.title, color=RYO_COLOR)
        
        lines = []
        for item in current_listings:
            market_id, seller_id, card_id, paint_price, glue_price, member, era, grp, rar, img, cat, unique_code = item[:12]
            
            name_line = f"**{grp} {member}**" if grp and member else f"**{grp or member}**"
            stars = get_rarity_display(rar, cat or "regular", era, card_id)
            
            price_parts = []
            if paint_price > 0:
                price_parts.append(f"🎨 `{paint_price:,}` Paint")
            if glue_price > 0:
                price_parts.append(f"🧪 `{glue_price:,}` Glue")
            price_str = " + ".join(price_parts) or "Free"
            
            u_code_add = f" (Guess Code: `{unique_code}`)" if unique_code else ""
            
            line = f"{name_line}\n{stars} ({era}){u_code_add}\n`{card_id}` | Market ID: `{market_id}`\n💰 Price: {price_str} | Seller: <@{seller_id}>"
            lines.append(line)
            
        if not lines:
            embed.description = "No items listed on the marketplace."
        else:
            embed.description = "\n\n".join(lines)
            first_img = current_listings[0][9]
            if first_img:
                embed.set_thumbnail(url=first_img)
                
        footer_text = f"Page {self.page + 1}/{self.total_pages}"
        if self.filters:
            f_parts = []
            for k, v in self.filters.items():
                if v is not None and v != "" and v is not False:
                    f_parts.append(f"{k.title()}: {v}")
            if f_parts:
                footer_text += f" | Filters: {', '.join(f_parts)}"
        footer_text += f" | Total Listings: {len(self.listings)}"
        embed.set_footer(text=footer_text)
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

    @discord.ui.button(emoji="📠", style=discord.ButtonStyle.success)
    async def copy_market_ids(self, interaction: discord.Interaction, button: discord.ui.Button):
        start = self.page * self.per_page
        end = start + self.per_page
        current_listings = self.listings[start:end]
        
        m_ids = [item[0] for item in current_listings]
        if not m_ids:
            return await interaction.response.send_message("No listings on this page.", ephemeral=True)
            
        await interaction.response.send_message(f"`{', '.join(m_ids)}`", ephemeral=True)

async def generate_valid_market_id():
    chars = string.ascii_uppercase + string.digits
    for _ in range(10):
        m_id = "M-" + "".join(random.choices(chars, k=5))
        exists = await run_query("SELECT 1 FROM ryo_marketplace WHERE market_id = %s", (m_id,), fetchone=True)
        if not exists:
            return m_id
    return "M-" + "".join(random.choices(chars, k=8))

market_group = app_commands.Group(name="market", description="Card marketplace commands")

@market_group.command(name="sell", description="List one or more cards from your inventory on the marketplace")
@app_commands.describe(
    codes="Card IDs from your inventory separated by commas (e.g. PP01, PP01, PP02)",
    paint_price="The paint price for EACH listed card (default: 0)",
    glue_price="The glue price for EACH listed card (default: 0)"
)
async def market_sell(interaction: discord.Interaction, codes: str, paint_price: int = 0, glue_price: int = 0):
    await interaction.response.defer(ephemeral=True)
    seller_id = str(interaction.user.id)
    
    await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (seller_id,))
    
    parts = [p.strip().lower() for p in codes.split(",") if p.strip()]
    if not parts:
        return await interaction.followup.send("❌ Please provide at least one valid card ID.", ephemeral=True)
        
    if paint_price < 0 or glue_price < 0:
        return await interaction.followup.send("❌ Price cannot be negative.", ephemeral=True)
        
    if paint_price == 0 and glue_price == 0:
         return await interaction.followup.send("❌ You must specify a price of at least 1 Paint or 1 Glue.", ephemeral=True)
         
    needed_counts = {}
    for p in parts:
        needed_counts[p] = needed_counts.get(p, 0) + 1
        
    canonical_codes = {}
    for code_lower in needed_counts:
        db_card = await run_query("SELECT card_id FROM ryo_cards WHERE LOWER(card_id) = %s", (code_lower,), fetchone=True)
        if not db_card:
            return await interaction.followup.send(f"❌ Card ID `{code_lower}` does not exist in the registry.", ephemeral=True)
        canonical_codes[code_lower] = db_card[0]
        
    for code_lower, count in needed_counts.items():
        db_card = await run_query("SELECT category, era FROM ryo_cards WHERE LOWER(card_id) = %s", (code_lower,), fetchone=True)
        is_guess = False
        if db_card:
            cat_db = db_card[0] if db_card[0] else 'regular'
            era_db = db_card[1] if db_card[1] else 'regular'
            is_guess = (cat_db.lower() == 'guess_minigame' or era_db.lower() == 'guess')
        if is_guess:
            return await interaction.followup.send(f"❌ Guess minigame cards cannot be listed on the marketplace.", ephemeral=True)

        owned_count_res = await run_query(
            "SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s AND LOWER(card_id) = %s",
            (seller_id, code_lower),
            fetchone=True
        )
        owned_count = owned_count_res[0] if owned_count_res else 0
        if owned_count < count:
            return await interaction.followup.send(
                f"❌ You do not have enough copies of `{canonical_codes[code_lower]}` in your inventory. "
                f"You requested to sell {count} copy(ies) but only own {owned_count}.",
                ephemeral=True
            )
            
    listed_cards_details = []
    for code_lower, count in needed_counts.items():
        for _ in range(count):
            inv_row = await run_query(
                "SELECT id, unique_code FROM ryo_inventory WHERE user_id = %s AND LOWER(card_id) = %s LIMIT 1",
                (seller_id, code_lower),
                fetchone=True
            )
            inv_id, unique_code = inv_row
            
            await run_query("DELETE FROM ryo_inventory WHERE id = %s", (inv_id,))
            
            market_id = await generate_valid_market_id()
            
            await run_query(
                "INSERT INTO ryo_marketplace (market_id, seller_id, card_id, paint_price, glue_price, unique_code) VALUES (%s, %s, %s, %s, %s, %s)",
                (market_id, seller_id, canonical_codes[code_lower], paint_price, glue_price, unique_code)
            )
            listed_cards_details.append(f"• `{canonical_codes[code_lower]}` (Market ID: `{market_id}`)")
            
    prices_desc = []
    if paint_price > 0: prices_desc.append(f"🎨 `{paint_price:,}` Paint")
    if glue_price > 0: prices_desc.append(f"🧪 `{glue_price:,}` Glue")
    prices_str = " + ".join(prices_desc)
    
    embed = discord.Embed(
        title="🛒 Cards Listed on Marketplace!",
        description=f"You successfully listed the following {len(listed_cards_details)} card(s) for **{prices_str}** each:\n" + "\n".join(listed_cards_details),
        color=RYO_COLOR
    )
    embed.set_footer(text="Use /market remove to cancel any listing, or /market view to check them.")
    await interaction.followup.send(embed=embed, ephemeral=True)

@market_group.command(name="remove", description="Remove your own cards listed on the marketplace")
@app_commands.describe(
    market_ids="The Market ID(s) to remove (separated by commas for multiple)"
)
async def market_remove(interaction: discord.Interaction, market_ids: str):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    
    parts = [p.strip().upper() for p in market_ids.split(",") if p.strip()]
    if not parts:
        return await interaction.followup.send("❌ Please provide at least one valid Market ID.", ephemeral=True)
        
    succeeded = []
    failed_not_found = []
    failed_not_owner = []
    
    for m_id in parts:
        row = await run_query("SELECT seller_id, card_id, unique_code FROM ryo_marketplace WHERE UPPER(market_id) = %s", (m_id,), fetchone=True)
        if not row:
            failed_not_found.append(m_id)
            continue
            
        seller_id, card_id, unique_code = row
        if seller_id != user_id:
            failed_not_owner.append(m_id)
            continue
            
        await run_query("DELETE FROM ryo_marketplace WHERE UPPER(market_id) = %s", (m_id,))
        await run_query("INSERT INTO ryo_inventory (user_id, card_id, unique_code) VALUES (%s, %s, %s)", (user_id, card_id, unique_code))
        succeeded.append(f"• `{card_id}` (Market ID: `{m_id}`)")
        
    response_lines = []
    if succeeded:
         response_lines.append(f"✅ **Successfully removed and returned to inventory:**\n" + "\n".join(succeeded))
    if failed_not_found:
         response_lines.append(f"❌ **Market IDs not found:** {', '.join([f'`{f}`' for f in failed_not_found])}")
    if failed_not_owner:
         response_lines.append(f"❌ **Market IDs you do not own:** {', '.join([f'`{f}`' for f in failed_not_owner])}")
         
    embed = discord.Embed(
         title="🗑️ Marketplace Removal",
         description="\n\n".join(response_lines),
         color=RYO_COLOR
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

@market_group.command(name="buy", description="Buy one or more cards listed on the marketplace using their Market IDs")
@app_commands.describe(
    market_ids="The Market ID(s) of the card(s) you want to buy (separated by commas for multiple)"
)
async def market_buy(interaction: discord.Interaction, market_ids: str):
    await interaction.response.defer()
    buyer_id = str(interaction.user.id)
    
    await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (buyer_id,))
    
    parts = [p.strip().upper() for p in market_ids.split(",") if p.strip()]
    if not parts:
        return await interaction.followup.send("❌ Please provide at least one valid Market ID.", ephemeral=True)
        
    parts = list(dict.fromkeys(parts))
    
    buyer_row = await run_query("SELECT paint, glue FROM ryo_users WHERE user_id = %s", (buyer_id,), fetchone=True)
    buyer_paint = buyer_row[0] if buyer_row else 0
    buyer_glue = buyer_row[1] if buyer_row else 0
    
    listings = []
    not_found = []
    own_listings = []
    
    for m_id in parts:
        row = await run_query(
            "SELECT m.market_id, m.seller_id, m.card_id, m.paint_price, m.glue_price, m.unique_code, c.member_name, c.era, c.group_name, c.rarity, c.image_url, c.category "
            "FROM ryo_marketplace m "
            "JOIN ryo_cards c ON m.card_id = c.card_id "
            "WHERE UPPER(m.market_id) = %s",
            (m_id,),
            fetchone=True
        )
        if not row:
            not_found.append(m_id)
            continue
            
        seller_id = row[1]
        if seller_id == buyer_id:
            own_listings.append(m_id)
            continue
            
        listings.append(row)
        
    error_parts = []
    if not_found:
        error_parts.append(f"❌ **Market ID(s) not found:** {', '.join([f'`{f}`' for f in not_found])}")
    if own_listings:
        error_parts.append(f"❌ **You cannot buy your own listings:** {', '.join([f'`{o}`' for o in own_listings])}")
        
    if error_parts:
        return await interaction.followup.send("\n".join(error_parts))
        
    if not listings:
        return await interaction.followup.send("❌ No valid listings selected to purchase.")
        
    total_paint = sum(r[3] for r in listings)
    total_glue = sum(r[4] for r in listings)
    
    if buyer_paint < total_paint or buyer_glue < total_glue:
        costs_desc = []
        if total_paint > 0: costs_desc.append(f"🎨 `{total_paint:,}` Paint")
        if total_glue > 0: costs_desc.append(f"🧪 `{total_glue:,}` Glue")
        costs_str = " and ".join(costs_desc)
        
        balance_desc = []
        balance_desc.append(f"🎨 `{buyer_paint:,}` Paint")
        balance_desc.append(f"🧪 `{buyer_glue:,}` Glue")
        balance_str = " and ".join(balance_desc)
        
        return await interaction.followup.send(
            f"❌ **Insufficient funds!**\n"
            f"The total cost for these {len(listings)} card(s) is **{costs_str}**.\n"
            f"Your current balance is **{balance_str}**."
        )
        
    successful_buys = []
    for row in listings:
        market_id, seller_id, card_id, paint_price, glue_price, unique_code, member, era, grp, rar, img, cat = row[:12]
        
        res = await run_query("DELETE FROM ryo_marketplace WHERE UPPER(market_id) = %s", (market_id.upper(),), return_rowcount=True)
        if res <= 0:
            continue
            
        await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (seller_id,))
        
        await run_query("UPDATE ryo_users SET paint = paint - %s, glue = glue - %s WHERE user_id = %s", (paint_price, glue_price, buyer_id))
        
        await run_query("UPDATE ryo_users SET paint = paint + %s, glue = glue + %s WHERE user_id = %s", (paint_price, glue_price, seller_id))
        
        await run_query("INSERT INTO ryo_inventory (user_id, card_id, unique_code) VALUES (%s, %s, %s)", (buyer_id, card_id, unique_code))
        
        stars = get_rarity_display(rar, cat, era, card_id)
        name_line = f"**{grp} {member}**" if grp and member else f"**{grp or member}**"
        
        price_parts = []
        if paint_price > 0: price_parts.append(f"🎨 `{paint_price:,}`")
        if glue_price > 0: price_parts.append(f"🧪 `{glue_price:,}`")
        price_str = " + ".join(price_parts) or "Free"
        
        successful_buys.append(f"• {name_line} {stars} ({era}) — Bought from <@{seller_id}> for {price_str}")
        
    if not successful_buys:
        return await interaction.followup.send("❌ Error: All selected listings were already purchased or removed.")
        
    embed = discord.Embed(
        title="🎉 Marketplace Purchase Successful!",
        description=f"Congratulations! You've successfully purchased {len(successful_buys)} card(s):\n\n" + "\n".join(successful_buys),
        color=RYO_COLOR
    )
    if len(listings) == 1 and listings[0][10]: 
        embed.set_image(url=listings[0][10])
        
    await interaction.followup.send(content=f"<@{buyer_id}>", embed=embed)

@market_group.command(name="view", description="View all cards listed in the marketplace")
@app_commands.describe(
    name="Filter by member name (can use commas)",
    group="Filter by group (can use commas)",
    era="Filter by era (can use commas)",
    rarity="Filter by rarity",
    unowned="Only show cards you do not own in your inventory",
    self_listed="Filter: True = only yours, False = only others",
    paint_price="Filter: Max paint price",
    glue_price="Filter: Max glue price"
)
async def market_view(
    interaction: discord.Interaction,
    name: str = None,
    group: str = None,
    era: str = None,
    rarity: int = None,
    unowned: bool = False,
    self_listed: bool = None,
    paint_price: int = None,
    glue_price: int = None
):
    await interaction.response.defer()
    viewer_id = str(interaction.user.id)
    
    q = (
        "SELECT m.market_id, m.seller_id, m.card_id, m.paint_price, m.glue_price, "
        "c.member_name, c.era, c.group_name, c.rarity, c.image_url, c.category, m.unique_code "
        "FROM ryo_marketplace m "
        "JOIN ryo_cards c ON m.card_id = c.card_id "
        "ORDER BY m.listed_at DESC"
    )
    results = await run_query(q, fetchall=True)
    if not results:
        return await interaction.followup.send("🛒 No items are currently listed on the marketplace.")
        
    owned_ids = set()
    if unowned:
        owned_rows = await run_query("SELECT DISTINCT card_id FROM ryo_inventory WHERE user_id = %s", (viewer_id,), fetchall=True)
        owned_ids = {r[0].lower() for r in owned_rows} if owned_rows else set()
        
    filtered_listings = []
    for row in results:
        m_id, seller_id, card_id, p_price, g_price, member_name, c_era, group_name, c_rarity, img, cat, unique_code = row[:12]
        
        if name:
            parts = [p.strip().lower() for p in name.split(",") if p.strip()]
            if parts and not any(p in (member_name or "").strip().lower() for p in parts):
                continue
                
        if group:
            parts = [p.strip().lower() for p in group.split(",") if p.strip()]
            if parts and not any(p in (group_name or "").strip().lower() for p in parts):
                continue
                
        if era:
            parts = [p.strip().lower() for p in era.split(",") if p.strip()]
            if parts and not any(p in (c_era or "").strip().lower() for p in parts):
                continue
                
        if rarity is not None and rarity != c_rarity:
            continue
            
        if unowned and card_id.lower() in owned_ids:
            continue
            
        if self_listed is not None:
            is_self = (seller_id == viewer_id)
            if self_listed != is_self:
                continue
                
        if paint_price is not None and p_price > paint_price:
            continue
            
        if glue_price is not None and g_price > glue_price:
            continue
            
        filtered_listings.append(row)
        
    if not filtered_listings:
        return await interaction.followup.send("❌ No marketplace listings match your filter criteria.")
        
    filters_summary = {
        "name": name,
        "group": group,
        "era": era,
        "rarity": rarity,
        "unowned": "True" if unowned else None,
        "self_listed": "True" if self_listed else ("False" if self_listed is False else None),
        "paint_price": f"<={paint_price}" if paint_price is not None else None,
        "glue_price": f"<={glue_price}" if glue_price is not None else None
    }
    
    view = MarketView("🛒 Card Marketplace", filtered_listings, user_id=viewer_id, filters=filters_summary)
    await interaction.followup.send(embed=view.create_embed(), view=view)

class QuestsView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.category = "Daily"
        self.page = 0
        self.per_page = 5
        self.total_pages = 1

    @discord.ui.button(label="Daily", style=discord.ButtonStyle.primary, row=0)
    async def daily_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.category = "Daily"
        self.page = 0
        await self.show_quests(interaction, "Daily")

    @discord.ui.button(label="Weekly", style=discord.ButtonStyle.primary, row=0)
    async def weekly_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.category = "Weekly"
        self.page = 0
        await self.show_quests(interaction, "Weekly")

    @discord.ui.button(label="Events", style=discord.ButtonStyle.primary, row=0)
    async def events_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.category = "Events"
        self.page = 0
        await self.show_quests(interaction, "Events")

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary, row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
        if self.page > 0:
            self.page -= 1
            await self.show_quests(interaction, self.category)
        else:
            await interaction.response.send_message("⚠️ Already on the first page!", ephemeral=True)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
        if self.page < self.total_pages - 1:
            self.page += 1
            await self.show_quests(interaction, self.category)
        else:
            await interaction.response.send_message("⚠️ Already on the last page!", ephemeral=True)

    async def show_quests(self, interaction: discord.Interaction, category: str):
        try:
            if str(interaction.user.id) != self.user_id:
                return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
            
            if not interaction.response.is_done():
                await interaction.response.defer()
            uid = self.user_id
            self.category = category
            all_quests = await run_query("SELECT quest_id, name, description, target_value, reward_paint, reward_glue, is_daily FROM ryo_quests WHERE category = %s ORDER BY quest_id ASC", (category,), fetchall=True)
            
            if not all_quests:
                embed = discord.Embed(title=f"📜 {category} Quests", description="No quests available in this category.", color=RYO_COLOR)
                self.total_pages = 1
                for item in self.children:
                    if isinstance(item, discord.ui.Button):
                        if str(item.emoji) in ["◀", "▶"]:
                            item.disabled = True
                return await interaction.edit_original_response(embed=embed, view=self)

            now_ms = int(time.time() * 1000)
            day_ms = 86400000
            quest_results = []
            staff_mode = is_staff(interaction) or is_mod(interaction)

            user_quests_rows = await run_query("SELECT quest_id, current_value, completed, last_reset FROM user_quests WHERE user_id = %s", (uid,), fetchall=True)
            user_quests_dict = {row[0]: (row[1], row[2], row[3]) for row in user_quests_rows} if user_quests_rows else {}

            for i, (q_id, name, desc, target, r_paint, r_glue, is_daily) in enumerate(all_quests, 1):
                prog = user_quests_dict.get(q_id)
                current_val, is_completed, last_reset = (0, False, 0)
                if prog:
                    current_val, is_completed, last_reset = prog
                    if is_daily:
                        current_day = (now_ms // day_ms) * day_ms
                        last_day = (last_reset // day_ms) * day_ms
                        if current_day > last_day:
                            current_val, is_completed, last_reset = (0, False, now_ms)
                            await run_query("UPDATE user_quests SET current_value = 0, completed = FALSE, last_reset = %s WHERE user_id = %s AND quest_id = %s", (last_reset, uid, q_id))
                else:
                    last_reset = now_ms
                    await run_query("INSERT INTO user_quests (user_id, quest_id, last_reset) VALUES (%s, %s, %s)", (uid, q_id, last_reset))
                
                quest_results.append((i, q_id, name, desc, current_val, target, is_completed, r_paint, r_glue))

            # Calculate pagination
            self.total_pages = max(1, (len(quest_results) - 1) // self.per_page + 1)
            if self.page >= self.total_pages:
                self.page = self.total_pages - 1
            if self.page < 0:
                self.page = 0

            # Update navigation button states dynamically
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    if str(item.emoji) == "◀":
                        item.disabled = (self.page == 0)
                    elif str(item.emoji) == "▶":
                        item.disabled = (self.page >= self.total_pages - 1)

            now_ts = int(time.time())
            day_ts = 86400
            next_daily_reset = (now_ts // day_ts + 1) * day_ts
            
            # Calculate next Monday 00:00 UTC for weekly
            days_until_monday = (7 - datetime.datetime.fromtimestamp(now_ts, datetime.timezone.utc).weekday()) % 7
            if days_until_monday == 0: days_until_monday = 7
            next_weekly_reset = ((now_ts // day_ts) + days_until_monday) * day_ts
            
            reset_time = next_daily_reset if category == "Daily" else next_weekly_reset
            reset_text = f"\n\n⏳ **Resets:** <t:{reset_time}:R>" if category != "Events" else ""

            embed = discord.Embed(title=f"📜 {category} Quests", description=f"Complete these tasks to earn rewards!{reset_text}", color=RYO_COLOR)
            
            # Sliced page results
            start = self.page * self.per_page
            end = start + self.per_page
            page_results = quest_results[start:end]

            for num, q_id, name, desc, val, target, completed, r_paint, r_glue in page_results:
                status = "✅ **COMPLETED**" if completed else f"🔄 **{val}/{target}**"
                bar_len = 10
                filled = int((val / target) * bar_len) if target > 0 else 0
                filled = min(filled, bar_len)
                bar = "🟦" * filled + "⬜" * (bar_len - filled)
                
                rewards = []
                if r_paint > 0: rewards.append(f"`{r_paint} Paint`")
                if r_glue > 0: rewards.append(f"`{r_glue} Glue`")
                reward_txt = " + ".join(rewards)
                
                field_name = f"{num}. {name} ({reward_txt})"
                
                embed.add_field(name=field_name, value=f"{desc}\n{bar} {status}", inline=False)
            
            if category == "Events":
                embed.set_footer(text=f"Page {self.page + 1} of {self.total_pages} | Event quests are limited-time.")
            else:
                reset_name = "Daily" if category == "Daily" else "Weekly"
                embed.set_footer(text=f"Page {self.page + 1} of {self.total_pages} | {reset_name} quests reset automatically.")

            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            print(f"❌ Error displaying quests: {e}")
            import traceback
            traceback.print_exc()
            try:
                await interaction.followup.send(f"❌ Failed to load quests: {e}", ephemeral=True)
            except:
                pass

@bot.tree.command(name="quests", description="View your active quests and progress")
async def quests(interaction: discord.Interaction):
    await interaction.response.defer()
    view = QuestsView(str(interaction.user.id))
    await view.show_quests(interaction, "Daily")

@bot.tree.command(name="addquest", description="Add a new quest (STAFF/MOD ONLY)")
@app_commands.describe(
    name="Name of the quest",
    description="What the user needs to do",
    quest_type="Type (draw, gift, burn)",
    category="Category (Daily, Weekly, Events)",
    target="Target number to complete",
    paint="Paint reward",
    glue="Glue reward",
    is_daily="Whether it's a daily quest (reset every 24h)"
)
async def addquest(interaction: discord.Interaction, name: str, description: str, quest_type: str, category: str, target: int, paint: int = 0, glue: int = 0, is_daily: bool = True):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer()
    
    # Check if a quest with same name and category already exists
    existing = await run_query("SELECT quest_id FROM ryo_quests WHERE LOWER(name) = %s AND LOWER(category) = %s", (name.lower(), category.lower()), fetchone=True)
    if existing:
        quest_id = existing[0]
        # Update details in case any of them changed
        await run_query(
            "UPDATE ryo_quests SET description = %s, quest_type = %s, target_value = %s, reward_paint = %s, reward_glue = %s, is_daily = %s WHERE quest_id = %s",
            (description, quest_type, target, paint, glue, is_daily, quest_id)
        )
        # Reset progress of this specific quest for all users
        await run_query("UPDATE user_quests SET current_value = 0, completed = FALSE, last_reset = %s WHERE quest_id = %s", (int(time.time() * 1000), quest_id))
        return await interaction.followup.send(f"🔄 Quest **{name}** already exists. Updated details and reset progress for all users!")

    await run_query(
        "INSERT INTO ryo_quests (name, description, quest_type, category, target_value, reward_paint, reward_glue, is_daily) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (name, description, quest_type, category, target, paint, glue, is_daily)
    )
    await interaction.followup.send(f"✅ Quest **{name}** added successfully to **{category}**!")

@bot.tree.command(name="deletequest", description="Remove a quest (STAFF/MOD ONLY)")
@app_commands.describe(
    category="Category of the quest",
    quest_id="The ID of the quest to remove",
    num="Number from the category list"
)
async def deletequest(interaction: discord.Interaction, category: str = None, quest_id: int = None, num: int = None):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    target_id = quest_id
    if category and num:
        rows = await run_query("SELECT quest_id FROM ryo_quests WHERE category = %s ORDER BY quest_id ASC", (category,), fetchall=True)
        if rows and 0 < num <= len(rows):
            target_id = rows[num-1][0]
        else:
            return await interaction.followup.send(f"❌ Could not find quest #{num} in {category}.", ephemeral=True)

    if not target_id:
        return await interaction.followup.send("❌ Please provide a Quest ID or Category + Number.", ephemeral=True)
    
    # Check if exists
    quest = await run_query("SELECT name FROM ryo_quests WHERE quest_id = %s", (target_id,), fetchone=True)
    if not quest:
        return await interaction.followup.send(f"❌ Quest ID `{target_id}` not found.", ephemeral=True)
    
    await run_query("DELETE FROM user_quests WHERE quest_id = %s", (target_id,))
    await run_query("DELETE FROM ryo_quests WHERE quest_id = %s", (target_id,))
    await interaction.followup.send(f"🗑️ Quest **{quest[0]}** (ID: {target_id}) removed.")

# --- Pack Shop System ---

@bot.tree.command(name="shopadd", description="Add or update a card pack in the staff shop (STAFF/MOD ONLY)")
@app_commands.describe(
    id="The unique ID of the pack (e.g. group_pack, r5_pack)",
    name="The display name of the pack",
    description="The details of the pack",
    price="The price of the pack in Paint"
)
async def shop_add(
    interaction: discord.Interaction,
    id: str,
    name: str,
    description: str,
    price: int
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ This command is restricted to Release Staff and Moderators.", ephemeral=True)
        
    if price < 0:
        return await interaction.response.send_message("❌ Price cannot be negative.", ephemeral=True)
        
    pack_id_clean = id.strip()
    name_clean = name.strip()
    desc_clean = description.strip()
    
    if not pack_id_clean or not name_clean or not desc_clean:
         return await interaction.response.send_message("❌ ID, name, and description cannot be empty.", ephemeral=True)
         
    # Check if exists to choose message wording
    exists = await run_query("SELECT 1 FROM ryo_shop_packs WHERE LOWER(pack_id) = LOWER(%s)", (pack_id_clean,), fetchone=True)
    
    await run_query(
        "INSERT INTO ryo_shop_packs (pack_id, name, description, price) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (pack_id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description, price = EXCLUDED.price",
        (pack_id_clean, name_clean, desc_clean, price)
    )
    
    status = "updated" if exists else "added"
    await interaction.response.send_message(f"✅ Successfully {status} pack `{pack_id_clean}` in the shop!", ephemeral=True)

@bot.tree.command(name="shopremove", description="Remove a pack from the staff shop (STAFF/MOD ONLY)")
@app_commands.describe(id="The ID of the pack to remove")
async def shop_remove(interaction: discord.Interaction, id: str):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ This command is restricted to Release Staff and Moderators.", ephemeral=True)
        
    pack_id_clean = id.strip()
    res = await run_query("DELETE FROM ryo_shop_packs WHERE LOWER(pack_id) = LOWER(%s)", (pack_id_clean,), return_rowcount=True)
    if res > 0:
        await interaction.response.send_message(f"✅ Successfully removed pack `{pack_id_clean}` from the shop.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Pack with ID `{pack_id_clean}` not found in the shop.", ephemeral=True)

@bot.tree.command(name="shopview", description="Browse available packs in the staff shop")
async def shop_view(interaction: discord.Interaction):
    packs = await run_query("SELECT pack_id, name, description, price FROM ryo_shop_packs ORDER BY price ASC", fetchall=True)
    if not packs:
        return await interaction.response.send_message(
            "🏪 The Ryo Pack Shop is currently empty. Staff have not listed any packs yet!", 
            ephemeral=True
        )
        
    desc_lines = []
    for p in packs:
        pack_id, name, description, price = p
        is_glue = "glue" in pack_id.lower() or "glue" in name.lower() or "glue" in description.lower()
        currency_emoji = GLUE_EMOJI if is_glue else PAINT_EMOJI
        desc_lines.append(f"`{pack_id}` • **{name}**\n*{description}*\n{currency_emoji} {price:,}")
        
    embed = discord.Embed(
        title="ryo's supply closet", 
        description="\n\n".join(desc_lines),
        color=RYO_COLOR
    )
    embed.set_footer(text="use /shopbuy [id] to purchase a pack!")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="shopbuy", description="Purchase a card pack from the shop using Paint")
@app_commands.describe(
    id="The ID of the pack you want to buy",
    quantity="The number of packs to purchase",
    group="Group name (Required if buying a 5-card Guaranteed Group pack)"
)
async def shop_buy(interaction: discord.Interaction, id: str, quantity: int, group: str = None):
    await interaction.response.defer()
    buyer_id = str(interaction.user.id)
    
    if quantity <= 0:
        return await interaction.followup.send("❌ Quantity must be at least 1.")
        
    pack_id_clean = id.strip()
    pack = await run_query("SELECT pack_id, name, description, price FROM ryo_shop_packs WHERE LOWER(pack_id) = LOWER(%s)", (pack_id_clean,), fetchone=True)
    if not pack:
        return await interaction.followup.send(f"❌ Pack with ID `{pack_id_clean}` does not exist in the shop registry.")
        
    real_pack_id, pack_name, description, price = pack
    
    # Ensure user exists
    await run_query("INSERT INTO ryo_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (buyer_id,))
    
    # Check balances
    is_glue = "glue" in real_pack_id.lower() or "glue" in pack_name.lower() or "glue" in description.lower()
    user_row = await run_query("SELECT paint, glue FROM ryo_users WHERE user_id = %s", (buyer_id,), fetchone=True)
    user_paint = user_row[0] if user_row else 0
    user_glue = user_row[1] if user_row else 0
    
    total_cost = price * quantity
    if is_glue:
        if user_glue < total_cost:
            return await interaction.followup.send(
                f"❌ **Insufficient glue!**\n"
                f"Ordering {quantity}x `{pack_name}` costs **{GLUE_EMOJI} {total_cost:,} Glue**.\n"
                f"Your current balance is **{GLUE_EMOJI} {user_glue:,} Glue**."
            )
    else:
        if user_paint < total_cost:
            return await interaction.followup.send(
                f"❌ **Insufficient paint!**\n"
                f"Ordering {quantity}x `{pack_name}` costs **{PAINT_EMOJI} {total_cost:,} Paint**.\n"
                f"Your current balance is **{PAINT_EMOJI} {user_paint:,} Paint**."
            )
        
    # Analyze the pack type to determine the reward logic
    is_group_pack = False
    is_r5_pack = False
    
    pack_id_lower = real_pack_id.lower()
    name_lower = pack_name.lower()
    desc_lower = description.lower()
    
    if "group" in pack_id_lower or "5g" in pack_id_lower or "group" in name_lower or "group" in desc_lower or "guaranteed" in desc_lower:
        is_group_pack = True
    elif "r5" in pack_id_lower or "5star" in pack_id_lower or "r5" in name_lower or "r5" in desc_lower or "5-star" in desc_lower or "5 star" in desc_lower:
        is_r5_pack = True
        
    drawn_cards = []
    
    if is_group_pack:
        if not group:
            return await interaction.followup.send(
                f"❌ The `{pack_name}` is a **5 Guaranteed Group Card Pack**. You **must** specify the `group` option (group name) when buying this pack."
            )
            
        # Retrieve cards from this group
        group_cards = await run_query(
            "SELECT card_id, member_name, group_name, era, rarity, image_url, category FROM ryo_cards "
            "WHERE LOWER(group_name) = LOWER(%s) AND LOWER(category) != 'guess_minigame' AND LOWER(era) != 'guess' AND LOWER(category) != 'custom' AND (disabled IS NULL OR disabled = FALSE)",
            (group.strip(),),
            fetchall=True
        )
        
        if not group_cards:
            return await interaction.followup.send(
                f"❌ No cards found in the registry for group `{group.strip()}`. Please check the group name and try again."
            )
            
        # Draw 5 random cards per pack quantity
        drawn_cards = random.choices(group_cards, k=5 * quantity)
        
    elif is_r5_pack:
        # Retrieve all rarity 5 cards
        r5_cards = await run_query(
            "SELECT card_id, member_name, group_name, era, rarity, image_url, category FROM ryo_cards "
            "WHERE rarity = 5 AND LOWER(category) != 'guess_minigame' AND LOWER(era) != 'guess' AND LOWER(category) != 'custom' AND (disabled IS NULL OR disabled = FALSE)",
            fetchall=True
        )
        
        if not r5_cards:
            return await interaction.followup.send(
                "❌ Error: There are no R5 cards currently standardly registered in the database for drawing."
            )
            
        # Draw 1 random R5 card per pack quantity
        drawn_cards = random.choices(r5_cards, k=1 * quantity)
    else:
        # Fallback if staff added some other unrecognized pack structure, default logic: 1 random card
        all_draw_cards = await run_query(
            "SELECT card_id, member_name, group_name, era, rarity, image_url, category FROM ryo_cards "
            "WHERE LOWER(category) != 'guess_minigame' AND LOWER(era) != 'guess' AND LOWER(category) != 'custom' AND (disabled IS NULL OR disabled = FALSE)",
            fetchall=True
        )
        
        if not all_draw_cards:
            return await interaction.followup.send(
                "❌ Error: No cards are registered in the database."
            )
        drawn_cards = random.choices(all_draw_cards, k=1 * quantity)

    # Perform currency subtraction
    if is_glue:
        await run_query("UPDATE ryo_users SET glue = glue - %s WHERE user_id = %s", (total_cost, buyer_id))
    else:
        await run_query("UPDATE ryo_users SET paint = paint - %s WHERE user_id = %s", (total_cost, buyer_id))
    
    # Process granting cards
    card_counts = {}
    for card in drawn_cards:
        cid = card[0]
        if cid not in card_counts:
            card_counts[cid] = {
                "details": card,
                "count": 0
            }
        card_counts[cid]["count"] += 1
        await grant_card(buyer_id, cid)
        
    results_detailed = []
    for cid, item in card_counts.items():
        card = item["details"]
        cnt = item["count"]
        
        member_name = card[1]
        group_name = card[2]
        era_name = card[3]
        rarity = card[4]
        category = card[6] if len(card) > 6 else "regular"
        
        stars = get_rarity_display(rarity, category, era_name, cid)
        name_line = f"**{group_name} {member_name}**" if group_name and member_name else f"**{group_name or member_name}**"
        results_detailed.append(f"• **{cnt}x** {stars} ({era_name}) `{cid}` {name_line}")
        
    total_stars = sum(c[4] for c in drawn_cards)
    
    currency_label = f"Glue {GLUE_EMOJI}" if is_glue else f"Paint {PAINT_EMOJI}"
    embed = discord.Embed(
        title=f"🎉 Pack Opened: {pack_name}!",
        description=f"<@{buyer_id}> bought **{quantity}x** `{pack_name}` for **{total_cost:,} {currency_label}**!\n\n"
                    f"**Cards Received ({len(drawn_cards)} total):**\n" + "\n".join(results_detailed),
        color=RYO_COLOR
    )
    
    if len(drawn_cards) == 1 and drawn_cards[0][5]:
        embed.set_image(url=drawn_cards[0][5])
        
    embed.set_footer(text=f"Total Stars: {total_stars} ⭐")
    await interaction.followup.send(content=f"<@{buyer_id}>", embed=embed)

@bot.tree.command(name="cooldowns", description="Check how much time is left to use your commands")
async def cooldowns(interaction: discord.Interaction):
    await interaction.response.defer()
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
        "craft": 3600,
        "claim": 120
    }
    
    # Query regular cooldowns
    cooldown_rows = await run_query("SELECT command, last_used FROM ryo_cooldowns WHERE user_id = %s", (uid,), fetchall=True)
    last_used_map = {row[0]: row[1] for row in cooldown_rows}
    
    # Query reminder settings
    rem_res = await run_query("SELECT command, enabled FROM ryo_reminder_settings WHERE user_id = %s", (uid,), fetchall=True) or []
    rem_settings = {row[0]: row[1] for row in rem_res}
    
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
        is_rem_on = rem_settings.get(cmd, True)
        rem_status = "🔔" if is_rem_on else "🔕"

        # Display nicely
        display_name = cmd.replace("-", " ").title()
        desc.append(f"**{display_name}** {rem_status}: {format_time(rem)}")
        
    # Guess Rewards
    rem_guess = 180 - (now - last_guess)
    is_guess_rem_on = rem_settings.get("guess-reward", True)
    guess_rem_status = "🔔" if is_guess_rem_on else "🔕"
    desc.append(f"**Guess Rewards** {guess_rem_status}: {format_time(rem_guess)}")
    
    embed = discord.Embed(
        title=f"⏳ {interaction.user.name}'s Cooldowns",
        description="\n".join(desc),
        color=RYO_COLOR
    )
    embed.set_footer(text="Commands you haven't used yet show as Ready.")
    await interaction.followup.send(embed=embed)

# --- Tag Collection View for Visual Grid ---
class TagCollectionView(discord.ui.View):
    def __init__(self, user_id, cards, tag_name, tag_emoji, timeout=120, page=0):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.cards = cards
        self.tag_name = tag_name
        self.tag_emoji = tag_emoji
        self.page = page
        self.total_pages = (len(cards) - 1) // PAGE_SIZE + 1

    async def update_message(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        start = self.page * PAGE_SIZE
        end = start + PAGE_SIZE
        page_cards = self.cards[start:end]
        
        owned_ids = {c[0] for c in self.cards}
        
        title_str = f"Tag: {self.tag_name} {self.tag_emoji} ({len(self.cards)} cards)"
        
        grid_img = await generate_collection_grid(page_cards, owned_ids, title_str, 100)
        file = discord.File(fp=grid_img, filename='tag_collection.jpg')
        
        embed = discord.Embed(title=f"🏷️ {title_str}", description=f"Showing cards assigned to tag **{self.tag_name}**.", color=RYO_COLOR)
        embed.set_author(name=f"{interaction.user.display_name}'s Tagged Cards")
        embed.set_image(url="attachment://tag_collection.jpg")
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

# --- Tag Autocomplete Helper ---
async def tag_name_autocomplete(interaction: discord.Interaction, current: str):
    uid = str(interaction.user.id)
    tags = await run_query("SELECT name FROM ryo_tags WHERE user_id = %s", (uid,), fetchall=True) or []
    choices = [app_commands.Choice(name=t[0], value=t[0]) for t in tags if current.lower() in t[0].lower()]
    return choices[:25]

# --- Tag Card Commands ---

@bot.tree.command(name="tagcreate", description="Create a new card tag with an emoji")
@app_commands.describe(
    name="The name of your custom tag (e.g., bias, fav)",
    emoji="The emoji for your tag (default Discord emoji or custom server emoji)"
)
async def tag_create(interaction: discord.Interaction, name: str, emoji: str):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    name_clean = name.strip()
    emoji_clean = emoji.strip()
    
    if not name_clean or not emoji_clean:
        return await interaction.followup.send("❌ Tag name and emoji cannot be empty.")
        
    # Check if duplicate exists for the same user
    exists = await run_query("SELECT 1 FROM ryo_tags WHERE user_id = %s AND LOWER(name) = LOWER(%s)", (uid, name_clean), fetchone=True)
    if exists:
        return await interaction.followup.send(f"❌ You already have a tag named `{name_clean}`. Use `/tagedit` to modify it or `/tagdelete` to remove it.")
        
    await run_query(
        "INSERT INTO ryo_tags (user_id, name, emoji) VALUES (%s, %s, %s)",
        (uid, name_clean, emoji_clean)
    )
    await interaction.followup.send(f"✅ Successfully created tag `{name_clean}` with emoji {emoji_clean}!")

@bot.tree.command(name="tagview", description="Show all your tags or view matching cards visually")
@app_commands.describe(
    name="Optional: The name of the tag to view its assigned cards in a grid"
)
async def tag_view(interaction: discord.Interaction, name: str = None):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    
    if name is None:
        # Show all tags created with names and emoji and serial number them like 1, 2...
        tags = await run_query("SELECT name, emoji FROM ryo_tags WHERE user_id = %s ORDER BY created_at ASC, id ASC", (uid,), fetchall=True)
        if not tags:
            return await interaction.followup.send("💡 You haven't created any tags yet! Create one using `/tagcreate`.")
            
        embed = discord.Embed(
            title="🏷️ Your Custom Tags",
            description="Use these tags to dynamically organize cards across your inventory!\n\n",
            color=RYO_COLOR
        )
        
        desc_lines = []
        for i, (tname, emoji) in enumerate(tags, 1):
            rules = await run_query("SELECT member_name, group_name, era, card_id FROM ryo_tag_rules WHERE user_id = %s AND LOWER(tag_name) = LOWER(%s)", (uid, tname), fetchall=True)
            
            count = 0
            if rules:
                inv_cards = await run_query(
                    "SELECT DISTINCT c.card_id, c.member_name, c.era, c.group_name, c.rarity, c.image_url, c.category "
                    "FROM ryo_inventory i "
                    "JOIN ryo_cards c ON i.card_id = c.card_id "
                    "WHERE i.user_id = %s",
                    (uid,), fetchall=True
                )
                for card in inv_cards:
                    cid, member, era, grp, rar, img, cat = card
                    for r_member, r_group, r_era, r_card_id in rules:
                        if matches_tag_rule(cid, member, era, grp, (r_member, r_group, r_era, r_card_id)):
                            count += 1
                            break
                            
            desc_lines.append(f"**{i}.** {emoji} `{tname}` ({count} matching cards in inventory)")
            
        embed.description += "\n".join(desc_lines)
        embed.set_footer(text="To assign cards, use /tagassign. To view a tag's cards in a grid, use /tagview [name].")
        return await interaction.followup.send(embed=embed)
        
    tag_name_clean = name.strip()
    tag_info = await run_query("SELECT name, emoji FROM ryo_tags WHERE user_id = %s AND LOWER(name) = LOWER(%s)", (uid, tag_name_clean), fetchone=True)
    if not tag_info:
        return await interaction.followup.send(f"❌ You do not have a tag named `{tag_name_clean}`. Create it first using `/tagcreate`.")
        
    real_name, tag_emoji = tag_info
    
    rules = await run_query("SELECT member_name, group_name, era, card_id FROM ryo_tag_rules WHERE user_id = %s AND LOWER(tag_name) = LOWER(%s)", (uid, tag_name_clean), fetchall=True)
    if not rules:
        return await interaction.followup.send(f"🏷️ Tag `{real_name}` has no card assignment rules set yet. Use `/tagassign` to assign cards!")
        
    # Get user's matching cards
    inv_cards = await run_query(
        "SELECT c.card_id, c.member_name, c.era, c.group_name, c.rarity, c.image_url, c.category "
        "FROM ryo_inventory i "
        "JOIN ryo_cards c ON i.card_id = c.card_id "
        "WHERE i.user_id = %s "
        "ORDER BY c.group_name ASC, c.era ASC, c.member_name ASC, c.rarity DESC",
        (uid,), fetchall=True
    )
    
    tagged_cards = []
    seen = set()
    for card in inv_cards:
        cid, member, era, grp, rar, img, cat = card
        if cid in seen:
            continue
        matched = False
        for r_member, r_group, r_era, r_card_id in rules:
            if matches_tag_rule(cid, member, era, grp, (r_member, r_group, r_era, r_card_id)):
                matched = True
                break
        if matched:
            tagged_cards.append(card)
            seen.add(cid)
            
    if not tagged_cards:
        return await interaction.followup.send(f"🏷️ There are active assignment rules for `{real_name}`, but none of the cards in your inventory match them.")
        
    view = TagCollectionView(uid, tagged_cards, real_name, tag_emoji)
    
    start = 0
    end = PAGE_SIZE
    page_cards = tagged_cards[start:end]
    
    title_str = f"Tag: {real_name} {tag_emoji} ({len(tagged_cards)} cards)"
    owned_ids = {c[0] for c in tagged_cards}
    
    grid_img = await generate_collection_grid(page_cards, owned_ids, title_str, 100)
    file = discord.File(fp=grid_img, filename='tag_collection.jpg')
    
    embed = discord.Embed(
        title=f"🏷️ {title_str}", 
        description=f"Showing cards assigned to tag **{real_name}**.", 
        color=RYO_COLOR
    )
    embed.set_author(name=f"{interaction.user.display_name}'s Tagged Cards")
    embed.set_image(url="attachment://tag_collection.jpg")
    embed.set_footer(text=f"Page 1 of {view.total_pages}")
    
    await interaction.followup.send(embed=embed, file=file, view=view)

@bot.tree.command(name="tagdelete", description="Delete a custom card tag and its associated assignment rules")
@app_commands.describe(name="The name of the tag to delete")
async def tag_delete(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    tag_name_clean = name.strip()
    
    existing = await run_query("SELECT name FROM ryo_tags WHERE user_id = %s AND LOWER(name) = LOWER(%s)", (uid, tag_name_clean), fetchone=True)
    if not existing:
        return await interaction.followup.send(f"❌ You do not have a tag named `{tag_name_clean}`.")
        
    real_name = existing[0]
    await run_query("DELETE FROM ryo_tags WHERE user_id = %s AND LOWER(name) = LOWER(%s)", (uid, tag_name_clean))
    await run_query("DELETE FROM ryo_tag_rules WHERE user_id = %s AND LOWER(tag_name) = LOWER(%s)", (uid, tag_name_clean))
    
    await interaction.followup.send(f"🗑️ Successfully deleted tag `{real_name}` and all of its associated card assignment rules.")

@bot.tree.command(name="tagedit", description="Edit the name or emoji of an existing tag using its /tagview serial number")
@app_commands.describe(
    id="The serial number of the tag from /tagview list",
    name="Optional: The new name for the tag",
    emoji="Optional: The new emoji for the tag"
)
async def tag_edit(interaction: discord.Interaction, id: int, name: str = None, emoji: str = None):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    
    tags = await run_query("SELECT id, name, emoji FROM ryo_tags WHERE user_id = %s ORDER BY created_at ASC, id ASC", (uid,), fetchall=True)
    if not tags:
        return await interaction.followup.send("❌ You don't have any tags to edit.")
        
    if id < 1 or id > len(tags):
         return await interaction.followup.send(f"❌ Invalid tag serial number. Choose a serial number between 1 and {len(tags)} from `/tagview`.")
         
    target_db_id, old_name, old_emoji = tags[id - 1]
    
    if not name and not emoji:
         return await interaction.followup.send("❌ You must specify at least one option to change (new name or new emoji).")
         
    updated_parts = []
    new_name_clean = name.strip() if name else None
    new_emoji_clean = emoji.strip() if emoji else None
    
    if new_name_clean:
         # Check for name conflict
         conflict = await run_query("SELECT 1 FROM ryo_tags WHERE user_id = %s AND LOWER(name) = LOWER(%s) AND id != %s", (uid, new_name_clean, target_db_id), fetchone=True)
         if conflict:
              return await interaction.followup.send(f"❌ You already have another tag named `{new_name_clean}`.")
              
         # Update rules
         await run_query("UPDATE ryo_tag_rules SET tag_name = %s WHERE user_id = %s AND LOWER(tag_name) = LOWER(%s)", (new_name_clean, uid, old_name))
         # Update tag
         await run_query("UPDATE ryo_tags SET name = %s WHERE id = %s", (new_name_clean, target_db_id))
         updated_parts.append(f"name: `{old_name}` ➡️ `{new_name_clean}`")
         tag_for_display = new_name_clean
    else:
         tag_for_display = old_name
         
    if new_emoji_clean:
         await run_query("UPDATE ryo_tags SET emoji = %s WHERE id = %s", (new_emoji_clean, target_db_id))
         updated_parts.append(f"emoji: {old_emoji} ➡️ {new_emoji_clean}")
         
    await interaction.followup.send(f"⚙️ Successfully edited tag `{tag_for_display}`:\n" + "\n".join(f"• {p}" for p in updated_parts))

@bot.tree.command(name="tagassign", description="Assign tags to cards in your inventory dynamically")
@app_commands.describe(
    tag_name="The name of the tag to assign",
    name="Optional: Member filter name",
    group="Optional: Group filter name",
    era="Optional: Era filter name",
    code="Optional: Card ID filter code"
)
async def tag_assign(
    interaction: discord.Interaction,
    tag_name: str,
    name: str = None,
    group: str = None,
    era: str = None,
    code: str = None
):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    tag_name_clean = tag_name.strip()
    
    tag_info = await run_query("SELECT name FROM ryo_tags WHERE user_id = %s AND LOWER(name) = LOWER(%s)", (uid, tag_name_clean), fetchone=True)
    if not tag_info:
        return await interaction.followup.send(f"❌ Tag `{tag_name_clean}` not found in your tag database. Register it first using `/tagcreate`.")
        
    real_tag_name = tag_info[0]
    
    norm_name = name.strip() if name else None
    norm_group = group.strip() if group else None
    norm_era = era.strip() if era else None
    norm_code = code.strip() if code else None
    
    if not (norm_name or norm_group or norm_era or norm_code):
        return await interaction.followup.send("❌ You must specify at least one filter criterion (name, group, era, or code).")
        
    # Check duplicate
    dup = await run_query(
        "SELECT 1 FROM ryo_tag_rules WHERE user_id = %s AND LOWER(tag_name) = LOWER(%s) "
        "AND (member_name = %s OR (member_name IS NULL AND %s IS NULL)) "
        "AND (group_name = %s OR (group_name IS NULL AND %s IS NULL)) "
        "AND (era = %s OR (era IS NULL AND %s IS NULL)) "
        "AND (card_id = %s OR (card_id IS NULL AND %s IS NULL))",
        (uid, tag_name_clean, norm_name, norm_name, norm_group, norm_group, norm_era, norm_era, norm_code, norm_code),
        fetchone=True
    )
    if dup:
        return await interaction.followup.send(f"❌ This exact card filter is already added as a rule under tag `{real_tag_name}`.")
        
    # Insert rule
    await run_query(
        "INSERT INTO ryo_tag_rules (user_id, tag_name, member_name, group_name, era, card_id) VALUES (%s, %s, %s, %s, %s, %s)",
        (uid, real_tag_name, norm_name, norm_group, norm_era, norm_code)
    )
    
    # Check matching cards
    inv_cards = await run_query(
        "SELECT DISTINCT c.card_id, c.member_name, c.era, c.group_name, c.rarity, c.image_url, c.category "
        "FROM ryo_inventory i "
        "JOIN ryo_cards c ON i.card_id = c.card_id "
        "WHERE i.user_id = %s",
        (uid,), fetchall=True
    )
    
    count = 0
    for card in inv_cards:
        cid, member, c_era, c_grp, rar, img, cat = card
        if matches_tag_rule(cid, member, c_era, c_grp, (norm_name, norm_group, norm_era, norm_code)):
            count += 1
            
    desc = []
    if norm_name: desc.append(f"Member Name: `{norm_name}`")
    if norm_group: desc.append(f"Group: `{norm_group}`")
    if norm_era: desc.append(f"Era: `{norm_era}`")
    if norm_code: desc.append(f"Card ID (Code): `{norm_code}`")
    
    filter_str = ", ".join(desc)
    await interaction.followup.send(
        f"✅ Tag assignment rule added successfully! Assigned tag **{real_tag_name}** to **{count}** matching card(s) in your inventory.\n"
        f"• Criteria: {filter_str}\n"
        f"*(Any matching cards you acquire in the future will automatically receive this tag too!)*"
    )

@bot.tree.command(name="tagremove", description="Remove card tag assignment criteria rules")
@app_commands.describe(
    tag_name="The name of the tag",
    name="Optional: Member filter name",
    group="Optional: Group filter name",
    era="Optional: Era filter name",
    code="Optional: Card ID filter code"
)
async def tag_remove(
    interaction: discord.Interaction,
    tag_name: str,
    name: str = None,
    group: str = None,
    era: str = None,
    code: str = None
):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    tag_name_clean = tag_name.strip()
    
    tag_info = await run_query("SELECT name FROM ryo_tags WHERE user_id = %s AND LOWER(name) = LOWER(%s)", (uid, tag_name_clean), fetchone=True)
    if not tag_info:
        return await interaction.followup.send(f"❌ You do not have a tag named `{tag_name_clean}`.")
        
    real_tag_name = tag_info[0]
    
    norm_name = name.strip() if name else None
    norm_group = group.strip() if group else None
    norm_era = era.strip() if era else None
    norm_code = code.strip() if code else None
    
    query = "DELETE FROM ryo_tag_rules WHERE user_id = %s AND LOWER(tag_name) = LOWER(%s)"
    params = [uid, tag_name_clean]
    
    # If no filters specified, clear all
    if not (norm_name or norm_group or norm_era or norm_code):
        rowcount = await run_query(query, tuple(params), return_rowcount=True)
        return await interaction.followup.send(f"✅ Successfully deleted **all** ({rowcount}) assignment criteria rules for tag **{real_tag_name}**.")
        
    query += " AND (member_name = %s OR (member_name IS NULL AND %s IS NULL))"
    query += " AND (group_name = %s OR (group_name IS NULL AND %s IS NULL))"
    query += " AND (era = %s OR (era IS NULL AND %s IS NULL))"
    query += " AND (card_id = %s OR (card_id IS NULL AND %s IS NULL))"
    params.extend([norm_name, norm_name, norm_group, norm_group, norm_era, norm_era, norm_code, norm_code])
    
    rowcount = await run_query(query, tuple(params), return_rowcount=True)
    if rowcount > 0:
        await interaction.followup.send(f"✅ Removed matching assignment criteria rule from tag **{real_tag_name}**.")
    else:
        await interaction.followup.send(f"❌ No matching criteria rule found under tag **{real_tag_name}**.")

# --- Autocomplete for Tag Name ---
@tag_view.autocomplete('name')
@tag_delete.autocomplete('name')
@tag_assign.autocomplete('tag_name')
@tag_remove.autocomplete('tag_name')
@safe_autocomplete
async def user_tag_name_autocomplete(interaction: discord.Interaction, current: str):
    return await tag_name_autocomplete(interaction, current)

@bot.tree.command(name="view", description="View a specific card's details and image")
@app_commands.describe(card_id="The ID of the card you want to view")
async def view(interaction: discord.Interaction, card_id: str):
    await interaction.response.defer()
    
    # Fetch card details
    card = await run_query(
        "SELECT card_id, member_name, era, group_name, rarity, image_url, category FROM ryo_cards WHERE LOWER(card_id) = LOWER(%s)",
        (card_id,),
        fetchone=True
    )
    
    if not card:
        return await interaction.followup.send(f"❌ Card with ID `{card_id}` not found.")
    
    cid, name, era, group, rarity, image_url, category = card
    
    # Count copies for the user
    user_copies = await run_query(
        "SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s AND card_id = %s",
        (str(interaction.user.id), cid),
        fetchone=True
    )
    copy_count = user_copies[0] if user_copies else 0
    
    # Create embed
    embed = discord.Embed(color=RYO_COLOR)
    embed.set_author(name=f"{interaction.user.name} is viewing a card!", icon_url=interaction.user.display_avatar.url)
    
    group_display = f" ({group})" if group else ""
    desc = [
        f"### {name}{group_display}",
        f"**Era:** {era or 'N/A'}",
        f"**Code:** `{cid}`",
        f"**Rarity:** {get_rarity_display(rarity, category, era, cid)}",
        f"**Category:** {(category or 'Regular').title()}"
    ]
    embed.description = "\n".join(desc)
    
    file = None
    if image_url:
        file = await get_discord_file_from_url(image_url, "view.png")
        if file:
            embed.set_image(url="attachment://view.png")
        else:
            embed.set_image(url=get_raw_url(image_url))
        
    embed.set_footer(text=f"You have {copy_count} copies of this card")
    
    if file:
        await interaction.followup.send(embed=embed, file=file)
    else:
        await interaction.followup.send(embed=embed)

# --- Craft Command ---
@bot.tree.command(name="craft", description="Draw 1 card from ongoing events (Cost: 15 Glue)")
@app_commands.describe(reminder="Turn reminder on/off")
async def craft_cmd(interaction: discord.Interaction, reminder: bool = None):
    uid = str(interaction.user.id)
    
    # Check cooldown (1 hour = 3600s)
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='craft'", (uid,), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < 3600:
        rem = 3600 - (now - last_used)
        target_time = int(time.time() + rem)
        return await interaction.response.send_message(f"⌛ Ryo is preparing the event stage! Try again <t:{target_time}:R>.", ephemeral=True)

    await interaction.response.defer()

    confirm_embed = discord.Embed(
        description=f"This command costs **15 glue** {GLUE_EMOJI}",
        color=RYO_COLOR
    )
    view = EventDrawConfirmView(uid)
    await interaction.followup.send(embed=confirm_embed, view=view)
    
    await view.wait()
    if not view.confirmed or not view.interaction:
        return
        
    interaction = view.interaction
    
    try:
        if reminder is not None:
            await run_query("INSERT INTO ryo_reminder_settings (user_id, command, enabled) VALUES (%s, 'craft', %s) ON CONFLICT (user_id, command) DO UPDATE SET enabled=%s", (uid, reminder, reminder))
        
        rem_enabled = await run_query("SELECT enabled FROM ryo_reminder_settings WHERE user_id=%s AND command='craft'", (uid,), fetchone=True)
        if not rem_enabled or rem_enabled[0]:
            await set_reminder(uid, "craft", 3600, interaction.channel_id)

        # Check glue balance
        res_glue = await run_query("SELECT glue FROM ryo_users WHERE user_id = %s", (uid,), fetchone=True)
        current_glue = res_glue[0] if res_glue else 0
        
        if current_glue < 15:
            return await interaction.edit_original_response(content=f"❌ You need **15 glue** {GLUE_EMOJI} to draw from events! (Balance: {current_glue}{GLUE_EMOJI})", embed=None, view=None)

        # Fetch active events
        events_check = await run_query("SELECT category, era FROM ryo_events", fetchall=True)
        if not events_check:
            return await interaction.edit_original_response(content="ℹ️ There are no active events at the moment. Only **Regular** cards are currently dropping.", embed=None, view=None)

        # Fetch cards from active events
        cards = await run_query("""
            SELECT card_id, member_name, era, group_name, rarity, image_url, category 
            FROM ryo_cards 
            WHERE (LOWER(category), LOWER(era)) IN (SELECT category, era FROM ryo_events)
            AND group_name NOT IN (SELECT entity_name FROM ryo_blocks WHERE user_id = %s AND entity_type = 'group')
            AND member_name NOT IN (SELECT entity_name FROM ryo_blocks WHERE user_id = %s AND entity_type = 'idol')
        """, (uid, uid), fetchall=True)
        
        if not cards:
            return await interaction.edit_original_response(content="❌ No cards found for the ongoing events in the database.", embed=None, view=None)
        
        card = random.choice(cards)
        
        # Deduct glue and set cooldown
        await run_query("UPDATE ryo_users SET glue = glue - 15 WHERE user_id = %s", (uid,))
        await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'craft', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used=EXCLUDED.last_used", (uid, now))
        
        # Add to inventory
        await grant_card(uid, card[0])
        
        # Get count of copies
        count_res = await run_query("SELECT COUNT(*) FROM ryo_inventory WHERE user_id = %s AND card_id = %s", (uid, card[0]), fetchone=True)
        copies = count_res[0] if count_res else 1
        
        # Build results embed
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
        description_lines.append(f"`{cid}` {era}")
        description_lines.append(f"Copies : {copies}")
        description_lines.append(f"\n*Spent 15 glue {GLUE_EMOJI}*")
        
        res_embed = discord.Embed(
            title=results_title,
            description="\n".join(description_lines),
            color=RYO_COLOR
        )
        
        file = None
        if card[5]:
            file = await get_discord_file_from_url(card[5], "craft_result.png")
            if file:
                res_embed.set_image(url="attachment://craft_result.png")
            else:
                res_embed.set_image(url=get_raw_url(card[5]))
        
        # Footer icon
        footer_icon = "https://cdn.discordapp.com/emojis/1497330599457722479.png"
        res_embed.set_footer(text=f"Event Card Claimed!", icon_url=footer_icon)
        
        if file:
            await interaction.edit_original_response(embed=res_embed, view=None, attachments=[file])
        else:
            await interaction.edit_original_response(embed=res_embed, view=None)
        
    except Exception as e:
        print(f"❌ Event draw error: {e}")
        await interaction.followup.send(f"⚠️ Failed to perform event draw: {e}")

# --- Event Management ---

@bot.tree.command(name="startevent", description="Make event cards from a specific category and era droppable")
@app_commands.describe(category="Event category", era="Era to start", rate="Target drop rate percentage for this event (e.g. 15.0)")
async def startevent(interaction: discord.Interaction, category: str, era: str, rate: float = 15.0):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer()
    await run_query("""
        INSERT INTO ryo_events (category, era, rate) 
        VALUES (%s, %s, %s) 
        ON CONFLICT (category, era) DO UPDATE SET rate = EXCLUDED.rate
    """, (category.lower(), era.lower(), rate))
    await interaction.followup.send(f"✅ Event started! Cards for `{category}` in `{era}` are now droppable with a `{rate}%` appearance rate!")

@bot.tree.command(name="endevent", description="Stop event cards from a specific category and era from dropping")
@app_commands.describe(category="Event category", era="Era to end")
async def endevent(interaction: discord.Interaction, category: str, era: str):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer()
    await run_query("DELETE FROM ryo_events WHERE category = %s AND era = %s", (category.lower(), era.lower()))
    await interaction.followup.send(f"✅ Event ended! Cards for `{category}` in `{era}` are no longer droppable.")

@bot.tree.command(name="events", description="Show all active events")
async def show_events(interaction: discord.Interaction):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    await interaction.response.defer()
    events = await run_query("SELECT category, era, rate FROM ryo_events", fetchall=True)
    if not events:
        return await interaction.followup.send("ℹ️ There are no active events. Only **Regular** cards are currently dropping.")
    
    desc = "\n".join([f"• **{cat.title()}** ({rate}%): {era.title()}" for cat, era, rate in events])
    embed = discord.Embed(title="🌟 Active Events", description=desc, color=RYO_COLOR)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="gift", description="Gift cards from your inventory to another player")
@app_commands.describe(
    user="Member to gift to",
    group="Filter by group",
    card_id="Filter by template Card ID (e.g. BTS-101)",
    name="Filter by idol name",
    era="Filter by era",
    rarity="Rarity 1-5 (comma-separated, e.g. 3,4,5)",
    dupes_only="Only gift duplicate cards (keeps one)",
    exclude_5_stars="Ignore all 5-star cards",
    exclude_members="Comma-separated names to exclude",
    copies="Number of cards to gift (defaults to 1)"
)
async def gift(
    interaction: discord.Interaction,
    user: discord.Member,
    group: str = None,
    card_id: str = None,
    name: str = None,
    era: str = None,
    rarity: str = None,
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

    rarity_list = []
    if rarity:
        for r in rarity.split(","):
            r = r.strip()
            if r.isdigit():
                rarity_list.append(int(r))

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
        item_rarity = item[2]
        member = (item[4] or "").lower()
        grp = (item[5] or "").lower()
        cera = (item[6] or "").lower()

        # Apply filters
        if card_id:
            parts = [p.strip().lower() for p in card_id.split(",") if p.strip()]
            if parts and cid.lower() not in parts: continue
        if group:
            parts = [p.strip().lower() for p in group.split(",") if p.strip()]
            if parts and not any(p in grp for p in parts): continue
        if name:
            parts = [p.strip().lower() for p in name.split(",") if p.strip()]
            if parts and not any(p == member for p in parts): continue
        if era:
            parts = [p.strip().lower() for p in era.split(",") if p.strip()]
            if parts and not any(p in cera for p in parts): continue
        if rarity_list and item_rarity not in rarity_list: continue
        if exclude_5_stars and item_rarity == 5: continue
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
        card_lines.append(f"x1 `{c['card_id']}` {stars} **{c['group_name']} {c['member_name']}**\n({c['era']})")
    
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
    card_id="Filter by specific Card ID (e.g. BTS-101)",
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
        if card_id:
            parts = [p.strip().lower() for p in card_id.split(",") if p.strip()]
            if parts and cid.lower() not in parts: continue
        if group:
            parts = [p.strip().lower() for p in group.split(",") if p.strip()]
            if parts and not any(p in grp for p in parts): continue
        if name:
            parts = [p.strip().lower() for p in name.split(",") if p.strip()]
            if parts and not any(p in member for p in parts): continue
        if era:
            parts = [p.strip().lower() for p in era.split(",") if p.strip()]
            if parts and not any(p in cera for p in parts): continue
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

async def check_url_status(url: str) -> str:
    if not url or not str(url).lower().startswith("http"):
        return "🔴 (No Valid URL)"
    
    target_url = get_raw_url(str(url).strip())
    try:
        session = await get_session()
    except Exception as es:
        return "⚪ (AioHttp Err)"
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        # First use HEAD request
        async with session.head(target_url, timeout=3, headers=headers, allow_redirects=True) as resp:
            if resp.status == 200:
                return "🟢 OK"
            else:
                # If HEAD failed or is not 200, try GET
                try:
                    async with session.get(target_url, timeout=3, headers=headers) as get_resp:
                        if get_resp.status == 200:
                            return "🟢 OK"
                        return f"🔴 {get_resp.status}"
                except Exception:
                    return f"🔴 {resp.status}"
    except asyncio.TimeoutError:
        return "⏳ Timeout"
    except Exception as e:
        return "⚠️ Failed Connection"

@bot.tree.command(name="missing_images", description="Inspect card image URLs and check for failures or blanks (STAFF/MOD ONLY)")
@app_commands.describe(
    failures_only="If True, only show cards with empty or suspicious URLs. If False, show all registry cards.",
    group="Filter by Group name",
    era="Filter by Era name",
    name="Filter by Member/Idol name"
)
async def missing_images(
    interaction: discord.Interaction,
    failures_only: bool = True,
    group: str = None,
    era: str = None,
    name: str = None
):
    if not (is_staff(interaction) or is_mod(interaction)):
        return await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    query = "SELECT card_id, member_name, era, group_name, image_url FROM ryo_cards WHERE 1=1"
    params = []
    
    if failures_only:
        query += (
            " AND (image_url IS NULL"
            " OR TRIM(image_url) = ''"
            " OR image_url NOT ILIKE 'http%'"
            " OR image_url ILIKE 'none'"
            " OR image_url ILIKE 'null'"
            " OR image_url ILIKE 'nan'"
            " OR image_url ILIKE 'n/a'"
            " OR image_url ILIKE '%placeholder%'"
            " OR image_url ILIKE '%example.com%')"
        )
        
    if group:
        parts = [p.strip().lower() for p in group.split(",") if p.strip()]
        if parts:
            query += " AND (" + " OR ".join(["LOWER(group_name) LIKE %s"] * len(parts)) + ")"
            params.extend([f"%{p}%" for p in parts])
        
    if era:
        parts = [p.strip().lower() for p in era.split(",") if p.strip()]
        if parts:
            query += " AND (" + " OR ".join(["LOWER(era) LIKE %s"] * len(parts)) + ")"
            params.extend([f"%{p}%" for p in parts])
        
    if name:
        parts = [p.strip().lower() for p in name.split(",") if p.strip()]
        if parts:
            query += " AND (" + " OR ".join(["LOWER(member_name) LIKE %s"] * len(parts)) + ")"
            params.extend([f"%{p}%" for p in parts])
        
    query += " ORDER BY card_id ASC"
    
    rows = await run_query(query, tuple(params) if params else None, fetchall=True)
    
    if not rows:
        filter_msg = []
        if group: filter_msg.append(f"group `{group}`")
        if era: filter_msg.append(f"era `{era}`")
        if name: filter_msg.append(f"name `{name}`")
        f_text = f" matching {', '.join(filter_msg)}" if filter_msg else ""
        if failures_only:
            return await interaction.followup.send(f"✅ All cards{f_text} in the registry have valid-looking image URLs!", ephemeral=True)
        else:
            return await interaction.followup.send(f"ℹ️ No cards found{f_text} in the registry.", ephemeral=True)
        
    total_found = len(rows)
    
    class MissingImagesView(discord.ui.View):
        def __init__(self, cards, user_id, timeout=120):
            super().__init__(timeout=timeout)
            self.cards = cards
            self.user_id = user_id
            self.page = 0
            self.per_page = 10
            self.total_pages = (len(cards) - 1) // self.per_page + 1
            self.current_statuses = []

        async def update_page_data(self):
            start = self.page * self.per_page
            end = start + self.per_page
            current_cards = self.cards[start:end]
            
            tasks = []
            for r in current_cards:
                tasks.append(check_url_status(r[4]))
            
            self.current_statuses = await asyncio.gather(*tasks)

        def create_embed(self):
            start = self.page * self.per_page
            end = start + self.per_page
            current_cards = self.cards[start:end]
            
            title_text = "⚠️ Suspicious Image URLs" if failures_only else "📂 Card Registry Image Status"
            embed = discord.Embed(
                title=title_text,
                description=f"Found **{len(self.cards)}** matching card(s).\nEach page dynamically verifies link connectivity.",
                color=RYO_COLOR
            )
            
            lines = []
            for i, r in enumerate(current_cards):
                status = self.current_statuses[i] if i < len(self.current_statuses) else "⚪ Pending"
                url_display = r[4] or "None"
                if len(url_display) > 55:
                    url_display = url_display[:52] + "..."
                lines.append(
                    f"**{start + i + 1}.** `{r[0]}`: {r[1]} ({r[3] or 'Solo'}) - *{r[2]}*\n"
                    f"   Status: {status} | Link: `<{url_display}>`"
                )
                
            list_str = "\n".join(lines)
            if not list_str:
                list_str = "No cards on this page."
                
            embed.description += f"\n\n{list_str}"
            embed.set_footer(text=f"Page {self.page + 1} of {self.total_pages} | Showing cards {start + 1} - {min(end, len(self.cards))}")
            return embed

        @discord.ui.button(emoji="<:rb_full_left:1493145347046768721>", style=discord.ButtonStyle.secondary)
        async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            if str(interaction.user.id) != self.user_id:
                return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
            await interaction.response.defer()
            self.page = 0
            await self.update_page_data()
            await interaction.edit_original_response(embed=self.create_embed(), view=self)

        @discord.ui.button(emoji="<:rb_left:1493145361601138721>", style=discord.ButtonStyle.secondary)
        async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            if str(interaction.user.id) != self.user_id:
                return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
            if self.page > 0:
                await interaction.response.defer()
                self.page -= 1
                await self.update_page_data()
                await interaction.edit_original_response(embed=self.create_embed(), view=self)
            else:
                await interaction.response.send_message("You are on the first page!", ephemeral=True)

        @discord.ui.button(emoji="<:rb_right:1493145374846615573>", style=discord.ButtonStyle.secondary)
        async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            if str(interaction.user.id) != self.user_id:
                return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
            if self.page < self.total_pages - 1:
                await interaction.response.defer()
                self.page += 1
                await self.update_page_data()
                await interaction.edit_original_response(embed=self.create_embed(), view=self)
            else:
                await interaction.response.send_message("You are on the last page!", ephemeral=True)

        @discord.ui.button(emoji="<:rb_full_right:1493145353996730421>", style=discord.ButtonStyle.secondary)
        async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            if str(interaction.user.id) != self.user_id:
                return await interaction.response.send_message("❌ This is not your menu!", ephemeral=True)
            await interaction.response.defer()
            self.page = self.total_pages - 1
            await self.update_page_data()
            await interaction.edit_original_response(embed=self.create_embed(), view=self)

    view = MissingImagesView(rows, str(interaction.user.id))
    await view.update_page_data()
    embed = view.create_embed()
    
    full_text = f"CARD IMAGE REGISTRY REPORT ({total_found} matched items):\n" + "="*50 + "\n"
    for r in rows:
        url_val = r[4] or "None"
        full_text += f"ID: {r[0]} | Name: {r[1]} | Group: {r[3]} | Era: {r[2]} | URL: {url_val}\n"
        
    file_bytes = io.BytesIO(full_text.encode('utf-8'))
    discord_file = discord.File(fp=file_bytes, filename="missing_images_report.txt")
    
    await interaction.followup.send(embed=embed, file=discord_file, view=view, ephemeral=True)

@search.autocomplete('category')
@bulkeditcard.autocomplete('category')
@safe_autocomplete
async def inventory_cat_autocomplete(interaction: discord.Interaction, current: str):
    cats = ["regular", "public event", "limited", "none", "booster event", "patreon event"]
    prefix = ""
    target_current = current
    if ',' in current:
        parts = current.split(',')
        prefix = ",".join(parts[:-1]) + ", "
        target_current = parts[-1].strip()
        
    choices = []
    for c in cats:
        if target_current.lower() in c.lower():
            name = c.title() if c != "none" else "None"
            choices.append(app_commands.Choice(name=f"{prefix}{name}", value=f"{prefix}{c}"))
    return choices[:25]

@startevent.autocomplete('category')
@endevent.autocomplete('category')
@safe_autocomplete
async def event_cat_autocomplete(interaction: discord.Interaction, current: str):
    cats = ["public event", "booster event", "patreon event", "limited"]
    return [app_commands.Choice(name=c.title(), value=c) for c in cats if current.lower() in c.lower()]

@tag_assign.autocomplete('group')
@tag_remove.autocomplete('group')
@shop_buy.autocomplete('group')
@market_view.autocomplete('group')
@collection_cmd.autocomplete('group')
@search.autocomplete('group')
@inventory.autocomplete('group')
@burn.autocomplete('group')
@gift.autocomplete('group')
@missing_images.autocomplete('group')
@bulkeditcard.autocomplete('group')
@addcustom.autocomplete('group')
@safe_autocomplete
async def group_autocomplete(interaction: discord.Interaction, current: str):
    # Use cache for performance
    now = time.time()
    if now - AUTOCOMPLETE_CACHE["last_update"] >= 600 and not _update_in_progress:
        asyncio.create_task(update_autocomplete_cache(force=True))
        
    prefix = ""
    target_current = current
    if ',' in current:
        parts = current.split(',')
        prefix = ",".join(parts[:-1]) + ", "
        target_current = parts[-1].strip()
        
    choices = [app_commands.Choice(name=f"{prefix}{g}", value=f"{prefix}{g}") for g in AUTOCOMPLETE_CACHE["groups"] if target_current.lower() in g.lower()]
    return choices[:25]

@tag_assign.autocomplete('era')
@tag_remove.autocomplete('era')
@market_view.autocomplete('era')
@collection_cmd.autocomplete('era')
@search.autocomplete('era')
@inventory.autocomplete('era')
@burn.autocomplete('era')
@gift.autocomplete('era')
@startevent.autocomplete('era')
@endevent.autocomplete('era')
@missing_images.autocomplete('era')
@bulkeditcard.autocomplete('era')
@addrarity.autocomplete('era')
@addcustom.autocomplete('era')
@safe_autocomplete
async def era_autocomplete(interaction: discord.Interaction, current: str):
    # Use cache for performance
    now = time.time()
    if now - AUTOCOMPLETE_CACHE["last_update"] >= 600 and not _update_in_progress:
        asyncio.create_task(update_autocomplete_cache(force=True))
        
    prefix = ""
    target_current = current
    if ',' in current:
        parts = current.split(',')
        prefix = ",".join(parts[:-1]) + ", "
        target_current = parts[-1].strip()
        
    choices = [app_commands.Choice(name=f"{prefix}{e}", value=f"{prefix}{e}") for e in AUTOCOMPLETE_CACHE["eras"] if target_current.lower() in e.lower()]
    return choices[:25]

@tag_assign.autocomplete('name')
@tag_remove.autocomplete('name')
@market_view.autocomplete('name')
@search.autocomplete('name')
@inventory.autocomplete('name')
@burn.autocomplete('name')
@gift.autocomplete('name')
@missing_images.autocomplete('name')
@bulkeditcard.autocomplete('member')
@safe_autocomplete
async def name_autocomplete(interaction: discord.Interaction, current: str):
    # Use cache for performance
    now = time.time()
    if now - AUTOCOMPLETE_CACHE["last_update"] >= 600 and not _update_in_progress:
        asyncio.create_task(update_autocomplete_cache(force=True))
        
    prefix = ""
    target_current = current
    if ',' in current:
        parts = current.split(',')
        prefix = ",".join(parts[:-1]) + ", "
        target_current = parts[-1].strip()
        
    choices = [app_commands.Choice(name=f"{prefix}{n}", value=f"{prefix}{n}") for n in AUTOCOMPLETE_CACHE["names"] if target_current.lower() in n.lower()]
    return choices[:25]

@bot.tree.command(name="bulkadd", description="Add multiple cards (STAFF/MOD ONLY)")
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

@bot.tree.command(name="staffblockidol", description="Add an approved blockable Idol to the player master block list (STAFF/MOD ONLY)")
@app_commands.describe(idol_name="Name of the idol to block", group="The group the idol belongs to")
async def staffblockidol(interaction: discord.Interaction, idol_name: str, group: str):
    if not (is_staff(interaction) or is_mod(interaction) or interaction.user.id in [691771271129858129, DEV_ID]):
        return await interaction.response.send_message("❌ Only the Owner, developers, or staff/mods can manage blockable groups/idols.", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    
    val_name = idol_name.strip()
    val_group = group.strip()
    
    # Verify the combination exists in ryo_cards
    row = await run_query(
        "SELECT DISTINCT member_name, group_name FROM ryo_cards WHERE member_name ILIKE %s AND group_name ILIKE %s LIMIT 1",
        (val_name, val_group),
        fetchone=True
    )
    if not row:
        return await interaction.followup.send(
            f"❌ Could not find an idol named `{val_name}` in group `{val_group}` in the card registry! Check spelling/typos.",
            ephemeral=True
        )
        
    canonical_idol, canonical_group = row[0], row[1]
    
    exists = await run_query(
        "SELECT 1 FROM ryo_staff_blocks WHERE entity_type = 'idol' AND entity_name = %s",
        (canonical_idol,),
        fetchone=True
    )
    if exists:
        return await interaction.followup.send(
            f"⚠️ `{canonical_idol}` (Group: `{canonical_group}`) is already in the approved blockable list.",
            ephemeral=True
        )
        
    await run_query(
        "INSERT INTO ryo_staff_blocks (entity_type, entity_name) VALUES ('idol', %s)",
        (canonical_idol,)
    )
    
    await interaction.followup.send(
        f"✅ Added `{canonical_idol}` (Group: `{canonical_group}`) as a blockable idol successfully!",
        ephemeral=True
    )

@bot.tree.command(name="staffblockgrp", description="Add an approved blockable Group to the player master block list (STAFF/MOD ONLY)")
@app_commands.describe(group_name="Name of the group to block")
async def staffblockgrp(interaction: discord.Interaction, group_name: str):
    if not (is_staff(interaction) or is_mod(interaction) or interaction.user.id in [691771271129858129, DEV_ID]):
        return await interaction.response.send_message("❌ Only the Owner, developers, or staff/mods can manage blockable groups/idols.", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    
    val_group = group_name.strip()
    
    # Verify group exists in ryo_cards
    row = await run_query(
        "SELECT DISTINCT group_name FROM ryo_cards WHERE group_name ILIKE %s LIMIT 1",
        (val_group,),
        fetchone=True
    )
    if not row:
        return await interaction.followup.send(
            f"❌ Could not find a group named `{val_group}` in the card registry! Check spelling/typos.",
            ephemeral=True
        )
        
    canonical_group = row[0]
    
    exists = await run_query(
        "SELECT 1 FROM ryo_staff_blocks WHERE entity_type = 'group' AND entity_name = %s",
        (canonical_group,),
        fetchone=True
    )
    if exists:
        return await interaction.followup.send(
            f"⚠️ `{canonical_group}` is already in the approved blockable list.",
            ephemeral=True
        )
        
    await run_query(
        "INSERT INTO ryo_staff_blocks (entity_type, entity_name) VALUES ('group', %s)",
        (canonical_group,)
    )
    
    await interaction.followup.send(
        f"✅ Added group `{canonical_group}` as a blockable group successfully!",
        ephemeral=True
    )

@staffblockidol.autocomplete('idol_name')
@safe_autocomplete
async def staffblockidol_name_autocomplete(interaction: discord.Interaction, current: str):
    now = time.time()
    if now - AUTOCOMPLETE_CACHE["last_update"] >= 600 and not _update_in_progress:
        asyncio.create_task(update_autocomplete_cache(force=True))
    choices = [app_commands.Choice(name=n, value=n) for n in AUTOCOMPLETE_CACHE["names"] if current.lower() in n.lower()]
    return choices[:25]

@staffblockidol.autocomplete('group')
@staffblockgrp.autocomplete('group_name')
@safe_autocomplete
async def staffblock_group_autocomplete(interaction: discord.Interaction, current: str):
    now = time.time()
    if now - AUTOCOMPLETE_CACHE["last_update"] >= 600 and not _update_in_progress:
        asyncio.create_task(update_autocomplete_cache(force=True))
    choices = [app_commands.Choice(name=g, value=g) for g in AUTOCOMPLETE_CACHE["groups"] if current.lower() in g.lower()]
    return choices[:25]

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
@safe_autocomplete
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
@safe_autocomplete
async def sync_scope_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name="Global", value="global"),
        app_commands.Choice(name="Guild", value="guild")
    ]

user_last_cmd_time = {}

@bot.tree.interaction_check
async def global_interaction_check(interaction: discord.Interaction) -> bool:
    if interaction.type == discord.InteractionType.application_command:
        uid = str(interaction.user.id)
        now = time.time()
        last_time = user_last_cmd_time.get(uid, 0.0)
        if now - last_time < 1.0:
            embed = discord.Embed(
                title="⏳ Slow Down!",
                description="Please wait 1 second between commands to avoid spamming.",
                color=discord.Color.orange()
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        user_last_cmd_time[uid] = now

        cmd_name = interaction.command.name if interaction.command else None
        if cmd_name not in ["sync"]:
            disabled = await get_disabled_commands()
            disabled_msg = None
            if "all" in disabled:
                disabled_msg = disabled["all"]
            elif cmd_name and cmd_name in disabled:
                disabled_msg = disabled[cmd_name]
                
            if disabled_msg:
                embed = discord.Embed(
                    title="🛑 Command Disabled",
                    description=disabled_msg,
                    color=discord.Color.red()
                )
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=embed)
                return False
    return True

@addquest.autocomplete('category')
@safe_autocomplete
async def quest_category_autocomplete(interaction: discord.Interaction, current: str):
    categories = ["Daily", "Weekly", "Events"]
    return [app_commands.Choice(name=c, value=c) for c in categories if current.lower() in c.lower()]

@deletequest.autocomplete('category')
@safe_autocomplete
async def delete_quest_category_autocomplete(interaction: discord.Interaction, current: str):
    categories = ["Daily", "Weekly", "Events"]
    return [app_commands.Choice(name=c, value=c) for c in categories if current.lower() in c.lower()]

@addquest.autocomplete('quest_type')
@safe_autocomplete
async def quest_type_autocomplete(interaction: discord.Interaction, current: str):
    types = ["draw", "gift", "burn"]
    return [app_commands.Choice(name=t.title(), value=t) for t in types if current.lower() in t.lower()]

@deletequest.autocomplete('quest_id')
@safe_autocomplete
async def quest_id_autocomplete(interaction: discord.Interaction, current: str):
    results = await run_query(
        "SELECT quest_id, name, category FROM ryo_quests WHERE name ILIKE %s OR CAST(quest_id AS TEXT) ILIKE %s LIMIT 25",
        (f"%{current}%", f"%{current}%"),
        fetchall=True
    ) or []
    return [app_commands.Choice(name=f"[{r[2]}] ID: {r[0]} | {r[1][:40]}", value=r[0]) for r in results]

@tag_assign.autocomplete('code')
@tag_remove.autocomplete('code')
@view.autocomplete('card_id')
@addrarity.autocomplete('card_id')
@safe_autocomplete
async def card_id_autocomplete(interaction: discord.Interaction, current: str):
    if not current:
        return []
    # Search by ID, member name, or group name to be more helpful
    results = await run_query(
        "SELECT card_id, member_name, era FROM ryo_cards WHERE (card_id ILIKE %s OR member_name ILIKE %s OR group_name ILIKE %s) LIMIT 25",
        (f"%{current}%", f"%{current}%", f"%{current}%"),
        fetchall=True
    ) or []
    
    choices = []
    for r in results:
        if not r or not r[0]: 
            continue
        cid = r[0]
        name = r[1] or "Unknown"
        era = r[2] or "N/A"
        
        label = f"{name} ({era}) [{cid}]"[:100]
        choices.append(app_commands.Choice(name=label, value=str(cid)))
    return choices

if RYOTOKEN and RYOTOKEN != "" and RYOTOKEN != "PLACEHOLDER":
    try:
        bot.run(RYOTOKEN)
    except discord.LoginFailure:
        print("❌ LOGIN ERROR: The Discord Bot Token (RYO_TOKEN) provided is invalid, improperly formatted, or has expired.")
        print("Please verify your RYO_TOKEN in ryo.env.")
    except discord.PrivilegedIntentsRequired:
        print("❌ INTENTS ERROR: The bot requires 'Message Content Intent', but it is disabled in your Discord Developer Portal.")
        print("Please navigate to https://discord.com/developers/applications, select your application, go to the 'Bot' tab, scroll down to 'Privileged Gateway Intents', and enable 'Message Content Intent'.")
    except Exception as e:
        print(f"❌ CRITICAL BOT ERROR during execution: {e}")
else:
    print("❌ ERROR: RYO_TOKEN is missing or set to PLACEHOLDER in ryo.env.")
    print("Please update ryo.env with your actual Discord Bot Token and Supabase Database URL.")
