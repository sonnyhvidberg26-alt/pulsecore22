# main.py
import os
import json
import asyncio
import io
import re
from datetime import datetime, timedelta
import requests
from typing import Optional, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from flask import Flask
from threading import Thread

# ---------------- CONFIG ----------------
def get_token():
    # First try environment variable
    token = os.environ.get("DISCORD_TOKEN")
    if token:
        return token
    # Fall back to Token.json file
    try:
        with open("Token.json", "r") as f:
            data = json.load(f)
            return data.get("token", "")
    except:
        return ""

TOKEN = get_token()
MAIN_GUILD = 1449127409877254296
SUPPORT_GUILD = 1449724820471283855

# Channels
GEN_CH = 1449193878749052988
MANIFEST_CH = 1449158130184229111
REQUEST_CH = 1449732612192206918
UPDATE_CH = 1450124908452647043
LEADERBOARD_CH = 1450122581855174737
ADD_CH = 1449724821402288151
NEWEST_ADDED_CH = 1450237703915442347
TOPREQUESTS_CH = 1450242627797647501
REQUESTED_LEADERBOARD_CH = 1450243490457059368
PREMIUM_COMMAND_CHANNEL = 1459637667028533318
INVITE_CHANNEL = 1459998901795819753

# Files
DB_FILE = "Ourgames.json"
ADDER_FILE = "adder_stats.json"
REQ_COUNTS_FILE = "request_counts.json"
PEOPLE_WHO_GEN_FILE = "Peoplewhogen.json"
PREMIUM_USERS_FILE = "Premium_users.json"
INVITES_FILE = "invites.json"

# API Configuration
DEATHSTRUCK_API_KEY = os.environ.get("DEATHSTRUCK_API_KEY", "")
DEATHSTRUCK_API_BASE = "https://deathstruckapi.lol"

# Roles
FREEMIUM_ROLE = 1449173422973255771
PREMIUM_ROLE = 1449396016251146424

STEAM_CACHE_TTL = 300  # seconds
INVITE_MIN_AGE_DAYS = 20

# ---------------- GLOBALS & LOCK ----------------
file_lock = asyncio.Lock()
steam_cache: Dict[str, tuple[dict, datetime]] = {}
guild_invite_cache: Dict[int, Dict[str, int]] = {}
invites_state: Dict[str, Any] = {}
premium_users: Dict[str, Any] = {}
# operational in-memory state
games: Dict[str, str] = {}
adder_stats: Dict[str, Any] = {}
request_counts: Dict[str, int] = {}
requests_log: Dict[str, tuple] = {}
people_whogen_state: Dict[str, Any] = {}
# freemium per-day counters (kept as attribute on bot for persistence across cogs)
# will set on bot later

# ---------------- HELPERS: JSON load/save (single copy) ----------------
def load_json(path: str, default=None):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to load {path}: {e}")
    return default if default is not None else {}

async def save_json_atomic(path: str, data: dict):
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[ERROR] Failed to save {path}: {e}")

# ---------------- INIT LOAD ----------------
def _initial_load():
    global games, adder_stats, request_counts, people_whogen_state, invites_state, premium_users
    games = load_json(DB_FILE, {}) or {}
    adder_stats = load_json(ADDER_FILE, {}) or {}
    request_counts = load_json(REQ_COUNTS_FILE, {}) or {}
    people_whogen_state = load_json(PEOPLE_WHO_GEN_FILE, {"counts": {}, "last_msg_id": None, "initial_post_done": False}) or {"counts": {}, "last_msg_id": None, "initial_post_done": False}
    invites_state = load_json(INVITES_FILE, {"users": {}, "total": {}}) or {"users": {}, "total": {}}
    premium_users = load_json(PREMIUM_USERS_FILE, {}) or {}
    # ensure dict shapes
    invites_state.setdefault("users", {})
    invites_state.setdefault("total", {})

_initial_load()

# ---------------- STEAM API WITH CACHE ----------------
def steam_cache_get(appid: str) -> Optional[dict]:
    entry = steam_cache.get(appid)
    if not entry:
        return None
    data, ts = entry
    if (datetime.utcnow() - ts).total_seconds() > STEAM_CACHE_TTL:
        steam_cache.pop(appid, None)
        return None
    return data

def steam_cache_set(appid: str, data: dict):
    steam_cache[appid] = (data, datetime.utcnow())

def get_steam(appid: str) -> Optional[dict]:
    """Fetch steam app details with in-memory cache."""
    cached = steam_cache_get(appid)
    if cached:
        return cached
    try:
        url = "https://store.steampowered.com/api/appdetails"
        params = {"appids": appid, "cc": "us", "l": "english"}
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict) or appid not in data or not data[appid].get("success"):
            return None
        payload = data[appid]["data"]
        steam_cache_set(appid, payload)
        return payload
    except Exception as e:
        print(f"[STEAM FETCH ERROR] {appid}: {e}")
        return None

