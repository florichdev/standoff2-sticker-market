import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CATALOG_FILE = Path(__file__).resolve().parent / "catalog.json"
CUSTOM_CATALOG_FILE = Path(__file__).resolve().parent / "custom_catalog.json"

STICKER_RARITIES: list[str] = [
    "Common",
    "Uncommon",
    "Rare",
    "Epic",
    "Legendary",
    "Arcane",
]

GAME_CATALOG: dict[str, dict[str, list[str]]] = {
    "Винтовки": {
        "AKR": [
            "2 Years Red",
            "Arctic Camo",
            "Carbon",
            "Celebration Red",
            "Digital Burst",
            "Dragon",
            "Evolution",
            "Fabric Storm",
            "Genesis",
            "Icewing",
            "Jade Stone",
            "La Reina",
            "Mirage Menace",
            "Nano",
            "Necromancer",
            "Night Fury",
            "Orchid",
            "Reis",
            "Scale",
            "Scylla",
            "Sketch",
            "Sport",
            "Sport -28",
            "Steel Grip",
            "Tag King",
            "Tiger",
            "Treasure Hunter",
            "Twilight",
            "Vermilion",
            "Worm"
        ],
        "AKR12": [
            "4 Years",
            "Armored",
            "Ashbringer",
            "Aurora",
            "Camouflage",
            "Carbon",
            "Carving",
            "Cold Core",
            "Cold-Hearted",
            "Cyber",
            "Emberbird",
            "Flow",
            "Geometric",
            "Hanami",
            "Haunt",
            "Ice Storm",
            "Kensei",
            "Mechanic",
            "Mimicry",
            "Pixel Camouflage",
            "Railgun",
            "Riot",
            "Roar",
            "Shark",
            "Speedrun",
            "Spring",
            "Steam Beast",
            "Steampunk",
            "Transistor",
            "Year of the Horse"
        ],
        "FAMAS": [
            "Anger",
            "Autumn",
            "Battle Steel",
            "Beagle",
            "Christmas Symbol",
            "Delta",
            "Feral",
            "Fury",
            "Gila",
            "Green Dester",
            "Gunsmoke",
            "HEXA",
            "Handcraft",
            "Hull",
            "Monster",
            "Oxide",
            "Protocol",
            "Raider",
            "Ritual",
            "Snow Storm",
            "Spline",
            "Stellar Blitz",
            "Yakuza"
        ],
        "M16": [
            "4 Years",
            "Accuracy",
            "Army Camouflage",
            "Basilisk",
            "Bastion",
            "Bigfoot",
            "Bulldozer",
            "Camouflage",
            "Challenger",
            "Cutting Edge",
            "Dust Devil",
            "Facet",
            "HEXA",
            "Iron",
            "Iron Will",
            "Mayhem",
            "Muraena",
            "Naga",
            "Needle",
            "Project X",
            "Raiva",
            "Retro Arcade",
            "Sea Glint",
            "Sharpshooter",
            "Shogun Stripes",
            "Triumphant",
            "Winged",
            "Wraithwood"
        ],
        "M4": [
            "Bastet",
            "Bullets and Roses",
            "Catalyst",
            "Demon",
            "Dire Wolf",
            "Evil",
            "Flex",
            "Flock",
            "Furious Sharpshooter",
            "Grand Prix",
            "Horseman",
            "Ironclad",
            "Kachi",
            "Lizard",
            "Minotaur",
            "N-Kai",
            "Necromancer",
            "Night Terror",
            "Night Wolf",
            "PRO",
            "Pixel Storm",
            "Powergame",
            "Predator",
            "Pro",
            "R.O.N.I.N. mk56",
            "Retro Film",
            "Revival",
            "Samurai",
            "Striker",
            "Sunset",
            "Tiger",
            "Urban Style",
            "Water Dragon",
            "Wildstyle",
            "Wolf"
        ],
        "M4A1": [
            "Arid Camo",
            "Bubblegum",
            "Contour",
            "Cyber",
            "Ferocity",
            "Immortal",
            "Impact",
            "Jade Stone",
            "K.I.N.G. v7.03",
            "King",
            "Kitsune",
            "Lincoln",
            "Mermaid",
            "Overdrive",
            "Paladin",
            "PawPaw",
            "Pixel Flakes",
            "Renegade",
            "Serpent",
            "Siren",
            "Sour",
            "Sparkling Gaze",
            "Stainless",
            "Tempest",
            "Treasure Hunter",
            "Year Of The Tiger"
        ],
        "Val": [
            "Forge",
            "Gilded Gale",
            "Iron Wolf",
            "Joker",
            "Krait",
            "Night City",
            "Oil Sheen",
            "Orbit",
            "Phantom Pack",
            "Ronin",
            "Rosa Mortal",
            "Solstice",
            "Vertex",
            "Widow's Weave"
        ]
    },
    "Снайперские винтовки": {
        "AWM": [
            "2 Years Red",
            "BOOM",
            "Blue Bloody",
            "Boom",
            "Celebration Red",
            "Cold-Blooded",
            "Cyberpunk",
            "Daemon",
            "Dark Camo",
            "Dragon",
            "Elevation",
            "Eye of Horus",
            "Festal Wrap",
            "Gear",
            "Genesis",
            "Hohei Taisho",
            "Jade Stone",
            "Kings",
            "Moonstone",
            "Nebula",
            "Nextgen",
            "PRO",
            "Phoenix",
            "Polar Light",
            "Polar Night",
            "Poseidon",
            "Ravage",
            "Scratch",
            "Spectral",
            "Sport",
            "Sport V2",
            "Stickerbomb",
            "Sylvan",
            "System Breach",
            "Treasure Hunter",
            "Vampire",
            "Venom",
            "Winter Sport",
            "Xenoguard",
            "qSport"
        ],
        "M40": [
            "Arctic",
            "Auric Bloom",
            "Beagle",
            "Constellations",
            "Cursed Fire",
            "Daybreak",
            "Disguise",
            "Grip",
            "Horus",
            "Impale",
            "Monster",
            "Northern Lights",
            "PRO",
            "Pro",
            "Quake",
            "Skyfall",
            "Smolder",
            "Stickerbomb",
            "Thief Of The Christmas",
            "Venom Shade",
            "White Ash",
            "Winter Track",
            "Wyvern"
        ],
        "M110": [
            "Celestial",
            "Celestial Beast",
            "Centipede",
            "Cyber",
            "Dark Pact",
            "Flow",
            "Graffiti",
            "Grid",
            "Harbinger",
            "Juicy",
            "Lines",
            "Modern Art",
            "Morph",
            "Project X",
            "Pursuit",
            "Reach Keeper",
            "Stickerbomb",
            "Tech Shard",
            "Themis",
            "Top Secret",
            "Transition",
            "Year Of The Tiger",
            "Z-07 M.A.R.K.S.M.A.N."
        ]
    },
    "Пистолеты": {
        "Berettas": [
            "Arctic Camo",
            "Artifacts",
            "Blazing Maw",
            "Cascade",
            "Construction Sector",
            "Contender",
            "Damascus",
            "Hexagon",
            "Hybrid",
            "Lumos Shard",
            "Marco",
            "Opener of Ways",
            "Royal Rose",
            "Soul",
            "Soul Devourer",
            "Spikes",
            "Verdict"
        ],
        "Deagle": [
            "Ace",
            "Aces",
            "Aureate",
            "Blood",
            "Blossom",
            "Blue Bloody",
            "Captain Morgan",
            "Cyberpunk",
            "Draco",
            "Dragon Glass",
            "Dust Devil",
            "Eclipse",
            "Fusion Core",
            "Gambit",
            "Glory",
            "Hunter",
            "Infection",
            "Jade Stone",
            "Mafia",
            "Morgan",
            "Orochi",
            "PRO",
            "Piranha",
            "Predator",
            "Razorwire",
            "Red Dragon",
            "Thunder",
            "Venator",
            "Vermilion",
            "Violet Flame",
            "Wildflower",
            "Winner",
            "Yakuza"
        ],
        "F/S": [
            "Aquamarine",
            "Camo Storm",
            "Corrosion",
            "Demonic Fog",
            "Enforcer",
            "Flashing Flame",
            "Hellspawn",
            "Holiday Frost",
            "Octopus",
            "Ophidian",
            "Pink Shot",
            "Poison",
            "Polymer",
            "Red Wraith",
            "Royal Frost",
            "Rush",
            "Sight Grey",
            "Tactical",
            "Toxic",
            "Venom",
            "Vines",
            "Wraith",
            "Zap",
            "Zone"
        ],
        "G22": [
            "Adam",
            "Bird",
            "Briar",
            "Carbon",
            "Casual",
            "Desert Camouflage/Camouflage",
            "Flock",
            "Flor de Muertos",
            "Frost Wyrm",
            "Frozen",
            "Haunt",
            "Impulse",
            "Inferno",
            "Lion Lord",
            "Monster",
            "Nest",
            "Ornament",
            "Pattern",
            "Pixel Camouflage",
            "Reindeer Sweater",
            "Relic",
            "Scale",
            "Space Bird",
            "Starfall",
            "Steel Grip",
            "Treasure Hunter",
            "Twilight",
            "White Carbon",
            "Wildstyle",
            "Yellow Line"
        ],
        "P350": [
            "4 Years",
            "Autumn",
            "Blizzard",
            "Cyber",
            "Emberbird",
            "Festal Wrap",
            "Forest Spirit",
            "Gearshift",
            "Impact",
            "Ink Spree",
            "Lab Prototype",
            "Nano",
            "Neon",
            "Oni",
            "Ooze",
            "Radiation",
            "Raider",
            "Rally",
            "Rhino",
            "Rider",
            "Sandspirit",
            "Savannah",
            "Silver Wing",
            "Skull",
            "Tag King",
            "Venom",
            "Vitrum"
        ],
        "Tec-9": [
            "Aurora",
            "Blossom",
            "Dalmatian",
            "Disguise",
            "Fable",
            "Gangster",
            "Gift Wrap",
            "Glitch",
            "Holiday Frost",
            "Iris",
            "Necromancer",
            "Needle",
            "Neo Core",
            "Night Slayer",
            "Reactor",
            "Restless",
            "Royal Frost",
            "Smoldering Coal",
            "Splash",
            "Splinter Storm",
            "Spot",
            "Stickerbomb",
            "Tie Dye",
            "Tropic",
            "Ultimatum",
            "Verglas"
        ],
        "USP": [
            "2 Years",
            "2 Years Red",
            "Celebration",
            "Celebration Red",
            "Celestial Tiger",
            "Chameleon",
            "Corrode",
            "Digital Burst",
            "Eels",
            "Fiend",
            "Genesis",
            "Geometric",
            "Ghosts",
            "Griffin",
            "Hologram",
            "Horror",
            "Hunter-02",
            "Ignite",
            "Line",
            "Mirage Menace",
            "Parrot",
            "Pisces",
            "Purple Camo",
            "Rainforest",
            "Ray",
            "Stickerbomb",
            "Stone Cold",
            "Suture",
            "Top Secret",
            "Warden",
            "Yellow"
        ]
    },
    "Пистолеты-пулемёты": {
        "MAC10": [
            "Arcane Surge",
            "Argo",
            "Arid",
            "Cardboard",
            "Constellations",
            "Corrode",
            "Escape Plan",
            "Fatal Combo",
            "Furious",
            "Melt",
            "Melt Away",
            "Noxious",
            "Pixel Camouflage",
            "Purple Mist",
            "Racer",
            "Ruby Shadow",
            "Shogun Stripes",
            "Tempest",
            "Thermal",
            "Turbo",
            "Unsealed",
            "Wild Rage"
        ],
        "MP5": [
            "4 Years",
            "Arcane Surge",
            "Blueprint",
            "Camouflage",
            "Clouds",
            "Contender",
            "Dusk",
            "Gorgon",
            "Graffiti",
            "Ignis",
            "Insanity",
            "Iron",
            "Juicy",
            "Modern Art",
            "Northern Fury",
            "Outrider",
            "Phantom Form",
            "Project Z9",
            "Reactor",
            "Sector",
            "Silver-Plated",
            "Slasher",
            "Space Blaster",
            "Tiles",
            "Vector",
            "Water Gun",
            "Zone"
        ],
        "MP7": [
            "2 Years",
            "2 Years Red",
            "Arcade",
            "Banana",
            "Beast",
            "Blizzard",
            "Celebration",
            "Celebration Red",
            "Contender",
            "Dawn",
            "Empire",
            "Festal Wrap",
            "Frequency",
            "Fright",
            "Future Tech",
            "Graffiti",
            "Lich",
            "Magma Trail",
            "Monkey",
            "Offroad",
            "Palace",
            "Precision",
            "R1NA",
            "Rebellion",
            "Revival",
            "Ridge",
            "Space Blaster",
            "Stickerbomb",
            "Thorn",
            "Urban Style",
            "Water Dragon",
            "Winter Sport"
        ],
        "P90": [
            "Burning King",
            "Clash",
            "Cold Lead",
            "Data Flow",
            "Devil",
            "Dragon Flame",
            "Exit Strategy",
            "Fissure",
            "Foxfire",
            "Fury",
            "Fusion Core",
            "Ghoul",
            "Hologram",
            "Horizon",
            "Iron Will",
            "Jungle",
            "Lotus Frieze",
            "Nebula",
            "Noir",
            "Oops",
            "Oracle",
            "Pilgrim",
            "Pilot",
            "Poker",
            "Project X",
            "Purple Mist",
            "R.O.N.I.N. mk9",
            "Radiation",
            "Revenant",
            "Samurai",
            "Tester",
            "Unstoppable",
            "Z-50 F.U.J.I.N."
        ],
        "UMP45": [
            "4 Years",
            "Arid",
            "Beast",
            "Cerberus",
            "Combat Gear",
            "Cyberpunk",
            "Deep Cover",
            "Gas",
            "Geometric",
            "Green Dester",
            "HEXA",
            "Heatwave",
            "Industrial",
            "Iron",
            "Leviathan",
            "Luminous",
            "Monster",
            "Mortem",
            "Peaceful Dream",
            "Pixel",
            "Pixel V2",
            "Professional",
            "Shark",
            "Smelter",
            "Spirit",
            "VHS",
            "Warchief",
            "White Carbon",
            "Winged",
            "Yakuza"
        ]
    },
    "Дробовики": {
        "FabM": [
            "BOOM",
            "Cranium",
            "Crush",
            "Cursed Fire",
            "Death Herald",
            "Fiend",
            "Flight",
            "Gangster",
            "Green",
            "Hercules",
            "Mayhem",
            "Parrot",
            "Reactor",
            "Seven",
            "Streak",
            "Thief Of The Christmas",
            "Tindalos",
            "Tropic",
            "Undertow",
            "Vein",
            "Waste",
            "Wildstyle"
        ],
        "SM1014": [
            "Arctic",
            "Blaster",
            "Bolt",
            "Branches",
            "Cyberline",
            "Facet",
            "Falling Leaves",
            "Fatal Combo",
            "Freezing Gasp",
            "Necromancer",
            "Northern Camouflage",
            "Pathfinder",
            "Penguin Patrol",
            "Piranha",
            "Quake",
            "Serpent",
            "Shark",
            "Signal Loss",
            "Triton",
            "Tropic",
            "Wasp",
            "Wave"
        ],
        "SPAS": [
            "Dangerous",
            "Eternal Flow",
            "Firestorm",
            "Glide",
            "Griffin",
            "Hammer",
            "Kheper",
            "Octopus",
            "Raider",
            "Taint",
            "Tensai",
            "Umbra",
            "Unicorn",
            "Vesper Haze",
            "Vibe",
            "Waypoint",
            "Zeus"
        ]
    },
    "Пулемёты": {
        "M60": [
            "Anubis",
            "Ares",
            "Aura",
            "Copper",
            "Demon",
            "Demonic Fog",
            "Fatal",
            "Flex",
            "Grunge",
            "Horror",
            "Mecha",
            "Quantum",
            "Shale",
            "Spaceware",
            "Steam Beast",
            "Turret",
            "Warp",
            "Y-20 R.A.I.J.I.N.",
            "Yeti"
        ]
    }
}


