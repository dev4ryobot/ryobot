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

async def log_action(user_id: str, action: str, details: str):
    # Dummy function to disable all logging in RyoBot as requested
    pass

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
TEDDY_ICON_URL = "https://cdn.discordapp.com/emojis/1497330599457722479.png"

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

# Custom emojis for bindershop pagination buttons (can be standard or custom <:name:id> strings)
EMOJI_FAR_LEFT = os.getenv("EMOJI_FAR_LEFT", "<:rb_full_left:1493145347046768721>")
EMOJI_LEFT = os.getenv("EMOJI_LEFT", "<:rb_left:1493145361601138721>")
EMOJI_RIGHT = os.getenv("EMOJI_RIGHT", "<:rb_right:1493145374846615573>")
EMOJI_FAR_RIGHT = os.getenv("EMOJI_FAR_RIGHT", "<:rb_full_right:1493145353996730421>")

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
    try:
        new_cache = {"cards": {}, "eras": {}}
        rows = await run_query("SELECT era, card_id, icon_url FROM ryo_rarity_custom", fetchall=True)
        if rows:
            for era, card_id, icon_url in rows:
                if card_id:
                    new_cache["cards"][card_id.lower()] = icon_url
                elif era:
                    new_cache["eras"][era.lower()] = icon_url
                    
        # Load custom rarity emojis from ryo_cards
        custom_cards = await run_query("SELECT card_id, custom_emoji FROM ryo_cards WHERE custom_emoji IS NOT NULL AND custom_emoji != ''", fetchall=True)
        if custom_cards:
            for cid, c_emoji in custom_cards:
                if cid and c_emoji:
                    new_cache["cards"][cid.lower()] = c_emoji.strip()
                    
        RARITY_CUSTOM_CACHE = new_cache
    except Exception as e:
        print(f"⚠️ Error loading custom rarity cache: {e}")

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

async def check_pack_cooldown(user_id, pack_id, cooldown_seconds):
    res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command=%s", (user_id, f"pack_{pack_id}"), fetchone=True)
    last_used = res[0] if res else 0
    now = int(time.time())
    if now - last_used < cooldown_seconds:
        return cooldown_seconds - (now - last_used)
    return 0