# ---------------- MISC HELPERS ----------------
def is_valid_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    return url.startswith("http://") or url.startswith("https://") or "drive.google.com" in url

async def download_and_send_file(interaction: discord.Interaction, steamid: str, steam_data: dict):
    """Download file from deathstruck API and send directly to user"""
    import aiohttp
    import io
    
    try:
        url = f"{DEATHSTRUCK_API_BASE}/lua/{steamid}"
        params = {"key": DEATHSTRUCK_API_KEY}
        
        # Download file asynchronously
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=30) as response:
                if response.status != 200:
                    await interaction.followup.send("❌ Failed to download file.", ephemeral=True)
                    return
                
                # Get filename from Content-Disposition or use Steam name
                filename = None
                if 'Content-Disposition' in response.headers:
                    import re
                    cd = response.headers['Content-Disposition']
                    match = re.search(r'filename="?([^"]+)"?', cd)
                    if match:
                        filename = match.group(1)
                
                if not filename:
                    # Fallback to Steam game name
                    safe_name = "".join(c for c in steam_data.get('name', f'game_{steamid}') 
                                     if c.isalnum() or c in (' ', '-', '_')).rstrip()
                    filename = f"{safe_name}.lua"
                
                # Read file content
                file_data = await response.read()
                
                # Send file to user's DMs
                try:
                    await interaction.user.send(
                        f"🎮 Here's your requested file: **{steam_data.get('name', 'Game')}**",
                        file=discord.File(io.BytesIO(file_data), filename=filename)
                    )
                    await interaction.followup.send(
                        f"✅ File sent to your DMs! Check your messages.", 
                        ephemeral=True
                    )
                except discord.Forbidden:
                    await interaction.followup.send(
                        "❌ I can't send DMs to you. Please enable DMs from server members.", 
                        ephemeral=True
                    )
                except Exception as e:
                    await interaction.followup.send(
                        f"❌ Failed to send file: {e}", 
                        ephemeral=True
                    )
                    
    except asyncio.TimeoutError:
        await interaction.followup.send("❌ Download timed out. Please try again.", ephemeral=True)
    except Exception as e:
        print(f"[DOWNLOAD ERROR] {steamid}: {e}")
        await interaction.followup.send("❌ Failed to download file.", ephemeral=True)

def build_manifest_view(link: str, steamid: str) -> discord.ui.View:
    view = discord.ui.View()
    if is_valid_url(link):
        view.add_item(discord.ui.Button(label="Download", url=link, style=discord.ButtonStyle.link))
    view.add_item(discord.ui.Button(label="View on Steam", url=f"https://store.steampowered.com/app/{steamid}", style=discord.ButtonStyle.link))
    return view

# ---------------- BOT SETUP ----------------
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
# attach an in-memory counter dict to the bot
bot.user_counts: Dict[int, int] = {}

# ---------------- TASKS ----------------
@tasks.loop(hours=24)
async def midnight_reset():
    try:
        bot.user_counts.clear()
        print("[RESET] Freemium counters cleared.")
    except Exception as e:
        print("[WARN] midnight_reset failed:", e)

@midnight_reset.before_loop
async def midnight_reset_before():
    await bot.wait_until_ready()
    # compute next midnight UTC
    now = datetime.utcnow()
    next_reset = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
    await discord.utils.sleep_until(next_reset)

# ---------------- PEOPLE WHO /gen LEADERBOARD (daily task) ----------------
async def post_requested_leaderboard_and_save():
    async with file_lock:
        counts = people_whogen_state.get("counts", {})
        last_msg_id = people_whogen_state.get("last_msg_id", None)

    sorted_users = sorted(counts.items(), key=lambda x: x[1].get("count", 0), reverse=True)[:10]

    embed = discord.Embed(
        title="📊 Daily Requested Leaderboard",
        description="Top users who used `/gen` (requested) — top 10",
        color=0x8A2BE2,
        timestamp=datetime.utcnow()
    )

    if not sorted_users:
        embed.add_field(name="No activity", value="No `/gen` requests recorded yet.", inline=False)
    else:
        for i, (uid, data) in enumerate(sorted_users, start=1):
            username = data.get("username", f"User {uid}")
            cnt = data.get("count", 0)
            embed.add_field(name=f"#{i} {username}", value=f"Requested: **{cnt}** times", inline=False)

    ch = bot.get_channel(REQUESTED_LEADERBOARD_CH)
    if not ch:
        print(f"[WARN] REQUESTED_LEADERBOARD_CH {REQUESTED_LEADERBOARD_CH} not found.")
        return

    # delete previous message if present
    if last_msg_id:
        try:
            prev = await ch.fetch_message(last_msg_id)
            await prev.delete()
        except Exception:
            pass

    new_msg = await ch.send(embed=embed)
    async with file_lock:
        people_whogen_state["last_msg_id"] = new_msg.id
        await save_json_atomic(PEOPLE_WHO_GEN_FILE, people_whogen_state)