DEFAULT_GAME_CATALOG: dict[str, dict[str, list[str]]] = copy.deepcopy(GAME_CATALOG)

_catalog_mtime: float = -1.0


def _sanitize_catalog_in_place(catalog_data: dict[str, dict[str, list[str]]]) -> bool:
    """Split any accidentally combined comma-separated skin names and remove empties."""
    modified = False
    if "Ножи" in catalog_data:
        del catalog_data["Ножи"]
        modified = True
    if "Перчатки" in catalog_data:
        del catalog_data["Перчатки"]
        modified = True
    for cat, wep_map in list(catalog_data.items()):
        for wep, skins in list(wep_map.items()):
            new_skins: list[str] = []
            seen: set[str] = set()
            for s in skins:
                parts = [p.strip() for p in s.split(",") if p.strip()]
                if len(parts) > 1 or (parts and parts[0] != s):
                    modified = True
                for part in parts:
                    if part not in seen:
                        seen.add(part)
                        new_skins.append(part)
            if new_skins != skins:
                wep_map[wep] = new_skins
                modified = True
    return modified


def save_catalog() -> bool:
    """Persist GAME_CATALOG to catalog.json atomically."""
    global _catalog_mtime
    try:
        CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        import os, tempfile; tf = tempfile.NamedTemporaryFile("w", dir=str(CATALOG_FILE.parent), delete=False, encoding="utf-8")
        with tf as f:
            json.dump(GAME_CATALOG, f, ensure_ascii=False, indent=2)
        os.replace(tf.name, CATALOG_FILE); _catalog_mtime = CATALOG_FILE.stat().st_mtime
        return True
    except Exception as exc:
        logger.error("Failed to save catalog.json: %s", exc)
        return False