async def update_pack_cooldown(user_id, pack_id):
    now = int(time.time())
    await run_query("INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, %s, %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used = %s", (user_id, f"pack_{pack_id}", now, now))

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
    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_cooldowns (
        user_id VARCHAR(50),
        command VARCHAR(100),
        last_used BIGINT,
        PRIMARY KEY (user_id, command)
    );
    """)
    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_cards (
        card_id VARCHAR(100) PRIMARY KEY,
        group_name VARCHAR(100),
        member_name VARCHAR(100),
        era VARCHAR(100),
        rarity INT,
        category VARCHAR(100) DEFAULT 'regular',
        image_url TEXT,
        custom_emoji TEXT
    );
    """)
    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_inventory (
        instance_id SERIAL PRIMARY KEY,
        user_id VARCHAR(50),
        card_id VARCHAR(100),
        obtained_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        unique_code VARCHAR(10),
        is_favorite BOOLEAN DEFAULT FALSE,
        tag_id INT
    );
    """)
    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_currencies (
        user_id VARCHAR(50) PRIMARY KEY,
        paint INT DEFAULT 0,
        glue INT DEFAULT 0
    );
    """)
    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_user_tags (
        tag_id SERIAL PRIMARY KEY,
        user_id VARCHAR(50),
        tag_name VARCHAR(50),
        emoji VARCHAR(100) DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # Unique codes for guess cards or legacy inventory items
    await run_query("CREATE INDEX IF NOT EXISTS idx_ryo_inventory_code ON ryo_inventory(unique_code);")
    await run_query("CREATE INDEX IF NOT EXISTS idx_ryo_inventory_user_card ON ryo_inventory(user_id, card_id);")

    # Add tag_id to ryo_inventory if it doesn't exist
    has_tag_col = await run_query("""
        SELECT COLUMN_NAME 
        FROM INFORMATION_SCHEMA.COLUMNS 
        WHERE TABLE_NAME = 'ryo_inventory' AND COLUMN_NAME = 'tag_id';
    """, fetchone=True)
    if not has_tag_col:
        await run_query("ALTER TABLE ryo_inventory ADD COLUMN tag_id INT REFERENCES ryo_user_tags(tag_id) ON DELETE SET NULL;")

    await run_query("""
    CREATE TABLE IF NOT EXISTS disabled_commands (
        command_name VARCHAR(100) PRIMARY KEY,
        disabled_text TEXT
    );
    """)

    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_packs (
        pack_id SERIAL PRIMARY KEY,
        pack_name VARCHAR(100) UNIQUE,
        price INT DEFAULT 0,
        glue_price INT DEFAULT 0,
        is_active BOOLEAN DEFAULT TRUE,
        required_role_id BIGINT,
        cooldown_seconds INT DEFAULT 0
    );
    """)
    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_pack_contents (
        pack_id INT REFERENCES ryo_packs(pack_id) ON DELETE CASCADE,
        card_id VARCHAR(100) REFERENCES ryo_cards(card_id) ON DELETE CASCADE,
        PRIMARY KEY (pack_id, card_id)
    );
    """)

    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_binder_instances (
        binder_instance_id SERIAL PRIMARY KEY,
        user_id VARCHAR(50) NOT NULL,
        binder_number INT NOT NULL,
        is_active BOOLEAN DEFAULT FALSE,
        UNIQUE (user_id, binder_number)
    );
    """)
    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_binder_slots (
        binder_instance_id INT REFERENCES ryo_binder_instances(binder_instance_id) ON DELETE CASCADE,
        slot INT CHECK (slot BETWEEN 1 AND 8),
        card_id VARCHAR(100) REFERENCES ryo_cards(card_id) ON DELETE SET NULL,
        PRIMARY KEY (binder_instance_id, slot)
    );
    """)

    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_binder_shop_items (
        purchase_id VARCHAR(100) PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        drop_emoji VARCHAR(100) NOT NULL,
        item_type VARCHAR(100) NOT NULL,
        image_url TEXT NOT NULL,
        paint_price INT DEFAULT 0,
        glue_price INT DEFAULT 0
    );
    """)

    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_user_binders (
        user_id VARCHAR(50) NOT NULL,
        purchase_id VARCHAR(100) NOT NULL,
        purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, purchase_id)
    );
    """)

    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_market (
        market_id SERIAL PRIMARY KEY,
        seller_id VARCHAR(50) NOT NULL,
        card_instance_id INT REFERENCES ryo_inventory(instance_id) ON DELETE CASCADE,
        price INT NOT NULL,
        currency VARCHAR(10) DEFAULT 'paint',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_sold BOOLEAN DEFAULT FALSE,
        buyer_id VARCHAR(50)
    );
    """)

    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_rarity_custom (
        era VARCHAR(100),
        card_id VARCHAR(100),
        icon_url TEXT,
        PRIMARY KEY (era, card_id)
    );
    """)

    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_quests (
        quest_id VARCHAR(100) PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        description TEXT NOT NULL,
        requirements JSONB NOT NULL,
        reward_paint INT DEFAULT 0,
        reward_glue INT DEFAULT 0,
        is_active BOOLEAN DEFAULT TRUE
    );
    """)
    await run_query("""
    CREATE TABLE IF NOT EXISTS ryo_user_quests (
        user_id VARCHAR(50),
        quest_id VARCHAR(100) REFERENCES ryo_quests(quest_id) ON DELETE CASCADE,
        progress JSONB NOT NULL,
        completed_at TIMESTAMP,
        PRIMARY KEY (user_id, quest_id)
    );
    """)

    await update_autocomplete_cache(force=True)
    await refresh_rarity_cache()


# --- Image Processing Helpers ---
def add_rounded_corners(im, radius=20):
    mask = Image.new('L', im.size, 0)
    draw = ImageDraw.Draw(mask)
    if hasattr(draw, 'rounded_rectangle'):
        draw.rounded_rectangle([0, 0, im.size[0] - 1, im.size[1] - 1], radius=radius, fill=255)
    else:
        draw.rectangle([0, 0, im.size[0], im.size[1]], fill=255)
    result = im.copy()
    result.putalpha(mask)
    return result

def draw_empty_slot(draw, x, y, card_w, card_h, slot):
    box = [x, y, x + card_w, y + card_h]
    if hasattr(draw, 'rounded_rectangle'):
        draw.rounded_rectangle(box, radius=20, fill=(235, 230, 245, 180), outline=(140, 110, 170, 255), width=5)
    else:
        draw.rectangle(box, fill=(235, 230, 245, 180), outline=(140, 110, 170, 255), width=5)
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 36)
    except Exception:
        font = ImageFont.load_default()
        
    slot_text = f"SLOT {slot}"
    try:
        text_bbox = draw.textbbox((0, 0), slot_text, font=font)
        tw = text_bbox[2] - text_bbox[0]
        th = text_bbox[3] - text_bbox[1]
    except Exception:
        tw, th = 100, 20
        
    tx = x + (card_w - tw) // 2
    ty = y + (card_h - th) // 2 - 35
    draw.text((tx, ty), slot_text, fill=(110, 80, 140, 255), font=font)
    
    plus_text = "+"
    try:
        plus_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 64)
        plus_bbox = draw.textbbox((0, 0), plus_text, font=plus_font)
        ptw = plus_bbox[2] - plus_bbox[0]
        pth = plus_bbox[3] - plus_bbox[1]
    except Exception:
        plus_font = font
        ptw, pth = 25, 25
        
    ptx = x + (card_w - ptw) // 2
    pty = y + (card_h - pth) // 2 + 25
    draw.text((ptx, pty), plus_text, fill=(110, 80, 140, 255), font=plus_font)

async def load_one_card(slot, card_id, card_w, card_h, card_details, resample_filter):
    """Downloads one card, scales it, rounds corners."""
    try:
        details = card_details.get(card_id.lower())
        url = details[1] if details else None
        
        if not url or not str(url).lower().startswith("http"):
            return None
            
        session = await get_session()
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.read()
                with Image.open(io.BytesIO(data)) as card_img:
                    card_img = card_img.convert("RGBA")
                    try:
                        resized = ImageOps.contain(card_img, (card_w, card_h), resample_filter)
                    except AttributeError:
                        resized = card_img.copy()
                        resized.thumbnail((card_w, card_h), resample_filter)
                        
                    rounded = add_rounded_corners(resized, radius=20)
                    return (slot, rounded)
    except Exception as e:
        print(f"Error loading card {card_id} for slot {slot}: {e}")
    return None

async def generate_binder_image_buffer(slots_map, card_details):
    resample_filter = getattr(Image, "Resampling", Image).LANCZOS
    canvas = None
    
    # Try multiple background paths including original absolute & local relative paths
    for bg_path in ["./src/assets/images/binder_background_1782549906509.jpg", "src/assets/images/binder_background_1782549906509.jpg", "/src/assets/images/binder_background_1782549906509.jpg"]:
        try:
            canvas = Image.open(bg_path)
            canvas = canvas.convert("RGBA")
            canvas = canvas.resize((2048, 1536), resample_filter)
            break
        except Exception:
            continue
            
    if not canvas:
        try:
            canvas = Image.new('RGBA', (2048, 1536), (45, 30, 60, 255))
            draw = ImageDraw.Draw(canvas)
            if hasattr(draw, 'rounded_rectangle'):
                draw.rounded_rectangle([60, 60, 990, 1476], radius=30, fill=(245, 240, 250, 255))
                draw.rounded_rectangle([1058, 60, 1988, 1476], radius=30, fill=(245, 240, 250, 255))
                for y_val in range(150, 1450, 250):
                    draw.rounded_rectangle([995, y_val, 1053, y_val+35], radius=15, fill=(100, 100, 100, 255))
            else:
                draw.rectangle([60, 60, 990, 1476], fill=(245, 240, 250, 255))
                draw.rectangle([1058, 60, 1988, 1476], fill=(245, 240, 250, 255))
                for y_val in range(150, 1450, 250):
                    draw.rectangle([995, y_val, 1053, y_val+35], fill=(100, 100, 100, 255))
        except Exception as e:
            print(f"Error drawing fallback background: {e}")
            canvas = Image.new('RGBA', (2048, 1536), (45, 30, 60, 255))

    card_w = 380
    card_h = 570

    SLOT_COORDS = {
        1: (112, 203),
        2: (512, 203),
        3: (112, 853),
        4: (512, 853),
        5: (1112, 203),
        6: (1512, 203),
        7: (1112, 853),
        8: (1512, 853)
    }

    tasks = []
    for slot_num in range(1, 9):
        if slot_num in slots_map:
            cid = slots_map[slot_num]
            if cid:
                tasks.append(load_one_card(slot_num, cid, card_w, card_h, card_details, resample_filter))

    loaded_cards = {}
    if tasks:
        results = await asyncio.gather(*tasks)
        for r in results:
            if r:
                slot_num, img = r
                loaded_cards[slot_num] = img

    draw = ImageDraw.Draw(canvas)
    for slot_num in range(1, 9):
        x, y = SLOT_COORDS[slot_num]
        if slot_num in loaded_cards:
            card_img = loaded_cards[slot_num]
            iw, ih = card_img.size
            px = x + (card_w - iw) // 2
            py = y + (card_h - ih) // 2
            canvas.paste(card_img, (px, py), card_img)
        else:
            draw_empty_slot(draw, x, y, card_w, card_h, slot_num)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# --- Discord Bot Setup ---
class RyoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="ryo!", intents=intents)

    async def setup_hook(self):
        await setup_database()
        
        # Globally load sync app commands
        @self.tree.command(name="sync_commands", description="Developer only: Sync global app commands")
        async def sync_commands(interaction: discord.Interaction):
            if interaction.user.id != DEV_ID:
                await interaction.response.send_message("❌ Access denied.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                synced = await self.tree.sync()
                await interaction.followup.send(f"✅ Successfully synced {len(synced)} global command(s).")
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to sync: {e}")

        # Command inhibitor middleware
        async def before_interaction(interaction: discord.Interaction):
            if interaction.user.id == DEV_ID:
                return True
            
            # Check disabled commands
            cmd_name = interaction.command.name.lower() if interaction.command else None
            if cmd_name:
                disabled_map = await get_disabled_commands()
                if cmd_name in disabled_map:
                    reason = disabled_map[cmd_name] or "This command is temporarily disabled."
                    try:
                        await interaction.response.send_message(f"⚠️ **Command Disabled**\n{reason}", ephemeral=True)
                    except discord.errors.InteractionResponded:
                        await interaction.followup.send(f"⚠️ **Command Disabled**\n{reason}", ephemeral=True)
                    return False
            return True

        self.tree.interaction_check = before_interaction

    async def close(self):
        global GLOBAL_SESSION
        if GLOBAL_SESSION and not GLOBAL_SESSION.closed:
            await GLOBAL_SESSION.close()
        if db_pool:
            db_pool.closeall()
        await super().close()

bot = RyoBot()


# --- Custom Error Handler ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        seconds = error.retry_after
        try:
            await interaction.response.send_message(
                f"⌛ Your drop request is cooling down! Try again in **{int(seconds)}s**.",
                ephemeral=True
            )
        except Exception:
            try:
                await interaction.followup.send(
                    f"⌛ Your drop request is cooling down! Try again in **{int(seconds)}s**.",
                    ephemeral=True
                )
            except Exception:
                pass
    else:
        print(f"[APP COMMAND ERROR] {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An unexpected error occurred while processing this command.", ephemeral=True)
            else:
                await interaction.followup.send("❌ An unexpected error occurred while processing this command.", ephemeral=True)
        except Exception:
            pass


# --- Autocomplete Logic ---
@safe_autocomplete
async def card_autocomplete(interaction: discord.Interaction, current: str):
    await update_autocomplete_cache()
    current_lower = current.lower()
    
    # Fast match
    matches = []
    for c_id in AUTOCOMPLETE_CACHE["names"]:
        if current_lower in c_id.lower():
            matches.append(app_commands.Choice(name=c_id, value=c_id))
            if len(matches) >= 25:
                break
    return matches

@safe_autocomplete
async def group_autocomplete(interaction: discord.Interaction, current: str):
    await update_autocomplete_cache()
    current_lower = current.lower()
    matches = []
    for g_id in AUTOCOMPLETE_CACHE["groups"]:
        if current_lower in g_id.lower():
            matches.append(app_commands.Choice(name=g_id, value=g_id))
            if len(matches) >= 25:
                break
    return matches

@safe_autocomplete
async def era_autocomplete(interaction: discord.Interaction, current: str):
    await update_autocomplete_cache()
    current_lower = current.lower()
    matches = []
    for e_id in AUTOCOMPLETE_CACHE["eras"]:
        if current_lower in e_id.lower():
            matches.append(app_commands.Choice(name=e_id, value=e_id))
            if len(matches) >= 25:
                break
    return matches


# --- Commands ---
@bot.tree.command(name="daily", description="Claim your daily paint and glue rewards!")
async def daily_cmd(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    
    # Cooldown Check
    cooldown_res = await run_query("SELECT last_used FROM ryo_cooldowns WHERE user_id=%s AND command='daily'", (user_id,), fetchone=True)
    last_used = cooldown_res[0] if cooldown_res else 0
    now = int(time.time())
    
    # 24 hours cooldown
    cooldown_seconds = 86400
    if now - last_used < cooldown_seconds:
        remaining = cooldown_seconds - (now - last_used)
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        await interaction.response.send_message(
            f"⌛ You've already claimed your daily rewards! Try again in **{hours}h {minutes}m**.",
            ephemeral=True
        )
        return

    # Update Cooldown
    await run_query(
        "INSERT INTO ryo_cooldowns (user_id, command, last_used) VALUES (%s, 'daily', %s) ON CONFLICT (user_id, command) DO UPDATE SET last_used = %s",
        (user_id, now, now)
    )

    paint_reward = random.randint(150, 300)
    glue_reward = random.randint(15, 30)

    # Add currencies
    await run_query(
        "INSERT INTO ryo_currencies (user_id, paint, glue) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET paint = ryo_currencies.paint + %s, glue = ryo_currencies.glue + %s",
        (user_id, paint_reward, glue_reward, paint_reward, glue_reward)
    )

    embed = discord.Markdown() # Wait, discord.Embed is standard
    embed = discord.Embed(
        title="☀️ Daily Rewards Claimed!",
        description=f"You successfully claimed your daily rewards:\n\n"
                    f"🖌️ **+{paint_reward}** Paint\n"
                    f"🧪 **+{glue_reward}** Glue",
        color=RYO_COLOR
    )
    embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="draw", description="Spend paint or glue to drop card packs!")
@app_commands.describe(pack_name="Name of the pack you want to buy (Optional)")
async def draw_cmd(interaction: discord.Interaction, pack_name: str = None):
    user_id = str(interaction.user.id)
    await interaction.response.defer()

    if pack_name:
        pack = await run_query("SELECT pack_id, pack_name, price, glue_price, required_role_id, cooldown_seconds FROM ryo_packs WHERE LOWER(pack_name) = LOWER(%s) AND is_active = TRUE", (pack_name.strip(),), fetchone=True)
    else:
        pack = await run_query("SELECT pack_id, pack_name, price, glue_price, required_role_id, cooldown_seconds FROM ryo_packs WHERE is_active = TRUE ORDER BY price ASC LIMIT 1", fetchone=True)

    if not pack:
        await interaction.followup.send("⚠️ No active packs found matching that name.")
        return

    pack_id, p_name, price_paint, price_glue, required_role_id, cooldown_seconds = pack

    # Role Requirement Check
    if required_role_id:
        has_role = False
        if hasattr(interaction.user, 'roles'):
            has_role = any(r.id == required_role_id for r in interaction.user.roles)
        if not has_role and interaction.user.id != DEV_ID:
            await interaction.followup.send(f"❌ You do not have the required role to buy the **{p_name}** pack.")
            return

    # Cooldown Check
    if cooldown_seconds and cooldown_seconds > 0:
        rem_cooldown = await check_pack_cooldown(user_id, pack_id, cooldown_seconds)
        if rem_cooldown > 0:
            minutes = int(rem_cooldown // 60)
            seconds = int(rem_cooldown % 60)
            await interaction.followup.send(f"⌛ The **{p_name}** pack is cooling down! Try again in **{minutes}m {seconds}s**.")
            return

    # Balance Check
    cur_res = await run_query("SELECT paint, glue FROM ryo_currencies WHERE user_id=%s", (user_id,), fetchone=True)
    p_bal = cur_res[0] if cur_res else 0
    g_bal = cur_res[1] if cur_res else 0

    if p_bal < price_paint or g_bal < price_glue:
        await interaction.followup.send(f"❌ Insufficient currencies to buy **{p_name}**!\nPrice: 🖌️ **{price_paint}** Paint, 🧪 **{price_glue}** Glue\nYour Bal: 🖌️ **{p_bal}** Paint, 🧪 **{g_bal}** Glue")
        return

    # Fetch contents
    contents = await run_query("SELECT card_id FROM ryo_pack_contents WHERE pack_id=%s", (pack_id,), fetchall=True)
    if not contents:
        await interaction.followup.send("⚠️ This pack is currently empty.")
        return

    card_ids = [c[0] for c in contents]
    
    # Fetch actual card details to filter out any missing ones
    rows = await run_query("SELECT card_id, image_url, member_name, group_name, era, rarity, category FROM ryo_cards WHERE card_id = ANY(%s)", (card_ids,), fetchall=True) or []
    if not rows:
        await interaction.followup.send("⚠️ The items in this pack could not be loaded from registry.")
        return

    # Pick 1 random card
    chosen_card = random.choice(rows)
    cid, img_url, member_name, group_name, era, rarity, category = chosen_card

    # Deduct funds
    await run_query(
        "UPDATE ryo_currencies SET paint = paint - %s, glue = glue - %s WHERE user_id = %s",
        (price_paint, price_glue, user_id)
    )

    # Grant Card
    code = await grant_card(user_id, cid)

    # Update Cooldown
    if cooldown_seconds and cooldown_seconds > 0:
        await update_pack_cooldown(user_id, pack_id)

    # Send Result
    rarity_display = get_rarity_display(rarity, category, era, cid)
    embed = discord.Embed(
        title=f"🎉 Pack Opened: {p_name}!",
        description=f"You spent 🖌️ **{price_paint}** Paint & 🧪 **{price_glue}** Glue and found:\n\n"
                    f"**{member_name}** ({group_name})\n"
                    f"Era: **{era}**\n"
                    f"Rarity: {rarity_display}\n"
                    f"ID: `{cid}`" + (f"\nCode: `{code}`" if code else ""),
        color=RYO_COLOR
    )
    if img_url:
        embed.set_image(url=img_url)
    embed.set_footer(text=f"Opened by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="binder", description="View and manage your custom card binders!")
@app_commands.describe(
    action="What to do with the binder (show, create, add, remove, list, activate, delete)",
    binder_num="Binder index (1-5)",
    slot="Binder slot (1-8)",
    card_id="Card Registry ID (e.g. member_era)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="show", value="show"),
    app_commands.Choice(name="create", value="create"),
    app_commands.Choice(name="add", value="add"),
    app_commands.Choice(name="remove", value="remove"),
    app_commands.Choice(name="list", value="list"),
    app_commands.Choice(name="activate", value="activate"),
    app_commands.Choice(name="delete", value="delete")
])
async def binder_cmd(
    interaction: discord.Interaction, 
    action: str, 
    binder_num: int = None, 
    slot: int = None, 
    card_id: str = None
):
    user_id = str(interaction.user.id)
    await interaction.response.defer()

    if action == "list":
        rows = await run_query("SELECT binder_number, is_active FROM ryo_binder_instances WHERE user_id = %s ORDER BY binder_number ASC", (user_id,), fetchall=True) or []
        if not rows:
            await interaction.followup.send("📁 You have no binders created yet. Use `/binder action:create binder_num:1` to start!")
            return
            
        desc = ""
        for b_num, is_act in rows:
            status = "🟢 **[ACTIVE]**" if is_act else "⚪ [Inactive]"
            desc += f"• **Binder {b_num}** - {status}\n"
            
        embed = discord.Embed(title=f"📁 {interaction.user.display_name}'s Binders", description=desc, color=RYO_COLOR)
        await interaction.followup.send(embed=embed)
        return

    if action == "create":
        if not binder_num or binder_num < 1 or binder_num > 5:
            await interaction.followup.send("❌ Please specify a valid binder index (1-5).")
            return
            
        # Check if already exists
        exists = await run_query("SELECT binder_instance_id FROM ryo_binder_instances WHERE user_id = %s AND binder_number = %s", (user_id, binder_num), fetchone=True)
        if exists:
            await interaction.followup.send(f"❌ You already have a **Binder {binder_num}** created.")
            return
            
        # Is this the first binder? Activate it by default
        has_binders = await run_query("SELECT 1 FROM ryo_binder_instances WHERE user_id = %s LIMIT 1", (user_id,), fetchone=True)
        is_active = not bool(has_binders)
        
        await run_query("INSERT INTO ryo_binder_instances (user_id, binder_number, is_active) VALUES (%s, %s, %s)", (user_id, binder_num, is_active))
        status_txt = "and set as **ACTIVE** 🟢" if is_active else ""
        await interaction.followup.send(f"✅ Successfully created **Binder {binder_num}** {status_txt}!")
        return

    # Find specified or default active binder
    if binder_num:
        if binder_num < 1 or binder_num > 5:
            await interaction.followup.send("❌ Please specify a valid binder index (1-5).")
            return
        binder = await run_query("SELECT binder_instance_id, binder_number, is_active FROM ryo_binder_instances WHERE user_id = %s AND binder_number = %s", (user_id, binder_num), fetchone=True)
    else:
        # Default to active binder
        binder = await run_query("SELECT binder_instance_id, binder_number, is_active FROM ryo_binder_instances WHERE user_id = %s AND is_active = TRUE", (user_id,), fetchone=True)
        if not binder:
            # Fallback to lowest binder number
            binder = await run_query("SELECT binder_instance_id, binder_number, is_active FROM ryo_binder_instances WHERE user_id = %s ORDER BY binder_number ASC LIMIT 1", (user_id,), fetchone=True)

    if not binder:
        await interaction.followup.send("❌ You don't have any binders created yet! Use `/binder action:create binder_num:1` to create one.")
        return

    binder_inst_id, b_num, is_active = binder

    if action == "activate":
        # Deactivate current
        await run_query("UPDATE ryo_binder_instances SET is_active = FALSE WHERE user_id = %s", (user_id,))
        # Activate this one
        await run_query("UPDATE ryo_binder_instances SET is_active = TRUE WHERE binder_instance_id = %s", (binder_inst_id,))
        await interaction.followup.send(f"🟢 **Binder {b_num}** is now your active showcase binder!")
        return

    if action == "delete":
        await run_query("DELETE FROM ryo_binder_instances WHERE binder_instance_id = %s", (binder_inst_id,))
        # If deleted active, set another active
        if is_active:
            next_b = await run_query("SELECT binder_instance_id FROM ryo_binder_instances WHERE user_id = %s ORDER BY binder_number ASC LIMIT 1", (user_id,), fetchone=True)
            if next_b:
                await run_query("UPDATE ryo_binder_instances SET is_active = TRUE WHERE binder_instance_id = %s", (next_b[0],))
        await interaction.followup.send(f"🗑️ Successfully deleted **Binder {b_num}**.")
        return

    if action == "show":
        rows = await run_query("SELECT slot, card_id FROM ryo_binder_slots WHERE binder_instance_id = %s", (binder_inst_id,), fetchall=True) or []
        slots_map = {row[0]: row[1] for row in rows}
        
        # Load card details
        card_ids = list(set([cid for cid in slots_map.values() if cid]))
        card_rows = []
        if card_ids:
            card_rows = await run_query("SELECT card_id, image_url, member_name FROM ryo_cards WHERE card_id = ANY(%s)", (card_ids,), fetchall=True) or []
            
        card_details = {r[0].lower(): r for r in card_rows}
        
        try:
            buffer = await generate_binder_image_buffer(slots_map, card_details)
        except Exception as e:
            print(f"Error rendering bindershow: {e}")
            await interaction.followup.send("⚠️ Failed to load binder showcase.")
            return

        embed = discord.Embed(
            title=f"📁 {interaction.user.display_name}'s Showcase - Binder {b_num}",
            description="Use `/binder action:add` or `/binder action:remove` to manage slots!",
            color=RYO_COLOR
        )
        file = discord.File(buffer, filename="binder.png")
        embed.set_image(url="attachment://binder.png")
        embed.set_footer(text=f"Active Binder: {b_num}/5", icon_url=interaction.user.display_avatar.url)
        
        await interaction.followup.send(file=file, embed=embed)
        return

    if action == "add":
        if not slot or slot < 1 or slot > 8:
            await interaction.followup.send("❌ Please specify a valid slot index (1-8).")
            return
        if not card_id:
            await interaction.followup.send("❌ Please specify a registry `card_id` to add to the slot.")
            return
            
        # Verify card exists in registry
        card = await run_query("SELECT card_id, member_name, group_name, era FROM ryo_cards WHERE LOWER(card_id) = LOWER(%s)", (card_id.strip(),), fetchone=True)
        if not card:
            await interaction.followup.send(f"❌ Card ID `{card_id}` not found in the global registry.")
            return
            
        canonical_id, m_name, g_name, era = card

        # Verify user owns at least one copy of this card
        owned = await run_query("SELECT instance_id FROM ryo_inventory WHERE user_id = %s AND LOWER(card_id) = LOWER(%s) LIMIT 1", (user_id, canonical_id), fetchone=True)
        if not owned:
            await interaction.followup.send(f"❌ You do not own the card **{m_name}** ({g_name}, {era})!")
            return

        # Insert or update slot
        await run_query("""
            INSERT INTO ryo_binder_slots (binder_instance_id, slot, card_id) 
            VALUES (%s, %s, %s) 
            ON CONFLICT (binder_instance_id, slot) 
            DO UPDATE SET card_id = EXCLUDED.card_id
        """, (binder_inst_id, slot, canonical_id))

        await interaction.followup.send(f"✅ Added **{m_name}** ({g_name}, {era}) to **Slot {slot}** inside **Binder {b_num}**!")
        return

    if action == "remove":
        if not slot or slot < 1 or slot > 8:
            await interaction.followup.send("❌ Please specify a valid slot index (1-8) to clear.")
            return
            
        await run_query("DELETE FROM ryo_binder_slots WHERE binder_instance_id = %s AND slot = %s", (binder_inst_id, slot))
        await interaction.followup.send(f"✅ Cleared **Slot {slot}** inside **Binder {b_num}**.")
        return


@bot.tree.command(name="quests", description="View, track, and complete daily quests for rewards!")
@app_commands.describe(action="Quest action (list, track, complete)")
@app_commands.choices(action=[
    app_commands.Choice(name="list", value="list"),
    app_commands.Choice(name="track", value="track"),
    app_commands.Choice(name="complete", value="complete")
])
async def quests_cmd(interaction: discord.Interaction, action: str, quest_id: str = None):
    user_id = str(interaction.user.id)
    await interaction.response.defer()

    if action == "list":
        quests = await run_query("SELECT quest_id, title, description, reward_paint, reward_glue FROM ryo_quests WHERE is_active = TRUE", fetchall=True) or []
        if not quests:
            await interaction.followup.send("📋 There are no active quests available right now.")
            return

        embed = discord.Embed(title="📋 Active Quests", color=RYO_COLOR)
        for q_id, title, desc, r_paint, r_glue in quests:
            # Check user progress
            prog_row = await run_query("SELECT progress, completed_at FROM ryo_user_quests WHERE user_id=%s AND quest_id=%s", (user_id, q_id), fetchone=True)
            status = "⚪ Not Started"
            if prog_row:
                completed_at = prog_row[1]
                if completed_at:
                    status = "🟢 Completed"
                else:
                    status = "🟡 In Progress"
            
            embed.add_field(
                name=f"{title} ({q_id})",
                value=f"*{desc}*\nStatus: **{status}**\nRewards: 🖌️ **{r_paint}** Paint, 🧪 **{r_glue}** Glue",
                inline=False
            )
        await interaction.followup.send(embed=embed)
        return

    if action == "track":
        if not quest_id:
            await interaction.followup.send("❌ Please specify a quest ID to track.")
            return

        quest = await run_query("SELECT requirements, title FROM ryo_quests WHERE quest_id=%s", (quest_id,), fetchone=True)
        if not quest:
            await interaction.followup.send("❌ Quest not found.")
            return

        reqs, title = quest
        # Fetch current user inventory to compare
        inventory = await run_query("SELECT card_id FROM ryo_inventory WHERE user_id = %s", (user_id,), fetchall=True) or []
        owned_card_ids = [row[0].lower() for row in inventory]

        # Calculate progress
        target_cards = reqs.get("cards", [])
        completed_targets = []
        missing_targets = []
        for target_id in target_cards:
            if target_id.lower() in owned_card_ids:
                completed_targets.append(target_id)
            else:
                missing_targets.append(target_id)

        prog_percentage = (len(completed_targets) / len(target_cards)) * 100 if target_cards else 100

        desc = f"**Quest Progress: {int(prog_percentage)}%**\n\n"
        if completed_targets:
            desc += "🟢 **Owned:**\n" + "\n".join([f"• `{c}`" for c in completed_targets]) + "\n\n"
        if missing_targets:
            desc += "❌ **Missing:**\n" + "\n".join([f"• `{c}`" for c in missing_targets])

        embed = discord.Embed(title=f"🎯 Quest Track: {title}", description=desc, color=RYO_COLOR)
        await interaction.followup.send(embed=embed)
        return

    if action == "complete":
        if not quest_id:
            await interaction.followup.send("❌ Please specify a quest ID to complete.")
            return

        quest = await run_query("SELECT requirements, reward_paint, reward_glue, title FROM ryo_quests WHERE quest_id=%s AND is_active=True", (quest_id,), fetchone=True)
        if not quest:
            await interaction.followup.send("❌ Active quest not found.")
            return

        reqs, r_paint, r_glue, title = quest

        # Check if already completed
        already_comp = await run_query("SELECT completed_at FROM ryo_user_quests WHERE user_id=%s AND quest_id=%s", (user_id, quest_id), fetchone=True)
        if already_comp and already_comp[0]:
            await interaction.followup.send(f"❌ You have already completed the **{title}** quest!")
            return

        # Verify requirements
        inventory = await run_query("SELECT card_id FROM ryo_inventory WHERE user_id = %s", (user_id,), fetchall=True) or []
        owned_card_ids = [row[0].lower() for row in inventory]

        target_cards = reqs.get("cards", [])
        for target_id in target_cards:
            if target_id.lower() not in owned_card_ids:
                await interaction.followup.send(f"❌ You do not meet the requirements for this quest! Use `/quests action:track quest_id:{quest_id}` to see what is missing.")
                return

        # Complete Quest & Reward
        now = datetime.datetime.utcnow()
        import json
        prog_json = json.dumps({"cards_owned": target_cards})
        
        await run_query("""
            INSERT INTO ryo_user_quests (user_id, quest_id, progress, completed_at) 
            VALUES (%s, %s, %s, %s) 
            ON CONFLICT (user_id, quest_id) 
            DO UPDATE SET completed_at = EXCLUDED.completed_at, progress = EXCLUDED.progress
        """, (user_id, quest_id, prog_json, now))

        # Reward Currencies
        await run_query("""
            INSERT INTO ryo_currencies (user_id, paint, glue) 
            VALUES (%s, %s, %s) 
            ON CONFLICT (user_id) 
            DO UPDATE SET paint = ryo_currencies.paint + EXCLUDED.paint, glue = ryo_currencies.glue + EXCLUDED.glue
        """, (user_id, r_paint, r_glue))

        embed = discord.Embed(
            title=f"🎉 Quest Completed: {title}!",
            description=f"Congratulations! You completed the quest and earned:\n\n"
                        f"🖌️ **+{r_paint}** Paint\n"
                        f"🧪 **+{r_glue}** Glue",
            color=RYO_COLOR
        )
        await interaction.followup.send(embed=embed)


@bot.tree.command(name="market", description="Trade cards in the marketplace!")
@app_commands.describe(
    action="Market action (list, sell, buy, cancel)",
    instance_id="The Instance ID of the card you want to sell/cancel",
    price="Sale price (Paint/Glue value)",
    currency="Currency to charge (paint, glue)",
    market_id="The ID of the market listing to buy"
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="list", value="list"),
        app_commands.Choice(name="sell", value="sell"),
        app_commands.Choice(name="buy", value="buy"),
        app_commands.Choice(name="cancel", value="cancel")
    ],
    currency=[
        app_commands.Choice(name="paint", value="paint"),
        app_commands.Choice(name="glue", value="glue")
    ]
)
async def market_cmd(
    interaction: discord.Interaction, 
    action: str, 
    instance_id: int = None, 
    price: int = None, 
    currency: str = "paint", 
    market_id: int = None
):
    user_id = str(interaction.user.id)
    await interaction.response.defer()

    if action == "list":
        listings = await run_query("""
            SELECT m.market_id, m.seller_id, m.price, m.currency, c.member_name, c.group_name, c.era, i.unique_code, i.instance_id
            FROM ryo_market m
            JOIN ryo_inventory i ON m.card_instance_id = i.instance_id
            JOIN ryo_cards c ON i.card_id = c.card_id
            WHERE m.is_sold = FALSE
            ORDER BY m.created_at DESC 
            LIMIT 10
        """, fetchall=True) or []

        if not listings:
            await interaction.followup.send("🛒 No listings available on the marketplace right now.")
            return

        embed = discord.Embed(title="🛒 Ryo Marketplace", color=RYO_COLOR)
        for m_id, seller_id, pr, curr, m_name, g_name, era, code, inst_id in listings:
            curr_emoji = "🖌️" if curr == "paint" else "🧪"
            code_txt = f" (`{code}`)" if code else ""
            embed.add_field(
                name=f"Listing #{m_id} - {m_name}{code_txt}",
                value=f"• Group: **{g_name}** | Era: **{era}**\n"
                      f"• Price: {curr_emoji} **{pr}** {curr.capitalize()}\n"
                      f"• Seller: <@{seller_id}> (Instance: `{inst_id}`)",
                inline=False
            )
        await interaction.followup.send(embed=embed)
        return

    if action == "sell":
        if not instance_id:
            await interaction.followup.send("❌ Please specify the `instance_id` of the card to sell.")
            return
        if not price or price <= 0:
            await interaction.followup.send("❌ Please specify a valid price higher than 0.")
            return

        # Check ownership and favorites
        card = await run_query("SELECT card_id, is_favorite FROM ryo_inventory WHERE instance_id = %s AND user_id = %s", (instance_id, user_id), fetchone=True)
        if not card:
            await interaction.followup.send("❌ You do not own a card with that instance ID.")
            return

        cid, is_fav = card
        if is_fav:
            await interaction.followup.send("❌ You cannot list a favorite card on the marketplace. Unfavorite it first!")
            return

        # Check if already listed
        listed = await run_query("SELECT market_id FROM ryo_market WHERE card_instance_id = %s AND is_sold = FALSE", (instance_id,), fetchone=True)
        if listed:
            await interaction.followup.send("❌ This card instance is already listed in the marketplace.")
            return

        # List card
        await run_query("""
            INSERT INTO ryo_market (seller_id, card_instance_id, price, currency) 
            VALUES (%s, %s, %s, %s)
        """, (user_id, instance_id, price, currency.lower()))

        await interaction.followup.send(f"✅ Successfully listed card instance `{instance_id}` on the marketplace for **{price}** {currency.capitalize()}!")
        return

    if action == "cancel":
        if not instance_id:
            await interaction.followup.send("❌ Please specify the `instance_id` of the card to cancel listing.")
            return

        listing = await run_query("SELECT market_id FROM ryo_market WHERE card_instance_id = %s AND seller_id = %s AND is_sold = FALSE", (instance_id, user_id), fetchone=True)
        if not listing:
            await interaction.followup.send("❌ Active listing not found for that card instance.")
            return

        await run_query("DELETE FROM ryo_market WHERE market_id = %s", (listing[0],))
        await interaction.followup.send(f"✅ Cancelled listing for card instance `{instance_id}`.")
        return

    if action == "buy":
        if not market_id:
            await interaction.followup.send("❌ Please specify a listing `market_id` to buy.")
            return

        listing = await run_query("SELECT seller_id, card_instance_id, price, currency FROM ryo_market WHERE market_id = %s AND is_sold = FALSE", (market_id,), fetchone=True)
        if not listing:
            await interaction.followup.send("❌ Listing not found or already sold.")
            return

        seller_id, inst_id, pr, curr = listing
        if seller_id == user_id:
            await interaction.followup.send("❌ You cannot buy your own card listing.")
            return

        # Check buyer funds
        cur_col = "paint" if curr == "paint" else "glue"
        cur_res = await run_query(f"SELECT {cur_col} FROM ryo_currencies WHERE user_id = %s", (user_id,), fetchone=True)
        buyer_bal = cur_res[0] if cur_res else 0

        if buyer_bal < pr:
            curr_emoji = "🖌️" if curr == "paint" else "🧪"
            await interaction.followup.send(f"❌ Insufficient funds! Price: {curr_emoji} **{pr}** {curr.capitalize()} (You have: **{buyer_bal}**).")
            return

        # Transact
        # Deduct from buyer
        await run_query(f"UPDATE ryo_currencies SET {cur_col} = {cur_col} - %s WHERE user_id = %s", (pr, user_id))
        # Add to seller
        await run_query(f"INSERT INTO ryo_currencies (user_id, {cur_col}) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET {cur_col} = ryo_currencies.{cur_col} + %s", (seller_id, pr, pr))
        
        # Transfer inventory ownership
        await run_query("UPDATE ryo_inventory SET user_id = %s, tag_id = NULL, is_favorite = FALSE WHERE instance_id = %s", (user_id, inst_id))
        
        # Mark listing as sold
        await run_query("UPDATE ryo_market SET is_sold = TRUE, buyer_id = %s WHERE market_id = %s", (user_id, market_id))

        await interaction.followup.send(f"🎉 Successfully purchased Listing #{market_id}! The card is now in your inventory.")


# --- Admin / Staff Commands ---
@bot.tree.command(name="award", description="Release Staff only: Award paint or glue to a user")
@app_commands.describe(user="The target user", paint="Paint amount", glue="Glue amount")
async def award_cmd(interaction: discord.Interaction, user: discord.User, paint: int = 0, glue: int = 0):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ This command is restricted to Release Staff.", ephemeral=True)
        return
        
    await interaction.response.defer()
    u_id = str(user.id)
    await run_query(
        "INSERT INTO ryo_currencies (user_id, paint, glue) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET paint = ryo_currencies.paint + %s, glue = ryo_currencies.glue + %s",
        (u_id, paint, glue, paint, glue)
    )
    await interaction.followup.send(f"✅ Awarded <@{u_id}>: 🖌️ **{paint}** Paint | 🧪 **{glue}** Glue.")


@bot.tree.command(name="disable_command", description="Developer only: Disable / Enable a command")
@app_commands.describe(command_name="Target command name", reason="Reason for disabling (Set empty to re-enable)")
async def disable_command_cmd(interaction: discord.Interaction, command_name: str, reason: str = None):
    if interaction.user.id != DEV_ID:
        await interaction.response.send_message("❌ Access denied.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    c_name = command_name.strip().lower()
    if reason:
        await run_query("INSERT INTO disabled_commands (command_name, disabled_text) VALUES (%s, %s) ON CONFLICT (command_name) DO UPDATE SET disabled_text = %s", (c_name, reason, reason))
        await interaction.followup.send(f"✅ Disabled command `/{c_name}`. Reason: {reason}")
    else:
        await run_query("DELETE FROM disabled_commands WHERE command_name = %s", (c_name,))
        await interaction.followup.send(f"✅ Enabled command `/{c_name}`.")
    
    await get_disabled_commands() # Reload cache immediately


# --- Binder Shop Implementation ---

def parse_emoji_helper(emoji_str):
    if not emoji_str:
        return None
    emoji_str = emoji_str.strip()
    match = re.match(r"^<(a?):([a-zA-Z0-9_]+):([0-9]+)>$", emoji_str)
    if match:
        animated_str, name, emoji_id = match.groups()
        return discord.PartialEmoji(name=name, id=int(emoji_id), animated=bool(animated_str))
    return discord.PartialEmoji(name=emoji_str)


@safe_autocomplete
async def type_autocomplete(interaction: discord.Interaction, current: str):
    current_lower = current.lower()
    choices = [
        app_commands.Choice(name="Colors", value="Colors"),
        app_commands.Choice(name="Theme", value="Theme")
    ]
    return [c for c in choices if current_lower in c.name.lower()]


class ColorsSelect(discord.ui.Select):
    def __init__(self, items, current_purchase_id):
        options = []
        for item in items:
            purchase_id, name, drop_emoji, item_type, _, _, _ = item
            if item_type == "Colors":
                options.append(discord.SelectOption(
                    label=name,
                    value=purchase_id,
                    emoji=parse_emoji_helper(drop_emoji),
                    default=(purchase_id == current_purchase_id)
                ))
        
        if not options:
            options.append(discord.SelectOption(label="No Colors Available", value="none", emoji="⚪"))
            super().__init__(placeholder="Select a Color...", options=options, disabled=True, row=0)
        else:
            super().__init__(placeholder="Select a Color...", options=options[:25], row=0)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.defer()
            return
        await self.view.handle_select(interaction, self.values[0])


class ThemeSelect(discord.ui.Select):
    def __init__(self, items, current_purchase_id):
        options = []
        for item in items:
            purchase_id, name, drop_emoji, item_type, _, _, _ = item
            if item_type == "Theme":
                options.append(discord.SelectOption(
                    label=name,
                    value=purchase_id,
                    emoji=parse_emoji_helper(drop_emoji),
                    default=(purchase_id == current_purchase_id)
                ))
        
        if not options:
            options.append(discord.SelectOption(label="No Themes Available", value="none", emoji="⚪"))
            super().__init__(placeholder="Select a Theme...", options=options, disabled=True, row=1)
        else:
            super().__init__(placeholder="Select a Theme...", options=options[:25], row=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.defer()
            return
        await self.view.handle_select(interaction, self.values[0])


class BinderShopView(discord.ui.View):
    def __init__(self, user_id, items, current_index=0):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.items = items
        self.current_index = current_index
        self.update_components()

    def update_components(self):
        self.clear_items()
        if not self.items:
            return

        current_item = self.items[self.current_index]
        current_purchase_id = current_item[0]

        # Add dropdowns
        self.add_item(ColorsSelect(self.items, current_purchase_id))
        self.add_item(ThemeSelect(self.items, current_purchase_id))

        # Add Buy button with green backdrop
        self.add_item(discord.ui.Button(
            label="Buy",
            style=discord.ButtonStyle.success,
            custom_id="buy",
            row=2
        ))

        # Disable navigation buttons based on position
        is_first = (self.current_index == 0)
        is_last = (self.current_index == len(self.items) - 1)

        # Pagination buttons
        self.add_item(discord.ui.Button(
            emoji=parse_emoji_helper(EMOJI_FAR_LEFT),
            style=discord.ButtonStyle.secondary,
            disabled=is_first,
            custom_id="far_left",
            row=2
        ))
        self.add_item(discord.ui.Button(
            emoji=parse_emoji_helper(EMOJI_LEFT),
            style=discord.ButtonStyle.secondary,
            disabled=is_first,
            custom_id="left",
            row=2
        ))
        self.add_item(discord.ui.Button(
            emoji=parse_emoji_helper(EMOJI_RIGHT),
            style=discord.ButtonStyle.secondary,
            disabled=is_last,
            custom_id="right",
            row=2
        ))
        self.add_item(discord.ui.Button(
            emoji=parse_emoji_helper(EMOJI_FAR_RIGHT),
            style=discord.ButtonStyle.secondary,
            disabled=is_last,
            custom_id="far_right",
            row=2
        ))

        # Assign interaction callbacks
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "buy":
                    child.callback = self.on_buy_click
                else:
                    child.callback = self.on_nav_click

    async def on_nav_click(self, interaction: discord.Interaction):
        custom_id = interaction.data["custom_id"]
        if custom_id == "far_left":
            self.current_index = 0
        elif custom_id == "left":
            self.current_index = max(0, self.current_index - 1)
        elif custom_id == "right":
            self.current_index = min(len(self.items) - 1, self.current_index + 1)
        elif custom_id == "far_right":
            self.current_index = len(self.items) - 1

        self.update_components()
        embed = build_bindershop_embed(interaction, self.items, self.current_index)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_buy_click(self, interaction: discord.Interaction):
        buyer_id = str(interaction.user.id)
        current_item = self.items[self.current_index]
        purchase_id, name, _, _, _, paint_price, glue_price = current_item

        # Check ownership
        owned = await run_query("SELECT 1 FROM ryo_user_binders WHERE user_id = %s AND purchase_id = %s", (buyer_id, purchase_id), fetchone=True)
        if owned:
            await interaction.response.send_message(f"❌ You already own the **{name}** binder design!", ephemeral=True)
            return

        # Check balance
        cur_res = await run_query("SELECT paint, glue FROM ryo_currencies WHERE user_id = %s", (buyer_id,), fetchone=True)
        p_bal = cur_res[0] if cur_res else 0
        g_bal = cur_res[1] if cur_res else 0

        if p_bal < paint_price or g_bal < glue_price:
            await interaction.response.send_message(
                f"❌ Insufficient funds to purchase **{name}**!\n"
                f"Cost: {PAINT_EMOJI} **{paint_price}** Paint | {GLUE_EMOJI} **{glue_price}** Glue\n"
                f"Your Bal: {PAINT_EMOJI} **{p_bal}** Paint | {GLUE_EMOJI} **{g_bal}** Glue",
                ephemeral=True
            )
            return

        # Transact
        await run_query("UPDATE ryo_currencies SET paint = paint - %s, glue = glue - %s WHERE user_id = %s", (paint_price, glue_price, buyer_id))
        await run_query("INSERT INTO ryo_user_binders (user_id, purchase_id) VALUES (%s, %s)", (buyer_id, purchase_id))

        await interaction.response.send_message(f"🎉 Successfully purchased the **{name}** binder design! Enjoy your new look!", ephemeral=True)

    async def handle_select(self, interaction: discord.Interaction, purchase_id):
        for idx, item in enumerate(self.items):
            if item[0] == purchase_id:
                self.current_index = idx
                break
        self.update_components()
        embed = build_bindershop_embed(interaction, self.items, self.current_index)
        await interaction.response.edit_message(embed=embed, view=self)


def build_bindershop_embed(interaction, items, current_index):
    current_item = items[current_index]
    purchase_id, name, drop_emoji, item_type, image_url, paint_price, glue_price = current_item

    # Format prices
    prices = []
    if paint_price > 0:
        prices.append(f"{PAINT_EMOJI} **{paint_price}** Paint")
    if glue_price > 0:
        prices.append(f"{GLUE_EMOJI} **{glue_price}** Glue")
    price_display = " | ".join(prices) if prices else "Free"

    embed = discord.Embed(
        description=f"> # <a:chalkflower:1515178945669107862> {name}\n"
                    f"**Purchase ID** - `{purchase_id}`\n"
                    f"**Price** - {price_display}",
        color=RYO_COLOR
    )
    embed.set_author(name="binder shop 📚", icon_url=interaction.user.display_avatar.url)
    
    if image_url:
        embed.set_image(url=image_url)

    embed.set_footer(text=f"page {current_index + 1}/{len(items)} | total items : {len(items)} | press 'buy' below to purchase this binder")
    return embed


@bot.tree.command(name="addbinder", description="Release Staff only: Add a new binder to the shop")
@app_commands.describe(
    purchase_id="Unique Purchase ID for this binder item",
    name="Name of the binder",
    drop_emoji="Drop emoji displayed next to the name",
    type="Binder type (Colors or Theme)",
    image_url="Binder image URL",
    paint_price="Price in Paint",
    glue_price="Price in Glue"
)
@app_commands.autocomplete(type=type_autocomplete)
async def addbinder_cmd(
    interaction: discord.Interaction,
    purchase_id: str,
    name: str,
    drop_emoji: str,
    type: str,
    image_url: str,
    paint_price: int = 0,
    glue_price: int = 0
):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ This command is restricted to Release Staff.", ephemeral=True)
        return

    await interaction.response.defer()
    
    b_type = type.strip()
    if b_type not in ["Colors", "Theme"]:
        await interaction.followup.send("❌ Invalid binder type. It must be either **Colors** or **Theme**.")
        return

    # Insert/update in DB
    await run_query("""
        INSERT INTO ryo_binder_shop_items (purchase_id, name, drop_emoji, item_type, image_url, paint_price, glue_price)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (purchase_id)
        DO UPDATE SET name = EXCLUDED.name, drop_emoji = EXCLUDED.drop_emoji, item_type = EXCLUDED.item_type,
                      image_url = EXCLUDED.image_url, paint_price = EXCLUDED.paint_price, glue_price = EXCLUDED.glue_price
    """, (purchase_id.strip(), name.strip(), drop_emoji.strip(), b_type, image_url.strip(), paint_price, glue_price))

    await interaction.followup.send(f"✅ Successfully added/updated **{name}** (`{purchase_id}`) under **{b_type}** to the binder shop!")


@bot.tree.command(name="bindershop", description="Open the Binder Shop to customize your binders!")
async def bindershop_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

    items = await run_query("SELECT purchase_id, name, drop_emoji, item_type, image_url, paint_price, glue_price FROM ryo_binder_shop_items ORDER BY name ASC", fetchall=True) or []
    if not items:
        await interaction.followup.send("🛒 No items available in the binder shop right now.")
        return

    embed = build_bindershop_embed(interaction, items, 0)
    view = BinderShopView(interaction.user.id, items, 0)
    await interaction.followup.send(embed=embed, view=view)


# --- Bot Run Entry Point ---
if __name__ == "__main__":
    if not RYOTOKEN:
        print("❌ Error: RYO_TOKEN not defined in environments.")
    else:
        bot.run(RYOTOKEN)