@tasks.loop(hours=24)
async def daily_requested_leaderboard_task():
    try:
        await post_requested_leaderboard_and_save()
        async with file_lock:
            people_whogen_state["counts"] = {}
            await save_json_atomic(PEOPLE_WHO_GEN_FILE, people_whogen_state)
    except Exception as e:
        print("[WARN] daily_requested_leaderboard_task failed:", e)

@daily_requested_leaderboard_task.before_loop
async def daily_requested_leaderboard_before():
    await bot.wait_until_ready()
    now = datetime.utcnow()
    next_midnight = datetime.combine(now.date(), datetime.min.time()) + timedelta(days=1)
    await discord.utils.sleep_until(next_midnight)

async def ensure_first_run_and_start_requested_task():
    async with file_lock:
        initial_done = people_whogen_state.get("initial_post_done", False)
    if not initial_done:
        try:
            await post_requested_leaderboard_and_save()
        except Exception as e:
            print(f"[WARN] initial requested leaderboard post failed: {e}")
        async with file_lock:
            people_whogen_state["initial_post_done"] = True
            await save_json_atomic(PEOPLE_WHO_GEN_FILE, people_whogen_state)
    if not daily_requested_leaderboard_task.is_running():
        daily_requested_leaderboard_task.start()

# ---------------- PREMIUM EXPIRATION LOOP ----------------
@tasks.loop(minutes=1)
async def premium_expiration_loop():
    now = datetime.utcnow()
    to_save = False
    async with file_lock:
        # reload disk to be safe
        try:
            disk = load_json(PREMIUM_USERS_FILE, {})
            if isinstance(disk, dict):
                premium_users.update(disk)
        except Exception:
            pass

        expired_keys = []
        for uid_str, info in list(premium_users.items()):
            exp = info.get("expires_at")
            if exp == "lifetime" or exp is None:
                continue
            try:
                exp_dt = datetime.fromisoformat(exp)
            except Exception:
                expired_keys.append(uid_str)
                continue
            if now >= exp_dt:
                expired_keys.append(uid_str)

        if not expired_keys:
            return

        guild = bot.get_guild(MAIN_GUILD)
        if guild is None:
            return

        role = guild.get_role(PREMIUM_ROLE)
        for uid_str in expired_keys:
            try:
                member = guild.get_member(int(uid_str))
            except Exception:
                member = None

            if member and role:
                try:
                    await member.remove_roles(role, reason="Premium expired")
                except Exception:
                    print(f"[WARN] Failed to remove premium role from {uid_str}")
            premium_users.pop(uid_str, None)
            to_save = True

        if to_save:
            await save_json_atomic(PREMIUM_USERS_FILE, premium_users)

# ---------------- UTIL: increment / record who used /gen ----------------
async def increment_requested_count_for_user(user: discord.User):
    uid = str(user.id)
    async with file_lock:
        counts = people_whogen_state.setdefault("counts", {})
        entry = counts.get(uid)
        if entry:
            entry["count"] = entry.get("count", 0) + 1
            entry["username"] = user.name
        else:
            counts[uid] = {"count": 1, "username": user.name}
        await save_json_atomic(PEOPLE_WHO_GEN_FILE, people_whogen_state)

# ---------------- LIMITED ROLE CHECK ----------------
def limit_ok(user: discord.User, role_ids: list) -> bool:
    is_premium = PREMIUM_ROLE in role_ids
    is_freemium = FREEMIUM_ROLE in role_ids
    if is_premium:
        return True
    if is_freemium:
        uid = user.id
        count = bot.user_counts.get(uid, 0)
        if count >= 8:
            return False
        bot.user_counts[uid] = count + 1
        return True
    # neither role → deny
    return False

# ---------------- INVITE TRACKING ----------------
async def save_invites_atomic():
    async with file_lock:
        await save_json_atomic(INVITES_FILE, invites_state)

def is_account_old_enough(member: discord.Member) -> bool:
    delta = datetime.utcnow() - member.created_at
    return delta.days >= INVITE_MIN_AGE_DAYS

# ---------------- ON READY (single, unified) ----------------
@bot.event
async def on_ready():
    print(f"[INFO] Logged in as {bot.user} (ID: {getattr(bot.user,'id', '?')})")

    # initialize invite cache per guild (safe)
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            guild_invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
        except Exception as e:
            print(f"[WARN] Failed to fetch invites for {guild.name}: {e}")

    # Try to import AntiAbuse if available
    try:
        import AntiAbuse  # type: ignore
        try:
            await AntiAbuse.setup(bot)
            print("[INFO] AntiAbuse module initialized")
        except Exception as e:
            print(f"[WARN] AntiAbuse.setup failed: {e}")
    except Exception:
        # If AntiAbuse not present, keep going silently
        print("[INFO] AntiAbuse not available (skipping)")

    # Sync slash commands to guilds (best-effort)
    try:
        await bot.tree.sync(guild=discord.Object(id=SUPPORT_GUILD))
        await bot.tree.sync(guild=discord.Object(id=MAIN_GUILD))
        print("[INFO] Slash commands synced successfully")
    except Exception as e:
        print(f"[WARN] Command sync failed: {e}")

    # Start background tasks (only once)
    try:
        if not midnight_reset.is_running():
            midnight_reset.start()
        if not daily_requested_leaderboard_task.is_running():
            await ensure_first_run_and_start_requested_task()
        if not premium_expiration_loop.is_running():
            premium_expiration_loop.start()
        print("[INFO] Background tasks started")
    except Exception as e:
        print(f"[WARN] Failed to start background tasks: {e}")

    # Ensure instructions pinned in GEN channel
    try:
        await send_instructions_if_missing()
    except Exception as e:
        print(f"[WARN] Failed to send instructions: {e}")

