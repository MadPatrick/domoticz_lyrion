"""
<plugin key="LyrionMusicServer" name="Lyrion Music Server" author="MadPatrick" version="2.1.6" wikilink="https://lyrion.org" externallink="https://github.com/MadPatrick/domoticz_Lyrion">
    <description>
        <h2><br/>Lyrion Music Server Plugin</h2>
        <p>Version 2.1.6</p>
        <p>Detects players, creates devices, and provides:</p>
        <ul>
            <li>Power / Play / Pause / Stop</li>
            <li>Volume (Dimmer)</li>
            <li>Track info (Text)</li>
            <li>Playlists (Selector) - per player (player-specific list)</li>
            <li>Sync / Unsync</li>
            <li>Favorites (Selector)</li>
            <li>Display text (via Actions device)</li>
            <li>Shuffle (Selector)</li>
            <li>Repeat (Selector)</li>
        </ul>
        <br/><span style="font-weight: bold;">Lyrion Server settings</span>
    </description>
    <params>
        <param field="Address" label="Server IP" width="200px" required="true" default="192.168.1.6"/>
        <param field="Port" label="Port" width="100px" required="true" default="9000"/>
        <param field="Username" label="Username" width="150px">
            <description>
                <br/><span style="color: yellow;">Login settings. Only needed when applicable</span>
            </description>
        </param>
        <param field="Password" label="Password" width="150px" password="true">
        </param>
        <param field="Mode1" label="Polling interval (On)" width="100px" default="10">
            <description>
                <br/>
            </description>
            <options>
                <option label="20 sec" value="20"/>
                <option label="5 sec" value="5"/>
                <option label="10 sec" value="10" default="10"/>
                <option label="30 sec" value="30"/>
                <option label="60 sec" value="60"/>
            </options>
        </param>
        <param field="Mode5" label="Polling interval (Off)" width="100px" default="60">
            <options>
            <option label="10 sec" value="10"/>
            <option label="300 sec" value="300"/>
            <option label="600 sec" value="600" default="60"/>
            <option label="1800 sec" value="1800"/>
            <option label="3600 sec" value="3600"/>
        </options>
        </param>
        <param field="Mode2" label="Max playlists to load" width="100px" default="5"/>
        <param field="Mode3" label="Debug logging" width="100px" default="No">
            <options>
                <option label="No" value="False"/>
                <option label="Yes" value="True"/>
            </options>
        </param>
        <param field="Mode4" label="Message text" width="300px" default="Hello from Domoticz!" />
    </params>
</plugin>
"""
# Lyrion Music Server Domoticz Plugin

import Domoticz
import requests
import time
import re


