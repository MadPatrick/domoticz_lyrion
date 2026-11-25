"""
<plugin key="LyrionMusicServer" name="Lyrion Music Server" author="MadPatrick" version="2.3.0" wikilink="https://lyrion.org" externallink="https://github.com/MadPatrick/domoticz_lyrion">
    <description>
        <h2>Lyrion Music Server</h2>
        <p><strong>Version:</strong> 2.3.0</p>
        <p>Automatically detects Lyrion Music Server players and creates a complete set of Domoticz controls for each player.</p>
        <h3>Features</h3>
        <ul>
            <li>Power, play, pause, stop and volume controls.</li>
            <li>Current station, artist and track information.</li>
            <li>Per-player playlists and global favorites selectors.</li>
            <li>Sync, unsync, shuffle and repeat controls.</li>
            <li>Sends configurable text to compatible player displays.</li>
        </ul>
        <h3>Configuration</h3>
        <p>Enter the server connection details. Username and password are only required when authentication is enabled.</p>
    </description>
    <params>
        <param field="Address" label="Server IP" width="200px" required="true" default="127.0.0.1">
            <description>
                <h4 style="margin:4px 0 6px 0;">Connection</h4>
                <br/>Plain IP/hostname connects over plain HTTP using the Port field below.
                <br/>To connect over HTTPS (e.g. through a reverse proxy in front of LMS) enter a full URL instead, for example <i>https://lms.example.com:9443</i> - the Port field is then ignored.
            </description>
        </param>
        <param field="Port" label="Port" width="100px" required="true" default="9000"/>
        <param field="Username" label="Username" width="150px">
            <description>
                <br/><span style="color: yellow;">Login settings. Only needed when applicable</span>
            </description>
        </param>
        <param field="Password" label="Password" width="150px" password="true">
        </param>
        <param field="ActivePollingInterval" label="Active polling interval" width="100px" default="10">
            <description>
                <h4 style="margin:14px 0 6px 0; border-top:1px solid #ccc; padding-top:8px;">Polling</h4>
            </description>
            <options>
                <option label="10 sec" value="10" default="true"/>
                <option label="20 sec" value="20"/>
                <option label="30 sec" value="30"/>
                <option label="60 sec" value="60"/>
                <option label="120 sec" value="120"/>
                <option label="600 sec" value="600"/>
            </options>
        </param>
        <param field="InactivePollingInterval" label="Inactive polling interval" width="100px" default="600">
            <options>
            <option label=" 1 min" value="60"/>
            <option label=" 5 min" value="300"/>
            <option label="10 min" value="600" default="true"/>
            <option label="30 min" value="1800"/>
            <option label="60 min" value="3600"/>
        </options>
        </param>
        <param field="ListRefreshInterval" label="List refresh interval" width="100px" default="10">
            <options>
            <option label="1 min" value="1"/>
            <option label="5 min" value="5"/>
            <option label="10 min" value="10" default="true"/>
            <option label="30 min" value="30"/>
            <option label="60 min" value="60"/>
            <option label="240 min" value="240"/>
        </options>
        </param>
        <param field="MaxPlaylists" label="Max playlists to load" width="100px" default="5"/>
        <param field="MessageText" label="Message text" width="300px" default="Hello from Domoticz!">
            <description>
                <h4 style="margin:14px 0 6px 0; border-top:1px solid #ccc; padding-top:8px;">Display</h4>
            </description>
        </param>
        <param field="EnableDebug" type="boolean" label="Debug" default="false">
            <description>
                <h4 style="margin:14px 0 6px 0; border-top:1px solid #ccc; padding-top:8px;">Logging</h4>
            </description>
        </param>
    </params>
</plugin>
"""
# Lyrion Music Server Domoticz Plugin

import Domoticz
import requests
import time
import re
import html
import threading
import queue

# Defaults - keep these in sync with the <params> defaults above.
DEFAULT_ACTIVE_POLL = 10         # seconds
DEFAULT_INACTIVE_POLL = 600      # seconds
DEFAULT_LIST_REFRESH_MIN = 10    # minutes
DEFAULT_MAX_PLAYLISTS = 5

# (connect, read) timeout: an unreachable server fails fast, a slow one still gets time to answer
REQUEST_TIMEOUT = (3, 10)
# onStop waits this long for an in-flight poll (just above the read timeout)
WORKER_JOIN_TIMEOUT = 12

DISPLAY_SUBJECT = "Lyrion"       # line1 of the display text
DISPLAY_DURATION = 60            # seconds