# ---------------- SEND INSTRUCTIONS (pinned embed) ----------------
async def send_instructions_if_missing():
    ch = bot.get_channel(GEN_CH)
    if not ch:
        return
    try:
        pins = await ch.pins()
        if any(m.author == bot.user for m in pins):
            return
    except Exception:
        pins = []
    embed = discord.Embed(
        title="📌 How to Request / Generate a Game",
        description=(
            "Working commands:\n"
            "`/gen <steam_id>` – Instant download if we have it\n"
            "`/request <steam_id>` – Ask us to add a missing game\n"
            "`/update <steam_id>` – Request an update for an existing game (support will provide the link)\n\n"
            "Bot will reply in your DMs 📩"
        ),
        color=0x8A2BE2
    )
    embed.add_field(name="🎩 Premium", value="Unlimited requests/day\nFaster support", inline=True)
    embed.add_field(name="🧑‍💻 Freemium", value="8 requests/day\nAverage support", inline=True)
    embed.set_footer(text="Make sure to follow the rules! 💜")
    try:
        msg = await ch.send(embed=embed)
        await msg.pin()
    except Exception:
        pass

# ---------------- MEMBER JOIN (invite detection) ----------------
@bot.event
async def on_member_join(member: discord.Member):
    # ignore bots
    if member.bot:
        return

    # ignore too-young accounts
    if not is_account_old_enough(member):
        return

    guild = member.guild
    inviter = None
    try:
        invites = await guild.invites()
    except Exception as e:
        print(f"[WARN] Failed to fetch invites for {guild.name}: {e}")
        return

    old_invites = guild_invite_cache.get(guild.id, {})
    for inv in invites:
        old_uses = old_invites.get(inv.code, 0)
        if inv.uses > old_uses:
            inviter = inv.inviter
            break

    # update cache
    guild_invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}

    if not inviter:
        return

    uid = str(inviter.id)
    invited_id = str(member.id)

    async def _update_state():
        async with file_lock:
            user_invites = invites_state["users"].setdefault(uid, {"name": inviter.name, "invited": []})
            if invited_id not in user_invites["invited"]:
                user_invites["invited"].append(invited_id)
            invites_state["total"][uid] = len(user_invites["invited"])
            await save_json_atomic(INVITES_FILE, invites_state)
    try:
        await _update_state()
    except Exception as e:
        print("[WARN] Failed to update invites state:", e)

    ch = guild.get_channel(INVITE_CHANNEL)
    if ch:
        try:
            await ch.send(
                f"🎉 **{inviter.name}** has invited **{member.name}**! "
                f"Total invites: **{invites_state['total'].get(uid, 0)}**"
            )
        except Exception:
            pass

# ---------------- DM MESSAGE HANDLER ----------------
@bot.event
async def on_message(message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return
    
    # Check if DM and content is "hey"
    if isinstance(message.channel, discord.DMChannel) and message.content.lower() == "hey":
        await message.channel.send("nahh")
    
    # Process commands (important!)
    await bot.process_commands(message)

# ---------------- COMMANDS ----------------

# /gen
@bot.tree.command(name="gen", description="Generate a game manifest", guild=discord.Object(id=MAIN_GUILD))
@app_commands.describe(steamid="Steam App ID")
async def gen(interaction: discord.Interaction, steamid: str):
    if interaction.channel_id != GEN_CH:
        return await interaction.response.send_message("❌ Use this command in the gen channel.", ephemeral=True)

    # role ids
    if isinstance(interaction.user, discord.Member):
        role_ids = [r.id for r in interaction.user.roles]
    else:
        role_ids = []

    if not limit_ok(interaction.user, role_ids):
        return await interaction.response.send_message("❌ You reached your daily limit.", ephemeral=True)

    steam = steam_cache_get(steamid) or get_steam(steamid)
    if not steam:
        return await interaction.response.send_message("❌ Steam ID not found.", ephemeral=True)

    # Try to get manifest from deathstruck API
    # Instead of getting link, we'll download and send directly
    await interaction.response.defer(ephemeral=True)  # Defer since we're downloading
    
    # Download and send file directly to user
    await download_and_send_file(interaction, steamid, steam)
    
    # Still post publicly that a manifest was generated (but without the file)
    embed = discord.Embed(
        title=steam.get("name", steamid),
        description=steam.get("short_description", ""),
        color=0x57F287,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="🎮 Genres", value=", ".join(g.get("description") for g in steam.get("genres", [])) or "N/A", inline=True)
    embed.add_field(name="💰 Price", value=steam.get("price_overview", {}).get("final_formatted", "Free") if steam.get("price_overview") else "Free", inline=True)
    embed.add_field(name="🆔 Steam ID", value=steamid, inline=True)
    embed.add_field(name="👤 Generated by", value=interaction.user.mention, inline=True)
    if steam.get("header_image"):
        embed.set_image(url=steam["header_image"])
    embed.set_footer(text=datetime.utcnow().strftime("Generated at %I:%M %p UTC"))

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="View on Steam", url=f"https://store.steampowered.com/app/{steamid}", style=discord.ButtonStyle.link))

    # post publicly (without file)
    try:
        await bot.get_channel(MANIFEST_CH).send(content=f"🎟️ **Manifest generated by {interaction.user.mention}**", embed=embed, view=view)
    except Exception as e:
        print(f"[WARN] Failed to post manifest publicly: {e}")

    await increment_requested_count_for_user(interaction.user)