class LMSPlugin:
    def __init__(self):
        self.url = ""
        self.auth = None

        self.pollInterval = 30
        self.offlinePollInterval = 60
        self.nextPoll = 0

        self.players = []
        self.max_playlists = 10

        self.imageID = 0
        self.debug = False

        # Display text settings
        self.displayText = ""           # Mode4: line2
        self.subjectText = "Lyrion"     # line1
        self.displayDuration = 60

        # Logging / init
        self.initialized = False
        self.createdDevices = 0

        # Track-change detection
        self.lastTrackIndex = {}

        # Server status tracking
        self.server_was_online = None
        self.last_success = 0
        self.offline_grace = 15
        self.update_notified = False

        # Playlist cache per speler
        self.playlist_cache = {}
        self.playlist_cache_ttl = 600  # seconden
        # favorites cache per speler        
        self.favorites_cache = {} 
        self.favorites_cache_ttl = 600 # seconden, kies zelf

        # Flag of er een actieve speler is (play/pause)
        self.any_active = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def log(self, msg):
        Domoticz.Log(msg)

    def debug_log(self, msg):
        if self.debug:
            Domoticz.Log("DEBUG: " + str(msg))

    def error(self, msg):
        Domoticz.Error(msg)

    def log_player(self, dev, action):
        if not dev:
            name = "Unknown"
        else:
            name = dev.Name.replace(" Control", "")
        self.log(f"{name} | {action}")

    @staticmethod
    def is_main_device_name(name: str) -> bool:
        return not any(x in name for x in ("Volume", "Track", "Actions", "Shuffle", "Repeat", "Playlists", "Favorites"))

    def get_free_unit(self):
        used = set(Devices.keys())
        for u in range(1, 256):
            if u not in used:
                return u
        return None

    # ------------------------------------------------------------------
    # Domoticz lifecycle
    # ------------------------------------------------------------------
    def onStart(self):
        self.log(f"Starting Plugin version {Parameters['Version']}")

        _IMAGE = "LMS"
        creating_new_icon = _IMAGE not in Images
        Domoticz.Image(f"{_IMAGE}.zip").Create()

        if _IMAGE in Images:
            self.imageID = Images[_IMAGE].ID
            if creating_new_icon:
                self.log("Icons created and loaded.")
            else:
                self.log(f"Icons found in database (ImageID={self.imageID}).")
        else:
            self.error(f"Unable to load icon pack '{_IMAGE}.zip'")

        # Poll interval (Mode1)
        try:
            self.pollInterval = int(Parameters.get("Mode1", 30))
        except (TypeError, ValueError):
            self.log("Mode1 leeg of ongeldig, default 30s gebruikt")
            self.pollInterval = 30

        # Offline interval (Mode5)
        mode5_raw = Parameters.get("Mode5", "")
        if not mode5_raw:
            default_offline = 60
            self.log(f"Mode5 leeg, automatisch instellen op {default_offline}s")
            Parameters["Mode5"] = str(default_offline)
            self.offlinePollInterval = default_offline
        else:
            try:
                self.offlinePollInterval = int(mode5_raw)
            except (TypeError, ValueError):
                self.log("Mode5 ongeldig, fallback naar 60s")
                self.offlinePollInterval = 60

        # Max playlists (Mode2)
        try:
            self.max_playlists = int(Parameters.get("Mode2", 50))
        except (TypeError, ValueError):
            self.max_playlists = 50

        # Debug logging (Mode3)
        self.debug = Parameters.get("Mode3", "False").lower() == "true"

        # Display text (Mode4)
        self.displayText = Parameters.get("Mode4", "")

        self.log(f"Poll interval: {self.pollInterval}s (Online) / {self.offlinePollInterval}s (Offline)")
        self.log("Starting initialization ......  Please wait ")

        # Server URL + Auth
        self.url = f"http://{Parameters['Address']}:{Parameters['Port']}/jsonrpc.js"
        user = Parameters.get("Username", "")
        pwd = Parameters.get("Password", "")
        self.auth = (user, pwd) if user else None

        Domoticz.Heartbeat(5)
        self.nextPoll = time.time() + 2

    def onStop(self):
        self.log("Plugin stopped.")

    def onHeartbeat(self):
        now = time.time()
        if now < self.nextPoll:
            return

        # Alles ophalen en any_active bepalen
        self.updateEverything()

        # Optie B: Mode1 bij play/pause, Mode5 bij stop/uit
        active = self.any_active
        interval = self.pollInterval if active else self.offlinePollInterval

        self.debug_log(f"Heartbeat done, active={active}, next poll in {interval}s")
        self.nextPoll = now + interval

    # ------------------------------------------------------------------
    # LMS JSON helper
    # ------------------------------------------------------------------
    def lms_query_raw(self, player, cmd_array):
        data = {"id": 1, "method": "slim.request", "params": [player, cmd_array]}
        try:
            r = requests.post(self.url, json=data, auth=self.auth, timeout=10)
            r.raise_for_status()
            result = r.json().get("result")
            self.debug_log(f"Query: player={player}, cmd={cmd_array}, result={result}")
            self.last_success = time.time()

            if self.server_was_online is not True:
                if self.server_was_online is False:
                    self.log("Lyrion Music Server is ONLINE.")
                self.server_was_online = True

            return result

        except Exception as e:
            now = time.time()
            if self.server_was_online is not False:
                if now - self.last_success > self.offline_grace:
                    self.log("Lyrion Music Server is OFFLINE")
                    self.server_was_online = False

            self.debug_log(f"LMS query failed: {e}")
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

    def send_button(self, playerid, button):
        return self.send_playercmd(playerid, ["button", button])

    # ------------------------------------------------------------------
    # DISPLAY TEXT
    # ------------------------------------------------------------------
    def send_display_text(self, playerid, line2_text):
        if not playerid or not line2_text:
            return

        line1 = self.subjectText[:64].replace('"', "'")
        line2 = line2_text[:128].replace('"', "'")
        d = self.displayDuration

        cmd = [
            "show",
            f"line1:{line1}",
            f"line2:{line2}",
            f"duration:{d}",
            "brightness:4",
            "font:large",
        ]

        self.send_playercmd(playerid, cmd)
        self.log(f"Display text sent to {playerid}: '{line1}' / '{line2}' ({d}s)")

    # ------------------------------------------------------------------
    # DEVICE CREATION / LOOKUP
    # ------------------------------------------------------------------
    def create_player_devices(self, name, mac):
        base = name
        unit = self.get_free_unit()
        if unit is None:
            self.error("Geen vrije Unit ID beschikbaar!")
            return None

        # main selector
        opts_main = {
            "LevelNames": "Off|Pause|Play|Stop",
            "LevelActions": "||||",
            "SelectorStyle": "0",
        }
        
        # Favorites selector
        opts_fav = {
            "LevelNames": "Select|Loading...",
            "LevelActions": "",
            "SelectorStyle": "1", # 1 is pulldown (dropdown)
        }

        Domoticz.Device(
            Name=f"{base} Control",
            Unit=unit,
            TypeName="Selector Switch",
            Switchtype=18,
            Options=opts_main,
            Image=self.imageID,
            Description=mac,
            Used=1,
        ).Create()

        # volume
        Domoticz.Device(
            Name=f"{base} Volume",
            Unit=unit + 1,
            TypeName="Dimmer",
            Image=self.imageID,
            Description=mac,
            Used=1,
        ).Create()

        # track text
        Domoticz.Device(
            Name=f"{base} Track",
            Unit=unit + 2,
            TypeName="Text",
            Image=self.imageID,
            Description=mac,
            Used=1,
        ).Create()

        # actions selector
        opts_act = {
            "LevelNames": "None|SendText|Sync to this|Unsync",
            "LevelActions": "||",
            "SelectorStyle": "0",
        }

        Domoticz.Device(
            Name=f"{base} Actions",
            Unit=unit + 3,
            TypeName="Selector Switch",
            Switchtype=18,
            Options=opts_act,
            Image=self.imageID,
            Description=mac,
            Used=1,
        ).Create()

        # shuffle selector
        opts_shuffle = {
            "LevelNames": "Off|Songs|Albums",
            "LevelActions": "||",
            "SelectorStyle": "0",
        }

        Domoticz.Device(
            Name=f"{base} Shuffle",
            Unit=unit + 4,
            TypeName="Selector Switch",
            Switchtype=18,
            Options=opts_shuffle,
            Image=self.imageID,
            Description=mac,
            Used=1,
        ).Create()

        # repeat selector
        opts_repeat = {
            "LevelNames": "Off|Track|Playlist",
            "LevelActions": "||",
            "SelectorStyle": "0",
        }

        Domoticz.Device(
            Name=f"{base} Repeat",
            Unit=unit + 5,
            TypeName="Selector Switch",
            Switchtype=18,
            Options=opts_repeat,
            Image=self.imageID,
            Description=mac,
            Used=1,
        ).Create()

        # playlists selector
        opts_pl = {
            "LevelNames": "Select|Loading...",
            "LevelActions": "",
            "SelectorStyle": "1",
        }

        Domoticz.Device(
            Name=f"{base} Playlists",
            Unit=unit + 6,
            TypeName="Selector Switch",
            Switchtype=18,
            Options=opts_pl,
            Image=self.imageID,
            Description=mac,
            Used=1,
        ).Create()
        
        Domoticz.Device(
            Name=f"{base} Favorites",
            Unit=unit + 7,
            TypeName="Selector Switch",
            Switchtype=18,
            Options=opts_fav,
            Image=self.imageID,
            Description=mac,
            Used=1,
        ).Create()

        self.createdDevices += 8
        self.log(f"Devices created for player '{name}'")
        return (unit, unit + 1, unit + 2, unit + 3, unit + 4, unit + 5, unit + 6, unit + 7)

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

        # Fallback: als niets gevonden, probeer naamfragment
        if not main:
            for uid, dev in Devices.items():
                if mac not in dev.Name:
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
    
        # Als geen enkel device bestaat, maak alles aan
        if not devices:
            return self.create_player_devices(name, mac)
    
        main, vol, text, actions, shuffle, repeat, plsel, favsel = devices
    
        # Track welke units al bestaan
        units_in_use = set(u for u in devices if u is not None)
        next_unit = self.get_free_unit()
    
        # Als main ontbreekt
        if main is None:
            main_unit = next_unit
            next_unit += 1
            opts_main = {
                "LevelNames": "Off|Pause|Play|Stop",
                "LevelActions": "||||",
                "SelectorStyle": "0",
            }
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
            self.log(f"Main device aangemaakt voor {name}")
    
        # Volume
        if vol is None:
            Domoticz.Device(
                Name=f"{name} Volume",
                Unit=next_unit,
                TypeName="Dimmer",
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            vol = next_unit
            self.log(f"Volume device aangemaakt voor {name}")
            next_unit += 1

        # Track
        if text is None:
            Domoticz.Device(
                Name=f"{name} Track",
                Unit=next_unit,
                TypeName="Text",
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            text = next_unit
            self.log(f"Track device aangemaakt voor {name}")
            next_unit += 1

        # Actions
        if actions is None:
            opts_act = {
                "LevelNames": "None|SendText|Sync to this|Unsync",
                "LevelActions": "||",
                "SelectorStyle": "0",
            }
            Domoticz.Device(
                Name=f"{name} Actions",
                Unit=next_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_act,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            actions = next_unit
            self.log(f"Actions device aangemaakt voor {name}")
            next_unit += 1

        # Shuffle
        if shuffle is None:
            opts_shuffle = {"LevelNames": "Off|Songs|Albums", "LevelActions": "||", "SelectorStyle": "0"}
            Domoticz.Device(
                Name=f"{name} Shuffle",
                Unit=next_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_shuffle,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            shuffle = next_unit
            self.log(f"Shuffle device aangemaakt voor {name}")
            next_unit += 1

        # Repeat
        if repeat is None:
            opts_repeat = {"LevelNames": "Off|Track|Playlist", "LevelActions": "||", "SelectorStyle": "0"}
            Domoticz.Device(
                Name=f"{name} Repeat",
                Unit=next_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_repeat,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            repeat = next_unit
            self.log(f"Repeat device aangemaakt voor {name}")
            next_unit += 1

        # Playlists
        if plsel is None:
            opts_pl = {"LevelNames": "Select|Loading...", "LevelActions": "", "SelectorStyle": "1"}
            Domoticz.Device(
                Name=f"{name} Playlists",
                Unit=next_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_pl,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            plsel = next_unit
            self.log(f"Playlists device aangemaakt voor {name}")
            next_unit += 1

        # Favorites
        if favsel is None:
            opts_fav = {"LevelNames": "Select|Loading...", "LevelActions": "", "SelectorStyle": "1"}
            Domoticz.Device(
                Name=f"{name} Favorites",
                Unit=next_unit,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts_fav,
                Image=self.imageID,
                Description=mac,
                Used=1,
            ).Create()
            favsel = next_unit
            self.log(f"Favorites device aangemaakt voor {name}")
            next_unit += 1

        return (main, vol, text, actions, shuffle, repeat, plsel, favsel)

    # ------------------------------------------------------------------
    # PLAYER-SPECIFIC PLAYLISTS
    # ------------------------------------------------------------------
    def get_player_playlists(self, mac):
        result = self.send_playercmd(mac, ["playlists", 0, self.max_playlists])
        if not result:
            return []

        pl_loop = result.get("playlists_loop", []) or []
        playlists = []
        for p in pl_loop[: self.max_playlists]:
            name = p.get("playlist", "")
            plid = p.get("id")
            if name:
                playlists.append({"id": plid, "playlist": name})
        return playlists

    def get_cached_playlists(self, mac):
        now = time.time()
        entry = self.playlist_cache.get(mac)
        if entry and now - entry["ts"] < self.playlist_cache_ttl:
            return entry["data"]

        playlists = self.get_player_playlists(mac)
        self.playlist_cache[mac] = {"ts": now, "data": playlists}
        return playlists
        
    def get_cached_favorites(self, mac):
        now = time.time()
        entry = self.favorites_cache.get(mac)

        if entry and now - entry["ts"] < self.favorites_cache_ttl:
            return entry["data"]

        favorites = self.get_cached_favorites(mac)
        self.favorites_cache[mac] = {"ts": now, "data": favorites}
        return favorites


    def update_player_playlist_selector(self, plsel_unit, playlists, active_playlist_name=None):
        if plsel_unit not in Devices:
            return

        dev_pl = Devices[plsel_unit]

        if not playlists:
            levelnames = "Select|No playlists"
        else:
            levelnames = "Select|" + "|".join(p["playlist"] for p in playlists)

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
                if pinfo["playlist"] == active_playlist_name:
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
        playlist_name = pl["playlist"]
        playlist_id = pl["id"]

        self.send_playercmd(mac, ["playlistcontrol", "cmd:load", f"playlist_id:{playlist_id}"])
        self.log(f"Loaded playlist '{playlist_name}' (ID {playlist_id}) on player {mac}")
        self.nextPoll = time.time() + 1
        
    # ------------------------------------------------------------------
    # FAVORITES
    # ------------------------------------------------------------------
    def get_player_favorites(self, mac):
        result = self.lms_query_raw("", ["favorites", "items", 0, 50])
        if not result:
            return []

        fav_loop = result.get("loop_loop", []) or []
        favorites = []

        for f in fav_loop:
            name = f.get("name", "")
            fid = f.get("id")
            if name and f.get("hasitems") == 0:
                favorites.append({"id": fid, "playlist": name})

        return favorites


    def update_favorites_selector(self, fav_unit, favorites):
        if fav_unit not in Devices:
            return

        dev_fav = Devices[fav_unit]

        if not favorites:
            levelnames = "Select|No Favorites"
        else:
            levelnames = "Select|" + "|".join(f["playlist"] for f in favorites)

        opts = {
            "LevelNames": levelnames,
            "LevelActions": "",
            "SelectorStyle": "1",
        }

        if dev_fav.Options.get("LevelNames", "") != levelnames:
            dev_fav.Update(nValue=0, sValue="0", Options=opts)

    # ------------------------------------------------------------------
    # MAIN UPDATE LOOP
    # ------------------------------------------------------------------
    def updateEverything(self):
        server = self.get_serverstatus()
        if not server:
            self.any_active = False
            return

        self.players = server.get("players_loop", []) or []

        # LMS update melding
        update_msg = server.get("newversion", "")
        clean_msg = ""
        if update_msg:
            import re
            clean_msg = re.sub('<[^<]+?>', '', update_msg)
            clean_msg = clean_msg.split('Klik op hier')[0].strip()
            if not self.update_notified:
                try:
                    Domoticz.Status(f"{clean_msg}")
                except Exception as e:
                    Domoticz.Error(f"Kon update notificatie niet versturen: {e}")
                self.update_notified = True
        else:
            self.update_notified = False

        # Nieuwe spelers → devices aanmaken
        for p in self.players:
            name = p.get("name", "Unknown")
            mac = p.get("playerid", "")
            if mac:
                self.ensure_player_devices(name, mac)

        any_active = False

        # Alle spelers updaten
        for p in self.players:
            mac = p.get("playerid")
            if not mac:
                continue

            devices = self.find_player_devices(mac)
            if not devices:
                continue

            main, vol, text, actions, shuffle, repeat, plsel, favsel = devices
            st = self.get_status(mac) or {}

            power = int(st.get("power", 0))
            mode = st.get("mode", "stop")
            sel_level = {"pause": 10, "play": 20, "stop": 30}.get(mode, 0)
            if power == 0:
                sel_level = 0

            # Optie B: actief bij play of pause
            if power == 1 and mode in ("play", "pause"):
                any_active = True

            remote = st.get("remote", 0)

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
                    new_sval = str(int(float(str(raw).replace("%", ""))))
                except Exception:
                    new_sval = "0"

                if dev_vol.sValue != new_sval:
                    self.log(f"Volume changed to : {new_sval}%")
                    n_val = 2 if int(new_sval) > 0 else 0
                    dev_vol.Update(nValue=n_val, sValue=new_sval)

            # Player-specific playlists
            player_pl = None
            if plsel:
                player_pl = self.get_cached_playlists(mac)

            # Track Text
            if text in Devices:
                dev_text = Devices[text]

                if power == 0 or mode in ["stop", "pause"]:
                    # ALS er een update beschikbaar is, laat deze zien
                    if clean_msg:
                        label = f"🔔 LMS update beschikbaar"
                    else:
                        label = ""  # gewoon leeg als geen update

                    if dev_text.sValue != label:
                        dev_text.Update(nValue=0, sValue=label)

                    if plsel:
                        self.update_player_playlist_selector(plsel, player_pl, active_playlist_name=None)

                else:
                    rm = st.get("remoteMeta", {})
                    pl_loop = st.get("playlist_loop", []) or []

                    current_title = st.get("current_title", "")
                    title = ""
                    artist = ""
                    station = ""

                    if remote == 1:
                        station = current_title
                        title = rm.get("title", "")
                        artist = rm.get("artist", "")
                    else:
                        if pl_loop and isinstance(pl_loop, list):
                            title = pl_loop[0].get("title", "")
                            artist = pl_loop[0].get("artist", "")
                        if not title:
                            title = current_title

                    lines = []
                    if station:
                        lines.append(f"&#128251; <b><span style='color:white;'>{station}</span></b>")
                    if artist:
                        lines.append(f"&#127908; <span style='color:#fcfc7e;'>{artist}</span>")  # lichtgeel
                    if title and title != station:
                        lines.append(f"&#127925; <span style='color:orange !important;'>{title}</span>")

                    label = "<br>".join(lines) if lines else " "
                    label = label[:255]

                    track_index = st.get("playlist_cur_index")
                    player_key = mac
                    changed = False

                    if track_index is not None:
                        if player_key not in self.lastTrackIndex or self.lastTrackIndex[player_key] != track_index:
                            changed = True
                            self.lastTrackIndex[player_key] = track_index

                    if dev_text.sValue != label or changed:
                        dev_text.Update(nValue=0, sValue=label)

            # Shuffle
            if shuffle in Devices:
                dev_shuffle = Devices[shuffle]
                try:
                    shuffle_state = int(st.get("playlist shuffle", 0))
                except Exception:
                    shuffle_state = 0
                level = shuffle_state * 10
                if dev_shuffle.sValue != str(level):
                    dev_shuffle.Update(nValue=0, sValue=str(level))

            # Repeat
            if repeat in Devices:
                dev_repeat = Devices[repeat]
                try:
                    repeat_state = int(st.get("playlist repeat", 0))
                except Exception:
                    repeat_state = 0
                level = repeat_state * 10
                if dev_repeat.sValue != str(level):
                    dev_repeat.Update(nValue=0, sValue=str(level))

            # Playlist selector update
            playlist_tracks = st.get("playlist_tracks", 0)
            playlist_name = st.get("playlist_name", "")
            playlist_is_active = (playlist_tracks > 1 and playlist_name not in ("", None) and remote == 0)

            if plsel:
                if playlist_is_active:
                    self.update_player_playlist_selector(plsel, player_pl, active_playlist_name=playlist_name)
                else:
                    self.update_player_playlist_selector(plsel, player_pl, active_playlist_name=None)
            if favsel:
                favorites = self.get_player_favorites(mac)
                self.update_favorites_selector(favsel, favorites)

        self.any_active = any_active

        if not self.initialized:
            self.log("Initialization complete:")
            self.log(f" Players           : {len(self.players)}")
            device_count = len(Devices)
            self.log(f" Devices           : {device_count}")
            self.log(f" Max playlists/player : {self.max_playlists}")
            self.initialized = True

    # ------------------------------------------------------------------
    # COMMAND HANDLER
    # ------------------------------------------------------------------
    def onCommand(self, Unit, Command, Level, Hue):
        if Unit not in Devices:
            return

        # Forceer snelle update na een commando
        self.nextPoll = time.time() + 1

        dev = Devices[Unit]
        devname = dev.Name
        mac = dev.Description

        self.debug_log(f"onCommand: Unit={Unit}, Name={devname}, Command={Command}, Level={Level}, mac={mac}")
        
        if "Favorites" in devname and Command == "Set Level":
            if Level == 0:
                dev.Update(nValue=0, sValue="0")
                return
            
            # Haal de lijst op (je kunt hier ook caching toevoegen net als bij playlists)
            favorites = self.get_player_favorites(mac)
            idx = int(Level // 10) - 1
            if 0 <= idx < len(favorites):
                fav_id = favorites[idx]["id"]
                # Commando om de favoriet af te spelen
                self.send_playercmd(mac, ["favorites", "playlist", "play", f"item_id:{fav_id}"])
                self.log(f"Playing Favorite: {favorites[idx]['playlist']}")
            
            self.nextPoll = time.time() + 1
            return

        if "Playlists" in devname and Command == "Set Level":
            if Level == 0:
                dev.Update(nValue=0, sValue="0")
                return
            self.play_playlist_for_player(mac, Level)
            return

        if "Actions" in devname and Command == "Set Level":
            self.handle_actions(dev, mac, Level)
            return

        if "Shuffle" in devname:
            if Command == "Set Level":
                mode = int(Level // 10)
            elif Command == "Off":
                mode = 0
                Level = 0
            else:
                return

            self.send_playercmd(mac, ["playlist", "shuffle", str(mode)])
            nval = 1 if mode > 0 else 0
            dev.Update(nValue=nval, sValue=str(Level))
            mode_name = {0: "Off", 1: "Songs", 2: "Albums"}.get(mode, f"Unknown ({mode})")
            self.log_player(dev, f"Shuffle {mode_name}")
            return

        if "Repeat" in devname:
            if Command == "Set Level":
                mode = int(Level // 10)
            elif Command == "Off":
                mode = 0
                Level = 0
            else:
                return

            self.send_playercmd(mac, ["playlist", "repeat", str(mode)])
            nval = 1 if mode > 0 else 0
            dev.Update(nValue=nval, sValue=str(Level))
            mode_name = {0: "Off", 1: "Track", 2: "Playlist"}.get(mode, f"Unknown ({mode})")
            self.log_player(dev, f"Repeat {mode_name}")
            return

        if Command in ["On", "Off"] and self.is_main_device_name(devname):
            self.handle_power(dev, mac, Command)
            return

        if "Volume" in devname and Command == "Set Level":
            self.handle_volume(dev, mac, Level)
            return

        if Command == "Set Level" and self.is_main_device_name(devname):
            self.handle_main_playback(dev, mac, Level)
            return

    # ------------------------------------------------------------------
    # Command helpers
    # ------------------------------------------------------------------
    def handle_volume(self, dev, mac, Level):
        self.send_playercmd(mac, ["mixer", "volume", str(Level)])
        dev.Update(nValue=2 if Level > 0 else 0, sValue=str(Level))
        self.log_player(dev, f"Volume {Level}%")

    def handle_actions(self, dev, mac, Level):
        # Level 10: SendText, 20: Sync, 30: Unsync
        if Level == 10:
            if self.displayText:
                self.send_display_text(mac, self.displayText)
            else:
                self.log("No display text configured in parameters (Mode4).")
            dev.Update(nValue=0, sValue="0")
            return

        if Level == 20:
            self.log(f"Syncing all players TO master: {mac}")
            server = self.get_serverstatus()
            if not server:
                self.log("Serverstatus niet beschikbaar, sync afgebroken.")
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
            self.log(f"Unsyncing player: {mac}")
            self.send_playercmd(mac, ["sync", "-"])
            dev.Update(nValue=0, sValue="0")
            return

        dev.Update(nValue=0, sValue="0")

    def handle_power(self, dev, mac, Command):
        if Command == "On":
            self.send_playercmd(mac, ["power", "1"])
            dev.Update(nValue=1, sValue=dev.sValue)
            self.log_player(dev, "Power On")
        elif Command == "Off":
            self.send_playercmd(mac, ["power", "0"])
            dev.Update(nValue=0, sValue="0")
            self.log_player(dev, "Power Off")

    def handle_main_playback(self, dev, mac, Level):
        # 0=Off, 10=Pause, 20=Play, 30=Stop
        if Level == 0:
            self.send_playercmd(mac, ["power", "0"])
            dev.Update(nValue=0, sValue="0")
            self.log_player(dev, "Power Off")
            return

        if Level == 10:
            self.send_playercmd(mac, ["pause", "1"])
            dev.Update(nValue=1, sValue="10")
            self.log_player(dev, "Pause")
            return

        if Level == 20:
            self.send_playercmd(mac, ["play"])
            dev.Update(nValue=1, sValue="20")
            self.log_player(dev, "Play")
            return

        if Level == 30:
            self.send_playercmd(mac, ["stop"])
            dev.Update(nValue=1, sValue="30")
            self.log_player(dev, "Stop")
            return


# ----------------------------------------------------------------------
# Domoticz plugin callbacks
# ----------------------------------------------------------------------
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
