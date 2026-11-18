"""The game's enum values: map settings store names as Murmur3 (32-bit, seed 0) hashes (RESEARCH_NOTES.md §92).
Names found by hashing the game image's strings; readable names make the Map Settings panel."""
import struct


def murmur3(s, seed=0):
    data = s.encode(); h = seed; c1, c2 = 0xcc9e2d51, 0x1b873593; n = len(data) // 4
    for i in range(n):
        k = struct.unpack_from('<I', data, 4 * i)[0]
        k = (k * c1) & 0xffffffff; k = ((k << 15) | (k >> 17)) & 0xffffffff; k = (k * c2) & 0xffffffff
        h ^= k; h = ((h << 13) | (h >> 19)) & 0xffffffff; h = (h * 5 + 0xe6546b64) & 0xffffffff
    t = data[4 * n:]; k = 0
    for i, b in enumerate(t): k |= b << (8 * i)
    if t:
        k = (k * c1) & 0xffffffff; k = ((k << 15) | (k >> 17)) & 0xffffffff; k = (k * c2) & 0xffffffff; h ^= k
    h ^= len(data); h ^= h >> 16; h = (h * 0x85ebca6b) & 0xffffffff; h ^= h >> 13
    h = (h * 0xc2b2ae35) & 0xffffffff; h ^= h >> 16
    return h


# values the game's maps use (GameBancParam), by setting
NAMES = {
    'RenderingMode': ['MainField', 'Dungeon', 'Museum'],
    'CloudEnvType': ['HyruleField', 'Kokiri', 'Gerudo', 'Heights', 'GanonCastle', 'FixedSunHigh', 'FixedSunMid'],
    'EnvEffectType': ['HyruleField', 'Market', 'KakarikoVillage', 'Graveyard', 'Graveyard_Adult', 'LostWood',
                      'SacredForestMeadow', 'KokiriForest', 'ZoraRiver', 'ZoraDomain', 'ZoraDomain_Adult',
                      'ZoraFountain', 'ZoraFountain_Adult', 'GoronCity', 'DeathMountainTrail', 'DeathMountainCrater',
                      'GerudoValley', 'GerudoFortress', 'HountedWasteland', 'DesertColossus', 'HyruleCastle',
                      'GanonCastle', 'InsideGanonCastle', 'CrumblingGanonCastle', 'FinalBattleField',
                      'InsideTheDekuTree', 'DodongoCavern', 'InsideJabuJabuBelly', 'ForestTemple', 'FireTemple',
                      'WaterTemple', 'ShadowTemple', 'SpiritTemple', 'BottomOfTheWell', 'IceCavern',
                      'GerudoTrainingGround', 'EnemyMuseum'],
    'WorldMapLocationType': ['Field', 'Market', 'Castle', 'Village', 'Grave', 'Forest', 'LostWood', 'Meadow',
                             'DekuTree', 'River', 'ZoraDomain', 'Fountain', 'Jabujabu', 'IceCavern', 'GoronCity',
                             'Trail', 'Crater', 'Dodongo', 'Fire', 'Valley', 'Fortress', 'Desert', 'Colossus',
                             'Spirit', 'Shadow', 'Water', 'Well'],
    'NavMeshMgrMode': ['Single', 'Terrain'],
    'StaticCompoundMode': ['Single'],
    'VolumeStatsMode': ['Single'],
    'VrsType': ['Default', 'Disable'],
    'TerrainSceneCategoryForBake': ['Adult', 'Child'],
    'AreaCategory': ['DreamWorld'],
}
_BY_HASH = {murmur3(n): n for names in NAMES.values() for n in names}
_BY_HASH[murmur3('None')] = 'None'


def name_of(value):
    """A hashed enum value -> its name (or the number as text when unknown)."""
    try:
        return _BY_HASH.get(int(value), str(int(value)))
    except (TypeError, ValueError):
        return str(value)


def value_of(name):
    return murmur3(name)