# /request
@bot.tree.command(name="request", description="Request a missing game", guild=discord.Object(id=MAIN_GUILD))
@app_commands.describe(steamid="Steam App ID")
async def request_cmd(interaction: discord.Interaction, steamid: str):
    if interaction.channel_id != GEN_CH:
        return await interaction.response.send_message("❌ Use this in the gen channel.", ephemeral=True)

    if isinstance(interaction.user, discord.Member):
        role_ids = [r.id for r in interaction.user.roles]
    else:
        role_ids = []

    if not limit_ok(interaction.user, role_ids):
        return await interaction.response.send_message("❌ You reached your daily request limit.", ephemeral=True)

    async with file_lock:
        if steamid in games:
            return await interaction.response.send_message("✅ This game is already in our database. Use `/gen` to download it.", ephemeral=True)

    steam = steam_cache_get(steamid) or get_steam(steamid)
    if not steam:
        return await interaction.response.send_message("❌ Steam ID not found.", ephemeral=True)

    async with file_lock:
        requests_log[steamid] = (interaction.user.id, interaction.user.name, steam)
        request_counts[steamid] = request_counts.get(steamid, 0) + 1
        await save_json_atomic(REQ_COUNTS_FILE, request_counts)

    embed = discord.Embed(
        title="🎮 New Game Request",
        description=f"**{steam.get('name','Unknown')}** (`{steamid}`) requested by {interaction.user.mention}",
        color=0xFEE75C,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Genres", value=", ".join(g.get("description") for g in steam.get("genres", [])) or "N/A", inline=True)
    embed.add_field(name="Price", value=steam.get("price_overview", {}).get("final_formatted", "Free") if steam.get("price_overview") else "Free", inline=True)
    if steam.get("header_image"):
        embed.set_thumbnail(url=steam["header_image"])
    embed.set_footer(text=datetime.utcnow().strftime("%I:%M %p UTC"))

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="View on Steam", url=f"https://store.steampowered.com/app/{steamid}", style=discord.ButtonStyle.link))

    try:
        req_ch = bot.get_channel(REQUEST_CH)
        role_mention = "<@&1451554378463842356>"
        await req_ch.send(content=role_mention, embed=embed, view=view, allowed_mentions=discord.AllowedMentions(roles=True))
    except Exception as e:
        print(f"[WARN] Failed to post request: {e}")

    try:
        await interaction.user.send(f"✅ Your request for **{steam.get('name','Unknown')}** has been sent to our game adders and will be added soon.", embed=embed, view=view)
    except discord.Forbidden:
        pass
    except Exception:
        pass

    await interaction.response.send_message(f"🎮 Your request for **{steam.get('name','Unknown')}** has been sent to our game adders and will be added soon.", ephemeral=True)


# /premium (guild-scoped)
DURATION_CHOICES = [
    app_commands.Choice(name="7 days", value="7 days"),
    app_commands.Choice(name="1 month", value="1 month"),
    app_commands.Choice(name="3 months", value="3 months"),
    app_commands.Choice(name="lifetime", value="lifetime"),
]

def _duration_to_timedelta(val: str) -> Optional[timedelta]:
    v = val.lower()
    if v == "7 days":
        return timedelta(days=7)
    if v == "1 month":
        return timedelta(days=30)
    if v == "3 months":
        return timedelta(days=90)
    if v == "lifetime":
        return None
    return None