class LMSPlugin:
    def __init__(self):
        self.url = ""
        self.auth = None

        # Sessions reuse TCP connections. requests.Session is not thread-safe,
        # so the polling worker gets its own session; commands from onCommand
        # (main thread) use self.http.
        self.http = requests.Session()
        self.http_worker = requests.Session()

        self.pollInterval = DEFAULT_ACTIVE_POLL
        self.offlinePollInterval = DEFAULT_INACTIVE_POLL
        self.nextPoll = 0

        self.players = []
        # mac -> player name from the last serverstatus (for log messages)
        self.player_names = {}
        self.max_playlists = DEFAULT_MAX_PLAYLISTS

        self.imageID = 0
        self.debug = False

        # Display text settings
        self.displayText = ""           # MessageText: line2

        # Logging / init
        self.initialized = False

        # Track device text limit (characters per field: station, artist, title)
        self.track_max_chars = 25

        # Track-change detection
        self.lastTrackIndex = {}

        # Server status tracking
        self.server_was_online = None
        self.last_success = 0
        self.offline_grace = 15
        self.last_update_version = ""

        # cache per speler
        self.playlist_cache = {}

        # Favorites are global on LMS, so cache globally (not per player)
        self.favorites_cache = {"ts": 0, "data": []}

        self.listPollInterval = DEFAULT_LIST_REFRESH_MIN * 60

        # Flag of er een actieve speler is (play/pause)
        self.any_active = False

        # Unit -> device "kind" mapping (e.g. "volume", "shuffle", ...),
        # rebuilt every poll from find_player_devices(). Used by onCommand.
        self.unit_kind = {}

        # updateEverything's fetch cycle (serverstatus + per-player status/lists)
        # runs on a background thread so a slow/unreachable server never blocks
        # Domoticz's single callback thread. The worker only calls the LMS query
        # helpers (which never touch Devices); all Devices[...] updates happen
        # in _processEverything(), on the main thread, from onHeartbeat.
        self._fetch_lock = threading.Lock()
        self._fetch_in_progress = False
        self._result_queue = queue.Queue()
        self._worker = None
        self._stopping = threading.Event()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _read_migrated_parameter(self, field, legacy_field, default=""):
        """Read a named setting, falling back to its former ModeX field.

        Empty defaults on the new settings make existing Domoticz hardware
        configurations continue to work until they are saved with the new
        field names.
        """
        raw = Parameters.get(field, "")
        if raw is None or str(raw).strip() == "":
            raw = Parameters.get(legacy_field, "")
        if raw is None or str(raw).strip() == "":
            return default
        return raw

    def _read_migrated_boolean_parameter(self, field, legacy_field, default=False, extra_truthy=()):
        raw = self._read_migrated_parameter(field, legacy_field, "true" if default else "false")
        truthy = {"true", "1", "yes", "on"} | {v.lower() for v in extra_truthy}
        return str(raw).strip().lower() in truthy

    def log(self, msg):
        Domoticz.Log(msg)

    def debug_log(self, msg):
        if self.debug:
            Domoticz.Debug(str(msg))

    def error(self, msg):
        Domoticz.Error(msg)

    def player_name(self, mac):
        return self.player_names.get(mac, mac or "Unknown")

    def log_player(self, dev, action):
        name = self.player_name(dev.Description) if dev else "Unknown"
        self.log(f"{name} | {action}")

    @staticmethod
    def level_label(name):
        """Selector level names are '|'-separated; a '|' inside a name would
        shift every following level."""
        return str(name).replace("|", "/")

    @staticmethod
    def is_main_device_name(name: str) -> bool:
        return not any(x in name for x in ("Volume", "Track", "Actions", "Shuffle", "Repeat", "Playlists", "Favorites"))

    def kind_from_name_fallback(self, name: str):
        """Best-effort device 'kind' guess from Device.Name. Only used as a
        fallback when self.unit_kind has no entry for the Unit yet (e.g. a
        command arrives before the first poll has populated the map)."""
        if "Favorites" in name:
            return "favorites"
        if "Playlists" in name:
            return "playlists"
        if "Actions" in name:
            return "actions"
        if "Shuffle" in name:
            return "shuffle"
        if "Repeat" in name:
            return "repeat"
        if "Volume" in name:
            return "volume"
        if self.is_main_device_name(name):
            return "main"
        return None

    def _load_device_icon(self):
        # The zip file on disk keeps its historical short name, but the
        # icon's Base (in icons.txt, used as the Images dict key) must start
        # with this plugin's key ("LyrionMusicServer") - Domoticz only loads
        # a plugin's pre-existing custom icons into Images at startup when
        # Base LIKE '<PluginKey>%'. The short "LMS" Base used before didn't
        # satisfy that, so Images never contained it on restart and it was
        # silently recreated (and re-logged as "created") every single time
        # instead of found.
        _ZIP_FILE = "LMS"
        _IMAGE = "LyrionMusicServer"
        existing_image = next(
            (image for name, image in Images.items()
             if str(name).casefold() == _IMAGE.casefold()),
            None,
        )
        if existing_image is not None:
            self.imageID = existing_image.ID
            self.log(f"Icons found in database (ImageID={self.imageID}).")
            return

        try:
            Domoticz.Image(f"{_ZIP_FILE}.zip").Create()
        except Exception as e:
            self.error(f"Unable to load icon pack '{_ZIP_FILE}.zip': {e}")
            return
        created_image = next(
            (image for name, image in Images.items()
             if str(name).casefold() == _IMAGE.casefold()),
            None,
        )
        if created_image is not None:
            self.imageID = created_image.ID
            self.log("Icons created and loaded.")
        else:
            self.error(f"Unable to load icon pack '{_ZIP_FILE}.zip'")

    def _apply_device_icon(self):
        if not self.imageID:
            return
        for device in Devices.values():
            if device.Image != self.imageID:
                device.Update(nValue=device.nValue, sValue=device.sValue, Image=self.imageID)

    # ------------------------------------------------------------------
    # Domoticz lifecycle
    # ------------------------------------------------------------------
    def onStart(self):
        self.log(f"Starting Plugin version {Parameters['Version']}")

        self._load_device_icon()
        self._apply_device_icon()

        # Poll interval (ActivePollingInterval)
        try:
            self.pollInterval = int(self._read_migrated_parameter("ActivePollingInterval", "Mode1", str(DEFAULT_ACTIVE_POLL)))
        except (TypeError, ValueError):
            self.log(f"ActivePollingInterval invalid, fallback to {DEFAULT_ACTIVE_POLL}s")
            self.pollInterval = DEFAULT_ACTIVE_POLL

        # Offline interval (InactivePollingInterval)
        inactive_raw = str(self._read_migrated_parameter("InactivePollingInterval", "Mode5", ""))
        if not inactive_raw:
            self.log(f"InactivePollingInterval is empty, fallback to {DEFAULT_INACTIVE_POLL}s")
            self.offlinePollInterval = DEFAULT_INACTIVE_POLL
        else:
            try:
                self.offlinePollInterval = int(inactive_raw)
            except (TypeError, ValueError):
                self.log(f"InactivePollingInterval invalid, fallback to {DEFAULT_INACTIVE_POLL}s")
                self.offlinePollInterval = DEFAULT_INACTIVE_POLL

        # List poll interval (ListRefreshInterval) waarde is in minuten, intern omzetten naar seconden
        try:
            self.listPollInterval = int(self._read_migrated_parameter("ListRefreshInterval", "Mode6", str(DEFAULT_LIST_REFRESH_MIN))) * 60
        except (TypeError, ValueError):
            self.listPollInterval = DEFAULT_LIST_REFRESH_MIN * 60
            self.log(f"ListRefreshInterval invalid, fallback to {DEFAULT_LIST_REFRESH_MIN} min")

        # Max playlists (MaxPlaylists)
        try:
            self.max_playlists = int(self._read_migrated_parameter("MaxPlaylists", "Mode2", str(DEFAULT_MAX_PLAYLISTS)))
        except (TypeError, ValueError):
            self.max_playlists = DEFAULT_MAX_PLAYLISTS

        # Debug logging (EnableDebug)
        self.debug = self._read_migrated_boolean_parameter("EnableDebug", "Mode3", False)
        if self.debug:
            Domoticz.Debugging(1)
            self.log("Debug logging enabled")
        else:
            Domoticz.Debugging(0)

        # Display text (MessageText)
        self.displayText = str(self._read_migrated_parameter("MessageText", "Mode4", ""))

        self.log(
            f"Poll interval: Online : {self.pollInterval}s | "
            f"Offline : {self.offlinePollInterval}s | Lists: {self.listPollInterval // 60}min"
        )
        self.log("Starting initialization ...... Please wait")

        # Server URL + Auth
        address = Parameters['Address'].strip()
        if '://' in address:
            # Full URL (e.g. https://... through a reverse proxy) - use as-is,
            # Port field is not appended since the URL already specifies one.
            self.url = f"{address.rstrip('/')}/jsonrpc.js"
        else:
            self.url = f"http://{address}:{Parameters['Port']}/jsonrpc.js"
        user = Parameters.get("Username", "")
        pwd = Parameters.get("Password", "")
        self.auth = (user, pwd) if user else None

        Domoticz.Heartbeat(5)
        self.nextPoll = time.time() + 2

    def onStop(self):
        # Let an in-flight poll finish (or abort between queries) before the
        # plugin is torn down: a thread still running inside Domoticz's
        # embedded interpreter at shutdown can hang or crash Domoticz.
        self._stopping.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=WORKER_JOIN_TIMEOUT)
            if worker.is_alive():
                self.error("Polling thread did not stop in time.")
        for session in (self.http, self.http_worker):
            try:
                session.close()
            except Exception:
                pass
        self.log("Plugin stopped.")

    def onHeartbeat(self):
        # Process any fetch cycle(s) the background worker finished since the
        # last tick - main/callback thread, safe here to touch Devices[...].
        was_active = self.any_active
        processed = False
        while True:
            try:
                bundle = self._result_queue.get_nowait()
            except queue.Empty:
                break
            self._processEverything(bundle)
            processed = True

        now = time.time()

        # The interval for the cycle that just finished was picked from the
        # previous cycle's any_active. If playback has just started, switch
        # to the active interval right away instead of waiting out the
        # (much longer) inactive one.
        if processed and self.any_active and not was_active:
            self.nextPoll = min(self.nextPoll, now + self.pollInterval)

        if now < self.nextPoll:
            return

        self._triggerFetchEverything()

        # any_active reflects the previous completed cycle (the current one is
        # still in flight on the worker thread) - a one-cycle lag is an
        # acceptable trade-off for not blocking here to get a fresh value.
        active = self.any_active
        interval = self.pollInterval if active else self.offlinePollInterval

        self.debug_log(f"Heartbeat done, active={active}, next poll in {interval}s")
        self.nextPoll = now + interval

    def _triggerFetchEverything(self):
        """Starts the background worker for one polling cycle. Runs on the main
        thread; only starts a thread and returns immediately."""
        with self._fetch_lock:
            if self._fetch_in_progress:
                self.debug_log("Fetch already in progress, skipping this heartbeat trigger.")
                return
            self._fetch_in_progress = True

        if self._stopping.is_set():
            with self._fetch_lock:
                self._fetch_in_progress = False
            return

        self._worker = threading.Thread(target=self._fetchEverythingWorker, daemon=True)
        self._worker.start()

    def _fetchEverythingWorker(self):
        """Runs on a background thread. Does ONLY the blocking LMS queries
        (get_serverstatus/get_status/get_cached_playlists/get_cached_favorites -
        none of which touch Devices) and hands raw results back through
        self._result_queue. Devices[...] creation/updates happen in
        _processEverything(), on the main thread, afterwards."""
        bundle = {"status": {}, "playlists": {}}
        try:
            server = self.get_serverstatus()
            bundle["server"] = server
            players = (server.get("players_loop", []) or []) if server else []
            players = [p for p in players if isinstance(p, dict)]
            bundle["players"] = players

            for p in players:
                if self._stopping.is_set():
                    return
                mac = p.get("playerid")
                if not mac:
                    continue
                # Keep None (query failed) distinct from an empty status, so
                # a transient failure does not turn the player "Off".
                bundle["status"][mac] = self.get_status(mac)
                # Fetched unconditionally here (cheap once cached - see
                # get_cached_playlists) since the worker thread can't check
                # whether a player's Playlists selector device exists yet;
                # _processEverything() still only uses/shows it when relevant.
                bundle["playlists"][mac] = self.get_cached_playlists(mac)

            if self._stopping.is_set():
                return
            bundle["favorites"] = self.get_cached_favorites()
        except Exception as e:
            self.error(f"Polling cycle failed: {e}")
        finally:
            if not self._stopping.is_set():
                self._result_queue.put(bundle)
            with self._fetch_lock:
                self._fetch_in_progress = False

    # ------------------------------------------------------------------
    # LMS JSON helper
    # ------------------------------------------------------------------
    def lms_query_raw(self, player, cmd_array):
        if self._stopping.is_set():
            return None
        data = {"id": 1, "method": "slim.request", "params": [player, cmd_array]}
        # The polling worker uses its own session (requests.Session is not thread-safe)
        session = self.http_worker if threading.current_thread() is self._worker else self.http
        try:
            r = session.post(self.url, json=data, auth=self.auth, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()

            # JSON decoding can fail even if HTTP is 200 (e.g. proxy/HTML)
            payload = r.json()

            if not isinstance(payload, dict):
                self.debug_log(f"LMS returned unexpected JSON type ({type(payload).__name__}): {payload}")
                return None

            result = payload.get("result")
            if isinstance(result, dict):
                self.debug_log(f"Query: player={player}, cmd={cmd_array}, result keys={sorted(result.keys())}")
            else:
                self.debug_log(f"Query: player={player}, cmd={cmd_array}, result={result!r}")
            self.last_success = time.time()

            if self.server_was_online is not True:
                if self.server_was_online is False:
                    self.log("Lyrion Music Server is ONLINE.")
                self.server_was_online = True

            if result is not None and not isinstance(result, dict):
                self.debug_log(f"LMS returned unexpected result type ({type(result).__name__}), ignored")
                return None
            return result

        except requests.exceptions.RequestException as e:
            # Network/HTTP errors only
            now = time.time()
            if self.server_was_online is not False:
                if now - self.last_success > self.offline_grace:
                    self.log(f"Lyrion Music Server is OFFLINE ({e})")
                    self.server_was_online = False

            self.debug_log(f"LMS query network/HTTP failed: {e}")
            return None

        except ValueError as e:
            # JSON decode error from r.json()
            self.debug_log(f"LMS returned invalid JSON: {e}")
            return None

    def get_serverstatus(self):
        return self.lms_query_raw("", ["serverstatus", 0, 999])

    def get_status(self, playerid):
        return self.lms_query_raw(playerid, ["status", "-", 1, "tags:adclmntyK"])

    def send_playercmd(self, playerid, cmd_array):
        for attempt in range(2):
            result = self.lms_query_raw(playerid, cmd_array)
            if result is not None:
                return result
            time.sleep(0.2)
        return None

    # ------------------------------------------------------------------
    # DISPLAY TEXT
    # ------------------------------------------------------------------
    def send_display_text(self, playerid, line2_text):
        if not playerid or not line2_text:
            return

        line1 = DISPLAY_SUBJECT[:64].replace('"', "'")
        line2 = line2_text[:128].replace('"', "'")
        d = DISPLAY_DURATION

        cmd = [
            "show",
            f"line1:{line1}",
            f"line2:{line2}",
            f"duration:{d}",
            "brightness:4",
            "font:large",
        ]

        self.send_playercmd(playerid, cmd)
        self.log(f"Display text sent to {self.player_name(playerid)}: '{line1}' / '{line2}' ({d}s)")

    def find_player_devices(self, mac):
        main = vol = text = actions = shuffle = repeat = playlistsel = favorites = None

        # Eerst op Description (ideaal)
        for uid, dev in Devices.items():
            if dev.Description != mac:
                continue
            if dev.Name.endswith("Volume"):
                vol = uid
            elif dev.Name.endswith("Track"):
                text = uid
            elif dev.Name.endswith("Actions"):
                actions = uid
            elif dev.Name.endswith("Shuffle"):
                shuffle = uid
            elif dev.Name.endswith("Repeat"):
                repeat = uid
            elif dev.Name.endswith("Playlists"):
                playlistsel = uid
            elif dev.Name.endswith("Favorites"):
                favorites = uid
            else:
                main = uid

        if main:
            return (main, vol, text, actions, shuffle, repeat, playlistsel, favorites)
        return None

    def ensure_player_devices(self, name, mac):
        """Check welke devices er bestaan en maak ontbrekende aan"""
        devices = self.find_player_devices(mac)

        if not devices:
            devices = (None, None, None, None, None, None, None, None)

        main, vol, text, actions, shuffle, repeat, plsel, favsel = devices

        used_units = set(Devices.keys())
        for u in devices:
            if u is not None:
                used_units.add(u)

        # StopIteration-safe allocator
        def next_free():
            for i in range(1, 256):
                if i not in used_units:
                    used_units.add(i)
                    return i
            return None

        def require_unit(purpose: str):
            u = next_free()
            if u is None:
                self.error(f"No free Domoticz units available (1..255). Cannot create device '{purpose}' for player '{name}' ({mac}).")
            return u

        # Main selector
        if main is None:
            opts_main = {
                "LevelNames": "Off|Pause|Play|Stop",
                "LevelActions": "|||",
                "SelectorStyle": "0",
            }
            main_unit = require_unit(f"{name} Control")
            if main_unit is None:
                return (main, vol, text, actions, shuffle, repeat, plsel, favsel)

            Domoticz.Device(
                Name=f"{name} Control",
                Unit=main_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_main,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            main = main_unit
            self.log(f"Main device created for {name}")

        # Volume
        if vol is None:
            vol_unit = require_unit(f"{name} Volume")
            if vol_unit is None:
                return (main, vol, text, actions, shuffle, repeat, plsel, favsel)

            Domoticz.Device(
                Name=f"{name} Volume",
                Unit=vol_unit,
                TypeName="Dimmer",
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            vol = vol_unit
            self.log(f"Volume device created for {name}")

        # Track
        if text is None:
            text_unit = require_unit(f"{name} Track")
            if text_unit is None:
                return (main, vol, text, actions, shuffle, repeat, plsel, favsel)

            Domoticz.Device(
                Name=f"{name} Track",
                Unit=text_unit,
                TypeName="Text",
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            text = text_unit
            self.log(f"Track device created for {name}")

        # Actions
        if actions is None:
            opts_act = {
                "LevelNames": "None|SendText|Sync to this|Unsync",
                "LevelActions": "|||",
                "SelectorStyle": "0",
            }
            act_unit = require_unit(f"{name} Actions")
            if act_unit is None:
                return (main, vol, text, actions, shuffle, repeat, plsel, favsel)

            Domoticz.Device(
                Name=f"{name} Actions",
                Unit=act_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_act,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            actions = act_unit
            self.log(f"Actions device created for {name}")

        # Shuffle
        if shuffle is None:
            opts_shuffle = {"LevelNames": "Off|Songs|Albums", "LevelActions": "||", "SelectorStyle": "0"}
            sh_unit = require_unit(f"{name} Shuffle")
            if sh_unit is None:
                return (main, vol, text, actions, shuffle, repeat, plsel, favsel)

            Domoticz.Device(
                Name=f"{name} Shuffle",
                Unit=sh_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_shuffle,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            shuffle = sh_unit
            self.log(f"Shuffle device created for {name}")

        # Repeat
        if repeat is None:
            opts_repeat = {"LevelNames": "Off|Playlist|Track", "LevelActions": "||", "SelectorStyle": "0"}
            rep_unit = require_unit(f"{name} Repeat")
            if rep_unit is None:
                return (main, vol, text, actions, shuffle, repeat, plsel, favsel)

            Domoticz.Device(
                Name=f"{name} Repeat",
                Unit=rep_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_repeat,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            repeat = rep_unit
            self.log(f"Repeat device created for {name}")

        # Playlists
        if plsel is None:
            opts_pl = {"LevelNames": "Select|Loading...", "LevelActions": "", "SelectorStyle": "1"}
            pl_unit = require_unit(f"{name} Playlists")
            if pl_unit is None:
                return (main, vol, text, actions, shuffle, repeat, plsel, favsel)

            Domoticz.Device(
                Name=f"{name} Playlists",
                Unit=pl_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_pl,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            plsel = pl_unit
            self.log(f"Playlists device created for {name}")

        # Favorites
        if favsel is None:
            opts_fav = {"LevelNames": "Select|Loading...", "LevelActions": "", "SelectorStyle": "1"}
            fav_unit = require_unit(f"{name} Favorites")
            if fav_unit is None:
                return (main, vol, text, actions, shuffle, repeat, plsel, favsel)

            Domoticz.Device(
                Name=f"{name} Favorites",
                Unit=fav_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_fav,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            favsel = fav_unit
            self.log(f"Favorites device created for {name}")

        return (main, vol, text, actions, shuffle, repeat, plsel, favsel)

    # ------------------------------------------------------------------
    # PLAYER-SPECIFIC PLAYLISTS
    # ------------------------------------------------------------------
    def get_player_playlists(self, mac):
        result = self.send_playercmd(mac, ["playlists", 0, self.max_playlists])
        if result is None:
            return None  # query failed - caller keeps the previous list

        pl_loop = result.get("playlists_loop", []) or []
        playlists = []
        for p in pl_loop[: self.max_playlists]:
            name = p.get("playlist", "")
            plid = p.get("id")
            if name:
                playlists.append({"id": plid, "name": name})
        return playlists

    def get_cached_playlists(self, mac):
        now = time.time()
        entry = self.playlist_cache.get(mac)
        if entry and now - entry["ts"] < self.listPollInterval:
            return entry["data"]

        playlists = self.get_player_playlists(mac)
        if playlists is None:
            # Don't cache a failure: keep the old list and retry next poll.
            return entry["data"] if entry else []
        self.playlist_cache[mac] = {"ts": now, "data": playlists}
        return playlists

    def get_cached_favorites(self):
        now = time.time()
        entry = self.favorites_cache
        if entry["ts"] and now - entry["ts"] < self.listPollInterval:
            return entry["data"]

        favorites = self.get_player_favorites()
        if favorites is None:
            return entry["data"]
        self.favorites_cache = {"ts": now, "data": favorites}
        return favorites

    def update_player_playlist_selector(self, plsel_unit, playlists, active_playlist_name=None):
        if plsel_unit not in Devices:
            return

        dev_pl = Devices[plsel_unit]

        if not playlists:
            levelnames = "Select|No playlists"
        else:
            levelnames = "Select|" + "|".join(self.level_label(p["name"]) for p in playlists)

        opts = {
            "LevelNames": levelnames,
            "LevelActions": "",
            "SelectorStyle": "1",
        }

        if dev_pl.Options.get("LevelNames", "") != levelnames:
            dev_pl.Update(nValue=0, sValue=dev_pl.sValue, Options=opts)
            dev_pl = Devices[plsel_unit]
            self.log(f"Playlist selector updated for '{dev_pl.Name}'.")

        if active_playlist_name and playlists:
            for idx, pinfo in enumerate(playlists):
                if pinfo["name"] == active_playlist_name:
                    expected_level = (idx + 1) * 10
                    if dev_pl.sValue != str(expected_level):
                        self.log(f"Setting playlist selector '{dev_pl.Name}' to level {expected_level} for '{active_playlist_name}'")
                        dev_pl.Update(nValue=0, sValue=str(expected_level))
                    break
        else:
            if dev_pl.sValue != "0":
                dev_pl.Update(nValue=0, sValue="0")

    def play_playlist_for_player(self, mac, Level):
        if Level == 0:
            self.log("Playlist selection reset to 'Select'.")
            return

        if Level < 10:
            return

        playlists = self.get_cached_playlists(mac)
        idx = int(Level // 10) - 1
        if idx < 0 or idx >= len(playlists):
            self.error("Invalid playlist index.")
            return

        pl = playlists[idx]
        playlist_name = pl["name"]
        playlist_id = pl["id"]

        self.send_playercmd(mac, ["playlistcontrol", "cmd:load", f"playlist_id:{playlist_id}"])
        self.log(f"Loaded playlist '{playlist_name}' (ID {playlist_id}) on {self.player_name(mac)}")
        self.nextPoll = time.time() + 1

    # ------------------------------------------------------------------
    # FAVORITES
    # LMS favorites zijn globaal
    # ------------------------------------------------------------------
    def get_player_favorites(self):
        result = self.lms_query_raw("", ["favorites", "items", 0, 50])
        if result is None:
            return None  # query failed - caller keeps the previous list

        fav_loop = result.get("loop_loop", []) or []
        favorites = []

        for f in fav_loop:
            name = f.get("name", "")
            fid = f.get("id")
            if name and fid and f.get("hasitems") == 0:
                favorites.append({"id": fid, "name": name})

        return favorites

    def update_favorites_selector(self, fav_unit, favorites):
        if fav_unit not in Devices:
            return

        dev_fav = Devices[fav_unit]

        if not favorites:
            levelnames = "Select|No favorites"
        else:
            parts = ["Select"]
            for f in favorites:
                short_name = self.level_label(f["name"][:25])
                candidate = "|".join(parts + [short_name])
                if len(candidate) > 150:
                    break
                parts.append(short_name)
            levelnames = "|".join(parts)

        opts = {
            "LevelNames": levelnames,
            "LevelActions": "",
            "SelectorStyle": "1",
        }

        if dev_fav.Options.get("LevelNames", "") != levelnames:
            self.debug_log(f"Updating Favorites list. String length: {len(levelnames)}")
            dev_fav.Update(nValue=0, sValue=dev_fav.sValue, Options=opts)

    # ------------------------------------------------------------------
    # MAIN UPDATE LOOP
    # ------------------------------------------------------------------
    def _processEverything(self, bundle):
        """Devices[...]-touching part of one polling cycle, given the raw data
        already fetched by _fetchEverythingWorker(). Safe to call from the
        main thread only."""
        server = bundle.get("server")
        if not server:
            self.any_active = False
            return

        self.players = bundle.get("players", [])
        for p in self.players:
            mac = p.get("playerid")
            if mac:
                self.player_names[mac] = p.get("name", mac)

        # LMS update notification
        update_msg = server.get("newversion", "")
        clean_msg = ""
        if update_msg:
            clean_msg = re.sub('<[^<]+?>', '', str(update_msg)).strip()
            # Keep only the first sentence: the rest is a language-dependent
            # "click here ..." link text.
            clean_msg = re.split(r'(?<=[.!?])\s', clean_msg, maxsplit=1)[0].strip()
            if clean_msg and clean_msg != self.last_update_version:
                try:
                    Domoticz.Status(f"{clean_msg}")
                except Exception as e:
                    self.error(f"Unable to send update notification: {e}")
                self.last_update_version = clean_msg
        else:
            self.last_update_version = ""

        # New players -> create devices
        for p in self.players:
            name = p.get("name", "Unknown")
            mac = p.get("playerid", "")
            if mac:
                self.ensure_player_devices(name, mac)

        any_active = False

        # Update all players. One player with unexpected data must not stop
        # the update of the others.
        for p in self.players:
            mac = p.get("playerid")
            if not mac:
                continue
            try:
                if self._update_player(mac, bundle, clean_msg):
                    any_active = True
            except Exception as e:
                self.error(f"Updating {self.player_name(mac)} failed: {e}")

        self.any_active = any_active

        if not self.initialized:
            self.log("Initialization complete:")
            self.log(f" Players              : {len(self.players)}")
            self.log(f" Devices              : {len(Devices)}")
            self.log(f" Max playlists/player : {self.max_playlists}")
            self.initialized = True

    def _update_player(self, mac, bundle, clean_msg):
        """Updates the devices of one player. Returns True when it is playing
        or paused (used to pick the polling interval)."""
        devices = self.find_player_devices(mac)
        if not devices:
            return False

        main, vol, text, actions, shuffle, repeat, plsel, favsel = devices

        # Keep the Unit -> kind mapping fresh for onCommand.
        for kind_name, unit_id in (
            ("main", main), ("volume", vol), ("track", text), ("actions", actions),
            ("shuffle", shuffle), ("repeat", repeat), ("playlists", plsel), ("favorites", favsel),
        ):
            if unit_id is not None:
                self.unit_kind[unit_id] = kind_name

        st = bundle.get("status", {}).get(mac)
        if st is None:
            # Status query failed this cycle: keep the devices as they are
            # instead of switching the player off.
            self.debug_log(f"No status for {self.player_name(mac)} this cycle, devices left unchanged")
            return False

        try:
            power = int(st.get("power", 0))
        except (TypeError, ValueError):
            power = 0
        mode = st.get("mode", "stop")
        sel_level = {"pause": 10, "play": 20, "stop": 30}.get(mode, 0)
        if power == 0:
            sel_level = 0

        active = power == 1 and mode in ("play", "pause")

        try:
            remote = int(st.get("remote", 0))
        except (TypeError, ValueError):
            remote = 0

        # Main selector
        if main in Devices:
            dev_main = Devices[main]
            n = 1 if power else 0
            s = str(sel_level)
            if dev_main.nValue != n or dev_main.sValue != s:
                dev_main.Update(nValue=n, sValue=s)

        # Volume
        if vol in Devices:
            dev_vol = Devices[vol]
            raw = st.get("mixer volume", 0)
            try:
                parsed_vol = int(float(str(raw).replace("%", "")))
            except (TypeError, ValueError):
                parsed_vol = 0

            # LMS reports a negative "mixer volume" when the player is
            # muted (e.g. -25 means "muted, was at 25"). A Domoticz
            # Dimmer's sValue must be 0-100, so represent a muted player
            # as volume 0 instead of writing a negative, out-of-range
            # value.
            new_vol = max(0, min(100, parsed_vol))
            new_sval = str(new_vol)

            if dev_vol.sValue != new_sval:
                self.debug_log(f"{self.player_name(mac)} | Volume changed to {new_sval}%")
                dev_vol.Update(nValue=2 if new_vol > 0 else 0, sValue=new_sval)

        # Player-specific playlists
        player_pl = bundle.get("playlists", {}).get(mac, []) if plsel else None

        # Track Text
        if text in Devices:
            dev_text = Devices[text]

            if power == 0 or mode in ["stop", "pause"]:
                label = "\U0001F514 LMS update available" if clean_msg else ""

                if dev_text.sValue != label:
                    dev_text.Update(nValue=0, sValue=label)

            else:
                rm = st.get("remoteMeta", {}) or {}
                if not isinstance(rm, dict):
                    rm = {}
                pl_loop = st.get("playlist_loop", []) or []

                current_title = str(st.get("current_title", "") or "")
                title = ""
                artist = ""
                station = ""

                if remote == 1:
                    station = current_title
                    title = str(rm.get("title", "") or "")
                    artist = str(rm.get("artist", "") or "")
                else:
                    if isinstance(pl_loop, list) and pl_loop and isinstance(pl_loop[0], dict):
                        title = str(pl_loop[0].get("title", "") or "")
                        artist = str(pl_loop[0].get("artist", "") or "")
                    if not title:
                        title = current_title

                n = self.track_max_chars
                lines = []
                # Metadata comes from LMS / the radio station's stream, so
                # escape it before it is put into HTML.
                if station:
                    lines.append(f"&#128251; <b><span style='color:#969696;'>{html.escape(station[:n])}</span></b>")
                if artist:
                    lines.append(f"&#127908; <span style='color:#FFD700;'>{html.escape(artist[:n])}</span>")
                if title and title != station:
                    lines.append(f"&#127925; <span style='color:#FFA500 !important;'>{html.escape(title[:n])}</span>")

                # Max 255 chars: drop whole lines instead of cutting a tag in half.
                while lines and len("<br>".join(lines)) > 255:
                    lines.pop()
                label = "<br>".join(lines) if lines else " "

                track_index = st.get("playlist_cur_index")
                changed = False

                if track_index is not None:
                    if self.lastTrackIndex.get(mac) != track_index:
                        changed = True
                        self.lastTrackIndex[mac] = track_index

                if dev_text.sValue != label or changed:
                    dev_text.Update(nValue=0, sValue=label)

        # Shuffle
        if shuffle in Devices:
            dev_shuffle = Devices[shuffle]
            try:
                shuffle_state = int(st.get("playlist shuffle", 0))
            except (TypeError, ValueError):
                shuffle_state = 0
            level = shuffle_state * 10
            if dev_shuffle.sValue != str(level):
                dev_shuffle.Update(nValue=0, sValue=str(level))

        # Repeat status update
        if repeat in Devices:
            dev_repeat = Devices[repeat]
            try:
                repeat_state = int(st.get("playlist repeat", 0))  # 0=Off, 1=Track, 2=Playlist
            except (TypeError, ValueError):
                repeat_state = 0

            level_map = {0: "0", 1: "20", 2: "10"}
            new_level = level_map.get(repeat_state, "0")

            if dev_repeat.sValue != new_level:
                dev_repeat.Update(nValue=0, sValue=new_level)

        # Playlist selector update
        if plsel:
            try:
                playlist_tracks = int(st.get("playlist_tracks", 0))
            except (TypeError, ValueError):
                playlist_tracks = 0
            playlist_name = st.get("playlist_name", "")
            playlist_is_active = (playlist_tracks > 1 and playlist_name not in ("", None) and remote == 0)
            self.update_player_playlist_selector(
                plsel, player_pl, active_playlist_name=playlist_name if playlist_is_active else None
            )

        if favsel:
            self.update_favorites_selector(favsel, bundle.get("favorites", []))

        return active

    # ------------------------------------------------------------------
    # COMMAND HANDLER
    # ------------------------------------------------------------------
    def onCommand(self, Unit, Command, Level, Hue):
        if Unit not in Devices:
            return

        self.nextPoll = time.time() + 1

        dev = Devices[Unit]
        devname = dev.Name
        mac = dev.Description

        if not mac:
            self.error(f"No MAC address for device {Unit} ('{devname}'), command ignored.")
            return

        # Route on the Unit -> kind mapping built in _update_player(). Note:
        # find_player_devices() still classifies devices by their name suffix,
        # so renaming a device in the UI can still confuse the mapping.
        kind = self.unit_kind.get(Unit)
        if kind is None:
            kind = self.kind_from_name_fallback(devname)

        self.debug_log(f"onCommand: Unit={Unit}, Name={devname}, Command={Command}, Level={Level}, mac={mac}, kind={kind}")

        if kind == "favorites" and Command == "Set Level":
            if Level == 0:
                dev.Update(nValue=0, sValue="0")
                return

            favorites = self.get_cached_favorites()
            svalue = str(Level)
            idx = (int(svalue) // 10) - 1

            if 0 <= idx < len(favorites):
                fav = favorites[idx]
                fav_id = fav["id"]
                result = self.send_playercmd(mac, ["favorites", "playlist", "play", f"item_id:{fav_id}"])
                if result is not None:
                    self.log(f"Playing Favorite: {fav['name']}")
                    dev.Update(nValue=1, sValue=svalue, Options=dev.Options)
                else:
                    self.error(f"Failed to play Favorite '{fav['name']}' on {self.player_name(mac)}.")

            self.nextPoll = time.time() + 1
            return

        if kind == "playlists" and Command == "Set Level":
            if Level == 0:
                dev.Update(nValue=0, sValue="0")
                return
            self.play_playlist_for_player(mac, Level)
            return

        if kind == "actions" and Command == "Set Level":
            self.handle_actions(dev, mac, Level)
            return

        if kind == "shuffle":
            if Command == "Set Level":
                mode = int(Level // 10)
            elif Command == "Off":
                mode = 0
                Level = 0
            else:
                return

            result = self.send_playercmd(mac, ["playlist", "shuffle", str(mode)])
            mode_name = {0: "Off", 1: "Songs", 2: "Albums"}.get(mode, f"Unknown ({mode})")
            if result is not None:
                nval = 1 if mode > 0 else 0
                dev.Update(nValue=nval, sValue=str(Level))
                self.log_player(dev, f"Shuffle {mode_name}")
            else:
                self.error(f"Failed to set Shuffle {mode_name} on {self.player_name(mac)}.")
            return

        if kind == "repeat":
            if Command == "Set Level":
                cmd_map = {0: 0, 10: 2, 20: 1}
                mode = cmd_map.get(Level, 0)
            elif Command == "Off":
                mode = 0
                Level = 0
            else:
                return

            result = self.send_playercmd(mac, ["playlist", "repeat", str(mode)])
            mode_name = {0: "Off", 1: "Track", 2: "Playlist"}.get(mode, f"Unknown ({mode})")
            if result is not None:
                nval = 1 if mode > 0 else 0
                dev.Update(nValue=nval, sValue=str(Level))
                self.log_player(dev, f"Repeat set to {mode_name}")
            else:
                self.error(f"Failed to set Repeat {mode_name} on {self.player_name(mac)}.")
            return

        if Command in ["On", "Off"] and kind == "main":
            self.handle_power(dev, mac, Command)
            return

        if kind == "volume" and Command == "Set Level":
            self.handle_volume(dev, mac, Level)
            return

        if Command == "Set Level" and kind == "main":
            self.handle_main_playback(dev, mac, Level)
            return

    # ------------------------------------------------------------------
    # Command helpers
    # ------------------------------------------------------------------
    def handle_volume(self, dev, mac, Level):
        result = self.send_playercmd(mac, ["mixer", "volume", str(Level)])
        if result is not None:
            dev.Update(nValue=2 if Level > 0 else 0, sValue=str(Level))
            self.log_player(dev, f"Volume {Level}%")
        else:
            self.error(f"Failed to set Volume {Level}% on {self.player_name(mac)}.")

    def handle_actions(self, dev, mac, Level):
        if Level == 10:
            if self.displayText:
                self.send_display_text(mac, self.displayText)
            else:
                self.log("No display text configured in parameters (MessageText).")
            dev.Update(nValue=0, sValue="0")
            return

        if Level == 20:
            self.log(f"Syncing all players to {self.player_name(mac)}")
            server = self.get_serverstatus()
            if not server:
                self.log("Server status not available, sync cancelled.")
                dev.Update(nValue=0, sValue="0")
                return

            players = server.get("players_loop", []) or []
            for p in players:
                pid = p.get("playerid")
                if pid and pid != mac:
                    self.send_playercmd(pid, ["sync", mac])
                    time.sleep(0.1)

            dev.Update(nValue=0, sValue="0")
            return

        if Level == 30:
            self.log(f"Unsyncing {self.player_name(mac)}")
            self.send_playercmd(mac, ["sync", "-"])
            dev.Update(nValue=0, sValue="0")
            return

        dev.Update(nValue=0, sValue="0")

    def handle_power(self, dev, mac, Command):
        if Command == "On":
            result = self.send_playercmd(mac, ["power", "1"])
            if result is not None:
                dev.Update(nValue=1, sValue=dev.sValue)
                self.log_player(dev, "Power On")
            else:
                self.error(f"Failed to power On {self.player_name(mac)}.")
        elif Command == "Off":
            result = self.send_playercmd(mac, ["power", "0"])
            if result is not None:
                dev.Update(nValue=0, sValue="0")
                self.log_player(dev, "Power Off")
            else:
                self.error(f"Failed to power Off {self.player_name(mac)}.")

    def handle_main_playback(self, dev, mac, Level):
        if Level == 0:
            result = self.send_playercmd(mac, ["power", "0"])
            if result is not None:
                dev.Update(nValue=0, sValue="0")
                self.log_player(dev, "Power Off")
            else:
                self.error(f"Failed to power Off {self.player_name(mac)}.")
            return

        if Level == 10:
            result = self.send_playercmd(mac, ["pause", "1"])
            if result is not None:
                dev.Update(nValue=1, sValue="10")
                self.log_player(dev, "Pause")
            else:
                self.error(f"Failed to Pause {self.player_name(mac)}.")
            return

        if Level == 20:
            result = self.send_playercmd(mac, ["play"])
            if result is not None:
                dev.Update(nValue=1, sValue="20")
                self.log_player(dev, "Play")
            else:
                self.error(f"Failed to Play {self.player_name(mac)}.")
            return

        if Level == 30:
            result = self.send_playercmd(mac, ["stop"])
            if result is not None:
                dev.Update(nValue=1, sValue="30")
                self.log_player(dev, "Stop")
            else:
                self.error(f"Failed to Stop {self.player_name(mac)}.")
            return


global _plugin
_plugin = LMSPlugin()


def onStart():
    _plugin.onStart()


def onStop():
    _plugin.onStop()


def onHeartbeat():
    _plugin.onHeartbeat()


def onCommand(Unit, Command, Level, Hue):
    _plugin.onCommand(Unit, Command, Level, Hue)