def load_catalog() -> None:
    """Load catalog from catalog.json or initialize from defaults and custom_catalog.json."""
    global _catalog_mtime, GAME_CATALOG

    if CATALOG_FILE.is_file():
        try:
            mtime = CATALOG_FILE.stat().st_mtime
            with open(CATALOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    GAME_CATALOG.clear()
                    GAME_CATALOG.update(data)
                    _catalog_mtime = mtime
                    if _sanitize_catalog_in_place(GAME_CATALOG):
                        save_catalog()
                    return
        except Exception as exc:
            logger.error("Failed to read catalog.json: %s", exc)

    # If catalog.json does not exist, initialize from defaults
    GAME_CATALOG.clear()
    GAME_CATALOG.update(copy.deepcopy(DEFAULT_GAME_CATALOG))

    # Merge legacy custom_catalog.json if present
    if CUSTOM_CATALOG_FILE.is_file():
        try:
            with open(CUSTOM_CATALOG_FILE, "r", encoding="utf-8") as f:
                legacy_items = json.load(f)
                if isinstance(legacy_items, list):
                    for item in legacy_items:
                        cat = str(item.get("category", "")).strip()
                        wep = str(item.get("weapon", "")).strip()
                        raw_skn = str(item.get("skin", "")).strip()
                        if cat and wep and raw_skn:
                            if cat not in GAME_CATALOG:
                                GAME_CATALOG[cat] = {}
                            if wep not in GAME_CATALOG[cat]:
                                GAME_CATALOG[cat][wep] = []
                            for single_skin in [p.strip() for p in raw_skn.split(",") if p.strip()]:
                                if single_skin not in GAME_CATALOG[cat][wep]:
                                    GAME_CATALOG[cat][wep].append(single_skin)
        except Exception as exc:
            logger.warning("Failed to merge custom_catalog.json: %s", exc)

    _sanitize_catalog_in_place(GAME_CATALOG)
    save_catalog()


def sync_catalog_if_modified() -> None:
    """Check if catalog.json was modified on disk and reload if necessary."""
    global _catalog_mtime
    if not CATALOG_FILE.is_file():
        return
    try:
        current_mtime = CATALOG_FILE.stat().st_mtime
        if current_mtime != _catalog_mtime:
            load_catalog()
    except OSError:
        pass


def sync_custom_catalog_if_modified() -> None:
    """Backward compatibility alias."""
    sync_catalog_if_modified()


def load_custom_catalog() -> list[dict[str, str]]:
    """Backward compatibility alias."""
    load_catalog()
    return get_custom_skins()


def get_categories() -> list[str]:
    """Return all weapon categories."""
    sync_catalog_if_modified()
    return list(GAME_CATALOG.keys())


def get_weapons(category: str) -> list[str]:
    """Return weapon models available in a specific category."""
    sync_catalog_if_modified()
    return list(GAME_CATALOG.get(category, {}).keys())


def get_skins(category: str, weapon: str) -> list[str]:
    """Return skins available for a specific category and weapon model."""
    sync_catalog_if_modified()
    return list(GAME_CATALOG.get(category, {}).get(weapon, []))


def get_sticker_rarities() -> list[str]:
    """Return all valid sticker rarities."""
    return STICKER_RARITIES.copy()


def is_valid_selection(category: str, weapon: str, skin: str) -> bool:
    """Validate that the category, weapon, and skin exist in the catalog."""
    sync_catalog_if_modified()
    return skin in GAME_CATALOG.get(category, {}).get(weapon, [])


def is_valid_sticker_rarity(rarity: str) -> bool:
    """Check if the sticker rarity is valid."""
    return rarity in STICKER_RARITIES


def add_category(category: str) -> bool:
    """Add a new category to the catalog."""
    sync_catalog_if_modified()
    cat = category.strip()
    if not cat:
        return False
    if cat not in GAME_CATALOG:
        GAME_CATALOG[cat] = {}
        return save_catalog()
    return True


def edit_category(old_category: str, new_category: str) -> bool:
    """Rename an existing category."""
    sync_catalog_if_modified()
    old_c = old_category.strip()
    new_c = new_category.strip()
    if not old_c or not new_c or old_c not in GAME_CATALOG:
        return False
    if old_c == new_c:
        return True
    GAME_CATALOG[new_c] = GAME_CATALOG.pop(old_c)
    return save_catalog()


def delete_category(category: str) -> bool:
    """Delete a category and all its weapons/skins."""
    sync_catalog_if_modified()
    cat = category.strip()
    if cat in GAME_CATALOG:
        del GAME_CATALOG[cat]
        return save_catalog()
    return False


def add_weapon(category: str, weapon: str) -> bool:
    """Add a new weapon model to a category."""
    sync_catalog_if_modified()
    cat = category.strip()
    wep = weapon.strip()
    if not cat or not wep:
        return False
    if cat not in GAME_CATALOG:
        GAME_CATALOG[cat] = {}
    if wep not in GAME_CATALOG[cat]:
        GAME_CATALOG[cat][wep] = []
        return save_catalog()
    return True


def edit_weapon(category: str, old_weapon: str, new_weapon: str) -> bool:
    """Rename an existing weapon model in a category."""
    sync_catalog_if_modified()
    cat = category.strip()
    old_w = old_weapon.strip()
    new_w = new_weapon.strip()
    if not cat or not old_w or not new_w or cat not in GAME_CATALOG:
        return False
    if old_w not in GAME_CATALOG[cat]:
        return False
    if old_w == new_w:
        return True
    GAME_CATALOG[cat][new_w] = GAME_CATALOG[cat].pop(old_w)
    return save_catalog()


def delete_weapon(category: str, weapon: str) -> bool:
    """Delete a weapon model and its skins from a category."""
    sync_catalog_if_modified()
    cat = category.strip()
    wep = weapon.strip()
    if cat in GAME_CATALOG and wep in GAME_CATALOG[cat]:
        del GAME_CATALOG[cat][wep]
        return save_catalog()
    return False


def add_skin(category: str, weapon: str, skin: str) -> bool:
    """Add a skin (or multiple if comma-separated) to a weapon model."""
    sync_catalog_if_modified()
    cat = category.strip()
    wep = weapon.strip()
    if not cat or not wep or not skin:
        return False

    if cat not in GAME_CATALOG:
        GAME_CATALOG[cat] = {}
    if wep not in GAME_CATALOG[cat]:
        GAME_CATALOG[cat][wep] = []

    # If admin typed comma-separated names, split each cleanly
    skin_names = [s.strip() for s in skin.split(",") if s.strip()]
    if not skin_names:
        return False

    for name in skin_names:
        if name not in GAME_CATALOG[cat][wep]:
            GAME_CATALOG[cat][wep].append(name)

    return save_catalog()


def edit_skin(category: str, weapon: str, old_skin: str, new_skin: str) -> bool:
    """Rename a skin in a specific weapon model."""
    sync_catalog_if_modified()
    cat = category.strip()
    wep = weapon.strip()
    old_s = old_skin.strip()
    new_s = new_skin.strip()
    if not cat or not wep or not old_s or not new_s:
        return False
    if cat not in GAME_CATALOG or wep not in GAME_CATALOG[cat]:
        return False

    skins = GAME_CATALOG[cat][wep]
    if old_s not in skins:
        return False
    if old_s == new_s:
        return True

    idx = skins.index(old_s)
    skins[idx] = new_s
    return save_catalog()


def delete_skin(category: str, weapon: str, skin: str) -> bool:
    """Delete a skin from a weapon model."""
    sync_catalog_if_modified()
    cat = category.strip()
    wep = weapon.strip()
    skn = skin.strip()
    if cat in GAME_CATALOG and wep in GAME_CATALOG[cat]:
        if skn in GAME_CATALOG[cat][wep]:
            GAME_CATALOG[cat][wep].remove(skn)
            return save_catalog()
    return False


def reset_catalog_to_default() -> bool:
    """Reset the entire catalog to factory defaults."""
    global GAME_CATALOG
    GAME_CATALOG.clear()
    GAME_CATALOG.update(copy.deepcopy(DEFAULT_GAME_CATALOG))
    return save_catalog()


def get_custom_skins() -> list[dict[str, str]]:
    """Return all custom/modified skins that differ from factory defaults."""
    sync_catalog_if_modified()
    custom: list[dict[str, str]] = []
    for cat, wep_dict in GAME_CATALOG.items():
        for wep, skins in wep_dict.items():
            default_skins = set(DEFAULT_GAME_CATALOG.get(cat, {}).get(wep, []))
            for skin in skins:
                if skin not in default_skins:
                    custom.append({"category": cat, "weapon": wep, "skin": skin})
    return custom


def add_custom_skin(category: str, weapon: str, skin: str) -> bool:
    """Backward compatibility alias."""
    return add_skin(category, weapon, skin)


def delete_custom_skin(category: str, weapon: str, skin: str) -> bool:
    """Backward compatibility alias."""
    return delete_skin(category, weapon, skin)


# Initial load at module import
load_catalog()