@bot.tree.command(name="premium", description="Give a user the Premium role for a duration", guild=discord.Object(id=MAIN_GUILD))
@discord.app_commands.describe(user="Member to grant premium", duration="Duration choice")
@app_commands.choices(duration=DURATION_CHOICES)
async def premium(interaction: discord.Interaction, user: discord.Member, duration: app_commands.Choice[str]):
    if interaction.guild_id != MAIN_GUILD or interaction.channel_id != PREMIUM_COMMAND_CHANNEL:
        await interaction.response.send_message("This command can only be used in the designated premium channel.", ephemeral=True)
        return

    invoker = interaction.user
    if isinstance(invoker, discord.Member):
        if not (invoker.guild_permissions.manage_roles or invoker.guild.owner_id == invoker.id):
            await interaction.response.send_message("You need Manage Roles permission (or be server owner) to use this.", ephemeral=True)
            return

    guild = interaction.guild
    if guild is None or guild.id != MAIN_GUILD:
        await interaction.response.send_message("Command must be used in the configured guild.", ephemeral=True)
        return

    bot_member = guild.me
    if not bot_member.guild_permissions.manage_roles:
        await interaction.response.send_message("I don't have Manage Roles permission. Please grant it and retry.", ephemeral=True)
        return

    premium_role = guild.get_role(PREMIUM_ROLE)
    if premium_role is None:
        await interaction.response.send_message("Configured premium role not found in this server.", ephemeral=True)
        return

    if premium_role.position >= bot_member.top_role.position:
        await interaction.response.send_message("I cannot manage the premium role because it is higher or equal to my top role.", ephemeral=True)
        return

    dur_value = duration.value
    delta = _duration_to_timedelta(dur_value)
    if dur_value.lower() == "lifetime":
        expires_at = "lifetime"
    else:
        expires_at = (datetime.utcnow() + delta).isoformat()

    try:
        await user.add_roles(premium_role, reason=f"Premium granted by {invoker} for {dur_value}")
    except discord.Forbidden:
        await interaction.response.send_message("I cannot assign the role due to permissions / role hierarchy.", ephemeral=True)
        return
    except Exception as e:
        await interaction.response.send_message(f"Failed to assign role: {e}", ephemeral=True)
        return

    async with file_lock:
        try:
            disk = load_json(PREMIUM_USERS_FILE, {})
            if isinstance(disk, dict):
                premium_users.update(disk)
        except Exception:
            pass

        key = str(user.id)
        existing = premium_users.get(key)
        if existing:
            existing_exp = existing.get("expires_at")
            if existing_exp == "lifetime":
                premium_users[key].update({
                    "granted_by": invoker.id,
                    "granted_at": datetime.utcnow().isoformat(),
                })
            else:
                try:
                    existing_dt = datetime.fromisoformat(existing_exp)
                    if expires_at == "lifetime":
                        premium_users[key]["expires_at"] = "lifetime"
                    else:
                        new_dt = datetime.fromisoformat(expires_at)
                        if new_dt > existing_dt:
                            premium_users[key]["expires_at"] = new_dt.isoformat()
                        premium_users[key].update({
                            "granted_by": invoker.id,
                            "granted_at": datetime.utcnow().isoformat(),
                        })
                except Exception:
                    premium_users[key] = {
                        "expires_at": expires_at,
                        "granted_by": invoker.id,
                        "granted_at": datetime.utcnow().isoformat(),
                    }
        else:
            premium_users[key] = {
                "expires_at": expires_at,
                "granted_by": invoker.id,
                "granted_at": datetime.utcnow().isoformat(),
            }
        await save_json_atomic(PREMIUM_USERS_FILE, premium_users)

    if expires_at == "lifetime":
        await interaction.response.send_message(f"✅ {user.mention} granted **Lifetime** Premium by {invoker.mention}.", ephemeral=False)
    else:
        try:
            dt = datetime.fromisoformat(expires_at)
            await interaction.response.send_message(f"✅ {user.mention} granted **Premium** for **{dur_value}**. Expires on **{dt.strftime('%d-%m-%Y %H:%M UTC')}** (UTC).", ephemeral=False)
        except Exception:
            await interaction.response.send_message(f"✅ {user.mention} granted **Premium** for **{dur_value}**.", ephemeral=False)


# /invites
@bot.tree.command(name="invites", description="Check your total invites", guild=discord.Object(id=MAIN_GUILD))
async def invites_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    async with file_lock:
        total = invites_state["total"].get(uid, 0)
        invited = invites_state["users"].get(uid, {}).get("invited", [])
    msg = f"You have invited **{total}** user{'s' if total != 1 else ''}."
    if invited:
        names = []
        for i in invited:
            member = interaction.guild.get_member(int(i)) if interaction.guild else None
            names.append(member.name if member else i)
        msg += f"\nInvited users: {', '.join(names)}"
    await interaction.response.send_message(msg, ephemeral=True)


# /update (user sends id only; forwarded to support update channel)
@bot.tree.command(name="update", description="Request an update for an existing game", guild=discord.Object(id=MAIN_GUILD))
@app_commands.describe(steamid="Steam App ID")
async def update_cmd(interaction: discord.Interaction, steamid: str):
    if interaction.channel_id != GEN_CH:
        return await interaction.response.send_message("❌ Use this in the gen channel.", ephemeral=True)

    async with file_lock:
        if steamid not in games:
            return await interaction.response.send_message("❌ Game not in database. Use `/request` to ask for it.", ephemeral=True)

    steam = steam_cache_get(steamid) or get_steam(steamid)
    if not steam:
        return await interaction.response.send_message("❌ Could not fetch Steam data.", ephemeral=True)

    embed = discord.Embed(
        title="New Update Request",
        description=f"**{steam.get('name','Unknown')}** (`{steamid}`)\nUpdate requested by {interaction.user.mention}",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Genres", value=", ".join(g.get("description") for g in steam.get("genres", [])) or "N/A", inline=True)
    embed.add_field(name="Price", value=steam.get("price_overview", {}).get("final_formatted", "Free") if steam.get("price_overview") else "Free", inline=True)
    if steam.get("header_image"):
        embed.set_thumbnail(url=steam["header_image"])
    embed.set_footer(text=datetime.utcnow().strftime("%I:%M %p UTC"))

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="View on Steam", url=f"https://store.steampowered.com/app/{steamid}", style=discord.ButtonStyle.link))

    try:
        await bot.get_channel(UPDATE_CH).send(embed=embed, view=view)
    except Exception as e:
        print(f"[WARN] Failed to forward update request: {e}")

    try:
        await interaction.user.send(embed=embed, view=view)
    except discord.Forbidden:
        pass
    except Exception:
        pass

    await interaction.response.send_message("🔄 Your update request has been sent to the support team.", ephemeral=True)


# /updates (support provides link)
@bot.tree.command(name="updates", description="Process an update request and provide new link", guild=discord.Object(id=SUPPORT_GUILD))
@app_commands.describe(steamid="Steam App ID", link="New download link")
async def updates_cmd(interaction: discord.Interaction, steamid: str, link: str):
    if interaction.channel_id != UPDATE_CH:
        return await interaction.response.send_message("❌ Use this in the update channel.", ephemeral=True)
    if not is_valid_url(link):
        return await interaction.response.send_message("❌ Invalid link format.", ephemeral=True)

    async with file_lock:
        is_update = steamid in games
        games[steamid] = link
        uid = str(interaction.user.id)
        if uid not in adder_stats:
            adder_stats[uid] = {"added": 0, "updated": 0, "username": interaction.user.name}
        if is_update:
            adder_stats[uid]["updated"] += 1
            action_text = "updated"
        else:
            adder_stats[uid]["added"] += 1
            action_text = "added"
        await save_json_atomic(DB_FILE, games)
        await save_json_atomic(ADDER_FILE, adder_stats)

        if steamid in requests_log:
            ruid, _, _ = requests_log.pop(steamid)
            user = bot.get_user(ruid)
            if user:
                try:
                    await user.send(f"❤️ Your requested game **{steamid}** has been {action_text} by {interaction.user}.")
                except Exception:
                    pass

    await interaction.response.send_message(f"✅ {steamid} {action_text} with new link. Your {action_text} count increased.", ephemeral=True)

    if not is_update:
        await post_new_game_announcement(steamid, link, interaction.user.name)


# /addgame (support)
@bot.tree.command(name="addgame", description="Add a new game (everyone can use)", guild=discord.Object(id=SUPPORT_GUILD))
@app_commands.describe(steamid="Steam App ID", link="Direct download link")
async def addgame(interaction: discord.Interaction, steamid: str, link: str):
    if interaction.channel_id != ADD_CH:
        return await interaction.response.send_message("❌ Use this in the add-games channel.", ephemeral=True)

    steam = steam_cache_get(steamid) or get_steam(steamid)
    name = steam.get("name") if steam else steamid
    uid = str(interaction.user.id)
    async with file_lock:
        existed = steamid in games
        if uid not in adder_stats:
            adder_stats[uid] = {"added": 0, "updated": 0, "username": interaction.user.name}
        if existed:
            adder_stats[uid]["updated"] += 1
            action_text = "updated"
        else:
            adder_stats[uid]["added"] += 1
            action_text = "added"
        games[steamid] = link
        await save_json_atomic(DB_FILE, games)
        await save_json_atomic(ADDER_FILE, adder_stats)
        if steamid in requests_log:
            ruid, _, _ = requests_log.pop(steamid)
            user = bot.get_user(ruid)
            if user:
                try:
                    await user.send(f"❤️ Your requested game **{name}** has been {action_text} by **{interaction.user}**.")
                except Exception:
                    pass
    await interaction.response.send_message(f"✅ {action_text.capitalize()} `{steamid}` → {link}")
    if not existed:
        await post_new_game_announcement(steamid, link, interaction.user.name)


# /who leaderboard
@bot.tree.command(name="who", description="Show leaderboard of game adders", guild=discord.Object(id=SUPPORT_GUILD))
async def who(interaction: discord.Interaction):
    if interaction.channel_id != LEADERBOARD_CH:
        return await interaction.response.send_message("❌ Use this in the leaderboard channel.", ephemeral=True)
    async with file_lock:
        sorted_adders = sorted(adder_stats.items(), key=lambda x: x[1].get("added",0)+x[1].get("updated",0), reverse=True)
    embed = discord.Embed(title="👑 Game Adder Leaderboard", description="Top contributors who add and update games", color=0xFFD700, timestamp=datetime.utcnow())
    for i, (uid, stats) in enumerate(sorted_adders[:8]):
        user_obj = bot.get_user(int(uid))
        username = user_obj.name if user_obj else stats.get("username", f"User {uid}")
        medal = "🥇" if i==0 else "🥈" if i==1 else "🥉" if i==2 else "🏅"
        embed.add_field(name=f"{medal} #{i+1} {username}", value=f"— **{stats.get('added',0)}** added / **{stats.get('updated',0)}** updated", inline=False)
    embed.set_footer(text=f"Updated at {datetime.utcnow().strftime('%H:%M UTC')}")
    await interaction.response.send_message(embed=embed)


# /games (count)
@bot.tree.command(name="games", description="Show total number of games in the database", guild=discord.Object(id=SUPPORT_GUILD))
async def games_cmd(interaction: discord.Interaction):
    if interaction.channel_id != LEADERBOARD_CH:
        return await interaction.response.send_message("❌ Use this in the leaderboard channel.", ephemeral=True)
    async with file_lock:
        total_games = len(games)
    embed = discord.Embed(title="🎮 Total Games in Database", description=f"Currently we have **{total_games}** games stored.", color=0x57F287, timestamp=datetime.utcnow())
    await interaction.response.send_message(embed=embed)


# /toprequests
@bot.tree.command(name="toprequests", description="Show the most requested games", guild=discord.Object(id=SUPPORT_GUILD))
async def toprequests(interaction: discord.Interaction):
    if interaction.channel_id != TOPREQUESTS_CH:
        return await interaction.response.send_message("❌ Use this command in the top-requests channel.", ephemeral=True)
    async with file_lock:
        if not request_counts:
            return await interaction.response.send_message("No requests recorded yet.", ephemeral=True)
        sorted_reqs = sorted(request_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    embed = discord.Embed(title="📈 Top Requested Games", description="Most requested games by users", color=0x8A2BE2, timestamp=datetime.utcnow())
    for i, (steamid, cnt) in enumerate(sorted_reqs, start=1):
        steam = steam_cache_get(steamid) or get_steam(steamid)
        name = steam.get("name") if steam else steamid
        embed.add_field(name=f"#{i} {name} (`{steamid}`)", value=f"Requested **{cnt}** times", inline=False)
    await interaction.response.send_message(embed=embed)


# ---------------- NEW GAME ANNOUNCEMENT ----------------
async def post_new_game_announcement(steamid: str, link: str, added_by_name: str):
    ch = bot.get_channel(NEWEST_ADDED_CH)
    if not ch:
        print(f"[WARN] NEWEST_ADDED_CH {NEWEST_ADDED_CH} not found.")
        return
    steam = steam_cache_get(steamid) or get_steam(steamid)
    if steam:
        title = steam.get("name", steamid)
        desc = steam.get("short_description", "")
        price = steam.get("price_overview", {}).get("final_formatted") if steam.get("price_overview") else "Free"
        genres = ", ".join(g.get("description") for g in steam.get("genres", [])) or "N/A"
        embed = discord.Embed(
            title="🎮 New game just added",
            description=f"**{title}** (`{steamid}`)\n\n{desc}",
            color=0x57F287,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Genres", value=genres, inline=True)
        embed.add_field(name="Price", value=price, inline=True)
        embed.add_field(name="Added by", value=added_by_name, inline=True)
        if steam.get("header_image"):
            embed.set_image(url=steam["header_image"])
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="View on Steam", url=f"https://store.steampowered.com/app/{steamid}", style=discord.ButtonStyle.link))
        await ch.send(embed=embed, view=view)
    else:
        embed = discord.Embed(
            title="🎮 New game just added",
            description=f"`{steamid}`",
            color=0x57F287,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Added by", value=added_by_name, inline=True)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="View on Steam", url=f"https://store.steampowered.com/app/{steamid}", style=discord.ButtonStyle.link))
        await ch.send(embed=embed, view=view)

# ---------------- KEEP ALIVE WEB SERVER ----------------
app = Flask("")

@app.route("/")
def home():
    return "I am alive!", 200

@app.route("/ping")
def ping():
    return "Pong!", 200

def run_flask():
    # Flask server will run in a daemon thread
    app.run(host="0.0.0.0", port=5000)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ---------------- START (main) ----------------
if __name__ == "__main__":
    # Basic sanity checks
    if not TOKEN:
        print("[ERROR] TOKEN is not set. Exiting.")
        raise SystemExit(1)

    # start simple keep-alive webserver (for uptime monitors / Replit)
    keep_alive()

    try:
        bot.run(TOKEN)
    except Exception as e:
        print("[ERROR] Bot failed to start:", e)
